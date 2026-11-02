"""Resonate Desktop - a minimal black batch audio converter for Windows (uses ffmpeg)."""

import math
import time
import tkinter as tk
import wave
import queue
import json
import os
import re
import subprocess
import sys
import threading
import webbrowser
from tkinter import filedialog

import customtkinter as ctk
from dropdowns import ResonateDropdown
import numpy as np
from PIL import Image, ImageOps, ImageDraw, ImageTk, ImageGrab

APP_NAME = "Resonate Desktop"
VERSION = "1.3.0"
REPO_URL = "https://github.com/Veloxstudios/resonate-desktop"
ICON_FILE = "resonate-taskbar.ico"
SETTINGS_ICON_FILE = "settings.ico"
SETTINGS_PATH = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME,
                             "settings.json")

# label -> (extension, ffmpeg codec args, supports bitrate)
FORMATS = {
    "MP3": ("mp3", ["-c:a", "libmp3lame"], True),
    "WAV": ("wav", ["-c:a", "pcm_s16le"], False),
    "FLAC": ("flac", ["-c:a", "flac"], False),
    "AAC": ("m4a", ["-c:a", "aac"], True),
    "ALAC": ("m4a", ["-c:a", "alac"], False),
    "OGG": ("ogg", ["-c:a", "libvorbis"], True),
    "OPUS": ("opus", ["-c:a", "libopus"], True),
    "WMA": ("wma", ["-c:a", "wmav2"], True),
    "AIFF": ("aiff", ["-c:a", "pcm_s16be"], False),
}
# Source codec (as ffmpeg names it) that each output format can copy without re-encoding.
COPY_CODECS = {"MP3": "mp3", "WAV": "pcm_s16le", "FLAC": "flac", "AAC": "aac", "ALAC": "alac",
               "OGG": "vorbis", "OPUS": "opus", "WMA": "wmav2", "AIFF": "pcm_s16be"}
BITRATES = ["64k", "96k", "128k", "160k", "192k", "256k", "320k"]
SAMPLE_RATES = ["Original", "22050", "44100", "48000", "96000"]
CHANNELS = ["Original", "Mono", "Stereo"]
DESTINATIONS = ["Same folder", "Choose"]

DEFAULT_PREFS = {
    "isolation_device": "Auto",
    "theme": "Dark",
    "format": "MP3",
    "bitrate": "192k",
    "sample_rate": SAMPLE_RATES[0],
    "channels": CHANNELS[0],
    "destination": DESTINATIONS[0],
    "output_dir": os.path.join(os.path.expanduser("~"), "Music"),
    "overwrite": False,
    "remember": True,
    "open_folder": False,
    "sound": True,
}
PREF_CHOICES = {"isolation_device": ["Auto", "CPU", "NVIDIA CUDA", "AMD DirectML (experimental)"], "theme": ["Light", "Dark"], "format": list(FORMATS), "bitrate": BITRATES, "sample_rate": SAMPLE_RATES,
                "channels": CHANNELS, "destination": DESTINATIONS}

INPUT_EXTS = (".mp3 .wav .flac .m4a .aac .ogg .opus .wma .aiff .aif .alac .amr .ac3 "
              ".mka .mp4 .mkv .mov .avi .webm .wmv .flv").split()
INPUT_TYPES = [("Audio / video files", " ".join("*" + e for e in INPUT_EXTS)),
               ("All files", "*.*")]

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Palette: true black window, off-black rounded cards, white accents.
BG = ('#ffffff', '#000000')
CARD = ('#f4f4f4', '#0e0e0e')
CARD_BORDER = ('#dddddd', '#1c1c1c')
ROW = ('#eaeaea', '#151515')
ROW_HOVER = ('#dedede', '#1d1d1d')
CONTROL = ('#e8e8e8', '#171717')
CONTROL_HOVER = ('#dadada', '#222222')
SELECTED = ('#151515', '#f2f2f2')
SEGMENT_ON = ('#cccccc', '#343434')
TEXT = ('#111111', '#f2f2f2')
TEXT_ON_LIGHT = ('#ffffff', '#0a0a0a')
MUTED = ('#555555', '#999999')
FAINT = ('#666666', '#777777')
OK = "#9be7b0"
BAD = "#ff8a8a"
RADIUS = 14


def resource(name):
    """A file bundled inside the exe, or next to this script."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def load_prefs():
    prefs = dict(DEFAULT_PREFS)
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return prefs
    for key, default in DEFAULT_PREFS.items():
        value = saved.get(key)
        if type(value) is not type(default):
            continue
        if key in PREF_CHOICES and value not in PREF_CHOICES[key]:
            continue
        prefs[key] = value
    return prefs


def save_prefs(prefs):
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2)
    except OSError:
        pass


def ffmpeg_version(ffmpeg):
    try:
        out = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True,
                             errors="replace", creationflags=NO_WINDOW).stdout
        m = re.search(r"ffmpeg version (\S+)", out)
        return m.group(1).split("-")[0] if m else "unknown"
    except OSError:
        return "not found"


def elide(text, limit=72):
    return text if len(text) <= limit else text[:limit // 2 - 1] + "…" + text[-(limit // 2):]


def find_ffmpeg():
    """ffmpeg.exe bundled inside the exe, next to it, or from imageio-ffmpeg."""
    if getattr(sys, "frozen", False):
        for base in (getattr(sys, "_MEIPASS", ""), os.path.dirname(sys.executable)):
            path = os.path.join(base, "ffmpeg.exe")
            if os.path.isfile(path):
                return path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def get_duration(ffmpeg, path):
    """Duration in seconds, read from ffmpeg's file info (0 if unknown)."""
    result = subprocess.run([ffmpeg, "-hide_banner", "-i", path],
                            capture_output=True, text=True, errors="replace",
                            creationflags=NO_WINDOW)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    if not m:
        return 0.0
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def probe_audio(ffmpeg, path):
    """Duration and first audio stream details from ffmpeg's file info."""
    result = subprocess.run([ffmpeg, "-hide_banner", "-i", path], capture_output=True, text=True,
                            errors="replace", creationflags=NO_WINDOW)
    info = {"duration": 0.0, "codec": None, "rate": 0, "channels": "", "bitrate": 0, "error": ""}
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    if m:
        h, mnt, s = m.groups()
        info["duration"] = int(h) * 3600 + int(mnt) * 60 + float(s)
    line = next((l for l in result.stderr.splitlines() if "Stream #" in l and "Audio:" in l), "")
    if line:
        codec = re.search(r"Audio: (\w+)", line)
        rate = re.search(r"(\d+) Hz", line)
        channels = re.search(r"Hz, ([^,(]+)", line)
        bitrate = re.search(r"(\d+) kb/s", line)
        info.update(codec=codec and codec.group(1), rate=int(rate.group(1)) if rate else 0,
                    channels=channels.group(1).strip() if channels else "",
                    bitrate=int(bitrate.group(1)) if bitrate else 0)
    elif "Input #0" not in result.stderr:
        lines = [l.strip() for l in result.stderr.splitlines() if l.strip()]
        info["error"] = lines[-1] if lines else "FFmpeg could not read this file"
    return info


def column_peaks(values, origin, bin_seconds, start, span, count):
    """Largest peak (0..1) in each of `count` equal columns covering [start, start+span).

    `values` holds one peak per bin of `bin_seconds`, the first bin starting at `origin`.
    """
    n = len(values)
    if not n or count < 1:
        return np.zeros(max(count, 0), np.float32)
    edges = (start - origin + np.arange(count + 1) * (span / count)) / bin_seconds
    if span / count >= bin_seconds:
        # One or more whole bins per column: take the exact maximum, never skip a transient.
        lo = np.clip(np.floor(edges[:-1]).astype(np.int64), 0, n - 1)
        hi = np.maximum(np.clip(np.ceil(edges[1:]).astype(np.int64), 1, n), lo + 1)
        out = np.maximum.reduceat(values[lo[0]:hi[-1]], lo - lo[0]).astype(np.float32)
    else:
        # Columns are narrower than a bin: interpolate between neighbouring bins, reading only the
        # visible slice (the whole envelope can hold millions of bins).
        centers = (edges[:-1] + edges[1:]) / 2 - .5
        first = int(np.clip(np.floor(centers[0]), 0, n - 1)); last = int(np.clip(np.ceil(centers[-1]) + 1, first + 1, n))
        out = np.interp(centers, np.arange(first, last), values[first:last]).astype(np.float32)
    out[(edges[1:] <= 0) | (edges[:-1] >= n)] = 0
    return out / 32768


class PeakPyramid:
    """Peak envelopes at several resolutions, so any zoom level reads a few thousand values at most."""

    BIN, FACTOR = 128, 4

    def __init__(self, base, rate):
        self.rate = rate
        self.levels = [base]
        while len(self.levels[-1]) > 2048:
            prev = self.levels[-1]
            whole = len(prev) // self.FACTOR * self.FACTOR
            level = prev[:whole].reshape(-1, self.FACTOR).max(axis=1)
            if whole < len(prev):
                level = np.append(level, prev[whole:].max())
            self.levels.append(level)

    def bin_seconds(self, level):
        return self.BIN * self.FACTOR ** level / self.rate

    def level_for(self, column_seconds):
        """The coarsest level that still has at least one bin per column."""
        level = 0
        while level + 1 < len(self.levels) and self.bin_seconds(level + 1) <= column_seconds:
            level += 1
        return level


def font(size, weight="Semilight"):
    return ctk.CTkFont(family=f"Segoe UI {weight}".strip(), size=size)


class Waveform(tk.Canvas):
    """Lightweight sound-wave animation; owns and cancels its timer."""

    def __init__(self, master, width=220, height=64):
        super().__init__(master, width=width, height=height, bg=BG[0 if ctk.get_appearance_mode() == "Light" else 1],
                         highlightthickness=0, bd=0)
        self.wave_width, self.wave_height = width, height
        self.running = False
        self.busy = False
        self.opacity = 1.0
        self.audio_levels = None
        self.timer = None
        self.bind("<Destroy>", self._dispose, add="+")
        self.bars = [self.create_line(0,0,0,0,width=3,capstyle=tk.ROUND) for _ in range(37)]
        self.draw()

    def draw(self):
        light = ctk.get_appearance_mode() == "Light"
        self.configure(bg=BG[0 if light else 1])

        phase = time.monotonic() * (4 if self.busy else 1.6) if self.running else 0
        levels = self.audio_levels() if self.audio_levels is not None and self.running else None
        for i in range(37):
            envelope = math.sin(math.pi * (i + 1) / 38)
            pulse = .42 + .58 * (.5+.5*math.sin(i*.32-phase))
            amplitude = 2 + (levels[i] if levels is not None else envelope*pulse) * (self.wave_height / 2 - 7)
            x = self.wave_width * (i + 1) / 38
            stops = ((35,35,35) if light else (245,245,245), (167,137,255), (45,104,255))
            position=i/36*2
            j=min(1,int(position)); mix=position-j
            color="#"+"".join(f"{round(a+(b-a)*mix):02x}" for a,b in zip(stops[j],stops[j+1]))
            background = (255,255,255) if light else (0,0,0)
            rgb = tuple(int(color[k:k+2],16) for k in (1,3,5))
            color = "#"+"".join(f"{round(a+(b-a)*self.opacity):02x}" for a,b in zip(background,rgb))
            self.coords(self.bars[i],x,self.wave_height/2-amplitude,x,self.wave_height/2+amplitude)
            self.itemconfigure(self.bars[i],fill=color,width=max(2,self.wave_width/100))
        if self.running:
            self.timer = self.after(16, self.draw)

    def animate(self, active):
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None
        self.running = active
        self.draw()

    def _dispose(self, event):
        if event.widget is self and self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None


