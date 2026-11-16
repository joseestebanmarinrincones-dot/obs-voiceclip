@echo off
setlocal
chcp 65001 >nul
title ObsVoiceClip - Desinstalador
set "PS1=%~dp0uninstall.ps1"
if not exist "%PS1%" set "PS1=%~dp0installer\uninstall.ps1"
if not exist "%PS1%" (
  echo No se encontro uninstall.ps1. Extrae el ZIP completo.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
endlocal
