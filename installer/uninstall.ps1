# ObsVoiceClip - desinstalador para Windows
# Cierra OBS antes de ejecutarlo. No borra clips, configuracion ni el modelo por defecto.

$ErrorActionPreference = "Continue"
$obsConfig = Join-Path $env:APPDATA "obs-studio"
$target = Join-Path $obsConfig "scripts\ObsVoiceClip"

function Read-Text([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return "" }
    return [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
}
function Write-Text([string]$Path, [string]$Text) {
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}
function Get-IniValue([string]$Path, [string]$Section, [string]$Key) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $inSection = $false
    foreach ($line in [System.IO.File]::ReadAllLines($Path, [System.Text.Encoding]::UTF8)) {
        $trim = $line.Trim()
        if ($trim -match '^\[(.+)\]$') { $inSection = ($Matches[1] -ieq $Section); continue }
        if ($inSection -and $trim -match '^([^=]+)=(.*)$' -and $Matches[1].Trim() -ieq $Key) { return $Matches[2] }
    }
    return $null
}
function Set-IniValue([string]$Path, [string]$Section, [string]$Key, [string]$Value) {
    $lines = @()
    if (Test-Path -LiteralPath $Path) { $lines = @([System.IO.File]::ReadAllLines($Path, [System.Text.Encoding]::UTF8)) }
    $sectionIndex = -1; $keyIndex = -1; $inSection = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $trim = $lines[$i].Trim()
        if ($trim -match '^\[(.+)\]$') { $inSection = ($Matches[1] -ieq $Section); if ($inSection) { $sectionIndex = $i }; continue }
        if ($inSection -and $trim -match '^([^=]+)=' -and $Matches[1].Trim() -ieq $Key) { $keyIndex = $i; break }
    }
    if ($keyIndex -ge 0) { $lines[$keyIndex] = "$Key=$Value" }
    elseif ($sectionIndex -ge 0) {
        $list = New-Object System.Collections.Generic.List[string]
        $list.AddRange([string[]]$lines[0..$sectionIndex])
        $list.Add("$Key=$Value")
        if ($sectionIndex + 1 -lt $lines.Count) { $list.AddRange([string[]]$lines[($sectionIndex + 1)..($lines.Count - 1)]) }
        $lines = $list.ToArray()
    } else {
        $lines += @("", "[$Section]", "$Key=$Value")
    }
    Write-Text $Path (($lines -join "`r`n") + "`r`n")
}

if (Get-Process -Name "obs64", "obs32" -ErrorAction SilentlyContinue) {
    Write-Host "OBS esta abierto. Cierralo y vuelve a ejecutar el desinstalador." -ForegroundColor Yellow
    Read-Host "Pulsa Enter para salir" | Out-Null
    exit 1
}

$userIni = Join-Path $obsConfig "user.ini"
if (Test-Path -LiteralPath $userIni) {
    $sceneFile = Get-IniValue $userIni "Basic" "SceneCollectionFile"
    if ($sceneFile) {
        $scenePath = Join-Path $obsConfig "basic\scenes\$sceneFile"
        if (Test-Path -LiteralPath $scenePath) {
            Copy-Item -LiteralPath $scenePath -Destination "$scenePath.voiceclip-backup-uninstall" -Force -ErrorAction SilentlyContinue
            try {
                $collection = Get-Content -LiteralPath $scenePath -Raw -Encoding UTF8 | ConvertFrom-Json
                if ($collection.modules -and $collection.modules.'scripts-tool') {
                    $clean = @()
                    foreach ($entry in @($collection.modules.'scripts-tool')) {
                        if ($entry -and $entry.path -and ([System.IO.Path]::GetFileName(([string]$entry.path)) -ine "obs_voice_clip.py")) {
                            $clean += $entry
                        }
                    }
                    $collection.modules.'scripts-tool' = $clean
                    Write-Text $scenePath ($collection | ConvertTo-Json -Depth 100)
                }
            } catch {}
        }
    }
    try {
        $raw = Get-IniValue $userIni "BasicWindow" "ExtraBrowserDocks"
        if ($raw) {
            $parsed = ConvertFrom-Json $raw
            $docks = if ($parsed -is [System.Array]) { $parsed } elseif ($null -eq $parsed) { @() } else { @($parsed) }
            $cleanDocks = @($docks | Where-Object { $_.title -ine "VoiceClip" -and ([string]$_.url -notmatch "/panel") })
            Set-IniValue $userIni "BasicWindow" "ExtraBrowserDocks" (ConvertTo-Json -InputObject @($cleanDocks) -Compress -Depth 8)
        }
    } catch {}
}

if (Test-Path -LiteralPath $target) {
    Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Plugin eliminado de $target" -ForegroundColor Green
} else {
    Write-Host "No se encontro la instalacion en $target" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Los datos (configuracion, modelo y logs) siguen en:" -ForegroundColor Gray
Write-Host "  $env:LOCALAPPDATA\ObsVoiceClip" -ForegroundColor Gray
Write-Host "Puedes borrar esa carpeta si quieres eliminar todo." -ForegroundColor Gray
Read-Host "Pulsa Enter para salir" | Out-Null
