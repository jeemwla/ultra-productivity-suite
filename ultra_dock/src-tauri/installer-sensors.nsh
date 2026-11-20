; Ultra Suite: optional "Full hardware sensors" step, shared by installer-hooks.nsh (Public) and
; installer-hooks-dev.nsh (Dev). Each edition passes its own service, folder and data names, so the Dev installer
; never sets up, changes or removes the Public helper (and the reverse).
;
; What the step does, after the files are installed:
;  - Asks once, Yes by default: "Set up full hardware sensors?" (installs the PC Sentinel sensor helper service and,
;    when it is missing or older than the pinned version, the free PawnIO driver). Windows asks for administrator
;    permission once; this per-user installer has no other elevation.
;  - Runs <install>\core\sensor-helper\UltraSuiteSensorHelper.exe --provision elevated. The helper copies itself to
;    Program Files, downloads the pinned official PawnIO installer, checks its SHA-256, runs "-install -silent"
;    (never -unrestricted; exit 0 and 3010 are success) and starts its service. See Provisioner.cs.
;  - Skips the question and the permission prompt when the helper is already set up at this version and PawnIO is
;    present at the pinned version or newer.
;  - Remembers a "No" so upgrades don't ask again; PC Sentinel's "Try again" button can still set it up later.
;  - Silent installs set it up only with /SENSORS=1. /SENSORS=0 skips it (and remembers No).
;  - A declined prompt, an offline PC or a blocked driver never fails the install: Ultra Suite installs and runs,
;    and PC Sentinel explains what is missing.
; Uninstalling removes this edition's helper service and its Program Files folder (one permission prompt), except
; during an /UPDATE install. PawnIO is never uninstalled: other programs (for example FanControl) use it too.

!define ULTRA_PAWNIO_PIN "2.2.0"
!define ULTRA_SENSOR_EXE "UltraSuiteSensorHelper.exe"

