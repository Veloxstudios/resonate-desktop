"""Runs in the separate Python 3.13 Demucs environment, not in the packaged UI."""
import json
import os
from pathlib import Path
import sys


def choose_device(torch,requested):
    if requested=='CPU':return 'cpu'
    if requested in ('Auto','NVIDIA CUDA') and torch.cuda.is_available():
        print('RESONATE:Using GPU: '+torch.cuda.get_device_name(0),flush=True)
        return 'cuda'
    if requested=='NVIDIA CUDA':
        print('RESONATE:CUDA is unavailable; using CPU for this task.',flush=True)
        return 'cpu'
    if requested in ('Auto','AMD DirectML (experimental)'):
        try:
            import torch_directml
            if torch_directml.is_available():
                device=torch_directml.device()
                print('RESONATE:Using experimental DirectML GPU acceleration.',flush=True)
                return device
        except (ImportError,RuntimeError,OSError):pass
    if requested=='AMD DirectML (experimental)':
        print('RESONATE:DirectML engine unavailable; using CPU. Install the optional Radeon engine to enable GPU processing.',flush=True)
    return 'cpu'


def directml_compatibility():
    # Demucs already runs FFT operations on CPU for MPS/XPU. Use that same
    # boundary for DirectML, whose complex FFT tensors are not supported.
    import inspect
    import textwrap
    import demucs.htdemucs as ht
    original=ht.spectro
    def spectro(x,*args,**kwargs):
        return original(x.cpu() if x.device.type=='privateuseone' else x,*args,**kwargs)
    ht.spectro=spectro
    source=textwrap.dedent(inspect.getsource(ht.HTDemucs.forward))
    marker='["mps", "xpu"]'
    if marker not in source:raise RuntimeError('This Demucs version needs a DirectML compatibility update')
    source=source.replace(marker,'["mps", "xpu", "privateuseone"]')
    namespace={}
    exec(compile(source,'<resonate-directml-adapter>','exec'),ht.__dict__,namespace)
    ht.HTDemucs.forward=namespace['forward']


def main():
    import numpy as np
    import soundfile as sf
    import torch
    from demucs.pretrained import get_model
    from demucs.apply import apply_model
    import torchaudio.functional as audio

    source, output, target = sys.argv[1:4]
    requested=sys.argv[4] if len(sys.argv)>4 else "Auto"
    device=choose_device(torch,requested)
    torch.set_num_threads(min(6, max(1, os.cpu_count() or 1)))
    # Official Demucs checkpoints include this harmless version string wrapper.
    if hasattr(torch.serialization,"add_safe_globals"):
        torch.serialization.add_safe_globals([torch.torch_version.TorchVersion])
    print('RESONATE:Loading the local six-stem model…', flush=True)
    model = get_model('htdemucs_6s')
    model.eval()
    if str(device).startswith('privateuseone'):
        try:directml_compatibility()
        except Exception:
            print('RESONATE:DirectML model adapter unavailable; using CPU.',flush=True);device='cpu'
    data, rate = sf.read(source, dtype='float32', always_2d=True)
    waveform = torch.from_numpy(data.T.copy())
    if waveform.shape[0] == 1:
        waveform = waveform.repeat(2, 1)
    if rate != model.samplerate:
        waveform = audio.resample(waveform, rate, model.samplerate)
    reference = waveform.mean(0)
    mean, deviation = reference.mean(), reference.std().clamp_min(1e-8)
    normalized = (waveform - mean) / deviation
    print('RESONATE:Separating on '+str(device)+' — vocals, drums, bass, guitar, piano and other sounds…', flush=True)
    try:
        with torch.inference_mode():
            separated = apply_model(model, normalized[None], device=device, split=True,
                                    segment=7, overlap=.25, shifts=0, progress=True)[0]
    except (RuntimeError,NotImplementedError) as error:
        if str(device)=='cpu':raise
        print('RESONATE:GPU could not run this model; retrying safely on CPU.',flush=True)
        del model
        if torch.cuda.is_available():torch.cuda.empty_cache()
        model=get_model('htdemucs_6s').cpu().eval()
        with torch.inference_mode():
            separated=apply_model(model,normalized[None],device='cpu',split=True,
                                  segment=7,overlap=.25,shifts=0,progress=True)[0]
    separated = separated * deviation + mean
    names = list(model.sources)
    stems = {name: separated[i].cpu().numpy().T for i, name in enumerate(names)}
    if target == 'instrumental':
        stems = {'instrumental': sum(value for name, value in stems.items() if name != 'vocals')}
    elif target != 'all':
        stems = {target: stems[target]}
    paths = []
    for name, samples in stems.items():
        peak = max(1., float(np.max(np.abs(samples))))
        path = Path(output) / (name + '.wav')
        sf.write(path, samples / peak, model.samplerate, subtype='PCM_24')
        paths.append(str(path.resolve()))
    print('STEMS:' + json.dumps(paths), flush=True)


if __name__ == '__main__':
    main()
