# ObsVoiceClip - instalador para Windows
# Ejecutar con: powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1
# Cierra OBS antes de ejecutarlo.

[CmdletBinding()]
param(
    [switch]$NoPrompt,
    [switch]$KeepBackups
)

$ErrorActionPreference = "Stop"
$script:Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$script:Utf8 = New-Object System.Text.UTF8Encoding($false)

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "  == $Text" -ForegroundColor Cyan
}

function Write-Ok([string]$Text) {
    Write-Host "  [OK] $Text" -ForegroundColor Green
}

function Write-Warn([string]$Text) {
    Write-Host "  [!] $Text" -ForegroundColor Yellow
}

function Write-Err([string]$Text) {
    Write-Host "  [X] $Text" -ForegroundColor Red
}

function Read-Text([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return "" }
    return [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
}

function Write-Text([string]$Path, [string]$Text) {
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    [System.IO.File]::WriteAllText($Path, $Text, $script:Utf8)
}

function Backup-File([string]$Path) {
    if (Test-Path -LiteralPath $Path) {
        $backup = "$Path.voiceclip-backup-$script:Stamp"
        if (-not (Test-Path -LiteralPath $backup)) {
            Copy-Item -LiteralPath $Path -Destination $backup -Force
            Write-Ok "Copia de seguridad: $backup"
        }
    }
}

function Get-IniValue([string]$Path, [string]$Section, [string]$Key) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $lines = [System.IO.File]::ReadAllLines($Path, [System.Text.Encoding]::UTF8)
    $inSection = $false
    foreach ($line in $lines) {
        $trim = $line.Trim()
        if ($trim -match '^\[(.+)\]$') {
            $inSection = ($Matches[1] -ieq $Section)
            continue
        }
        if ($inSection -and $trim -match '^([^=]+)=(.*)$') {
            if ($Matches[1].Trim() -ieq $Key) {
                return $Matches[2]
            }
        }
    }
    return $null
}

function Set-IniValue([string]$Path, [string]$Section, [string]$Key, [string]$Value) {
    $lines = @()
    if (Test-Path -LiteralPath $Path) {
        $lines = @([System.IO.File]::ReadAllLines($Path, [System.Text.Encoding]::UTF8))
    }
    $sectionIndex = -1
    $keyIndex = -1
    $inSection = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $trim = $lines[$i].Trim()
        if ($trim -match '^\[(.+)\]$') {
            $inSection = ($Matches[1] -ieq $Section)
            if ($inSection) { $sectionIndex = $i }
            continue
        }
        if ($inSection -and $trim -match '^([^=]+)=') {
            if ($Matches[1].Trim() -ieq $Key) { $keyIndex = $i; break }
        }
    }
    if ($keyIndex -ge 0) {
        $lines[$keyIndex] = "$Key=$Value"
    } elseif ($sectionIndex -ge 0) {
        $insertAt = $sectionIndex + 1
        $new = New-Object System.Collections.Generic.List[string]
        if ($insertAt -gt 0) { $new.AddRange([string[]]$lines[0..($insertAt - 1)]) }
        $new.Add("$Key=$Value")
        if ($insertAt -lt $lines.Count) { $new.AddRange([string[]]$lines[$insertAt..($lines.Count - 1)]) }
        $lines = $new.ToArray()
    } else {
        $new = New-Object System.Collections.Generic.List[string]
        $new.AddRange([string[]]$lines)
        if ($new.Count -gt 0 -and $new[$new.Count - 1] -ne "") { $new.Add("") }
        $new.Add("[$Section]")
        $new.Add("$Key=$Value")
        $lines = $new.ToArray()
    }
    Write-Text $Path (($lines -join "`r`n") + "`r`n")
}

function Find-Python312 {
    $candidates = @()
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        try {
            $resolved = & $py.Source -3.12 -c "import sys; print(sys.executable)" 2>$null
            if ($resolved) { $candidates += ($resolved | Select-Object -First 1) }
        } catch {}
    }
    $candidates += @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        "C:\Python312\python.exe",
        (Join-Path $env:ProgramFiles "Python312\python.exe")
    )
    foreach ($candidate in ($candidates | Where-Object { $_ } | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidate)) { continue }
        try {
            $version = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if (($version | Select-Object -First 1) -eq "3.12") {
                return (Resolve-Path -LiteralPath $candidate).Path
            }
        } catch {}
    }
    return $null
}