class Halftone(tk.Canvas):
    """Antialiased dot waves rendered as one image, with elapsed-time pacing."""

    def __init__(self, master):
        self.light = ctk.get_appearance_mode() == "Light"
        self.background = "#ffffff" if self.light else "#000000"
        super().__init__(master, width=500, height=350,
                         bg=self.background, highlightthickness=0)
        self.opacity = 1.0
        self.dots = []
        for y in range(22, 330, 9):
            for x in range(20, 485, 9):
                left = math.hypot(x-175, y-175)
                right = math.hypot(x-325, y-175)
                distance = min(left, right)
                if distance > 150:
                    continue
                color = ("#242424" if self.light else "#f5f5f5") if left <= right else "#2864ff"
                self.dots.append((x,y,distance,min(1,max(0,(151-distance)/16)),color))
        self.image_item = self.create_image(0,0,anchor="nw")
        self.photo = None
        self.job = None
        self.started = time.perf_counter()
        self.bind("<Destroy>", self.dispose, add="+")
        self.tick()

    def tick(self):
        now = time.perf_counter()
        phase = (now-self.started)*math.tau/3.8
        image = Image.new("RGB", (1000,700), self.background)
        draw = ImageDraw.Draw(image)
        for x,y,distance,envelope,color in self.dots:
            wave = .5+.5*math.sin(distance*.045-phase)
            r = (1.1+1.85*wave)*envelope
            draw.ellipse(((x-r)*2,(y-r)*2,(x+r)*2,(y+r)*2),fill=color)
        image = image.resize((500,350),Image.Resampling.LANCZOS)
        if self.opacity < 1:
            image = Image.blend(Image.new("RGB",image.size,self.background),image,self.opacity)
        if self.photo is None:
            self.photo = ImageTk.PhotoImage(image,master=self)
            self.itemconfigure(self.image_item,image=self.photo)
        else:
            self.photo.paste(image)
        # Aim for 60 fps, accounting for rendering time rather than adding it.
        elapsed = time.perf_counter()-now
        self.job = self.after(max(1,round((1/60-elapsed)*1000)),self.tick)

    def dispose(self,event):
        if event.widget is self and self.job is not None:
            self.after_cancel(self.job)
            self.job=None


class DotVignette(tk.Canvas):
    """Subtle animated edge dots with a quiet center for the welcome copy."""

    def __init__(self, master):
        self.light = ctk.get_appearance_mode() == "Light"
        super().__init__(master, bg="#ffffff" if self.light else "#000000",
                         highlightthickness=0)
        self.opacity = 1.0
        self.points = []
        self.last_size = None
        self.job = None
        self.bind("<Configure>", self.rebuild)
        self.bind("<Destroy>", self.dispose, add="+")
        self.tick()

    def rebuild(self, event):
        size = (event.width, event.height)
        if size == self.last_size:
            return
        self.last_size = size
        self.delete("all")
        self.points = []
        w, h = max(1,event.width), max(1,event.height)
        for y in range(10,h,20):
            for x in range(10,w,20):
                # Only small quarter-circle fields at the four corners.
                reach = min(140, w*.19, h*.22)
                d = math.hypot(min(x,w-x), min(y,h-y))
                strength = max(0, 1-d/reach)
                if strength <= 0:
                    continue
                dot=self.create_oval(0,0,0,0,outline="",
                                     fill="#c2c2cc" if self.light else "#292930")
                self.points.append((dot,x,y,strength))

    def tick(self):
        t=time.monotonic()
        for dot,x,y,strength in self.points:
            r=strength*(.5+1.8*(.5+.5*math.sin(x*.024+y*.018-t*1.3)))
            self.coords(dot,x-r,y-r,x+r,y+r)
            base = 255 if self.light else 0
            ink = 194 if self.light else 41
            c = round(base+(ink-base)*self.opacity)
            self.itemconfigure(dot,fill=f"#{c:02x}{c:02x}{c:02x}")
        self.job=self.after(25,self.tick)

    def dispose(self,event):
        if event.widget is self and self.job is not None:
            self.after_cancel(self.job)
            self.job=None


class SurfaceFade:
    """Fade one raster layer instead of repainting every native control."""

    def __init__(self, owner, surface, background=BG):
        self.owner, self.surface = owner, surface
        self.background = background
        self.job = None
        self.level = 1.0
        self.layer = None
        self.photo = None
        self.snapshot = None
        surface.bind("<Destroy>", self.dispose, add="+")

    def prepare(self):
        if self.layer is not None:
            return
        self.owner.update_idletasks()
        target = getattr(self.surface,"_parent_frame",self.surface)
        w,h = max(1,target.winfo_width()),max(1,target.winfo_height())
        x,y = target.winfo_rootx(),target.winfo_rooty()
        index = 0 if ctk.get_appearance_mode()=="Light" else 1
        bg = self.background[index] if isinstance(self.background,(tuple,list)) else self.background
        # Capture only this app's visible panel; never store the capture on disk.
        self.snapshot = ImageGrab.grab(bbox=(x,y,x+w,y+h)).convert("RGB")
        self.blank = Image.new("RGB",self.snapshot.size,bg)
        self.layer = tk.Canvas(self.owner,highlightthickness=0,bd=0,bg=bg)
        self.layer.place(x=x-self.owner.winfo_rootx(),y=y-self.owner.winfo_rooty(),width=w,height=h)
        self.photo = ImageTk.PhotoImage(self.blank,master=self.layer)
        self.item = self.layer.create_image(0,0,anchor="nw",image=self.photo)
        tk.Misc.lift(self.layer)

    def apply(self,level):
        self.level=level
        if level>=1:
            if self.layer is not None:
                self.layer.destroy()
                self.layer=None
                self.snapshot=None
                self.photo=None
            return
        self.prepare()
        self.photo.paste(Image.blend(self.blank,self.snapshot,max(0,min(1,level))))

    def to(self,target,done=None):
        if self.job is not None:
            self.owner.after_cancel(self.job)
            self.job=None
        self.prepare()
        start,began=self.level,time.perf_counter()
        def tick():
            self.job=None
            if not self.surface.winfo_exists():
                return
            frame=time.perf_counter()
            t=min(1,(frame-began)/.32)
            self.apply(start+(target-start)*(t*t*(3-2*t)))
            if t<1:
                self.job=self.owner.after(max(1,round((1/60-(time.perf_counter()-frame))*1000)),tick)
            elif done:
                # Remove the overlay explicitly: CTk destroy events can name an internal frame.
                self.clear_layer()
                done()
        tick()

    def clear_layer(self):
        if self.layer is not None:
            self.layer.destroy()
            self.layer=None
        self.snapshot=None
        self.photo=None

    def dispose(self,event):
        if event.widget is self.surface or str(event.widget)==str(self.surface):
            if self.job is not None:
                self.owner.after_cancel(self.job)
                self.job=None
            self.clear_layer()


class MotionButton(ctk.CTkButton):
    """Smooth reversible foreground and background transitions."""

    def __init__(self, *args, **kwargs):
        action = kwargs.get("command")
        if action is not None:
            def clicked():
                root = self._root()
                if hasattr(root,"play_click"):
                    root.play_click()
                action()
            kwargs["command"] = clicked
        kwargs.update(fg_color=BG, text_color=TEXT, hover=False)
        super().__init__(*args, **kwargs)
        self._motion_job = None
        self.bind("<Enter>", lambda e: self._ease(True), add="+")
        self.bind("<Leave>", lambda e: self._ease(False), add="+")
        self.bind("<Destroy>", self._dispose, add="+")

    def _dispose(self, event):
        if event.widget is self and self._motion_job is not None:
            self.after_cancel(self._motion_job)
            self._motion_job = None

    def _ease(self, hovered):
        if self._motion_job is not None:
            self.after_cancel(self._motion_job)
            self._motion_job = None
        if self.cget("state") == "disabled":
            return
        light = ctk.get_appearance_mode() == "Light"
        index = 0 if light else 1
        def rgb(color):
            if isinstance(color, (tuple, list)):
                color = color[index]
            return tuple(int(color[i:i+2], 16) for i in (1, 3, 5))
        start_bg, start_fg = rgb(self.cget("fg_color")), rgb(self.cget("text_color"))
        target_bg = rgb(TEXT if hovered else BG)
        target_fg = rgb(BG if hovered else TEXT)
        def blend(a, b, t):
            return "#" + "".join(f"{round(x+(y-x)*t):02x}" for x,y in zip(a,b))
        def step(n=1):
            self._motion_job = None
            if self.cget("state") == "disabled":
                self.configure(fg_color=BG, text_color=TEXT)
                return
            t = 1-(1-n/12)**3
            self.configure(fg_color=blend(start_bg,target_bg,t),
                           text_color=blend(start_fg,target_fg,t))
            if n < 12:
                self._motion_job = self.after(16, lambda: step(n+1))
        step()


