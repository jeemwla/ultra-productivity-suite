; Ultra Suite Laptop Pack installer (add-on that installs on top of Ultra Suite)
; Build:  makensis /DVERSION=1.0.0 06_laptop_pack\installer.nsi   (build_laptop_pack.ps1 does this)
Unicode true
ManifestDPIAware true
!ifndef VERSION
  !define VERSION "1.0.0"
!endif
!define NAME "Ultra Suite Laptop Pack"
!define ADDON_DIR "$APPDATA\UltraSuite\addons\laptop_pack"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\UltraSuiteLaptopPack"

Name "${NAME}"
OutFile "..\build\dist\UltraSuite-LaptopPack-${VERSION}-x64.exe"
RequestExecutionLevel user
InstallDir "${ADDON_DIR}"
SetCompressor /SOLID lzma
BrandingText "Ultra Suite"

!include "MUI2.nsh"
!define MUI_ICON "..\02_translucent_taskbar_dock\src-tauri\appicons\sentinel.ico"
!define MUI_UNICON "..\02_translucent_taskbar_dock\src-tauri\appicons\sentinel.ico"
!define MUI_WELCOMEPAGE_TITLE "Laptop Pack for PC Sentinel"
!define MUI_WELCOMEPAGE_TEXT "Adds laptop tools to PC Sentinel:$\r$\n$\r$\n  - Battery health and wear compared with the battery's design capacity$\r$\n  - Charge cycles, charge and discharge rate in watts, voltage$\r$\n  - Time left on battery and time until full$\r$\n  - Battery health history from Windows' battery report$\r$\n  - Built-in screen brightness$\r$\n$\r$\nUltra Suite must already be installed. Works on any laptop brand. No admin rights needed."
!define MUI_FINISHPAGE_TITLE "Laptop Pack is installed"
!define MUI_FINISHPAGE_TEXT "Open PC Sentinel and choose Battery in the sidebar."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Var SuiteDir

Function .onInit
  ReadRegStr $SuiteDir HKCU "Software\Ultra Suite\Ultra Suite" ""
  StrCmp $SuiteDir "" 0 found
    MessageBox MB_YESNO|MB_ICONEXCLAMATION "Ultra Suite doesn't seem to be installed on this PC.$\r$\n$\r$\nThe Laptop Pack is an add-on for PC Sentinel in Ultra Suite. Install it anyway?" /SD IDYES IDYES found
    Abort
  found:
FunctionEnd

Section "Laptop Pack" SecMain
  SetOutPath "$INSTDIR"
  File "laptop_pack.py"
  File "README.md"
  WriteUninstaller "$INSTDIR\Uninstall Laptop Pack.exe"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${NAME}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "Ultra Suite"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$SuiteDir\appicons\sentinel.ico"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall Laptop Pack.exe"'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
  ; restart Ultra Suite so PC Sentinel loads the add-on
  StrCmp $SuiteDir "" done
  IfFileExists "$SuiteDir\Ultra Suite.exe" 0 done
    ExecWait '"$SuiteDir\Ultra Suite.exe" --quit'
    Sleep 1500
    Exec '"$SuiteDir\Ultra Suite.exe" --background'
  done:
SectionEnd

Section "Uninstall"
  Delete "$INSTDIR\laptop_pack.py"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\Uninstall Laptop Pack.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${UNINST_KEY}"
SectionEnd
