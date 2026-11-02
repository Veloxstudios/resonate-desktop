# Optional Radeon/DirectML engine. It is isolated from the CPU/CUDA environment.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$enginePath = Join-Path $PSScriptRoot 'isolation-engine-directml'
py -3.12 -c "import sys; assert sys.version_info[:2] == (3, 12)"
if ($LASTEXITCODE -ne 0) { throw 'Install 64-bit Python 3.12 first, then run this setup again.' }
py -3.12 -m venv $enginePath
$enginePython = Join-Path $enginePath 'Scripts\python.exe'
& $enginePython -m pip install --upgrade pip
& $enginePython -m pip install 'torch==2.4.1' 'torchaudio==2.4.1' --index-url https://download.pytorch.org/whl/cpu
if ($LASTEXITCODE -ne 0) { throw 'Could not install the DirectML-compatible PyTorch runtime.' }
& $enginePython -m pip install 'torch-directml==0.2.5.dev240914' 'demucs==4.1.0' 'numpy<2.4' soundfile
if ($LASTEXITCODE -ne 0) { throw 'Could not install the optional DirectML engine.' }
& $enginePython -c "import torch_directml; print('DirectML available:', torch_directml.is_available())"
Write-Host 'Choose AMD DirectML (experimental) in Resonate settings. Unsupported operations fall back to CPU.'