function Get-ActiveSceneCollectionFile([string]$ObsConfig) {
    $userIni = Join-Path $ObsConfig "user.ini"
    $fileName = Get-IniValue $userIni "Basic" "SceneCollectionFile"
    if ($fileName) {
        $path = Join-Path (Join-Path $ObsConfig "basic\scenes") $fileName
        if (Test-Path -LiteralPath $path) { return $path }
    }
    $scenes = Get-ChildItem -LiteralPath (Join-Path $ObsConfig "basic\scenes") -Filter "*.json" -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notlike "*.bak" } | Sort-Object LastWriteTime -Descending
    if ($scenes) { return $scenes[0].FullName }
    return $null
}

function Get-ActiveProfileIni([string]$ObsConfig) {
    $userIni = Join-Path $ObsConfig "user.ini"
    $profileDir = Get-IniValue $userIni "Basic" "ProfileDir"
    if ($profileDir) {
        $path = Join-Path (Join-Path $ObsConfig "basic\profiles") (Join-Path $profileDir "basic.ini")
        if (Test-Path -LiteralPath $path) { return $path }
    }
    $profiles = Get-ChildItem -LiteralPath (Join-Path $ObsConfig "basic\profiles") -Filter "basic.ini" -Recurse -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending
    if ($profiles) { return $profiles[0].FullName }
    return $null
}

Write-Host ""
Write-Host "  ==============================================" -ForegroundColor Magenta
Write-Host "   ObsVoiceClip - Instalador" -ForegroundColor White
Write-Host "   Clips por voz + overlay + GitHub para OBS" -ForegroundColor DarkGray
Write-Host "  ==============================================" -ForegroundColor Magenta
Write-Host ""

# 1. Comprobar OBS cerrado
$obsProcesses = Get-Process -Name "obs64", "obs32" -ErrorAction SilentlyContinue
if ($obsProcesses) {
    Write-Err "OBS Studio esta abierto. Cierralo completamente y vuelve a ejecutar el instalador."
    if (-not $NoPrompt) { Read-Host "Pulsa Enter para salir" | Out-Null }
    exit 1
}

$obsConfig = Join-Path $env:APPDATA "obs-studio"
if (-not (Test-Path -LiteralPath $obsConfig)) {
    Write-Err "No se encontro la configuracion de OBS en $obsConfig. Abre OBS al menos una vez."
    if (-not $NoPrompt) { Read-Host "Pulsa Enter para salir" | Out-Null }
    exit 1
}

$packageRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $packageRoot "obs_voice_clip.py"))) {
    Write-Err "No se encuentra obs_voice_clip.py junto al instalador. Extrae el ZIP completo."
    if (-not $NoPrompt) { Read-Host "Pulsa Enter para salir" | Out-Null }
    exit 1
}

