; Ultra Suite installer hooks (Tauri NSIS)

!macro NSIS_HOOK_PREINSTALL
  ; Updating over a running copy: quit it cleanly first, so Ultra Dock puts every taskbar button back
  IfFileExists "$INSTDIR\${MAINBINARYNAME}.exe" 0 +3
    ExecWait '"$INSTDIR\${MAINBINARYNAME}.exe" --quit'
    Sleep 1500
!macroend

!macro NSIS_HOOK_POSTINSTALL
  ; Start menu entries for each app
  CreateDirectory "$SMPROGRAMS"
  CreateShortCut "$SMPROGRAMS\Code Red Alarm.lnk" "$INSTDIR\${MAINBINARYNAME}.exe" "--open alarm" "$INSTDIR\appicons\alarm.ico" 0
  CreateShortCut "$SMPROGRAMS\PC Sentinel.lnk" "$INSTDIR\${MAINBINARYNAME}.exe" "--open sentinel" "$INSTDIR\appicons\sentinel.ico" 0
  CreateShortCut "$SMPROGRAMS\Ultra Dock.lnk" "$INSTDIR\${MAINBINARYNAME}.exe" "--show-dock" "$INSTDIR\appicons\dock.ico" 0

  ; Clean up what older versions left behind: the deck shortcut, and the separate Laptop Pack add-on
  ; (its battery features are built into PC Sentinel now)
  Delete "$SMPROGRAMS\deck Deck Setup.lnk"
  RMDir /r "$APPDATA\UltraSuite\addons\laptop_pack"
  RMDir "$APPDATA\UltraSuite\addons"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\UltraSuiteLaptopPack"

  ; Ask once about anonymous stats (never in silent installs)
  CreateDirectory "$APPDATA\UltraSuite"
  IfSilent ultra_no_consent
  MessageBox MB_YESNO|MB_ICONQUESTION "Help make Ultra Suite better?$\r$\n$\r$\nSend anonymous install and uninstall counts. No names, files or personal data are collected, and you can turn this off at any time." IDYES ultra_yes
  ultra_no_consent:
    FileOpen $0 "$APPDATA\UltraSuite\consent.txt" w
    FileWrite $0 "no"
    FileClose $0
    Goto ultra_done
  ultra_yes:
    FileOpen $0 "$APPDATA\UltraSuite\consent.txt" w
    FileWrite $0 "yes"
    FileClose $0
    Exec '"$INSTDIR\${MAINBINARYNAME}.exe" --background --event install'
  ultra_done:
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ; Optional "why are you leaving?" survey (skipped in silent uninstalls)
  IfSilent +2
  ExecWait '"$INSTDIR\${MAINBINARYNAME}.exe" --uninstall-survey'
  ; Quit cleanly first so every taskbar button Ultra Dock tucked away comes back; force only as a last resort
  ExecWait '"$INSTDIR\${MAINBINARYNAME}.exe" --quit'
  Sleep 1500
  nsExec::Exec 'taskkill /IM "${MAINBINARYNAME}.exe" /F'
  nsExec::Exec 'taskkill /IM ultrasuite-core.exe /F'
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "UltraSuite"
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  Delete "$SMPROGRAMS\Code Red Alarm.lnk"
  Delete "$SMPROGRAMS\PC Sentinel.lnk"
  Delete "$SMPROGRAMS\Ultra Dock.lnk"
  Delete "$SMPROGRAMS\deck Deck Setup.lnk"
!macroend
