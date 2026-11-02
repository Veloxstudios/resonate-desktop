# Installs the local AI isolation engine (Demucs) for Resonate Desktop.
# Put this file in the same folder as "Resonate Desktop.exe", then run:
#   powershell -ExecutionPolicy Bypass -File setup-isolation.ps1
# NVIDIA GPUs get the CUDA build automatically; use -Cpu to force the smaller CPU-only build.
# For AMD Radeon (experimental), use setup-isolation-directml.ps1 instead.
param(
    [switch]$Cpu,
    [string]$Target = (Join-Path $PSScriptRoot 'isolation-engine')
)
$ErrorActionPreference = 'Stop'

# PyTorch 2.6 supports Python 3.10 to 3.13; prefer the newest one installed.
$version = $null
foreach ($candidate in '3.13', '3.12', '3.11', '3.10') {
    & py "-$candidate" -c "import sys; assert sys.maxsize > 2**32" 2>$null
    if ($LASTEXITCODE -eq 0) { $version = $candidate; break }
}
if (-not $version) {
    throw 'Install 64-bit Python 3.13 from python.org (with the "py launcher" option), then run this setup again.'
}

$cuda = -not $Cpu -and [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue)
if ($cuda) {
    & nvidia-smi -L 2>$null | Out-Null
    $cuda = $LASTEXITCODE -eq 0
}
$index = if ($cuda) { 'https://download.pytorch.org/whl/cu124' } else { 'https://download.pytorch.org/whl/cpu' }
Write-Host "Using Python $version; installing the $(if ($cuda) { 'NVIDIA CUDA' } else { 'CPU' }) engine into $Target"
if ($cuda) { Write-Host 'The CUDA download is about 2.5 GB.' }

if (Test-Path (Join-Path $Target 'Scripts\python.exe')) {
    Write-Host 'Updating the existing engine.'
} else {
    & py "-$version" -m venv $Target
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the engine folder.' }
}
$python = Join-Path $Target 'Scripts\python.exe'
& $python -m pip install --upgrade pip
& $python -m pip install 'torch==2.6.0' 'torchaudio==2.6.0' --index-url $index
if ($LASTEXITCODE -ne 0) { throw 'Could not install PyTorch.' }
& $python -m pip install 'demucs==4.1.0' 'numpy<2.4' soundfile
if ($LASTEXITCODE -ne 0) { throw 'Could not install Demucs.' }

# Download the six-stem model now so the first isolation doesn't wait for it.
$env:TORCH_HOME = Join-Path $Target 'models'
& $python -c "import torch; torch.serialization.add_safe_globals([torch.torch_version.TorchVersion]); from demucs.pretrained import get_model; get_model('htdemucs_6s'); print('CUDA available:', torch.cuda.is_available())"
if ($LASTEXITCODE -ne 0) { throw 'Could not download the Demucs model.' }
Write-Host 'Done. Restart Resonate Desktop, then use Isolate in the timeline.'
