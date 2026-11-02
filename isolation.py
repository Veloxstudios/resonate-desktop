"""Local stem-separation adapter; heavy inference runs outside the GUI process."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from isolation_runtime import launch_engine
from tkinter import filedialog


def find_engine(device="Auto"):
    roots = [Path(__file__).resolve().parent, Path(sys.executable).resolve().parent,
             Path.home() / 'Downloads' / 'Audio Converter']
    for root in roots:
        for folder in [root, *list(root.parents)[:3]]:
            engine='isolation-engine-directml' if device=='AMD DirectML (experimental)' else 'isolation-engine'
            python = folder / engine / 'Scripts' / 'python.exe'
            if not python.is_file() and engine!='isolation-engine':python=folder/'isolation-engine'/'Scripts'/'python.exe'
            if not python.is_file() and device=='Auto':python=folder/'isolation-engine-directml'/'Scripts'/'python.exe'
            if python.is_file():
                return python
    return None


def start_isolation(timeline):
    device=timeline.app.prefs.get("isolation_device","Auto")
    python = find_engine(device)
    if python is None:
        timeline.status.configure(text='AI isolation needs the local engine. Download setup-isolation.ps1 from the Resonate release page, put it next to Resonate Desktop.exe and run it (Settings > GPU setup notes has details).')
        return
    folder = filedialog.askdirectory(parent=timeline.app, title='Choose where to save isolated audio')
    if not folder:
        return
    clip = copy.deepcopy(timeline.clips[timeline.selected])
    target = timeline.isolation_mode.get()
    target = {'Vocals / voice': 'vocals', 'All 6 stems': 'all'}.get(target, target.lower())
    timeline.stop()
    timeline.busy = True
    timeline.cancelled = False
    timeline.status.configure(text='Preparing local AI isolation… First use may download model weights.')
    runner = Path(timeline.api.resource('isolation_runner.py'))
    if not runner.is_file():
        runner = Path(__file__).with_name('isolation_runner.py')

    def worker():
        from multitrack import mix_command
        try:
            with tempfile.TemporaryDirectory(prefix='resonate-isolate-') as scratch:
                prepared = str(Path(scratch) / 'selected.wav')
                clip['offset'] = 0
                clip['fade_in'] = clip['fade_out'] = 0
                command = mix_command(timeline.app.ffmpeg, [clip], prepared,
                                      codec=['-c:a', 'pcm_s16le', '-ar', '44100'])
                timeline.proc = subprocess.Popen(command, stdout=subprocess.DEVNULL,
                                                 stderr=subprocess.PIPE, creationflags=timeline.api.NO_WINDOW)
                _, error = timeline.proc.communicate()
                if timeline.proc.returncode or timeline.cancelled:
                    raise RuntimeError(error.decode(errors='replace')[-200:] or 'Isolation cancelled')
                if timeline.closed:
                    return
                output = Path(tempfile.mkdtemp(prefix='Resonate-stems-', dir=folder))
                environment = os.environ.copy()
                environment['TORCH_HOME'] = str(python.parent.parent / 'models')
                environment['PYTHONIOENCODING'] = 'utf-8'
                timeline.proc = launch_engine(python, [str(runner), prepared, str(output), target, device],
                                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                 encoding='utf-8', errors='replace', env=environment,
                                                 creationflags=timeline.api.NO_WINDOW)
                paths = None
                log = []
                for line in timeline.proc.stdout:
                    log.append(line.strip())
                    log = log[-20:]
                    if line.startswith('RESONATE:'):
                        timeline.messages.put(('notice', line.strip().split(':', 1)[1]))
                    elif line.startswith('STEMS:'):
                        paths = json.loads(line.split(':', 1)[1])
                code = timeline.proc.wait()
                if code or not paths:
                    raise RuntimeError('Isolation cancelled' if timeline.cancelled else 'Isolation failed: ' + ' '.join(log)[-350:])
                if not timeline.closed:
                    timeline.messages.put(('stems', (paths, timeline.clips[timeline.selected]['offset'])))
        except Exception as error:
            if not timeline.closed:
                timeline.messages.put(('finished', str(error)))

    threading.Thread(target=worker, daemon=True).start()