; EDITION: Public|Dev. SERVICE: Windows service name. FOLDER: Program Files folder name.
; CONF: roaming settings folder ($APPDATA\...). LOCAL: local data folder ($LOCALAPPDATA\...).
!macro ULTRA_SENSOR_SETUP EDITION SERVICE FOLDER CONF LOCAL
  Push $R0
  Push $R1
  Push $R2
  Push $R3
  Push $R4
  Push $R5
  Push $R6
  Push $R7
  Push $R8
  Push $R9
  StrCpy $R9 "$INSTDIR\core\sensor-helper\${ULTRA_SENSOR_EXE}"
  IfFileExists "$R9" 0 ultra_sens_done ; a build without the helper: nothing to set up

  ; Command line: /SENSORS=1 or /SENSORS=0
  StrCpy $R1 ""
  ClearErrors
  ${GetOptions} $CMDLINE "/SENSORS=" $R1
  IfErrors 0 +2
    StrCpy $R1 ""
  ReadINIStr $R2 "${CONF}\sensor_helper.ini" "choice" "full_sensors"

  ; What is there now (64-bit registry view, read-only)
  SetRegView 64
  ReadRegStr $R3 HKLM "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO" "DisplayVersion"
  ReadRegStr $R4 HKLM "SYSTEM\CurrentControlSet\Services\${SERVICE}" "ImagePath"
  SetRegView lastused
  StrCpy $R5 ""
  StrCpy $R6 ""
  ClearErrors
  FileOpen $R0 "$PROGRAMFILES64\${FOLDER}\version.txt" r
  IfErrors +3
    FileRead $R0 $R5
    FileClose $R0
  ClearErrors
  FileOpen $R0 "$INSTDIR\core\sensor-helper\version.txt" r
  IfErrors +3
    FileRead $R0 $R6
    FileClose $R0

  ; PawnIO needed when absent or older than the pin ($R7: 0 same, 1 installed is newer, 2 pin is newer)
  StrCpy $R8 "1"
  StrCmp $R3 "" ultra_sens_pawn_known
  ${VersionCompare} "$R3" "${ULTRA_PAWNIO_PIN}" $R7
  StrCmp $R7 "2" ultra_sens_pawn_known
    StrCpy $R8 "0"
  ultra_sens_pawn_known:

  StrCmp $R1 "0" ultra_sens_remember_no
  ; Already set up at this version with a current PawnIO: no question, no permission prompt.
  StrCmp $R4 "" ultra_sens_needed
  StrCmp $R6 "" ultra_sens_needed
  StrCmp $R5 $R6 0 ultra_sens_needed
  StrCmp $R8 "0" ultra_sens_done
  ultra_sens_needed:
  StrCmp $R1 "1" ultra_sens_run
  IfSilent ultra_sens_done
  StrCmp $R2 "no" ultra_sens_done
  StrCmp $R4 "" ultra_sens_ask_new
  StrCmp $R5 $R6 ultra_sens_ask_driver
    MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON1 "Update full hardware sensors?$\r$\n$\r$\nPC Sentinel's sensor helper has an update. Windows will ask for administrator permission once." IDYES ultra_sens_run
    Goto ultra_sens_remember_no
  ultra_sens_ask_driver:
    MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON1 "Finish setting up full hardware sensors?$\r$\n$\r$\nThe free PawnIO sensor driver isn't installed yet (or is older than PC Sentinel needs). Windows will ask for administrator permission once." IDYES ultra_sens_run
    Goto ultra_sens_remember_no
  ultra_sens_ask_new:
    MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON1 "Set up full hardware sensors? (recommended)$\r$\n$\r$\nPC Sentinel can show CPU temperatures, fan speeds and power. This installs its read-only sensor helper and, if needed, the free PawnIO driver from its official download. Windows will ask for administrator permission once.$\r$\n$\r$\nWithout it, PC Sentinel shows what Windows reports by itself. You can set it up later from PC Sentinel." IDYES ultra_sens_run
  ultra_sens_remember_no:
    CreateDirectory "${CONF}"
    WriteINIStr "${CONF}\sensor_helper.ini" "choice" "full_sensors" "no"
    Goto ultra_sens_done

  ultra_sens_run:
    CreateDirectory "${CONF}"
    CreateDirectory "${LOCAL}"
    WriteINIStr "${CONF}\sensor_helper.ini" "choice" "full_sensors" "yes"
    Delete "${LOCAL}\sensor-setup-result.ini"
    DetailPrint "Setting up full hardware sensors (Windows asks for permission)..."
    ClearErrors
    ExecShellWait "runas" "$R9" '--provision --edition ${EDITION} --pawnio --result "${LOCAL}\sensor-setup-result.ini"' SW_HIDE
    IfErrors 0 ultra_sens_read
      DetailPrint "Full hardware sensors were not set up (permission not given)."
      IfSilent ultra_sens_done
      MessageBox MB_OK|MB_ICONINFORMATION "Full hardware sensors were not set up because Windows permission wasn't given.$\r$\n$\r$\nUltra Suite works without them. You can set them up later from PC Sentinel."
      Goto ultra_sens_done
  ultra_sens_read:
    ReadINIStr $R0 "${LOCAL}\sensor-setup-result.ini" "result" "service"
    ReadINIStr $R1 "${LOCAL}\sensor-setup-result.ini" "result" "restart"
    ReadINIStr $R2 "${LOCAL}\sensor-setup-result.ini" "result" "message"
    ReadINIStr $R3 "${LOCAL}\sensor-setup-result.ini" "result" "pawnio"
    DetailPrint "Sensor helper: $R0, PawnIO: $R3"
    IfSilent ultra_sens_done
    StrCmp $R1 "1" 0 +3
      MessageBox MB_OK|MB_ICONINFORMATION "Full hardware sensors are almost ready.$\r$\n$\r$\nRestart the PC to finish installing the sensor driver."
      Goto ultra_sens_done
    StrCmp $R0 "running" 0 ultra_sens_problem
    StrCmp $R3 "present" ultra_sens_done
    StrCmp $R3 "installed" ultra_sens_done
  ultra_sens_problem:
    StrCmp $R2 "" ultra_sens_done
    MessageBox MB_OK|MB_ICONINFORMATION "Full hardware sensors: $R2$\r$\n$\r$\nUltra Suite is installed and works. PC Sentinel shows what is missing and has a Try again button."
  ultra_sens_done:
  Pop $R9
  Pop $R8
  Pop $R7
  Pop $R6
  Pop $R5
  Pop $R4
  Pop $R3
  Pop $R2
  Pop $R1
  Pop $R0
!macroend

!macro ULTRA_SENSOR_REMOVE EDITION SERVICE FOLDER LOCAL
  Push $R0
  Push $R4
  Push $R9
  ; An /UPDATE install replaces the program in place and keeps the helper.
  ${If} $UpdateMode <> 1
    SetRegView 64
    ReadRegStr $R4 HKLM "SYSTEM\CurrentControlSet\Services\${SERVICE}" "ImagePath"
    SetRegView lastused
    ${If} $R4 != ""
      StrCpy $R9 "$INSTDIR\core\sensor-helper\${ULTRA_SENSOR_EXE}"
      IfFileExists "$R9" +2
        StrCpy $R9 "$PROGRAMFILES64\${FOLDER}\${ULTRA_SENSOR_EXE}"
      DetailPrint "Removing the sensor helper service (Windows asks for permission). PawnIO stays installed."
      ClearErrors
      ExecShellWait "runas" "$R9" '--unprovision --edition ${EDITION}' SW_HIDE
      ${If} ${Errors}
        IfSilent +2
        MessageBox MB_OK|MB_ICONINFORMATION "The ${SERVICE} service was left in place because Windows permission wasn't given.$\r$\n$\r$\nTo remove it later, run this as administrator:$\r$\n$\"$PROGRAMFILES64\${FOLDER}\${ULTRA_SENSOR_EXE}$\" --unprovision --edition ${EDITION}"
      ${EndIf}
    ${EndIf}
  ${EndIf}
  Pop $R9
  Pop $R4
  Pop $R0
!macroend
