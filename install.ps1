<#
 media-tool installer for Windows (PowerShell 5.1+):

   irm https://raw.githubusercontent.com/mat-jan/media-tool/main/install.ps1 | iex

 Files downloaded by PowerShell carry no "Mark of the Web", so SmartScreen does not warn.
 Installs into %LOCALAPPDATA%\media-tool and creates a desktop shortcut. No administrator rights needed.
 Options (environment variables): MEDIA_TOOL_VERSION=v1.0.0-beta.1, MEDIA_TOOL_INSTALL_DIR=...
 Uninstall: $env:MEDIA_TOOL_UNINSTALL=1; irm .../install.ps1 | iex
#>
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Repo   = 'mat-jan/media-tool'
$Marker = '.media-tool-install'
$Dir    = if ($env:MEDIA_TOOL_INSTALL_DIR) { $env:MEDIA_TOOL_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA 'media-tool' }
$Lnk    = Join-Path ([Environment]::GetFolderPath('Desktop')) 'media-tool.lnk'

function Test-SafeToReplace($p) {
    if (-not (Test-Path $p)) { return $true }
    if (Test-Path (Join-Path $p $Marker)) { return $true }
    return -not (Get-ChildItem -Force $p | Select-Object -First 1)
}

if ($env:MEDIA_TOOL_UNINSTALL) {
    if (Test-Path $Dir) {
        if (-not (Test-Path (Join-Path $Dir $Marker))) { throw "$Dir was not created by this installer - refusing to delete it" }
        Remove-Item -Recurse -Force $Dir
        Write-Host "Removed $Dir"
    }
    if (Test-Path $Lnk) { Remove-Item -Force $Lnk }
    return
}

$asset = if ($env:PROCESSOR_ARCHITECTURE -eq 'AMD64') { 'windows-x64' } else { 'universal' }
if ($asset -eq 'universal') { Write-Host 'No prebuilt binary for this CPU - installing the universal package (needs Python 3.9+).' }

$tag = $env:MEDIA_TOOL_VERSION
if (-not $tag) {
    Write-Host 'Looking up the newest release...'
    $tag = (Invoke-RestMethod "https://api.github.com/repos/$Repo/releases")[0].tag_name
}
if (-not $tag) { throw 'Could not determine the newest release (GitHub API rate limit?). Set $env:MEDIA_TOOL_VERSION' }
$base = "https://github.com/$Repo/releases/download/$tag"

$tmp = Join-Path ([IO.Path]::GetTempPath()) ("media-tool-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    $zip = Join-Path $tmp 'pkg.zip'
    Write-Host "Downloading media-tool $tag ($asset)..."
    Invoke-WebRequest "$base/media-tool-$asset.zip" -OutFile $zip -UseBasicParsing

    try {
        $sums = (Invoke-WebRequest "$base/SHA256SUMS.txt" -UseBasicParsing).Content
        $want = ($sums -split "`n" | Where-Object { $_ -match " media-tool-$asset\.zip\s*$" } | Select-Object -First 1) -split '\s+' | Select-Object -First 1
        $got  = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
        if (-not $want -or $want.ToLower() -ne $got) { throw 'checksum mismatch - the download is corrupted or was tampered with' }
        Write-Host 'Checksum OK.'
    } catch [System.Net.WebException] {
        Write-Host 'Note: this release has no SHA256SUMS.txt - skipping checksum verification.'
    }

    Expand-Archive -Path $zip -DestinationPath (Join-Path $tmp 'x') -Force
    $src = Join-Path $tmp 'x\media-tool'
    if (-not (Test-Path $src)) { throw 'unexpected archive layout' }
    if (-not (Test-SafeToReplace $Dir)) { throw "$Dir exists and was not created by this installer - choose another MEDIA_TOOL_INSTALL_DIR" }
    if (Test-Path $Dir) { Remove-Item -Recurse -Force $Dir }
    New-Item -ItemType Directory -Path $Dir | Out-Null
    Copy-Item -Recurse -Force "$src\*" $Dir
    New-Item -ItemType File -Path (Join-Path $Dir $Marker) | Out-Null
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

try {
    $sh = New-Object -ComObject WScript.Shell
    $s = $sh.CreateShortcut($Lnk)
    $s.TargetPath = Join-Path $Dir 'Start-Windows.bat'
    $s.WorkingDirectory = $Dir
    $s.Save()
} catch { Write-Host 'Could not create the desktop shortcut (not critical).' }

Write-Host ''
Write-Host "Installed media-tool $tag in $Dir"
Write-Host 'Run it: double-click the "media-tool" shortcut on your desktop, or Start-Windows.bat in the install folder.'
Write-Host 'Uninstall: $env:MEDIA_TOOL_UNINSTALL=1; irm https://raw.githubusercontent.com/mat-jan/media-tool/main/install.ps1 | iex'
