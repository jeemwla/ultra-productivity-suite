; Ultra Suite installer hooks (Tauri NSIS)

!macro NSIS_HOOK_POSTINSTALL
  ; Start menu entries for each app
  CreateDirectory "$SMPROGRAMS"
  CreateShortCut "$SMPROGRAMS\Code Red Alarm.lnk" "$INSTDIR\${MAINBINARYNAME}.exe" "--open alarm" "$INSTDIR\appicons\alarm.ico" 0
  CreateShortCut "$SMPROGRAMS\PC Sentinel.lnk" "$INSTDIR\${MAINBINARYNAME}.exe" "--open sentinel" "$INSTDIR\appicons\sentinel.ico" 0
  CreateShortCut "$SMPROGRAMS\Ultra Dock.lnk" "$INSTDIR\${MAINBINARYNAME}.exe" "--show-dock" "$INSTDIR\appicons\dock.ico" 0
  CreateShortCut "$SMPROGRAMS\deck Deck Setup.lnk" "$INSTDIR\${MAINBINARYNAME}.exe" "--deck" "$INSTDIR\appicons\deck.ico" 0

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