class AudioEditor(ctk.CTkFrame):
    """Per-file tags and waveform selection; source audio stays untouched."""

    TAGS = ("title", "artist", "album", "date", "genre", "tracknumber")
    WAVE_H, RULER_H = 210, 32
    # Timeline label spacing in seconds; the smallest that leaves ~70 px between labels wins.
    STEPS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600)
    PLAYHEAD = "#ff6b6b"
    HELP = "Drag across the waveform to select a range. Drag the timeline to scrub. Tags and trim apply to the converted copy."
    STALL_SECONDS = 30   # FFmpeg producing nothing for this long counts as stuck
    CHUNK = 1 << 19      # bytes of PCM analysed per read; keeps memory flat for any file length

    def __init__(self, app, row):
        super().__init__(app,fg_color=BG,corner_radius=0)
        self.place(relx=0,rely=0,relwidth=1,relheight=1)
        self.lift()
        app.active_editor=self
        self.row,self.app= row,app
        self.duration=0; self.pyramid=None; self.result=queue.Queue()
        self.loading=False; self.decode_proc=None; self.decode_cancel=False; self.stalled=False
        self.decoded=0.0; self.estimate=0.0; self.began=self.last_data=0.0
        self.fields={}
        self.position=0.0; self.playing=False; self.preview=None
        self.alias=f"resonate_preview_{id(self)}"; self.closed=False
        self.mode=None; self.last_seek=0.0; self.tick_job=None
        self.zoom=1.0;self.view_start=0.0
        self.zoom_job=None;self.zoom_target=1.0;self.zooming=False
        self.precise_timer=False;self.draw_items={};self.hidden_items=set();self.ruler_text={};self.draw_style=None;self.last_clock=None
        self.app.activity_wave.animate(False)
        # Deep-zoom detail comes from one worker thread; only the newest request is ever processed.
        self.detail_result=queue.Queue();self.detail_key=None;self.detail=None
        self.detail_serial=0;self.detail_job=None;self.detail_request=None
        self.detail_event=threading.Event();self.detail_worker=None
        header=ctk.CTkFrame(self,fg_color="transparent");header.pack(fill="x",padx=24,pady=(18,8))
        MotionButton(header,text="← Back",width=85,command=self.destroy).pack(side="left",padx=(0,18))
        ctk.CTkLabel(header,text="Audio editor",font=font(24),text_color=TEXT).pack(side="left")
        MotionButton(header,text="Multi-clip timeline",width=140,command=app.open_timeline).pack(side="right",padx=8)
        ctk.CTkLabel(header,text=elide(os.path.basename(row.path),40),font=font(12),text_color=MUTED).pack(side="right")
        body=ctk.CTkScrollableFrame(self,fg_color=BG,corner_radius=0)
        body.pack(fill="both",expand=True,padx=8,pady=(0,12))
        body._parent_canvas.configure(yscrollcommand=self.deferred_scroll(body._scrollbar))
        zoom_bar=ctk.CTkFrame(body,fg_color="transparent");zoom_bar.pack(fill="x",padx=16,pady=8)
        ctk.CTkLabel(zoom_bar,text="AUDIO  /  TRIM",font=font(11),text_color=MUTED).pack(side="left")
        MotionButton(zoom_bar,text="Fit",width=45,command=lambda:self.set_zoom(1)).pack(side="right",padx=3)
        MotionButton(zoom_bar,text="Selection",width=85,command=self.zoom_selection).pack(side="right",padx=3)
        MotionButton(zoom_bar,text="+",width=34,command=lambda:self.set_zoom(self.zoom_target*1.45)).pack(side="right",padx=3)
        MotionButton(zoom_bar,text="−",width=34,command=lambda:self.set_zoom(self.zoom_target/1.45)).pack(side="right",padx=3)
        self.zoom_label=ctk.CTkLabel(zoom_bar,text="1.0×",text_color=MUTED,width=60);self.zoom_label.pack(side="right",padx=8)
        self.canvas=tk.Canvas(body,height=self.WAVE_H+self.RULER_H,bg="#101018",highlightthickness=0)
        self.canvas.pack(fill="x",padx=16,pady=(0,0))
        self.scrollbar=ctk.CTkScrollbar(body,orientation="horizontal",command=self.scroll_timeline)
        self.scrollbar.pack(fill="x",padx=16,pady=(0,10))
        # Loading progress is drawn on the empty canvas and the button floats over it, so showing
        # and hiding them never re-lays-out the scrollable editor (CTk redraws are slow there).
        self.load_btn=MotionButton(body,text="Cancel",width=80,command=self.cancel_or_retry)
        self.load_state=("",0.0,False)
        self.canvas.bind("<MouseWheel>",self.wheel)
        self.canvas.bind("<Left>",lambda e:self.nudge(-.01))
        self.canvas.bind("<Right>",lambda e:self.nudge(.01))
        self.canvas.bind("<Shift-Left>",lambda e:self.nudge(-.001))
        self.canvas.bind("<Shift-Right>",lambda e:self.nudge(.001))
        self.canvas.bind("<Configure>",lambda e:self.paint())
        self.canvas.bind("<Button-1>",self.begin_drag)
        self.canvas.bind("<B1-Motion>",self.drag)
        self.canvas.bind("<ButtonRelease-1>",self.end_drag)
        self.canvas.bind("<Motion>",self.hover)
        transport=ctk.CTkFrame(body,fg_color="transparent");transport.pack(fill="x",padx=24,pady=(0,6))
        self.transport=transport
        self.play_btn=MotionButton(transport,text="Play",width=80,command=self.toggle_play,state="disabled")
        self.play_btn.pack(side="left")
        self.clock_label=ctk.CTkLabel(transport,text="0:00.0 / 0:00.0",font=font(13),text_color=MUTED)
        self.clock_label.pack(side="left",padx=12)
        MotionButton(transport,text="Set end",width=90,command=lambda:self.mark(self.end_var)).pack(side="right")
        MotionButton(transport,text="Set start",width=90,command=lambda:self.mark(self.start_var)).pack(side="right",padx=6)
        self.space_binding=app.bind("<space>",self.space,add="+")
        self.escape_binding=app.bind("<Escape>",lambda e:self.destroy(),add="+")
        controls=ctk.CTkFrame(body,fg_color="transparent");controls.pack(fill="x",padx=24)
        self.start_var=ctk.StringVar(value=str(row.edits.get("start",0)))
        self.end_var=ctk.StringVar(value=str(row.edits.get("end",0)))
        for label,var in (("Start (seconds)",self.start_var),("End (seconds)",self.end_var)):
            ctk.CTkLabel(controls,text=label,text_color=MUTED).pack(side="left",padx=5)
            entry=ctk.CTkEntry(controls,textvariable=var,width=100);entry.pack(side="left",padx=5)
            entry.bind("<KeyRelease>",lambda e:self.paint())
        form=ctk.CTkFrame(body,fg_color="transparent");form.pack(fill="x",padx=24,pady=12)
        form.grid_columnconfigure(1,weight=1)
        tags={}
        try:
            from mutagen import File
            media=File(row.path,easy=True)
            if media is not None: tags={k:v[0] for k,v in (media.tags or {}).items() if v}
        except Exception:
            pass
        tags.update(row.edits.get("tags",{}))
        for i,key in enumerate(self.TAGS):
            ctk.CTkLabel(form,text=key.replace("tracknumber","Track number").title(),text_color=MUTED).grid(row=i,column=0,sticky="w",padx=(0,14),pady=3)
            var=ctk.StringVar(value=tags.get(key,""));self.fields[key]=var
            ctk.CTkEntry(form,textvariable=var).grid(row=i,column=1,sticky="ew",pady=3)
        self.notice=ctk.CTkLabel(body,text=self.HELP,text_color=MUTED,wraplength=640)
        self.notice.pack(pady=4)
        fast_row=ctk.CTkFrame(body,fg_color="transparent");fast_row.pack(fill="x",padx=24,pady=(6,0))
        self.fast_var=ctk.BooleanVar(value=bool(row.edits.get("fast",False)))
        app.switch(fast_row,"Fast trim (copy audio without re-encoding)",self.fast_var,self.update_fast_hint).pack(anchor="w")
        self.fast_hint=ctk.CTkLabel(fast_row,text="",font=font(11),text_color=MUTED,justify="left",anchor="w",wraplength=620)
        self.fast_hint.pack(anchor="w",pady=(2,0))
        self.update_fast_hint()
        actions=ctk.CTkFrame(body,fg_color="transparent");actions.pack(pady=8)
        MotionButton(actions,text="Apply to converted copy",width=210,command=self.apply).pack(side="left",padx=6)
        self.save_tags=MotionButton(actions,text="Save ID3 to source MP3",width=190,command=self.save_id3)
        self.save_tags.pack(side="left",padx=6)
        if not row.path.lower().endswith(".mp3"): self.save_tags.configure(state="disabled")
        self.poll_job=None
        self.bind("<Destroy>",self.cleanup,add="+")
        self.start_decode()

    def destroy(self):
        if not self.closed:
            self.cleanup(type("Event",(),{"widget":self})())
        super().destroy()

    def deferred_scroll(self,bar):
        """CTkScrollbar.set flushes all pending layout. Called directly by the scrolling canvas it
        recurses through every resize (over a second per change), so apply only the latest position, once per idle."""
        latest=[]
        def flush():
            values=latest.pop()
            if bar.winfo_exists():bar.set(*values)
        def command(first,last):
            if not latest:self.after_idle(flush)
            latest[:]=[(first,last)]
        return command

    def cleanup(self,event):
        if event.widget is not self:return
        if self.closed:return
        self.closed=True
        self.app.unbind("<space>",self.space_binding)
        self.app.unbind("<Escape>",self.escape_binding)
        self.app.active_editor=None
        self.precision_timer(False)
        if self.app.winfo_exists() and self.app.activity_wave.winfo_exists():self.app.activity_wave.animate(True)
        self.detail_serial+=1;self.detail_event.set()
        for job in ("poll_job","tick_job","detail_job","zoom_job"):
            if getattr(self,job) is not None:self.after_cancel(getattr(self,job));setattr(self,job,None)
        self.mci(f"close {self.alias}")
        self.stop_decode()
        self.discard_preview()
        if self.preview:threading.Thread(target=self.discard_preview,args=(True,),daemon=True).start()

    def stop_decode(self):
        proc=self.decode_proc
        if proc is not None and proc.poll() is None:
            try:proc.kill()
            except OSError:pass

    def discard_preview(self,patient=False):
        """Delete the temp WAV. Off the UI thread, keep trying for a few seconds: a just-killed
        FFmpeg or a virus scanner can hold a freshly written file briefly."""
        for attempt in range(30 if patient else 1):
            if not self.preview:return
            try:os.remove(self.preview);self.preview=None
            except FileNotFoundError:self.preview=None
            except OSError:
                if patient:time.sleep(.1)

    def mci(self,command,buffer=None):
        """Send one Windows MCI command; returns 0 on success."""
        if sys.platform!="win32":return 1
        import ctypes
        return ctypes.windll.winmm.mciSendStringW(command,buffer,len(buffer) if buffer is not None else 0,None)

    def start_decode(self):
        self.loading=True;self.decode_cancel=False;self.stalled=False
        self.decoded=0.0;self.estimate=0.0;self.began=self.last_data=time.monotonic()
        self.load_btn.configure(text="Cancel",state="normal")
        self.load_btn.place(in_=self.canvas,relx=.5,y=172,anchor="n")
        self.draw_loading("Reading file…",0.0,False)
        self.set_notice(self.HELP)
        threading.Thread(target=self.decode,daemon=True).start()
        if self.poll_job is None:self.poll()

    def set_notice(self,text,color=MUTED):
        # A changed label re-lays-out the whole scrollable editor, so skip no-op updates.
        if (text,color)!=getattr(self,"notice_state",(self.HELP,MUTED)):
            self.notice.configure(text=text,text_color=color)
        self.notice_state=(text,color)

    def cancel_or_retry(self):
        if self.loading:
            self.decode_cancel=True;self.stop_decode()
            self.load_btn.configure(state="disabled");self.draw_loading("Cancelling…",self.load_state[1])
        else:self.start_decode()

    def decode(self):
        """One FFmpeg pass feeds both the waveform analysis (a PCM pipe) and the playback WAV."""
        import tempfile
        path=None
        try:
            info=probe_audio(self.app.ffmpeg,self.row.path)
            if info["error"]:raise ValueError(f"FFmpeg couldn't read this file: {info['error']}")
            if not info["codec"]:raise ValueError("This file has no audio track.")
            self.estimate=info["duration"]
            # Analyse at the source rate (capped at 48 kHz) so short transients still show.
            rate=min(info["rate"] or 44100,48000);channels=1 if info["channels"]=="mono" else 2
            # Playback copy: full quality up to 15 minutes, then mono 22 kHz to keep the temp file small.
            short=0<self.estimate<=900
            play=["-ac","2","-ar","44100"] if short else ["-ac","1","-ar","22050"]
            handle,path=tempfile.mkstemp(prefix="resonate-preview-",suffix=".wav");os.close(handle)
            self.preview=path
            if self.closed or self.decode_cancel:raise InterruptedError()
            proc=subprocess.Popen([self.app.ffmpeg,"-v","error","-nostdin","-y","-i",self.row.path,
                                   "-map","0:a:0","-ac",str(channels),"-ar",str(rate),"-f","s16le","pipe:1",
                                   "-map","0:a:0",*play,"-c:a","pcm_s16le",path],
                                  stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=NO_WINDOW)
            self.decode_proc=proc
            errors=[]
            threading.Thread(target=lambda:errors.extend(proc.stderr),daemon=True).start()
            frame=2*channels;block=PeakPyramid.BIN*frame
            parts=[];carry=b"";total=0
            while True:
                data=proc.stdout.read(self.CHUNK)
                if not data or self.closed or self.decode_cancel:break
                total+=len(data);self.last_data=time.monotonic();self.decoded=total/frame/rate
                data=carry+data;whole=len(data)//block*block;carry=data[whole:]
                if whole:
                    samples=np.frombuffer(data,dtype="<i2",count=whole//2).reshape(-1,PeakPyramid.BIN*channels)
                    parts.append(np.maximum(samples.max(axis=1).astype(np.int32),
                                            -samples.min(axis=1).astype(np.int32)).astype(np.uint16))
            if self.closed or self.decode_cancel:
                self.stop_decode();proc.wait();raise InterruptedError()
            if len(carry)>=frame:
                tail=np.frombuffer(carry,dtype="<i2",count=len(carry)//2)
                parts.append(np.array([max(int(tail.max()),-int(tail.min()))],dtype=np.uint16))
            code=proc.wait()
            if self.stalled:raise ValueError(f"FFmpeg stopped responding for {self.STALL_SECONDS} seconds.")
            if code:
                lines=[l.decode("utf-8","replace").strip() for l in errors if l.strip()]
                detail=lines[-1] if lines else f"FFmpeg exited with code {code}"
                raise ValueError("No audio track found." if "matches no streams" in detail else f"Couldn't decode this file: {detail}")
            if not parts:raise ValueError("No audio found in this file.")
            self.result.put(("ready",total/frame/rate,PeakPyramid(np.concatenate(parts),rate)))
        except InterruptedError:
            self.discard_preview(True)
            if not self.closed:self.result.put(("cancelled",))
        except Exception as error:
            self.stop_decode()
            # The temp WAV can only be deleted once FFmpeg has let go of it.
            if self.decode_proc is not None:
                try:self.decode_proc.wait(timeout=10)
                except subprocess.TimeoutExpired:pass
            self.discard_preview(True)
            if not self.closed:self.result.put(("failed",str(error)))
        if self.closed:self.discard_preview(True)

    def poll(self):
        self.poll_job=None
        try:
            while True:self.handle_message(self.result.get_nowait())
        except queue.Empty:pass
        if self.loading and not self.closed:
            self.show_progress()
            self.poll_job=self.after(100,self.poll)

    def show_progress(self):
        now=time.monotonic();elapsed=now-self.began
        proc=self.decode_proc
        if proc is not None and proc.poll() is None and not self.decode_cancel and now-self.last_data>self.STALL_SECONDS:
            self.stalled=True;self.stop_decode()
        if self.decode_cancel:return
        parts=[f"{self.clock(elapsed,False)} elapsed"];fraction=0.0
        if self.decoded:parts.insert(0,f"{self.clock(self.decoded,False)} decoded")
        if self.estimate:
            fraction=min(1,self.decoded/self.estimate);parts.insert(0,f"{fraction:.0%}")
        if elapsed>1 and self.decoded:parts.append(f"{self.decoded/elapsed:.0f}× realtime")
        self.draw_loading("  ·  ".join(parts),fraction)

    def draw_loading(self,text=None,fraction=None,failed=None):
        """Progress, or the failure message, drawn in the empty waveform area."""
        old_text,old_fraction,old_failed=self.load_state
        self.load_state=(old_text if text is None else text,old_fraction if fraction is None else fraction,
                         old_failed if failed is None else failed)
        text,fraction,failed=self.load_state
        light=ctk.get_appearance_mode()=="Light"
        canvas=self.canvas;canvas.delete("loading")
        width=max(1,canvas.winfo_width());middle=width/2
        canvas.configure(bg="#eeeeef" if light else "#0e0e10")
        title="Couldn't load this file" if failed else "Preparing waveform and playback"
        canvas.create_text(middle,74,text=title,fill="#333338" if light else "#e6e6ee",font=("Segoe UI",13),tags="loading")
        if not failed:
            left=middle-160;right=middle+160
            canvas.create_rectangle(left,106,right,110,fill="#d0d0d8" if light else "#24242c",outline="",tags="loading")
            if fraction>0:canvas.create_rectangle(left,106,left+320*fraction,110,fill="#8e8aff",outline="",tags="loading")
        canvas.create_text(middle,136,text=text,width=max(100,width-80),justify="center",
                           fill=BAD if failed else ("#555560" if light else "#9a9aa6"),font=("Segoe UI",10),tags="loading")

    def handle_message(self,message):
        kind=message[0]
        if kind=="ready":
            self.loading=False;self.load_btn.place_forget();self.canvas.delete("loading")
            self.draw_style=None  # the loading screen changed the canvas background
            self.duration,self.pyramid=message[1],message[2]
            if not self.row.edits.get("end"):self.end_var.set(f"{self.duration:.3f}")
            self.set_notice(self.HELP)
            if self.preview and self.mci(f'open "{self.preview}" type waveaudio alias {self.alias}')==0:
                self.mci(f"set {self.alias} time format milliseconds");self.play_btn.configure(state="normal")
            else:self.notice.configure(text="Playback isn't available for this file. Trimming still works.",text_color=MUTED)
            self.paint()
            return
        self.loading=False
        self.load_btn.configure(text="Retry",state="normal")
        if kind=="cancelled":
            self.draw_loading("Loading cancelled. Retry to see the waveform and play the file.",0.0)
            self.notice.configure(text="Loading was cancelled.",text_color=MUTED)
        else:
            self.draw_loading(message[1],0.0,True)
            self.notice.configure(text=message[1],text_color=BAD)

    @staticmethod
    def clock(seconds,precise=True):
        minutes,secs=divmod(max(0,seconds),60)
        return f"{int(minutes)}:{secs:06.3f}" if precise else f"{int(minutes)}:{int(secs):02d}"

    @property
    def span(self):return self.duration/max(1,self.zoom)

    def time_x(self,value):return (value-self.view_start)/max(.000001,self.span)*max(1,self.canvas.winfo_width())

    def x_time(self,x):return max(0,min(self.duration,self.view_start+x/max(1,self.canvas.winfo_width())*self.span))

    def precision_timer(self,active):
        if sys.platform!="win32" or active==self.precise_timer:return
        import ctypes
        if active:
            self.precise_timer=ctypes.windll.winmm.timeBeginPeriod(1)==0
        else:
            ctypes.windll.winmm.timeEndPeriod(1);self.precise_timer=False

    def set_zoom(self,value,anchor=None,fraction=None):
        if not self.duration:return
        if self.zoom_job is not None:
            self.after_cancel(self.zoom_job);self.zoom_job=None
        fraction=.5 if fraction is None else fraction
        if anchor is None:
            anchor=self.position
            if not self.view_start<=anchor<=self.view_start+self.span:anchor=self.view_start+self.span*.5
        self.zoom_target=max(1,min(value,max(1,self.duration/.05)))
        start_zoom=self.zoom;start_view=self.view_start
        final_span=self.duration/self.zoom_target
        target_view=max(0,min(self.duration-final_span,anchor-fraction*final_span))
        began=time.perf_counter();self.zooming=True
        self.precision_timer(True)
        next_frame=began
        def tick():
            nonlocal next_frame
            self.zoom_job=None
            frame=time.perf_counter();t=min(1,(frame-began)/.22)
            ease=t*t*(3-2*t)
            self.zoom=math.exp(math.log(start_zoom)+(math.log(self.zoom_target)-math.log(start_zoom))*ease)
            # Keep the point under the mouse fixed as the scale changes.
            natural=anchor-fraction*self.span
            initial_natural=anchor-fraction*(self.duration/start_zoom)
            self.view_start=max(0,min(self.duration-self.span,natural+(start_view-initial_natural)*(1-ease)))
            if t>=1:
                self.zoom=self.zoom_target;self.view_start=target_view;self.zooming=False
            self.paint()
            if t<1:
                next_frame+=1/60
                now=time.perf_counter()
                if next_frame<now:next_frame=now+1/60
                self.zoom_job=self.after(max(1,int((next_frame-now)*1000)),tick)
            else:self.precision_timer(False)
        tick()

    def zoom_selection(self):
        try:a,b=float(self.start_var.get()),float(self.end_var.get())
        except ValueError:return
        if b>a:self.set_zoom(self.duration/((b-a)*1.15),anchor=(a+b)/2)

    def nudge(self,delta):
        if not self.duration:return "break"
        self.position=max(0,min(self.duration,self.position+delta))
        if not self.view_start<=self.position<=self.view_start+self.span:
            self.view_start=max(0,min(self.duration-self.span,self.position-self.span/2));self.paint()
        else:self.place_playhead()
        if self.playing:self.seek()
        return "break"

    def scroll_timeline(self,*args):
        if not self.duration:return
        if args[0]=="moveto":self.view_start=float(args[1])*self.duration
        else:self.view_start+=int(args[1])*self.span*(.8 if args[2]=="pages" else .1)
        self.view_start=max(0,min(self.duration-self.span,self.view_start));self.paint()

    def wheel(self,event):
        direction=1 if event.delta>0 else -1
        if event.state&4:
            fraction=event.x/max(1,self.canvas.winfo_width())
            self.set_zoom(self.zoom_target*(1.2 if direction>0 else 1/1.2),self.x_time(event.x),fraction)
            return "break"
        if event.state&1:
            self.scroll_timeline("scroll",-direction,"units");return "break"

    def visible_peaks(self,start,span,count):
        """Peak (0..1) per drawn column, from the cached envelope level that fits the zoom."""
        pyramid=self.pyramid;column=span/count
        if column>=pyramid.bin_seconds(0):
            level=pyramid.level_for(column)
            return column_peaks(pyramid.levels[level],0,pyramid.bin_seconds(level),start,span,count)
        # Zoomed past the finest envelope: use sample-level detail once the worker has it.
        detail=self.detail
        if detail is not None:
            values,origin,bin_seconds=detail
            if origin<=start and start+span<=origin+len(values)*bin_seconds and bin_seconds<=column:
                return column_peaks(values,origin,bin_seconds,start,span,count)
        self.request_detail(start,span,column)
        return column_peaks(pyramid.levels[0],0,pyramid.bin_seconds(0),start,span,count)

    def request_detail(self,start,span,column):
        if self.zooming or self.closed or not self.preview or self.play_btn.cget("state")!="normal":return
        # Read a window three views wide so small pans reuse it.
        first=max(0,start-span);last=min(self.duration,start+2*span)
        key=(round(first,6),round(last,6),round(column,9))
        if key==self.detail_key:return
        self.detail_key=key;self.detail_serial+=1
        self.detail_request=(self.detail_serial,self.preview,first,last,column/2)
        if self.detail_worker is None:
            self.detail_worker=threading.Thread(target=self.detail_loop,daemon=True);self.detail_worker.start()
        self.detail_event.set()
        if self.detail_job is None:self.detail_job=self.after(30,self.poll_detail)

    def detail_loop(self):
        while True:
            self.detail_event.wait();self.detail_event.clear()
            if self.closed:return
            request=self.detail_request
            if request is None:continue
            serial,path,first,last,bin_seconds=request
            try:
                with wave.open(path,"rb") as audio:
                    rate=audio.getframerate();channels=audio.getnchannels()
                    offset=min(audio.getnframes(),int(first*rate));audio.setpos(offset)
                    raw=audio.readframes(max(1,int((last-first)*rate)+1))
                samples=np.frombuffer(raw,dtype="<i2",count=len(raw)//(2*channels)*channels).reshape(-1,channels)
                peaks=np.abs(samples.astype(np.int32)).max(axis=1)
                size=max(1,int(bin_seconds*rate));whole=len(peaks)//size*size
                values=peaks[:whole].reshape(-1,size).max(axis=1) if whole else peaks[:0]
                if whole<len(peaks):values=np.append(values,peaks[whole:].max())
                if serial==self.detail_serial and not self.closed:
                    self.detail_result.put((serial,(values.astype(np.uint16),offset/rate,size/rate)))
            except (OSError,EOFError,wave.Error,ValueError):pass

    def poll_detail(self):
        self.detail_job=None
        latest=None
        try:
            while True:
                serial,detail=self.detail_result.get_nowait()
                if serial==self.detail_serial:latest=detail
        except queue.Empty:pass
        if latest is not None:self.detail=latest;self.paint()
        elif not self.closed:self.detail_job=self.after(40,self.poll_detail)

    def draw_item(self,key,kind,coords,**style):
        item=self.draw_items.get(key)
        if item is None:
            item=getattr(self.canvas,"create_"+kind)(*coords,**style)
            self.draw_items[key]=item
        else:
            self.canvas.coords(item,*coords)
            if self.draw_style_changed:self.canvas.itemconfigure(item,**style)
            if key in self.hidden_items:
                self.canvas.itemconfigure(item,state="normal");self.hidden_items.remove(key)
        self.used_items.add(key)
        return item

    def paint(self):
        light=ctk.get_appearance_mode()=="Light"
        self.draw_style_changed=self.draw_style!=light;self.draw_style=light
        self.used_items=set()
        if self.draw_style_changed:self.canvas.configure(bg="#eeeeef" if light else "#0e0e10")
        width=max(1,self.canvas.winfo_width());height=self.WAVE_H
        if not self.duration:
            if self.load_state[0]:self.draw_loading()
            return
        if not self.zooming:
            self.zoom_label.configure(text=f"{self.zoom:.1f}×")
            # CTkScrollbar.set flushes all pending layout work, so only call it when it moves.
            scroll=(round(self.view_start/self.duration,5),round((self.view_start+self.span)/self.duration,5))
            if scroll!=getattr(self,"last_scroll",None):self.scrollbar.set(*scroll);self.last_scroll=scroll
        span=max(.000001,self.span);view=self.view_start;scale=width/span
        self.paint_ruler(width)
        center=self.RULER_H+(height-self.RULER_H)/2
        self.draw_item("track","rectangle",(0,self.RULER_H+14,width,height-14),fill="#dce2ef" if light else "#111a2b",outline="#34496c")
        # One column every 2 px, each showing the loudest sample it covers.
        count=width//2+2;xs=np.arange(count,dtype=np.float64)*2
        amps=np.maximum(1,self.visible_peaks(view,count*2/scale,count)*(height-self.RULER_H-34)/2)
        upper=np.column_stack((xs,center-amps)).ravel()
        lower=np.column_stack((xs[::-1],(center+amps)[::-1])).ravel()
        shape=np.concatenate((upper,lower)).tolist()
        self.draw_item("wave","polygon",shape,fill="#416ee8" if light else "#8ba5ff",outline="")
        try:
            a=(float(self.start_var.get())-view)*scale;b=(float(self.end_var.get())-view)*scale
            if a>0:self.draw_item("shade_left","rectangle",(0,self.RULER_H,min(a,width),height),fill="#000000",stipple="gray50",outline="")
            if b<width:self.draw_item("shade_right","rectangle",(max(0,b),self.RULER_H,width,height),fill="#000000",stipple="gray50",outline="")
            self.draw_item("selection","rectangle",(max(0,a),self.RULER_H+13,min(width,b),height-13),outline="#a697ff",width=2)
            for key,x in (("in",a),("out",b)):
                if 0<=x<=width:self.draw_item(key,"rectangle",(x-4,self.RULER_H+13,x+4,height-13),fill="#a697ff",outline="")
        except ValueError:pass
        for key,item in self.draw_items.items():
            if key not in self.used_items and key not in self.hidden_items:
                self.canvas.itemconfigure(item,state="hidden");self.hidden_items.add(key)
        self.place_playhead()
        self.canvas.tag_raise("playhead")

    def paint_ruler(self,width):
        self.draw_item("ruler","rectangle",(0,0,width,self.RULER_H),fill="#171719",outline="")
        span=max(.000001,self.span);scale=width/span
        step=next((v for v in self.STEPS if v*scale>=85),self.STEPS[-1])
        small=step/5;start=math.floor(self.view_start/small)
        for k,n in enumerate(range(start,int((self.view_start+span)/small)+2)):
            t=n*small;x=(t-self.view_start)*scale;major=n%5==0
            self.draw_item(f"tick{k}","line",(x,self.RULER_H,x,self.RULER_H-(12 if major else 5)),fill="#777780")
            if major:
                key=f"time{k}"
                item=self.draw_item(key,"text",(x+4,10),text=self.clock(t,step<1),anchor="w",fill="#b0b0bb",font=("Segoe UI",9))
                text=self.clock(t,step<1)
                if self.ruler_text.get(key)!=text:
                    self.canvas.itemconfigure(item,text=text);self.ruler_text[key]=text

    def playhead_x(self):return self.time_x(self.position) if self.duration else 0

    def place_playhead(self):
        self.canvas.delete("playhead")
        clock=f"{self.clock(self.position)} / {self.clock(self.duration)}"
        if clock!=self.last_clock:
            self.clock_label.configure(text=clock);self.last_clock=clock
        if not self.duration:return
        x=self.playhead_x()
        if not 0<=x<=self.canvas.winfo_width():return
        self.canvas.create_line(x,0,x,self.WAVE_H,fill=self.PLAYHEAD,width=2,tags="playhead")
        self.canvas.create_polygon(x-6,0,x+6,0,x,9,fill=self.PLAYHEAD,outline="",tags="playhead")

    def on_scrubber(self,event):
        return event.y<=self.RULER_H or abs(event.x-self.playhead_x())<=5

    def hover(self,event):
        if self.mode is None:
            self.canvas.configure(cursor="sb_h_double_arrow" if self.duration and self.on_scrubber(event) else "")

    def begin_drag(self,event):
        if not self.duration:return
        self.canvas.focus_set()
        self.anchor=max(0,min(event.x,self.canvas.winfo_width()))
        self.anchor_time=self.x_time(self.anchor)
        self.mode="scrub" if self.on_scrubber(event) else "press"
        if event.y>self.RULER_H:
            for key,var in (("start",self.start_var),("end",self.end_var)):
                try:
                    if abs(event.x-self.time_x(float(var.get())))<8:self.mode=key
                except ValueError:pass
        if self.mode=="scrub":self.scrub(event.x)

    def drag(self,event):
        if not self.duration or self.mode is None:return
        x=max(0,min(event.x,self.canvas.winfo_width()));w=max(1,self.canvas.winfo_width())
        if self.mode=="scrub":self.scrub(x);return
        if self.mode in ("start","end"):
            value=self.x_time(x)
            if self.mode=="start":self.start_var.set(f"{min(value,float(self.end_var.get())-.001):.3f}")
            else:self.end_var.set(f"{max(value,float(self.start_var.get())+.001):.3f}")
            self.paint();return
        # A press only becomes a range selection once the mouse has really moved.
        if self.mode=="press" and abs(x-self.anchor)<4:return
        self.mode="select"
        self.start_var.set(f"{min(self.anchor_time,self.x_time(x)):.3f}")
        self.end_var.set(f"{max(self.anchor_time,self.x_time(x)):.3f}");self.paint()

    def end_drag(self,event):
        if self.mode=="press":self.scrub(self.anchor)  # A plain click moves the playhead.
        if self.mode in ("scrub","press") and self.playing:self.seek()
        self.mode=None;self.hover(event)

    def scrub(self,x):
        w=max(1,self.canvas.winfo_width())
        self.position=self.x_time(max(0,min(x,w)));self.place_playhead()
        # Follow the drag while playing, without flooding MCI with seeks.
        if self.playing and time.monotonic()-self.last_seek>0.12:self.seek()

    def seek(self):
        self.last_seek=time.monotonic()
        self.mci(f"play {self.alias} from {int(self.position*1000)}")

    def toggle_play(self):
        if self.playing:
            self.playing=False;self.mci(f"stop {self.alias}");self.play_btn.configure(text="Play");return
        if self.position>=self.duration-0.05:self.position=0.0
        self.playing=True;self.play_btn.configure(text="Pause");self.seek();self.tick()

    def tick(self):
        self.tick_job=None
        if not self.playing:return
        import ctypes
        buffer=ctypes.create_unicode_buffer(64)
        self.mci(f"status {self.alias} mode",buffer)
        if buffer.value!="playing":
            self.playing=False;self.play_btn.configure(text="Play")
            if buffer.value=="stopped" and self.mode!="scrub":self.position=self.duration
            self.place_playhead();return
        if self.mode!="scrub" and self.mci(f"status {self.alias} position",buffer)==0 and buffer.value.isdigit():
            self.position=int(buffer.value)/1000
            if not self.view_start<=self.position<=self.view_start+self.span:
                self.view_start=min(max(0,self.duration-self.span),self.position);self.paint()
            else:self.place_playhead()
        self.tick_job=self.after(30,self.tick)

    def mark(self,var):
        if not self.duration:return
        var.set(f"{self.position:.3f}");self.paint()

    def space(self,event):
        if isinstance(self.focus_get(),tk.Entry) or self.play_btn.cget("state")=="disabled":return
        self.play_btn.invoke();return "break"

    def tags(self):return {key:var.get().strip() for key,var in self.fields.items()}

    def update_fast_hint(self):
        if not self.fast_var.get():
            self.fast_hint.configure(text="Off: the trim is re-encoded and cut at the exact time you chose.",text_color=MUTED);return
        app=self.app
        settings={"edits":{self.row.path:{"fast":True,"end":1}},"format":app.format_var.get(),"bitrate":app.bitrate_var.get(),
                  "rate":app.rate_var.get(),"channels":app.channels_var.get()}
        ok,reason=app.copy_plan(self.row.path,settings)
        verdict=("With the current output settings this file will be copied." if ok else
                 f"With the current output settings this file will be re-encoded instead ({reason}).")
        self.fast_hint.configure(text="Much faster and no quality loss, but cuts snap to the source's packet boundaries, "
                                      "so the start and end can land a few tens of milliseconds off. Only used for lossy "
                                      "formats (MP3, AAC, OGG, OPUS, WMA) when the output format and settings match the "
                                      f"source. {verdict}",text_color=MUTED)

    def apply(self):
        try:
            start,end=float(self.start_var.get()),float(self.end_var.get())
            if not (math.isfinite(start) and math.isfinite(end) and 0<=start<end<=self.duration+.01):raise ValueError()
        except ValueError:
            self.notice.configure(text="Choose a valid start and end within the file duration.",text_color=BAD);return
        self.row.edits={"start":start,"end":end,"tags":self.tags(),"fast":self.fast_var.get()}
        self.row.set_status("Edited",OK);self.destroy()

    def save_id3(self):
        try:
            from mutagen.easyid3 import EasyID3
            from mutagen.id3 import ID3NoHeaderError
            try: tags=EasyID3(self.row.path)
            except ID3NoHeaderError:tags=EasyID3()
            for key,value in self.tags().items():
                if value:tags[key]=value
                elif key in tags:del tags[key]
            tags.save(self.row.path,v2_version=3)
            self.notice.configure(text="ID3 tags saved to the source MP3. Audio was not re-encoded.",text_color=OK)
        except Exception as error:self.notice.configure(text=str(error),text_color=BAD)


class FileRow(ctk.CTkFrame):
    """One rounded pill in the file list: name, folder, status and a remove button."""

    def __init__(self, master, path, on_remove):
        super().__init__(master, fg_color=ROW, corner_radius=10, height=46)
        self.path = path
        self.edits = {}
        self.grid_columnconfigure(0, weight=1)
        name = ctk.CTkLabel(self, text=os.path.basename(path), font=font(13), text_color=TEXT,
                            anchor="w")
        name.grid(row=0, column=0, sticky="ew", padx=(14, 8), pady=(6, 0))
        folder = ctk.CTkLabel(self, text=elide(os.path.dirname(path)), font=font(11, "Light"),
                              text_color=MUTED, anchor="w")
        folder.grid(row=1, column=0, sticky="ew", padx=(14, 8), pady=(0, 6))
        self.status = ctk.CTkLabel(self, text="", font=font(12), text_color=MUTED, width=70,
                                   anchor="e")
        self.status.grid(row=0, column=1, rowspan=2, padx=(0, 4))
        self.remove_btn = MotionButton(self, text="✕", width=28, height=28, corner_radius=8,
                                        fg_color="transparent", hover_color=CONTROL_HOVER,
                                        text_color=MUTED, font=font(12),
                                        command=lambda: on_remove(self))
        self.remove_btn.grid(row=0, column=3, rowspan=2, padx=(0, 10))
        self.edit_btn=MotionButton(self,text="Edit",width=55,height=28,command=lambda:self.winfo_toplevel().open_editor(self))
        self.edit_btn.grid(row=0,column=2,rowspan=2,padx=6)
        for w in (self, name, folder, self.status):
            w.bind("<Enter>", lambda e: self.configure(fg_color=ROW_HOVER))
            w.bind("<Leave>", lambda e: self.configure(fg_color=ROW))

    def set_status(self, text, color=MUTED):
        self.status.configure(text=text, text_color=color)


class App(ctk.CTk):
    def __init__(self):
        ctk.set_appearance_mode("dark")
        super().__init__(fg_color=BG)
        self.title(APP_NAME)
        self.set_icon()
        self.geometry("860x720")
        self.minsize(720, 620)
        self.ffmpeg = find_ffmpeg()
        self.prefs = load_prefs()
        ctk.set_appearance_mode(self.prefs["theme"])
        self.rows = []
        self.worker = None
        self.cancel_requested = False
        self.proc = None
        self.settings_panel = None
        self.settings_fade = None
        self.settings_open = False

        self.build_ui()
        self.bind_all("<ButtonRelease-1>",self.control_click,add="+")
        self.on_format_change()
        self.bind("<Escape>", lambda e: self.close_settings())
        self.after(10, self.style_title_bar)
        self.after_idle(self.show_welcome)

    def gear_image(self, angle):
        dark = self.gear_source.rotate(angle, resample=Image.Resampling.BICUBIC)
        light = ImageOps.invert(dark.convert("RGB")).convert("RGBA")
        light.putalpha(dark.getchannel("A"))
        return ctk.CTkImage(light_image=light, dark_image=dark, size=(28, 28))

    def spin_gear(self, target=180):
        if not hasattr(self, "gear_source"):
            return
        if self.spin_job is not None:
            self.after_cancel(self.spin_job)
            self.spin_job = None
        start = self.gear_angle
        def step(n=1):
            self.spin_job = None
            t = 1 - (1 - n / 24) ** 3
            self.gear_angle = start + (target - start) * t
            self.settings_btn.configure(image=self.gear_image(self.gear_angle))
            if n < 24:
                self.spin_job = self.after(16, lambda: step(n+1))
        step()

    def change_theme(self, theme):
        if self.settings_fade is not None:
            if self.settings_fade.job is not None:
                self.after_cancel(self.settings_fade.job)
                self.settings_fade.job = None
            self.settings_fade.apply(1)
            self.settings_fade = None
            if not self.settings_open:
                self.settings_panel.place_forget()
        self.prefs["theme"] = theme
        save_prefs(self.prefs)
        ctk.set_appearance_mode(theme)
        self.activity_wave.animate(self.activity_wave.running)
        def refresh(widget):
            if isinstance(widget, MotionButton):
                if widget._motion_job is not None:
                    widget.after_cancel(widget._motion_job)
                    widget._motion_job = None
                widget.configure(fg_color=BG, text_color=TEXT)
            for child in widget.winfo_children():
                refresh(child)
        refresh(self)
        self.settings_btn.configure(fg_color="transparent")
        self.style_title_bar()

    def loading_levels(self):
        if getattr(self,"active_music",None)!="loading-bed.mp3" or sys.platform!="win32":
            return None
        import ctypes
        position=ctypes.create_unicode_buffer(64)
        result=ctypes.windll.winmm.mciSendStringW("status resonate_background position",position,64,None)
        if result or not position.value.isdigit():
            return [0.0]*37
        data=self.loading_spectrum
        frame=int(position.value)/data["step_ms"]
        index=min(int(frame),len(data["frames"])-1)
        following=min(index+1,len(data["frames"])-1)
        mix=frame-int(frame)
        return [a+(b-a)*mix for a,b in zip(data["frames"][index],data["frames"][following])]

    def control_click(self,event):
        widget=event.widget
        while widget is not None and widget is not self:
            if isinstance(widget,MotionButton):
                return  # The command wrapper handles mouse and keyboard activation once.
            if isinstance(widget,(ctk.CTkSwitch,ctk.CTkSegmentedButton,ctk.CTkOptionMenu,ctk.CTkCheckBox)):
                if widget.cget("state")!="disabled":
                    self.play_click()
                return
            widget=getattr(widget,"master",None)

    def open_timeline(self):
        from multitrack import Timeline
        current=getattr(self,"active_editor",None)
        if isinstance(current,Timeline):
            current.lift();return
        if current is not None and current.winfo_exists():current.destroy()
        self.close_settings()
        workspace=Timeline(self,sys.modules[__name__])
        if not workspace.clips and self.rows:workspace.add_queued()

    def open_editor(self,row):
        current=getattr(self,"active_editor",None)
        if current is not None and current.winfo_exists():current.destroy()
        self.close_settings()
        AudioEditor(self,row)

    def play_click(self):
        if not self.prefs.get("sound",True) or sys.platform!="win32":
            return
        try:
            import winsound
            winsound.PlaySound(resource("click.wav"),winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except (OSError,RuntimeError):
            pass

    def background_audio(self, name=None):
        """One background channel shared by welcome and loading screens."""
        self.active_music = name
        if sys.platform != "win32":
            return
        import ctypes
        send = ctypes.windll.winmm.mciSendStringW
        send("close resonate_background",None,0,None)
        if name:
            path=resource(name)
            if os.path.isfile(path):
                result=send(f'open "{path}" type mpegvideo alias resonate_background',None,0,None)
                if result==0:
                    send("set resonate_background time format milliseconds",None,0,None)
                    send("setaudio resonate_background volume to 300",None,0,None)
                    send("play resonate_background"+(" repeat" if name=="welcome-bed.mp3" else ""),None,0,None)

    def destroy(self):
        self.background_audio()
        self.welcome_audio(False)
        super().destroy()

    def welcome_audio(self, play):
        """Use Windows' MP3 player without an extra playback dependency."""
        if sys.platform != "win32":
            return
        import ctypes
        send = ctypes.windll.winmm.mciSendStringW
        send("close resonate_welcome", None, 0, None)
        if play:
            path = resource("welcome.mp3")
            if os.path.isfile(path):
                result = send(f'open "{path}" type mpegvideo alias resonate_welcome',
                              None, 0, None)
                if result == 0:
                    send("play resonate_welcome", None, 0, None)

    def show_welcome(self):
        self.activity_wave.animate(False)
        self.welcome = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self.welcome.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.welcome.lift()
        self.leaving_welcome = False
        self.welcome_jobs = []
        vignette = DotVignette(self.welcome)
        vignette.place(relx=0, rely=0, relwidth=1, relheight=1)
        center = ctk.CTkFrame(self.welcome, fg_color=BG)
        center.place(relx=.5, rely=.48, anchor="center")
        Halftone(center).pack(pady=(0, 12))
        title = ctk.CTkLabel(center, text="", width=480, font=font(32), text_color=TEXT)
        title.pack()
        subtitle = ctk.CTkLabel(center, text="", font=font(14), text_color=MUTED)
        subtitle.pack(pady=(10, 20))
        slot = ctk.CTkFrame(center, width=480, height=80, fg_color=BG)
        slot.pack()
        slot.pack_propagate(False)
        start = MotionButton(slot, text="Get started", width=180, height=44,
                             corner_radius=12, border_width=1, border_color=CARD_BORDER,
                             font=font(14), command=self.begin_session)
        def schedule(delay, fn):
            self.welcome_jobs.append(self.after(delay, fn))
        def reveal(label, text, target, began=None):
            if not label.winfo_exists():
                return
            if began is None:
                began = time.perf_counter()
                label.configure(text=text)
            t = min(1,(time.perf_counter()-began)/.65)
            eased = t*t*(3-2*t)
            i = 0 if ctk.get_appearance_mode() == "Light" else 1
            bg, fg = BG[i], target[i]
            color = "#"+"".join(f"{round(int(bg[k:k+2],16)+(int(fg[k:k+2],16)-int(bg[k:k+2],16))*eased):02x}" for k in (1,3,5))
            label.configure(text_color=color)
            if t < 1:
                schedule(16,lambda:reveal(label,text,target,began))
        def rise(n=0):
            if not start.winfo_exists():
                return
            t=1-(1-n/24)**3
            start.place(relx=.5,y=75-67*t,anchor="n")
            if n<24:
                schedule(16,lambda:rise(n+1))
            else:
                start.focus_set()
        schedule(180,lambda:reveal(title,"Welcome to Resonance",TEXT))
        schedule(800,lambda:reveal(subtitle,"Your sound. A new frequency.",MUTED))
        schedule(1250,rise)
        self.welcome.bind("<Destroy>", self._welcome_closed, add="+")
        self.background_audio("welcome-bed.mp3")
        self.welcome_audio(True)
        start.focus_set()
        start.bind("<Return>", lambda event: self.begin_session(), add="+")

    def _welcome_closed(self, event):
        if event.widget is self.welcome:
            for job in self.welcome_jobs:
                self.after_cancel(job)
            self.welcome_jobs.clear()
            self.welcome_audio(False)

    def begin_session(self):
        if not self.welcome.winfo_exists() or self.leaving_welcome:
            return
        self.leaving_welcome = True
        for job in self.welcome_jobs:
            self.after_cancel(job)
        self.welcome_jobs.clear()
        self.welcome_audio(False)
        self.welcome_fade = SurfaceFade(self,self.welcome)
        def finished():
            self.welcome.destroy()
            self.show_loading()
        self.welcome_fade.to(0,finished)

    def show_loading(self, processing=False):
        if not hasattr(self,"loading_spectrum"):
            with open(resource("loading-wave.json"),encoding="utf-8") as file:
                self.loading_spectrum=json.load(file)
        if not processing and not getattr(self,"startup_sting_played",False):
            self.startup_sting_played=True
            self.background_audio("loading-bed.mp3")
        self.activity_wave.animate(False)
        self.loading = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self.loading.place(relx=0, rely=0, relwidth=1, relheight=1)
        center = ctk.CTkFrame(self.loading, fg_color=BG)
        center.place(relx=.5, rely=.48, anchor="center")
        ctk.CTkLabel(center, text="Resonate", font=font(36), text_color=TEXT).pack()
        wave = Waveform(center, width=340, height=110)
        wave.audio_levels = self.loading_levels
        wave.busy = processing
        wave.pack(pady=18)
        wave.animate(True)
        message = ctk.CTkLabel(center, text="Finding your tune…" if processing else "Find your frequency.",
                               font=font(14), text_color=MUTED)
        message.pack(pady=(6,12))
        if processing:
            self.processing_surface = self.loading
            self.processing_job = None
            meter = ctk.CTkProgressBar(center,width=300,height=4,progress_color="#8e8aff",fg_color=CONTROL)
            meter.pack(pady=(12,10)); meter.set(0)
            detail = ctk.CTkLabel(center,text="",font=font(12),text_color=MUTED,wraplength=460)
            detail.pack()
            cancel = MotionButton(center,text="Cancel",command=self.cancel,width=100,height=32)
            cancel.pack(pady=(20,0))
            began=time.monotonic()
            phrases=("Finding your tune…","Counting the notes…","Following the rhythm…",
                     "Tuning the strings…","Shaping your sound…","Keeping the harmony…")
            def update():
                if not self.processing_surface or not self.processing_surface.winfo_exists():
                    return
                message.configure(text="Bringing the music to a stop…" if self.cancel_requested else phrases[int((time.monotonic()-began)/3)%len(phrases)])
                progress=self.progress.get()
                meter.set(progress)
                detail.configure(text=f"{round(progress*100)}%  ·  {self.status_var.get()}")
                self.processing_job=self.after(100,update)
            update()
        loading_fade = SurfaceFade(self, center)
        loading_fade.apply(0)
        loading_fade.to(1)
        def finish_loading():
            def reveal_main():
                self.background_audio()
                self.loading.destroy()
                self.activity_wave.animate(True)
                self.main_fade = SurfaceFade(self,self.activity_wave.master)
                self.main_fade.apply(0)
                self.main_fade.to(1)
            wave.animate(False)

            loading_fade.to(0,reveal_main)
        if not processing:
            self.after(max(6000,self.loading_spectrum["duration_ms"]), finish_loading)

    def set_icon(self):
        """Window/taskbar icon."""
        path = resource(ICON_FILE)
        if not os.path.isfile(path):
            return
        try:
            import ctypes
            # Own taskbar identity, so Windows shows our icon rather than python's.
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Resonate.Desktop")
        except Exception:
            pass
        self.iconbitmap(path)

    def style_title_bar(self):
        """Black title bar and border to match the window (Windows 11; dark elsewhere)."""
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            set_attr = ctypes.windll.dwmapi.DwmSetWindowAttribute
            # COLORREF values are 0x00BBGGRR.
            light = ctk.get_appearance_mode() == "Light"
            for attr, value in ((20, 0 if light else 1),            # immersive dark mode
                                (34, 0x001C1C1C),   # border color
                                (35, 0x00FFFFFF if light else 0x00000000),   # caption color
                                (36, 0x00111111 if light else 0x00F2F2F2)):  # caption text color
                v = ctypes.c_int(value)
                set_attr(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
        except Exception:
            pass

    # ---------- small widget helpers ----------
    def card(self, master):
        return ctk.CTkFrame(master, fg_color=CARD, corner_radius=RADIUS, border_width=1,
                            border_color=CARD_BORDER)

    def caption(self, master, text):
        return ctk.CTkLabel(master, text=text.upper(), font=font(11), text_color=MUTED,
                            anchor="w")

    def ghost_button(self, master, text, command, width=100):
        return MotionButton(master, text=text, command=command, width=width, height=32,
                             corner_radius=10, fg_color=CONTROL, hover_color=CONTROL_HOVER,
                             border_width=1, border_color=CARD_BORDER, text_color=TEXT,
                             font=font(13))

    def segmented(self, master, values, variable, command=None):
        return ctk.CTkSegmentedButton(master, values=values, variable=variable, command=command,
                                      height=32, corner_radius=10, font=font(13),
                                      fg_color=CONTROL, unselected_color=CONTROL,
                                      unselected_hover_color=CONTROL_HOVER,
                                      selected_color=SEGMENT_ON,
                                      selected_hover_color="#3e3e3e",
                                      text_color=TEXT, text_color_disabled=FAINT)

    def option_menu(self, master, values, variable, width=120):
        return ResonateDropdown(master, values=values, variable=variable, width=width,
                                 height=32, corner_radius=10, font=font(13),
                                 dropdown_font=font(13), fg_color=CONTROL,
                                 button_color=CONTROL, button_hover_color=CONTROL_HOVER,
                                 text_color=TEXT, text_color_disabled=FAINT,
                                 dropdown_fg_color=CARD, dropdown_hover_color=CONTROL_HOVER,
                                 dropdown_text_color=TEXT)

    def switch(self, master, text, variable, command=None):
        return ctk.CTkSwitch(master, text=text, variable=variable, command=command,
                             font=font(12, "Light"), text_color=MUTED, switch_width=34,
                             switch_height=18, fg_color=CONTROL_HOVER, progress_color=SELECTED,
                             button_color="#9a9a9a", button_hover_color=TEXT)

    # ---------- UI ----------
    def build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        p = self.prefs

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(22, 14))
        header.grid_columnconfigure(0, weight=1)
        self.activity_wave = Waveform(header, width=100, height=36)
        self.activity_wave.grid(row=0, column=1, rowspan=2, padx=(14, 18))
        ctk.CTkLabel(header, text=APP_NAME, font=font(30, "Light"),
                     text_color=TEXT).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(header, text="Convert audio and extract sound from video. Everything stays "
                     "on your computer.", font=font(13, "Light"),
                     text_color=MUTED).grid(row=1, column=0, sticky="w")
        gear = None
        if os.path.isfile(resource(SETTINGS_ICON_FILE)):
            img = Image.open(resource(SETTINGS_ICON_FILE))
            img.size = max(img.info.get("sizes", {(64, 64)}))
            self.gear_source = img.convert("RGBA")
            gear = self.gear_image(0)
        self.settings_btn = MotionButton(header, text="" if gear else "Settings", image=gear,
                                          width=44, height=44, corner_radius=12,
                                          fg_color="transparent", hover_color=CONTROL_HOVER,
                                          text_color=TEXT, font=font(13),
                                          command=self.toggle_settings)
        self.settings_btn.grid(row=0, column=2, rowspan=2, sticky="e")
        self.spin_job = None
        self.gear_angle = 0.0
        self.settings_btn.bind("<Enter>", lambda e: self.spin_gear(180), add="+")
        self.settings_btn.bind("<Leave>", lambda e: self.spin_gear(0), add="+")
        self.settings_btn._ease = lambda hovered: None
        self.settings_btn.configure(fg_color="transparent")

        # Files card
        files = self.card(self)
        files.grid(row=1, column=0, sticky="nsew", padx=24, pady=(0, 12))
        files.grid_columnconfigure(0, weight=1)
        files.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(files, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 8))
        self.caption(top, "Files").pack(side="left")
        self.count_label = ctk.CTkLabel(top, text="", font=font(11), text_color=FAINT)
        self.count_label.pack(side="left", padx=8)
        self.timeline_btn=self.ghost_button(top,"Timeline",self.open_timeline,width=85)
        self.timeline_btn.pack(side="right",padx=(6,0))
        self.clear_btn = self.ghost_button(top, "Clear", self.clear_files, width=70)
        self.clear_btn.pack(side="right")
        self.folder_btn = self.ghost_button(top, "Add folder", self.add_folder)
        self.folder_btn.pack(side="right", padx=8)
        self.add_btn = self.ghost_button(top, "Add files", self.add_files)
        self.add_btn.pack(side="right")

        self.list_frame = ctk.CTkScrollableFrame(files, fg_color="transparent", corner_radius=0,
                                                 scrollbar_button_color=CONTROL,
                                                 scrollbar_button_hover_color=CONTROL_HOVER)
        self.list_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 12))
        self.list_frame.grid_columnconfigure(0, weight=1)
        self.empty_label = ctk.CTkLabel(self.list_frame, text="No files yet\nAdd files or a "
                                        "whole folder to begin", font=font(14, "Light"),
                                        text_color=FAINT, justify="center")

        # Output card
        out = self.card(self)
        out.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 12))
        out.grid_columnconfigure(1, weight=1)
        lp = {"padx": (18, 12), "sticky": "w"}

        self.caption(out, "Format").grid(row=0, column=0, pady=(18, 6), **lp)
        self.format_var = ctk.StringVar(value=p["format"])
        self.format_seg = self.segmented(out, list(FORMATS), self.format_var,
                                         lambda _: self.on_format_change())
        self.format_seg.grid(row=0, column=1, sticky="ew", padx=(0, 18), pady=(18, 6))

        self.caption(out, "Quality").grid(row=1, column=0, pady=6, **lp)
        q = ctk.CTkFrame(out, fg_color="transparent")
        q.grid(row=1, column=1, sticky="ew", padx=(0, 18), pady=6)
        self.bitrate_var = ctk.StringVar(value=p["bitrate"])
        self.bitrate_menu = self.option_menu(q, BITRATES, self.bitrate_var, width=96)
        self.bitrate_menu.pack(side="left")
        ctk.CTkLabel(q, text="Sample rate", font=font(12, "Light"),
                     text_color=MUTED).pack(side="left", padx=(18, 8))
        self.rate_var = ctk.StringVar(value=p["sample_rate"])
        self.option_menu(q, SAMPLE_RATES, self.rate_var, width=110).pack(side="left")
        ctk.CTkLabel(q, text="Channels", font=font(12, "Light"),
                     text_color=MUTED).pack(side="left", padx=(18, 8))
        self.channels_var = ctk.StringVar(value=p["channels"])
        self.segmented(q, CHANNELS, self.channels_var).pack(side="left")

        self.caption(out, "Save to").grid(row=2, column=0, pady=(6, 12), **lp)
        s = ctk.CTkFrame(out, fg_color="transparent")
        s.grid(row=2, column=1, sticky="ew", padx=(0, 18), pady=(6, 12))
        s.grid_columnconfigure(1, weight=1)
        self.dest_var = ctk.StringVar(value=p["destination"])
        self.segmented(s, DESTINATIONS, self.dest_var,
                       lambda _: self.on_dest_change()).grid(row=0, column=0, sticky="w")
        self.outdir_var = ctk.StringVar(value=p["output_dir"])
        self.outdir_entry = ctk.CTkEntry(s, textvariable=self.outdir_var, height=32,
                                         corner_radius=10, fg_color=CONTROL, border_width=1,
                                         border_color=CARD_BORDER, text_color=TEXT,
                                         font=font(12))
        self.outdir_entry.grid(row=0, column=1, sticky="ew", padx=8)
        self.browse_btn = self.ghost_button(s, "Browse", self.choose_outdir, width=80)
        self.browse_btn.grid(row=0, column=2)
        self.overwrite_var = ctk.BooleanVar(value=p["overwrite"])
        self.switch(out, "Overwrite existing files", self.overwrite_var
                    ).grid(row=3, column=1, sticky="w", pady=(0, 18))
        self.on_dest_change()
        ctk.CTkLabel(out,text="Codec limits apply: MP3 / OGG / WMA up to 48 kHz; Opus uses 48 kHz. Bitrates adapt when needed.",
                     font=font(10),text_color=MUTED,wraplength=610).grid(row=4,column=0,columnspan=2,padx=18,pady=(0,10))

        # Footer on the translucent backdrop: status, thin progress line, actions.
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=3, column=0, sticky="ew", padx=28, pady=(2, 22))
        foot.grid_columnconfigure(0, weight=1)
        self.status_var = ctk.StringVar(value="Ready")
        ctk.CTkLabel(foot, textvariable=self.status_var, font=font(13, "Light"),
                     text_color=MUTED, anchor="w").grid(row=0, column=0, sticky="ew")
        self.progress = ctk.CTkProgressBar(foot, height=3, corner_radius=2, fg_color="#262626",
                                           progress_color="#6d83ff")
        self.progress.set(0)
        self.progress.grid(row=1, column=0, sticky="ew", pady=(8, 0), padx=(0, 20))
        self.cancel_btn = self.ghost_button(foot, "Cancel", self.cancel, width=90)
        self.cancel_btn.grid(row=0, column=1, rowspan=2, padx=(0, 10))
        self.convert_btn = MotionButton(foot, text="Convert", command=self.start, width=140,
                                         height=40, corner_radius=12, fg_color=SELECTED,
                                         hover_color="#d4d4d4", text_color=TEXT_ON_LIGHT,
                                         text_color_disabled="#6a6a6a", font=font(14, ""))
        self.convert_btn.grid(row=0, column=2, rowspan=2)
        self.set_busy(False)
        self.refresh_list()

    # ---------- Settings ----------
    def build_settings_panel(self):
        panel = ctk.CTkScrollableFrame(self, fg_color=CARD, corner_radius=RADIUS, border_width=1,
                             border_color="#2a2a2a", width=330)
        panel.grid_columnconfigure(0, weight=1)
        pad = {"padx": 22, "sticky": "w"}

        top = ctk.CTkFrame(panel, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=(22, 12), pady=(18, 4))
        ctk.CTkLabel(top, text="Settings", font=font(22, "Light"),
                     text_color=TEXT).pack(side="left")
        MotionButton(top, text="✕", width=32, height=32, corner_radius=10,
                      fg_color="transparent", hover_color=CONTROL_HOVER, text_color=MUTED,
                      font=font(13), command=self.close_settings).pack(side="right")

        self.remember_var = ctk.BooleanVar(value=self.prefs["remember"])
        self.open_folder_var = ctk.BooleanVar(value=self.prefs["open_folder"])
        self.sound_var = ctk.BooleanVar(value=self.prefs["sound"])

        self.caption(panel, "Choices").grid(row=1, column=0, pady=(18, 8), **pad)
        self.switch(panel, "Remember my last format and options", self.remember_var,
                    self.on_pref_toggle).grid(row=2, column=0, pady=4, **pad)
        ctk.CTkLabel(panel, text="Reopens with the options you last converted with.",
                     font=font(11, "Light"), text_color=FAINT, anchor="w",
                     justify="left").grid(row=3, column=0, pady=(0, 6), padx=(62, 22),
                                          sticky="w")
        self.ghost_button(panel, "Reset to defaults", self.reset_choices, width=150
                          ).grid(row=4, column=0, pady=(4, 0), **pad)

        self.caption(panel, "When conversion finishes").grid(row=5, column=0, pady=(24, 8),
                                                             **pad)
        self.switch(panel, "Open the output folder", self.open_folder_var,
                    self.on_pref_toggle).grid(row=6, column=0, pady=4, **pad)
        self.switch(panel, "Play a sound", self.sound_var,
                    self.on_pref_toggle).grid(row=7, column=0, pady=4, **pad)

        self.caption(panel, "About").grid(row=8, column=0, pady=(24, 8), **pad)
        for i, text in enumerate((f"{APP_NAME}  {VERSION}",
                                  f"Audio engine  ·  FFmpeg {ffmpeg_version(self.ffmpeg)}")):
            ctk.CTkLabel(panel, text=text, font=font(12, "Light"),
                         text_color=MUTED).grid(row=9 + i, column=0, **pad)
        self.ghost_button(panel, "Get the latest version",
                          lambda: webbrowser.open(REPO_URL + "/releases/latest"), width=180
                          ).grid(row=11, column=0, pady=(12, 0), **pad)
        self.caption(panel, "Appearance").grid(row=12, column=0, pady=(18, 6), **pad)
        self.theme_var = ctk.StringVar(value=self.prefs["theme"])
        self.segmented(panel, ["Light", "Dark"], self.theme_var, self.change_theme
                       ).grid(row=13, column=0, pady=(0, 14), **pad)
        self.caption(panel,"AI isolation device").grid(row=14,column=0,pady=(14,6),**pad)
        self.isolation_device_var=ctk.StringVar(value=self.prefs["isolation_device"])
        self.option_menu(panel,["Auto","CPU","NVIDIA CUDA","AMD DirectML (experimental)"],self.isolation_device_var,width=280).grid(row=15,column=0,padx=22,pady=(0,6),sticky="w")
        self.isolation_device_var.trace_add("write",lambda *args:self.save_isolation_device())
        ctk.CTkLabel(panel,text="GPU accelerates AI separation. Audio trimming stays on CPU.\nAMD DirectML is experimental; unavailable devices fall back to CPU.",wraplength=275,justify="left",text_color=MUTED,font=font(11)).grid(row=16,column=0,padx=22,pady=(0,18),sticky="w")
        self.ghost_button(panel,"GPU setup notes",lambda:os.startfile(resource("processing-devices.txt")),width=180).grid(row=17,column=0,padx=22,pady=(0,18),sticky="w")
        return panel

    def save_isolation_device(self):
        self.prefs['isolation_device']=self.isolation_device_var.get()
        save_prefs(self.prefs)

    def toggle_settings(self):
        if self.settings_open:
            self.close_settings()
            return
        if self.settings_panel is None:
            self.settings_panel = self.build_settings_panel()
            self.bind("<Configure>", lambda e: self.fit_settings(), add="+")
        self.settings_open = True
        self.fit_settings()
        self.settings_panel.place(relx=1.0, x=-24, y=92, anchor="ne")
        self.settings_panel.lift()
        if self.settings_fade is None:
            self.settings_fade = SurfaceFade(self, self.settings_panel)
            self.settings_fade.apply(0)
        self.settings_fade.to(1)

    def fit_settings(self):
        """Stretch the panel from below the header to just above the window's bottom edge."""
        scale = ctk.ScalingTracker.get_window_scaling(self)
        height = int(self.winfo_height() / scale) - 116
        if height > 100 and self.settings_panel.cget("height") != height:
            self.settings_panel.configure(height=height)

    def close_settings(self):
        if self.settings_panel and self.settings_open:
            self.settings_open = False
            def hidden():
                if not self.settings_open:
                    self.settings_panel.place_forget()
                    self.settings_fade.apply(1)
                    self.settings_fade = None
            if self.settings_fade is None:
                self.settings_fade = SurfaceFade(self,self.settings_panel)
            self.settings_fade.to(0,hidden)

    def current_choices(self):
        return {
            "format": self.format_var.get(),
            "bitrate": self.bitrate_var.get(),
            "sample_rate": self.rate_var.get(),
            "channels": self.channels_var.get(),
            "destination": self.dest_var.get(),
            "output_dir": self.outdir_var.get().strip(),
            "overwrite": self.overwrite_var.get(),
        }

    def on_pref_toggle(self):
        self.prefs.update(remember=self.remember_var.get(),
                          open_folder=self.open_folder_var.get(), sound=self.sound_var.get())
        if self.prefs["remember"]:
            self.prefs.update(self.current_choices())
        save_prefs(self.prefs)

    def reset_choices(self):
        d = DEFAULT_PREFS
        self.format_var.set(d["format"])
        self.bitrate_var.set(d["bitrate"])
        self.rate_var.set(d["sample_rate"])
        self.channels_var.set(d["channels"])
        self.dest_var.set(d["destination"])
        self.outdir_var.set(d["output_dir"])
        self.overwrite_var.set(d["overwrite"])
        self.on_format_change()
        self.on_dest_change()
        self.prefs.update(self.current_choices())
        save_prefs(self.prefs)
        self.status_var.set("Choices reset to defaults")

    def on_format_change(self):
        has_bitrate = FORMATS[self.format_var.get()][2]
        self.bitrate_menu.configure(state="normal" if has_bitrate else "disabled")

    def on_dest_change(self):
        state = "normal" if self.dest_var.get() == "Choose" else "disabled"
        self.outdir_entry.configure(state=state,
                                    text_color=TEXT if state == "normal" else FAINT)
        self.browse_btn.configure(state=state)

    def refresh_list(self):
        n = len(self.rows)
        self.count_label.configure(text=f"{n}" if n else "")
        if n:
            self.empty_label.grid_forget()
        else:
            self.empty_label.grid(row=0, column=0, pady=80)

    def add_paths(self, paths):
        known = {r.path for r in self.rows}
        for p in paths:
            p = os.path.normpath(p)
            if p not in known:
                known.add(p)
                row = FileRow(self.list_frame, p, self.remove_row)
                row.grid(row=len(self.rows), column=0, sticky="ew", padx=6, pady=3)
                self.rows.append(row)
        self.refresh_list()
        if self.rows:
            self.status_var.set(f"{len(self.rows)} file(s) ready")

    def add_files(self):
        self.add_paths(filedialog.askopenfilenames(title="Choose audio files",
                                                   filetypes=INPUT_TYPES))

    def add_folder(self):
        folder = filedialog.askdirectory(title="Choose a folder with audio files")
        if not folder:
            return
        found = []
        for root, _, names in os.walk(folder):
            for name in sorted(names):
                if os.path.splitext(name)[1].lower() in INPUT_EXTS:
                    found.append(os.path.join(root, name))
        if not found:
            self.status_var.set("No audio or video files found in that folder")
        self.add_paths(found)

    def remove_row(self, row):
        self.rows.remove(row)
        row.destroy()
        for i, r in enumerate(self.rows):
            r.grid_configure(row=i)
        self.refresh_list()

    def clear_files(self):
        for r in self.rows:
            r.destroy()
        self.rows.clear()
        self.progress.set(0)
        self.status_var.set("Ready")
        self.refresh_list()

    def choose_outdir(self):
        folder = filedialog.askdirectory(title="Choose output folder")
        if folder:
            self.outdir_var.set(os.path.normpath(folder))

    # ---------- Conversion ----------
    def set_busy(self, busy):
        self.activity_wave.busy = busy
        self.activity_wave.animate(True)
        idle = "disabled" if busy else "normal"
        for w in (self.convert_btn, self.add_btn, self.folder_btn, self.clear_btn):
            w.configure(state=idle)
        for r in self.rows:
            r.remove_btn.configure(state=idle)
            r.edit_btn.configure(state=idle)
        self.cancel_btn.configure(state="normal" if busy else "disabled")

    def start(self):
        if not self.rows:
            self.status_var.set("Add at least one file first")
            return
        same_folder = self.dest_var.get() == "Same folder"
        outdir = self.outdir_var.get().strip()
        if not same_folder:
            if not outdir:
                self.status_var.set("Choose an output folder")
                return
            os.makedirs(outdir, exist_ok=True)
        if self.prefs["remember"]:
            self.prefs.update(self.current_choices())
            save_prefs(self.prefs)
        self.cancel_requested = False
        self.output_folders = []
        for r in self.rows:
            r.set_status("")
        self.progress.set(0)
        self.set_busy(True)
        self.show_loading(processing=True)
        settings = {
            "edits": {r.path:dict(r.edits) for r in self.rows},
            "format": self.format_var.get(),
            "bitrate": self.bitrate_var.get(),
            "rate": self.rate_var.get(),
            "channels": self.channels_var.get(),
            "same_folder": same_folder,
            "outdir": outdir,
            "overwrite": self.overwrite_var.get(),
        }
        self.worker = threading.Thread(target=self.run_all, args=(list(self.rows), settings),
                                       daemon=True)
        self.worker.start()

    def cancel(self):
        self.cancel_requested = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.status_var.set("Cancelling…")

    def ui(self, fn, *args):
        self.after(0, fn, *args)

    def output_path(self, src, s):
        ext = FORMATS[s["format"]][0]
        folder = os.path.dirname(src) if s["same_folder"] else s["outdir"]
        stem = os.path.splitext(os.path.basename(src))[0]
        dst = os.path.join(folder, f"{stem}.{ext}")
        if os.path.normcase(os.path.abspath(dst)) == os.path.normcase(os.path.abspath(src)):
            dst = os.path.join(folder, f"{stem} (converted).{ext}")
        if not s["overwrite"]:
            base, n = os.path.splitext(dst)[0], 1
            while os.path.exists(dst):
                dst = f"{base} ({n}).{ext}"
                n += 1
        return dst

    def copy_plan(self, src, s):
        """Whether a fast trim can copy audio packets as-is: (allowed, reason it can't)."""
        edit=s.get("edits",{}).get(src,{})
        if not (edit.get("fast") and edit.get("end")):return False,""
        fmt=s["format"]
        # Lossless outputs re-encode with no quality loss and an exact cut, and a copied FLAC
        # keeps the source's total length in its header, so copying only pays off for lossy formats.
        if not FORMATS[fmt][2]:return False,f"{fmt} is lossless, so it's re-encoded exactly with no quality loss"
        info=probe_audio(self.ffmpeg,src)
        if info["codec"]!=COPY_CODECS[fmt]:return False,f"source audio is {info['codec'] or 'unknown'}, not {fmt}"
        if s["rate"]!="Original":return False,"a sample-rate change was requested"
        if s["channels"]!="Original":return False,"a channel change was requested"
        if FORMATS[fmt][2]:
            wanted=int(s["bitrate"][:-1])
            if not info["bitrate"] or abs(info["bitrate"]-wanted)>max(8,wanted*.05):
                return False,f"{s['bitrate']} differs from the source's {info['bitrate'] or '?'} kb/s"
        return True,""

    def encode_args(self, s, has_bitrate):
        cmd=[]
        if has_bitrate:
            bitrate=s["bitrate"]
            if s["format"]=="MP3" and s["rate"]=="22050":bitrate=str(min(int(bitrate[:-1]),160))+"k"
            if s["format"]=="OPUS" and s["channels"]=="Mono":bitrate=str(min(int(bitrate[:-1]),256))+"k"
            if s["format"]=="OGG":
                maximum=(64 if s["channels"]=="Mono" else 128) if s["rate"]=="22050" else (128 if s["channels"]=="Mono" else 256)
                bitrate=str(min(int(bitrate[:-1]),maximum))+"k"
            cmd += ["-b:a",bitrate]
        if s["format"]=="OPUS":
            cmd += ["-ar","48000"]
        elif s["format"] in ("MP3","OGG") and s["rate"]=="96000":
            cmd += ["-ar","48000"]
        elif s["format"]=="WMA":
            rate="48000" if s["rate"] in ("Original","96000") else s["rate"]
            cmd += ["-ar",rate]
        elif s["rate"] != SAMPLE_RATES[0]:
            cmd += ["-ar",s["rate"]]
        if s["channels"] == "Mono":
            cmd += ["-ac", "1"]
        elif s["channels"] == "Stereo":
            cmd += ["-ac", "2"]
        return cmd

    def build_cmd(self, src, dst, s, copy=False):
        _, codec, has_bitrate = FORMATS[s["format"]]
        edit=s.get("edits",{}).get(src,{})
        trim=bool(edit.get("end"));start=edit.get("start",0) if trim else 0
        cmd = [self.ffmpeg, "-hide_banner", "-nostdin", "-y"]
        # Seek on the input to jump near the cut instead of decoding everything before it. When
        # re-encoding, land a second early and trim that exactly on the output: decoders such as
        # MP3 need a few frames of history before their first sample is right.
        preroll=0 if copy else min(start,1.0)
        if start>0:cmd += ["-ss",f"{start-preroll:.6f}"]
        cmd += ["-i", src, "-vn"]
        if preroll>0:cmd += ["-ss",f"{preroll:.6f}"]
        if trim:cmd += ["-t",f"{edit['end']-start:.6f}"]
        cmd += ["-map","0:a:0"]
        if copy:cmd += ["-c:a","copy"]  # copy_plan already checked the settings match the source
        else:cmd += codec+self.encode_args(s,has_bitrate)
        cmd += ["-map_metadata","0"]
        for key,value in edit.get("tags",{}).items():
            cmd += ["-metadata",("track" if key=="tracknumber" else key)+"="+value]
        if s["format"]=="MP3":cmd += ["-id3v2_version","3"]
        cmd += ["-progress", "pipe:1", "-nostats", dst]
        return cmd

    def run_all(self, rows, s):
        total = len(rows)
        ok, failed = 0, 0
        for i, row in enumerate(rows):
            if self.cancel_requested:
                break
            src = row.path
            self.ui(self.status_var.set, f"Converting {i + 1} of {total}  ·  "
                                         f"{os.path.basename(src)}")
            self.ui(row.set_status, "0%", TEXT)
            dst = self.output_path(src, s)
            note = ""
            try:
                success, err, note = self.convert_one(src, dst, s, i, total, row)
            except Exception as e:  # e.g. ffmpeg missing
                success, err = False, str(e)
            if success:
                ok += 1
                self.ui(row.set_status, "Done" + note, OK)
                if os.path.dirname(dst) not in self.output_folders:
                    self.output_folders.append(os.path.dirname(dst))
            else:
                if os.path.exists(dst):
                    try:
                        os.remove(dst)
                    except OSError:
                        pass
                if self.cancel_requested:
                    self.ui(row.set_status, "Cancelled", MUTED)
                else:
                    failed += 1
                    self.ui(row.set_status, "Failed", BAD)
                    print(f"{src}: {err}", file=sys.stderr)
            self.ui(self.progress.set, (i + 1) / total)
        self.ui(self.finish, ok, failed, total)

    def convert_one(self, src, dst, s, index, total, row):
        """Returns (success, error, status note)."""
        copy, reason = self.copy_plan(src, s)
        note = ""
        if copy:
            success, err = self.run_ffmpeg(src, dst, s, index, total, row, True)
            if success or self.cancel_requested:
                return success, err, " · copied"
            reason = "copying failed"
        if s.get("edits",{}).get(src,{}).get("fast"):
            note = " · re-encoded"
            print(f"{src}: fast trim not used, {reason}", file=sys.stderr)
        return (*self.run_ffmpeg(src, dst, s, index, total, row, False), note)

    def run_ffmpeg(self, src, dst, s, index, total, row, copy):
        duration = get_duration(self.ffmpeg, src)
        edit=s.get("edits",{}).get(src,{})
        if edit.get("end"):duration=edit["end"]-edit.get("start",0)
        self.proc = subprocess.Popen(self.build_cmd(src, dst, s, copy), stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, errors="replace",
                                     creationflags=NO_WINDOW)
        # Drain stderr in the background so ffmpeg never blocks on a full pipe.
        err_lines = []
        t = threading.Thread(target=lambda: err_lines.extend(self.proc.stderr), daemon=True)
        t.start()
        for line in self.proc.stdout:
            key, _, val = line.strip().partition("=")
            if key == "out_time_us" and duration > 0 and val.isdigit():
                frac = min(int(val) / 1e6 / duration, 1)
                self.ui(row.set_status, f"{frac:.0%}", TEXT)
                self.ui(self.progress.set, (index + frac) / total)
        code = self.proc.wait()
        t.join(timeout=2)
        if code == 0:
            return True, ""
        lines = [l.strip() for l in err_lines if l.strip()]
        return False, lines[-1] if lines else f"ffmpeg exited with code {code}"

    def finish(self, ok, failed, total):
        self.background_audio()
        if getattr(self,"processing_job",None) is not None:
            self.after_cancel(self.processing_job)
            self.processing_job=None
        if getattr(self,"processing_surface",None) is not None:
            self.processing_surface.destroy()
            self.processing_surface=None
        self.proc = None
        self.set_busy(False)
        if self.cancel_requested:
            self.status_var.set(f"Cancelled  ·  {ok} converted")
            return
        if failed:
            self.status_var.set(f"{ok} of {total} converted  ·  {failed} failed")
        else:
            self.status_var.set(f"All {ok} file(s) converted")
        if self.prefs["sound"]:
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONHAND if failed else winsound.MB_OK)
            except Exception:
                pass
        if self.prefs["open_folder"] and self.output_folders:
            try:
                os.startfile(self.output_folders[0])
            except OSError:
                pass


