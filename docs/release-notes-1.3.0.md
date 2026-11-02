# Resonate Desktop 1.3.0

## Multi-clip audio timeline

- New **Timeline** workspace inside the main Resonate window (Files → Timeline, or from the audio editor).
- Put multiple audio clips on separate lanes. Overlapping clips are mixed together.
- Drag clips to position them, drag their edges to trim, or type exact source in/out and timeline positions.
- Split at the playhead, remove clips, undo, zoom, scroll horizontally, and Fit.
- Save timeline projects and open them again (`.resonate.json`).
- Preview the combined mix, and export it in any of the nine supported formats.
- Per-clip **Fade in / Fade out** with adjustable durations, shown on the clip.
- Split and trimmed clips stay sample-aligned with the source, including MP3s.

## Audio effects and AI isolation

- Slow clips down or speed them up from **0.25× to 4×** without changing pitch.
- Per-clip **Reverse**.
- Clip lengths, waveforms, trimming and splitting all follow speed and reverse.
- **Local AI isolation** (Demucs) of vocals, instrumental, drums, bass, guitar, piano, other, or all six stems.
  - Results are saved as audio files and added to the timeline, lined up with the original clip.
  - Isolation separates musical stems. It does not reliably separate individual speakers.
  - **Needs the local isolation engine, installed once with `setup-isolation.ps1`** (attached below). Install 64-bit Python 3.13, put the script next to `Resonate-Desktop.exe` and run it. NVIDIA GPUs get the CUDA build automatically (about 2.5 GB download); add `-Cpu` for the smaller CPU-only build.

## Processing devices

- Settings → **AI isolation device**: Auto, CPU, NVIDIA CUDA, or AMD DirectML (experimental). Your choice is remembered.
- Tested with CUDA on an NVIDIA RTX 4060.
- If the GPU is unavailable or fails, isolation falls back to CPU automatically.
- **AMD DirectML is experimental and has not been tested on Radeon hardware.** It needs a separate optional setup (`setup-isolation-directml.ps1`).
- The device setting only affects AI isolation. Trimming and conversion always run on the CPU.
- GPU setup notes open from Settings.

## Editor and interaction

- The single-file editor now opens inside the main window, keeping playback, seeking, waveform selection, ID3 editing and precise trim controls.
- **Long recordings:** much faster trimmed exports (a 5 s cut near the end of a 2-hour MP3 went from 5.8 s to 0.3 s).
  - The editor loads in one pass, with progress and Cancel/Retry.
  - Clear errors for unreadable files and files with no audio.
- Optional **Fast trim**: copies the audio without re-encoding, when the output matches the source format and settings. Accurate re-encoding stays the default.
- Smoother zoom animation and drawing. In our tests both timelines zoom at about 60 FPS on a 1,000-pixel-wide view. That's a measured result on one PC, not a guarantee.
- Fixed playhead dragging on the ruler, the red line, and empty timeline space.
- Settings can be opened from the multi-clip timeline.

## New dropdowns

- Dropdowns open as borderless panels inside the app instead of native popups.
- True black in dark mode, with white highlighting and black text for the hovered and selected item.
- Checkmark on the selected item, clearer spacing, scrolling, arrow keys and Enter, and close with Esc or a click outside.
- Used for conversion options, timeline controls and the processing-device setting. Light mode is still supported.

## Still here

- Resonate app and taskbar icons, and the matching settings icon.
- Animated welcome screen (two white-and-blue halftone circles), with background music, a spoken greeting and click sounds.
- About eight seconds of startup loading with an audio-reactive waveform. The loading sound plays once at startup.
- Conversion screen with real progress, cancel, and rotating music captions.
- Light and dark mode.

---

**Download:** `Resonate-Desktop.exe` (Windows 10/11, 64-bit). FFmpeg is built in. For AI isolation, also download `setup-isolation.ps1` (or `setup-isolation-directml.ps1` for experimental AMD support). Windows SmartScreen may warn because the app isn't code-signed: choose *More info → Run anyway*.
