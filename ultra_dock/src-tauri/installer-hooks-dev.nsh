; Ultra Suite (Dev) installer hooks (Tauri NSIS). Used only by build_release.ps1 -Edition Dev.
; The Dev edition installs next to the consumer edition and must never stop, change or remove it: every name here is
; the Dev one (Ultra Suite Dev.exe, ultrasuite-dev-core.exe, %APPDATA%\UltraSuite-Dev, Run value UltraSuiteDev).

!macro NSIS_HOOK_PREINSTALL
  ; Updating over a running Dev copy: quit it cleanly first, so its Dock puts every taskbar button back
  IfFileExists "$INSTDIR\${MAINBINARYNAME}.exe" 0 +3
    ExecWait '"$INSTDIR\${MAINBINARYNAME}.exe" --quit'
    Sleep 1500
  ; A Dev core left from an earlier session keeps its files locked. Only the Dev core is stopped, never the consumer one.
  nsExec::Exec 'taskkill /IM ultrasuite-dev-core.exe /F'
  Sleep 1000
  StrCmp "$INSTDIR" "" ultra_dev_skip_clean
  IfFileExists "$INSTDIR\${MAINBINARYNAME}.exe" 0 ultra_dev_skip_clean
    RMDir /r "$INSTDIR\core"
    RMDir /r "$INSTDIR\appicons"
  ultra_dev_skip_clean:
!macroend

!macro NSIS_HOOK_POSTINSTALL
  CreateDirectory "$SMPROGRAMS"
  CreateShortCut "$SMPROGRAMS\Code Red Alarm (Dev).lnk" "$INSTDIR\${MAINBINARYNAME}.exe" "--open alarm" "$INSTDIR\appicons\alarm.ico" 0
  CreateShortCut "$SMPROGRAMS\PC Sentinel (Dev).lnk" "$INSTDIR\${MAINBINARYNAME}.exe" "--open sentinel" "$INSTDIR\appicons\sentinel.ico" 0
  CreateShortCut "$SMPROGRAMS\Ultra Dock (Dev).lnk" "$INSTDIR\${MAINBINARYNAME}.exe" "--show-dock" "$INSTDIR\appicons\dock.ico" 0
  CreateShortCut "$SMPROGRAMS\Insights (Dev).lnk" "$INSTDIR\${MAINBINARYNAME}.exe" "--open insights" "$INSTDIR\appicons\insights.ico" 0
  ; The Dev edition never sends anonymous statistics, so it does not ask.
  CreateDirectory "$APPDATA\UltraSuite-Dev"
  FileOpen $0 "$APPDATA\UltraSuite-Dev\consent.txt" w
  FileWrite $0 "no"
  FileClose $0
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ; No survey in the Dev edition. Quit cleanly first so every taskbar button comes back; force only as a last resort.
  ExecWait '"$INSTDIR\${MAINBINARYNAME}.exe" --quit'
  Sleep 1500
  nsExec::Exec 'taskkill /IM "${MAINBINARYNAME}.exe" /F'
  nsExec::Exec 'taskkill /IM ultrasuite-dev-core.exe /F'
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "UltraSuiteDev"
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  Delete "$SMPROGRAMS\Code Red Alarm (Dev).lnk"
  Delete "$SMPROGRAMS\PC Sentinel (Dev).lnk"
  Delete "$SMPROGRAMS\Ultra Dock (Dev).lnk"
  Delete "$SMPROGRAMS\Insights (Dev).lnk"
!macroend