if __name__ == "__main__":
    if len(sys.argv)>2 and sys.argv[1]=='--verify-isolation-engine':
        from isolation import find_engine
        from isolation_runtime import launch_engine
        python=find_engine()
        report={"frozen":getattr(sys,"frozen",False),"engine":str(python)}
        try:
            probe="import sys,ctypes,json,torch;print(json.dumps({'python':sys.version,'ctypes':ctypes.__file__,'torch':torch.__version__,'cuda':torch.cuda.is_available()}))"
            process=launch_engine(python,['-c',probe],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',creationflags=NO_WINDOW)
            out,error=process.communicate(timeout=90)
            if process.returncode:raise RuntimeError(error)
            report['runtime']=json.loads(out)
            if len(sys.argv)>3:
                target=os.path.join(os.path.dirname(os.path.abspath(sys.argv[2])),'frozen-stems')
                os.makedirs(target,exist_ok=True)
                process=launch_engine(python,[resource('isolation_runner.py'),sys.argv[3],target,'all','Auto'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',creationflags=NO_WINDOW)
                out,_=process.communicate(timeout=180)
                if process.returncode:raise RuntimeError(out)
                report['stems']=[name for name in os.listdir(target) if name.endswith('.wav')]
                if len(report['stems'])!=6:raise RuntimeError('Expected six stem files')
            report['ok']=True
        except Exception as error:report.update(ok=False,error=str(error))
        with open(sys.argv[2],'w',encoding='utf-8') as stream:json.dump(report,stream,indent=2)
    else:
        App().mainloop()