# 2. Copiar archivos
Write-Step "Copiando el plugin"
$scriptsDir = Join-Path $obsConfig "scripts"
$target = Join-Path $scriptsDir "ObsVoiceClip"
New-Item -ItemType Directory -Force -Path $target | Out-Null
$items = @("obs_voice_clip.py", "README.md", "LEEME.txt", "LICENSE", "manifest.json", "web", "helper")
foreach ($item in $items) {
    $source = Join-Path $packageRoot $item
    if (-not (Test-Path -LiteralPath $source)) { continue }
    $destination = Join-Path $target $item
    if (Test-Path -LiteralPath $source -PathType Container) {
        New-Item -ItemType Directory -Force -Path $destination | Out-Null
        Copy-Item -Path (Join-Path $source "*") -Destination $destination -Recurse -Force
    } else {
        Copy-Item -LiteralPath $source -Destination $destination -Force
    }
}
# No copiar la carpeta vendor (los DLL van dentro del EXE); ahorra ~50 MB.
$vendor = Join-Path $target "helper\vendor"
if (Test-Path -LiteralPath $vendor) { Remove-Item -LiteralPath $vendor -Recurse -Force }
$helperExe = Get-ChildItem -LiteralPath (Join-Path $target "helper") -Filter "voice_helper*.exe" -File -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $helperExe) {
    Write-Warn "No se encontro el helper de voz. El plugin intentara usar Python del sistema."
} else {
    Write-Ok "Archivos copiados en $target"
}
if (-not (Test-Path -LiteralPath (Join-Path $target "installer"))) {
    New-Item -ItemType Directory -Force -Path (Join-Path $target "installer") | Out-Null
}
Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $target "installer\install.ps1") -Force -ErrorAction SilentlyContinue
$uninstaller = Join-Path $PSScriptRoot "uninstall.ps1"
if (Test-Path -LiteralPath $uninstaller) {
    Copy-Item -LiteralPath $uninstaller -Destination (Join-Path $target "installer\uninstall.ps1") -Force
}
foreach ($launcher in @("Instalar.bat", "Desinstalar.bat")) {
    $sourceLauncher = Join-Path $PSScriptRoot $launcher
    if (Test-Path -LiteralPath $sourceLauncher) {
        Copy-Item -LiteralPath $sourceLauncher -Destination (Join-Path $target $launcher) -Force
    }
}

# 3. Python de OBS
Write-Step "Configurando Python para OBS"
$python = Find-Python312
$userIni = Join-Path $obsConfig "user.ini"
if ($python) {
    $pythonDir = Split-Path -Parent $python
    Backup-File $userIni
    Set-IniValue $userIni "Python" "Path64bit" ($pythonDir -replace "\\", "/")
    Write-Ok "Python 3.12: $pythonDir"
} else {
    Write-Warn "No se encontro Python 3.12. Instalalo desde python.org y marca 'Add python.exe to PATH'."
    Write-Warn "Despues abre OBS > Herramientas > Scripts > Python Settings y selecciona la carpeta de Python."
}

# 4. Replay Buffer
Write-Step "Activando el Replay Buffer"
$profileIni = Get-ActiveProfileIni $obsConfig
if ($profileIni) {
    Backup-File $profileIni
    foreach ($section in @("SimpleOutput", "AdvOut")) {
        Set-IniValue $profileIni $section "RecRB" "true"
        Set-IniValue $profileIni $section "RecRBTime" "30"
        $currentSize = Get-IniValue $profileIni $section "RecRBSize"
        if (-not $currentSize -or [int]$currentSize -lt 512) {
            Set-IniValue $profileIni $section "RecRBSize" "512"
        }
    }
    Write-Ok "Replay Buffer activado en $profileIni"
} else {
    Write-Warn "No se encontro el perfil activo. Activa Repeticion en Ajustes > Salida."
}

# 5. Cargar script en la coleccion de escenas
Write-Step "Registrando el script en OBS"
$sceneFile = Get-ActiveSceneCollectionFile $obsConfig
if ($sceneFile) {
    Backup-File $sceneFile
    try {
        $collection = Get-Content -LiteralPath $sceneFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not $collection.PSObject.Properties.Name.Contains("modules") -or $null -eq $collection.modules) {
            $collection | Add-Member -NotePropertyName "modules" -NotePropertyValue ([pscustomobject]@{}) -Force
        }
        if (-not $collection.modules.PSObject.Properties.Name.Contains("scripts-tool") -or $null -eq $collection.modules.'scripts-tool') {
            $collection.modules | Add-Member -NotePropertyName "scripts-tool" -NotePropertyValue @() -Force
        }
        $scriptPath = (Join-Path $target "obs_voice_clip.py") -replace "\\", "/"
        $entries = @($collection.modules.'scripts-tool')
        $found = $false
        $clean = @()
        foreach ($entry in $entries) {
            if (-not $entry -or -not $entry.path) { continue }
            $entryPath = [string]$entry.path
            $normalized = $entryPath -replace "\\", "/"
            if ($normalized -ieq $scriptPath -or ([System.IO.Path]::GetFileName($normalized) -ieq "obs_voice_clip.py")) {
                $entry.path = $scriptPath
                $clean += $entry
                $found = $true
            } else {
                $clean += $entry
            }
        }
        if (-not $found) {
            $clean += [pscustomobject]@{ path = $scriptPath; settings = [pscustomobject]@{} }
        }
        $collection.modules.'scripts-tool' = $clean
        $json = $collection | ConvertTo-Json -Depth 100
        Write-Text $sceneFile $json
        Write-Ok "Script registrado en $(Split-Path -Leaf $sceneFile)"
    } catch {
        Write-Warn "No se pudo registrar el script en la coleccion: $($_.Exception.Message)"
        Write-Warn "Anade manualmente: Herramientas > Scripts > + > $target\obs_voice_clip.py"
    }
} else {
    Write-Warn "No se encontro la coleccion de escenas activa."
}

