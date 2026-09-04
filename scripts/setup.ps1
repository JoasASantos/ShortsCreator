# ShortsCreator — setup for Windows PowerShell.
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#
# or, from an already-open PowerShell:
#
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   .\scripts\setup.ps1
#
# The -Scope Process form matters: it lasts only for this window. Loosening the
# policy machine-wide to run one setup script is a permanent hole for a
# temporary need, so this script warns and never suggests it.
#
# Idempotent, like the sh version: re-running it after fixing one missing
# dependency is the intended workflow. Nothing it could not do is fatal — it
# collects those and prints them at the end, then the doctor has the last word.

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$Venv = Join-Path $Root '.venv'
$Py = Join-Path $Venv 'Scripts\python.exe'
$Skipped = New-Object System.Collections.Generic.List[string]

function Say  ($m) { Write-Host ""; Write-Host "==> $m" -ForegroundColor White }
function Info ($m) { Write-Host "    $m" }
function Warn ($m) { Write-Host "    ! $m" -ForegroundColor Yellow }
function Skip ($m) { $Skipped.Add($m); Warn $m }
function Have ($n) { [bool](Get-Command $n -ErrorAction SilentlyContinue) }

# ------------------------------------------------------------ package manager

$Manager = ''
if (Have 'winget') { $Manager = 'winget' }
elseif (Have 'choco') { $Manager = 'choco' }

Say "ShortsCreator setup — Windows (package manager: $(if ($Manager) { $Manager } else { 'none detected' }))"

if (-not $Manager) {
  Warn "neither winget nor choco is available, so nothing can be installed"
  Warn "automatically. The download links are printed at the end."
}

# winget puts a freshly installed binary on the machine PATH, but this process
# inherited its environment when it started and will not see it. Re-reading
# both scopes into the current session is what makes `ffmpeg` usable in the
# same run instead of only after a reboot.
function Refresh-Path {
  $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
  $user    = [Environment]::GetEnvironmentVariable('Path', 'User')
  $env:Path = ($machine, $user | Where-Object { $_ }) -join ';'
}

# One package, three names: winget id, choco id, and the link for whoever has
# neither.
function Install-Pkg ($Label, $WingetId, $ChocoId, $Link) {
  switch ($Manager) {
    'winget' {
      Info "winget install $WingetId"
      winget install --id $WingetId --accept-source-agreements --accept-package-agreements -e
      Refresh-Path
    }
    'choco' {
      Info "choco install $ChocoId"
      choco install $ChocoId -y
      Refresh-Path
    }
    default { Skip "install $Label by hand: $Link" }
  }
}

# ---------------------------------------------------------------- python

Say "Python virtualenv"

# 3.11 is the floor: the backend uses X | Y unions and tomllib.
function Find-Python {
  foreach ($name in @('python', 'python3', 'py')) {
    if (-not (Have $name)) { continue }
    # not $args — that name is an automatic variable inside a function
    $probe = if ($name -eq 'py') { @('-3', '-c') } else { @('-c') }
    try {
      & $name @probe 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>$null
      if ($LASTEXITCODE -eq 0) { return $name }
    } catch { }
  }
  return $null
}

$PyBin = Find-Python
if (-not $PyBin) {
  Warn "no Python 3.11+ found"
  Install-Pkg 'Python 3.12' 'Python.Python.3.12' 'python' 'https://www.python.org/downloads/windows/'
  $PyBin = Find-Python
}

if (-not $PyBin) {
  Skip "install Python 3.11+, then re-run this script"
}
elseif (Test-Path $Py) {
  Info "$Venv already exists — reusing it"
}
else {
  Info "creating $Venv with $PyBin"
  if ($PyBin -eq 'py') { & py -3 -m venv $Venv } else { & $PyBin -m venv $Venv }
}

if (Test-Path $Py) {
  Say "Backend dependencies"
  # The activation script is .venv\Scripts\Activate.ps1 on Windows, not
  # .venv/bin/activate. Calling the venv's python by full path skips the
  # question entirely, and skips needing the execution policy for it too.
  Info "to activate this venv later:  .\.venv\Scripts\Activate.ps1"
  & $Py -m pip install --upgrade pip --quiet
  & $Py -m pip install -r backend\requirements.txt
}
else {
  Skip "python -m venv .venv  then  .venv\Scripts\pip install -r backend\requirements.txt"
}

