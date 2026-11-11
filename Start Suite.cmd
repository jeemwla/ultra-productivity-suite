@echo off
title Ultra Productivity Suite
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_all_windows.ps1"
timeout /t 8 >nul
