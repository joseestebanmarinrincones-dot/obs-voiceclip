@echo off
setlocal
chcp 65001 >nul
title ObsVoiceClip - Instalador
set "PS1=%~dp0install.ps1"
if not exist "%PS1%" set "PS1=%~dp0installer\install.ps1"
if not exist "%PS1%" (
  echo No se encontro install.ps1. Extrae el ZIP completo.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
if errorlevel 1 (
  echo.
  echo El instalador termino con errores.
  pause
)
endlocal