# ------------------------------------------------------------------ node

Say "Node and the web dependencies"
if ((Have 'node') -and (Have 'npm')) {
  Info "node $(node --version), npm $(npm --version)"
}
else {
  Warn "node/npm missing"
  Install-Pkg 'Node 20+ LTS' 'OpenJS.NodeJS.LTS' 'nodejs-lts' 'https://nodejs.org/en/download'
}

if (Have 'npm') {
  Push-Location web
  try { npm install } finally { Pop-Location }
}
else {
  Skip "install Node 20+, then: cd web; npm install"
}

# ----------------------------------------------------------------- ffmpeg

Say "FFmpeg"
if ((Have 'ffmpeg') -and (Have 'ffprobe')) {
  Info (ffmpeg -version 2>$null | Select-Object -First 1)
}
else {
  # The .Full build is the one that carries libass; the plain Gyan.FFmpeg
  # essentials build does not, which costs the single-pass caption path below.
  Install-Pkg 'FFmpeg (full build)' 'Gyan.FFmpeg.Full' 'ffmpeg-full' 'https://www.gyan.dev/ffmpeg/builds/'
  if (-not (Have 'ffmpeg')) {
    Warn "ffmpeg is installed but not visible in this window yet."
    Warn "Close this PowerShell and open a new one, then re-run this script."
  }
}

# libass gets its own check because ffmpeg being on PATH says nothing about
# whether that build has the `ass` filter. The pipeline falls back to PNG
# overlays without it, so this is a note, not an error.
if (Have 'ffmpeg') {
  $filters = ffmpeg -hide_banner -filters 2>$null
  $hasAss = $filters | Where-Object { ($_ -split '\s+' | Where-Object { $_ }) -contains 'ass' }
  if ($hasAss) {
    Info "libass present — captions burn in one pass"
  }
  else {
    Warn "this FFmpeg build has no libass: captions fall back to PNG overlays"
    Warn "(slower render, same finished video). For a build with it, see: make doctor"
  }
}

# ----------------------------------------------------------------- yt-dlp

Say "yt-dlp"
if (Have 'yt-dlp') {
  Info "yt-dlp $(yt-dlp --version 2>$null)"
}
elseif ((Test-Path $Py) -and (& $Py -c 'import yt_dlp' 2>$null; $LASTEXITCODE -eq 0)) {
  # requirements.txt already installed it as a library; the console script is
  # only on PATH while the venv is active, and the pipeline imports it anyway.
  Info "yt-dlp available inside the venv"
}
elseif (Test-Path $Py) {
  & $Py -m pip install --upgrade yt-dlp
}
else {
  Skip "install yt-dlp: pip install yt-dlp"
}

# -------------------------------------------------------------------- env

Say "Configuration file"
if (Test-Path .env) {
  Info ".env already exists — left untouched"
}
elseif (Test-Path .env.example) {
  Copy-Item .env.example .env
  Info "created .env from .env.example — fill in the keys you need"
}
else {
  Skip "no .env.example to copy from"
}

# ----------------------------------------------------------------- doctor

Say "Checking what is still missing"
if (Test-Path $Py) {
  Push-Location backend
  try { & $Py -m app.pipeline.doctor } catch { } finally { Pop-Location }
}
else {
  Warn "no venv yet, so the doctor cannot run. Fix the items above and re-run."
}

if ($Skipped.Count -gt 0) {
  Say "Not done automatically — run these yourself:"
  foreach ($item in $Skipped) { Write-Host "    $item" }
}

Write-Host ""
Write-Host "Done. Fill in .env, then start the two services:"
Write-Host "  .venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --port 8000 --reload"
Write-Host "  cd web; npm run dev"
Write-Host ""
Write-Host "If PowerShell refused to run this file, it is the execution policy."
Write-Host "Allow it for this window only, never machine-wide:"
Write-Host "  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass"
