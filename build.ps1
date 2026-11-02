# Builds dist\Resonate Desktop.exe (single file, FFmpeg bundled inside).
# Usage:  powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m pip install --quiet -r requirements.txt

# Grab the static FFmpeg that ships with imageio-ffmpeg and bundle it as ffmpeg.exe.
$ffmpeg = python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"
New-Item -ItemType Directory -Force build\bin | Out-Null
Copy-Item $ffmpeg build\bin\ffmpeg.exe -Force

python -m PyInstaller --noconfirm --onefile --windowed `
    --name "Resonate Desktop" --icon "..\resonate-taskbar.ico" `
    --exclude-module imageio_ffmpeg --collect-all customtkinter `
    --add-binary "bin\ffmpeg.exe;." `
    --add-data "..\resonate.ico;." --add-data "..\resonate-taskbar.ico;." --add-data "..\settings.ico;." --add-data "..\welcome.mp3;." --add-data "..\welcome-bed.mp3;." --add-data "..\loading-bed.mp3;." --add-data "..\loading-wave.json;." --add-data "..\click.wav;." `
    --workpath build --specpath build --distpath dist `
    resonate_desktop.py

Write-Host "Built: dist\Resonate Desktop.exe"
