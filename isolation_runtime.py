"""Launch the external Python engine without inheriting bundled Python DLLs."""
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import threading

_launch_lock = threading.Lock()


def engine_environment(python, environment=None):
    clean = dict(os.environ if environment is None else environment)
    for key in list(clean):
        upper = key.upper()
        if upper.startswith(('PYTHON', '_PYI', 'PYINSTALLER')):
            clean.pop(key)
    bundle = getattr(sys, '_MEIPASS', None)
    if bundle:
        bundle = os.path.normcase(os.path.abspath(bundle))
        def external(path):
            try:
                resolved = os.path.normcase(os.path.abspath(path))
                return resolved != bundle and not resolved.startswith(bundle + os.sep)
            except (OSError, ValueError):
                return False
        clean['PATH'] = os.pathsep.join(p for p in clean.get('PATH', '').split(os.pathsep) if p and external(p))
    clean['PYTHONIOENCODING'] = 'utf-8'
    clean['PYTHONUTF8'] = '1'
    clean['TORCH_HOME'] = str(Path(python).parent.parent / 'models')
    return clean


def launch_engine(python, arguments, **kwargs):
    """Sanitize only the child; restore the GUI's DLL directory immediately."""
    environment = engine_environment(python, kwargs.pop('env', None))
    command = [str(python), '-I', '-X', 'utf8', '-u', *map(str, arguments)]
    kwargs.setdefault('cwd', str(Path(python).parent))
    with _launch_lock:
        kernel = None
        previous = None
        if sys.platform == 'win32' and getattr(sys, 'frozen', False):
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.GetDllDirectoryW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p]
            kernel.GetDllDirectoryW.restype = ctypes.c_uint
            kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
            kernel.SetDllDirectoryW.restype = ctypes.c_int
            size = kernel.GetDllDirectoryW(0, None)
            buffer = ctypes.create_unicode_buffer(size + 1)
            kernel.GetDllDirectoryW(len(buffer), buffer)
            previous = buffer.value or None
            if not kernel.SetDllDirectoryW(None):
                raise ctypes.WinError(ctypes.get_last_error())
        try:
            return subprocess.Popen(command, env=environment, **kwargs)
        finally:
            if kernel is not None:
                kernel.SetDllDirectoryW(previous)