# 6. Dock del panel
Write-Step "Añadiendo el dock del panel"
try {
    Backup-File $userIni
    $raw = Get-IniValue $userIni "BasicWindow" "ExtraBrowserDocks"
    $docks = @()
    if ($raw) {
        try {
            $parsed = ConvertFrom-Json $raw
            if ($null -eq $parsed) { $docks = @() }
            elseif ($parsed -is [System.Array]) { $docks = $parsed }
            else { $docks = @($parsed) }
        } catch { $docks = @() }
    }
    $exists = $false
    foreach ($dock in $docks) {
        if ($dock.title -ieq "VoiceClip" -or ([string]$dock.url -match "/panel")) { $exists = $true; break }
    }
    if (-not $exists) {
        $docks += [pscustomobject]@{
            title = "VoiceClip"
            url = "http://127.0.0.1:17877/panel?dock=1"
            uuid = [guid]::NewGuid().ToString()
        }
        $json = ConvertTo-Json -InputObject @($docks) -Compress -Depth 8
        Set-IniValue $userIni "BasicWindow" "ExtraBrowserDocks" $json
        Write-Ok "Dock VoiceClip añadido (aparecera al abrir OBS)."
    } else {
        Write-Ok "El dock VoiceClip ya estaba configurado."
    }
} catch {
    Write-Warn "No se pudo añadir el dock automaticamente: $($_.Exception.Message)"
    Write-Warn "Anadelo en Ver > Docks > Docks de navegador personalizados con la URL http://127.0.0.1:17877/panel?dock=1"
}

# 7. Resumen
Write-Host ""
Write-Host "  ==============================================" -ForegroundColor Green
Write-Host "   Instalacion completada" -ForegroundColor Green
Write-Host "  ==============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Panel:   http://127.0.0.1:17877/panel" -ForegroundColor White
Write-Host "  Overlay: http://127.0.0.1:17877/overlay?overlay=1" -ForegroundColor White
Write-Host ""
Write-Host "  Al abrir OBS:" -ForegroundColor Cyan
Write-Host "   1. El plugin aparecera cargado (Herramientas > Scripts)." -ForegroundColor Gray
Write-Host "   2. El dock VoiceClip aparecera en la interfaz. Si no, anadelo a mano." -ForegroundColor Gray
Write-Host "   3. El modelo de voz se descarga solo (~40 MB) la primera vez." -ForegroundColor Gray
Write-Host "   4. Configura tu token de GitHub en el panel para subir los clips." -ForegroundColor Gray
Write-Host "   5. Pulsa 'Anadir a la escena actual' para crear el overlay." -ForegroundColor Gray
Write-Host ""
Write-Host "  Si OBS pide reiniciar para el Replay Buffer, cierralo y vuelve a abrirlo una vez." -ForegroundColor Yellow
Write-Host ""

if (-not $NoPrompt) {
    $open = Read-Host "¿Abrir OBS ahora? (s/N)"
    if ($open -match '^[sSyY]') {
        $obsExe = Join-Path $env:ProgramFiles "obs-studio\bin\64bit\obs64.exe"
        if (Test-Path -LiteralPath $obsExe) {
            Start-Process -FilePath $obsExe -WorkingDirectory (Split-Path -Parent $obsExe)
        } else {
            Write-Warn "No se encontro obs64.exe. Abre OBS manualmente."
            Read-Host "Pulsa Enter para salir" | Out-Null
        }
    }
}
