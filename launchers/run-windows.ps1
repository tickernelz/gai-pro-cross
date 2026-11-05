$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $PSScriptRoot
$python = if ($env:PYTHON) { $env:PYTHON } else { "py" }
$venv = Join-Path $here ".venv"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
  & $python -3 -m venv $venv
}
& (Join-Path $venv "Scripts\python.exe") -m pip install --quiet --upgrade pip
& (Join-Path $venv "Scripts\python.exe") -m pip install --quiet -e $here
& (Join-Path $venv "Scripts\python.exe") -m gai_pro serve @args
