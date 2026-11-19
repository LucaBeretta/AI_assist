# Sets up the private Python listed in python-runtime.txt under .\runtime.
# Start AI Assist.cmd runs this only when no installed Python can open the
# window. The archive is accepted only if its SHA-256 digest matches the
# manifest. No administrator rights are needed and nothing else is changed.
# Exit codes: 0 installed, 1 failed, 2 declined by the user.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$root = $PSScriptRoot
$runtime = Join-Path $root 'runtime'
$staging = Join-Path $runtime '.staging'

$arch = $env:PROCESSOR_ARCHITEW6432
if (-not $arch) { $arch = $env:PROCESSOR_ARCHITECTURE }
if ($arch -eq 'AMD64') { $platform = 'x86_64-pc-windows-msvc' }
elseif ($arch -eq 'ARM64') { $platform = 'aarch64-pc-windows-msvc' }
else {
    Write-Host "A private Python is not available for this processor ($arch)."
    exit 1
}

$base = $null
$entry = $null
foreach ($line in Get-Content -LiteralPath (Join-Path $root 'python-runtime.txt')) {
    $fields = $line.Trim() -split '\s+'
    if ($fields[0] -eq 'base') { $base = $fields[1] }
    elseif ($fields[0] -eq $platform -and $fields.Count -ge 3) { $entry = $fields }
}
if (-not $base -or -not $entry -or $entry[2].Length -ne 64) {
    Write-Host "python-runtime.txt has no valid entry for $platform."
    exit 1
}
$file = $entry[1]
$expected = $entry[2].ToLowerInvariant()

Write-Host 'AI Assist can set up its own private Python with Tkinter (about 45 MB, downloaded from github.com).'
Write-Host "It is stored only in: $runtime"
Write-Host 'No administrator rights are needed and other Python installations are not changed.'
Write-Host 'This goes on the internet, so it needs two confirmations.'
$answer = Read-Host 'Download it now? [y/N]'
if ($answer -notmatch '^\s*[Yy]') { exit 2 }
$answer = Read-Host "Second check: type yes to download $file"
if ("$answer".Trim() -ne 'yes') { exit 2 }

function Remove-Staging {
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
}

try {
    Remove-Staging
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    $archive = Join-Path $staging $file
    # "+" must be escaped in the release URL.
    $url = $base + '/' + $file.Replace('+', '%2B')
    Write-Host "Downloading $file"
    $curl = Join-Path $env:SystemRoot 'System32\curl.exe'
    if (Test-Path -LiteralPath $curl) {
        & $curl -fL --proto '=https' --progress-bar -o $archive $url
        if ($LASTEXITCODE -ne 0) { throw 'The download failed. Check the internet connection and try again.' }
    } else {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $archive
    }

    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
    if ($actual -ne $expected) {
        throw 'The download did not match its expected SHA-256 checksum and was discarded.'
    }

    $tar = Join-Path $env:SystemRoot 'System32\tar.exe'
    if (-not (Test-Path -LiteralPath $tar)) { throw 'This Windows version has no tar.exe to unpack the download.' }
    & $tar -xzf $archive -C $staging
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $staging 'python\python.exe'))) {
        throw 'The downloaded Python could not be unpacked.'
    }

    $target = Join-Path $runtime 'python'
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
    Move-Item -LiteralPath (Join-Path $staging 'python') -Destination $target
    Remove-Staging
    Write-Host 'Private Python installed.'
    Write-Host ''
    exit 0
} catch {
    Write-Host $_.Exception.Message
    try { Remove-Staging } catch { }
    exit 1
}
