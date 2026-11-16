# Construye helper/voice_helper.exe con PyInstaller.
# Requisitos: Python 3.12, PyInstaller y pyaudiowpatch instalados.
#   python -m pip install pyaudiowpatch pyinstaller vosk

[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$VoskDir = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$helperDir = Join-Path $root "helper"
$vendor = Join-Path $helperDir "vendor\vosk"
$dist = Join-Path $root "dist"
$work = Join-Path $root "build\pyinstaller"
$spec = Join-Path $root "build"

Write-Host "Python: $Python"
& $Python -c "import sys; print(sys.version)"
if ($LASTEXITCODE -ne 0) { throw "No se pudo ejecutar Python" }

if (-not $VoskDir) {
    $VoskDir = (& $Python -c "import pathlib, vosk; print(pathlib.Path(vosk.__file__).parent)").Trim()
}
if (-not (Test-Path -LiteralPath (Join-Path $VoskDir "libvosk.dll"))) {
    throw "No se encontro libvosk.dll. Instala vosk con: $Python -m pip install vosk"
}
New-Item -ItemType Directory -Force -Path $vendor | Out-Null
$resolvedVosk = (Resolve-Path -LiteralPath $VoskDir).Path
$resolvedVendor = (Resolve-Path -LiteralPath $vendor).Path
if ($resolvedVosk -ne $resolvedVendor) {
    Copy-Item -Path (Join-Path $VoskDir "*.dll") -Destination $vendor -Force
    Write-Host "DLL de Vosk copiadas a $vendor"
} else {
    Write-Host "Usando las DLL de Vosk ya presentes en $vendor"
}

& $Python -m PyInstaller `
    --noconfirm --clean --onefile --noconsole `
    --name voice_helper `
    --distpath $dist `
    --workpath $work `
    --specpath $spec `
    --add-binary "$vendor\*.dll;vendor\vosk" `
    --hidden-import _portaudiowpatch `
    (Join-Path $helperDir "voice_helper.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller fallo" }

$built = Join-Path $dist "voice_helper.exe"
if (-not (Test-Path -LiteralPath $built)) { throw "No se genero $built" }
Copy-Item -LiteralPath $built -Destination (Join-Path $helperDir "voice_helper.exe") -Force
Write-Host "Listo: $(Join-Path $helperDir 'voice_helper.exe') ($([math]::Round((Get-Item (Join-Path $helperDir 'voice_helper.exe')).Length / 1MB, 1)) MB)"
