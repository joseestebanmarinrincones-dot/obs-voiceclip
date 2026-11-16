# Empaqueta la release Windows en dist/obs-voiceclip-v<version>.zip
[CmdletBinding()]
param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$manifestPath = Join-Path $root "manifest.json"
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $Version) { $Version = [string]$manifest.version }
$dist = Join-Path $root "dist"
$stageRoot = Join-Path $dist "ObsVoiceClip"
$zipPath = Join-Path $dist "obs-voiceclip-v$Version.zip"

$helperBuild = Join-Path $root "dist\voice_helper"
$helperExe = Join-Path $helperBuild "voice_helper.exe"
if (-not (Test-Path -LiteralPath $helperExe)) {
    throw "Falta dist\voice_helper\voice_helper.exe. Ejecuta tools\build_helper.ps1 primero."
}
if (Test-Path -LiteralPath $stageRoot) { Remove-Item -LiteralPath $stageRoot -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stageRoot | Out-Null
New-Item -ItemType Directory -Force -Path $dist | Out-Null

# Archivos principales
Copy-Item -LiteralPath (Join-Path $root "obs_voice_clip.py") -Destination $stageRoot -Force
Copy-Item -LiteralPath (Join-Path $root "README.md") -Destination $stageRoot -Force
Copy-Item -LiteralPath (Join-Path $root "LEEME.txt") -Destination $stageRoot -Force
Copy-Item -LiteralPath (Join-Path $root "LICENSE") -Destination $stageRoot -Force
Copy-Item -LiteralPath $manifestPath -Destination $stageRoot -Force

# Web
$webStage = Join-Path $stageRoot "web"
New-Item -ItemType Directory -Force -Path $webStage | Out-Null
Copy-Item -Path (Join-Path $root "web\*") -Destination $webStage -Recurse -Force

# Helper: carpeta versionada (PyInstaller onedir) y fuente.
# La carpeta versionada evita que una actualizacion falle si el helper anterior
# sigue bloqueado por un proceso auxiliar que aun se esta cerrando.
$helperStage = Join-Path $stageRoot "helper"
New-Item -ItemType Directory -Force -Path $helperStage | Out-Null
$versionedHelperDir = Join-Path $helperStage ("voice_helper-$Version")
New-Item -ItemType Directory -Force -Path $versionedHelperDir | Out-Null
Copy-Item -Path (Join-Path $helperBuild "*") -Destination $versionedHelperDir -Recurse -Force
Copy-Item -LiteralPath (Join-Path $root "helper\voice_helper.py") -Destination $helperStage -Force

# Instalador
$installerStage = Join-Path $stageRoot "installer"
New-Item -ItemType Directory -Force -Path $installerStage | Out-Null
Copy-Item -Path (Join-Path $root "installer\*") -Destination $installerStage -Recurse -Force

# El instalador busca la raiz del paquete en el directorio padre de installer.
# Para que funcione desde la raiz extraida, copiamos tambien el lanzador alli.
Copy-Item -LiteralPath (Join-Path $root "installer\Instalar.bat") -Destination $stageRoot -Force
Copy-Item -LiteralPath (Join-Path $root "installer\Desinstalar.bat") -Destination $stageRoot -Force

if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
Compress-Archive -Path $stageRoot -DestinationPath $zipPath -CompressionLevel Optimal
Write-Host ""
Write-Host "Release: $zipPath" -ForegroundColor Green
Write-Host "Tamano: $([math]::Round((Get-Item $zipPath).Length / 1MB, 1)) MB" -ForegroundColor Gray
Get-FileHash -LiteralPath $zipPath -Algorithm SHA256 | Format-List
