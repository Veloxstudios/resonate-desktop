"""Resonate Desktop - a minimal black batch audio converter for Windows (uses ffmpeg)."""

import json
import os
import re
import subprocess
import sys
import threading
import webbrowser
from tkinter import filedialog

import customtkinter as ctk
from PIL import Image

APP_NAME = "Resonate Desktop"
VERSION = "1.0.0"
REPO_URL = "https://github.com/Veloxstudios/resonate-desktop"
ICON_FILE = "resonate.ico"
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
BITRATES = ["64k", "96k", "128k", "160k", "192k", "256k", "320k"]
SAMPLE_RATES = ["Original", "22050", "44100", "48000", "96000"]
CHANNELS = ["Original", "Mono", "Stereo"]
DESTINATIONS = ["Same folder", "Choose"]

DEFAULT_PREFS = {
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
PREF_CHOICES = {"format": list(FORMATS), "bitrate": BITRATES, "sample_rate": SAMPLE_RATES,
                "channels": CHANNELS, "destination": DESTINATIONS}

INPUT_EXTS = (".mp3 .wav .flac .m4a .aac .ogg .opus .wma .aiff .aif .alac .amr .ac3 "
              ".mka .mp4 .mkv .mov .avi .webm .wmv .flv").split()
INPUT_TYPES = [("Audio / video files", " ".join("*" + e for e in INPUT_EXTS)),
               ("All files", "*.*")]

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Palette: true black window, off-black rounded cards, white accents.
BG = "#000000"
CARD = "#0e0e0e"
CARD_BORDER = "#1c1c1c"
ROW = "#151515"
ROW_HOVER = "#1d1d1d"
CONTROL = "#171717"
CONTROL_HOVER = "#222222"
SELECTED = "#f2f2f2"
SEGMENT_ON = "#343434"
TEXT = "#f2f2f2"
TEXT_ON_LIGHT = "#0a0a0a"
MUTED = "#7c7c7c"
FAINT = "#4a4a4a"
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


def font(size, weight="Semilight"):
    return ctk.CTkFont(family=f"Segoe UI {weight}".strip(), size=size)


class FileRow(ctk.CTkFrame):
    """One rounded pill in the file list: name, folder, status and a remove button."""

    def __init__(self, master, path, on_remove):
        super().__init__(master, fg_color=ROW, corner_radius=10, height=46)
        self.path = path
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
        self.remove_btn = ctk.CTkButton(self, text="✕", width=28, height=28, corner_radius=8,
                                        fg_color="transparent", hover_color=CONTROL_HOVER,
                                        text_color=MUTED, font=font(12),
                                        command=lambda: on_remove(self))
        self.remove_btn.grid(row=0, column=2, rowspan=2, padx=(0, 10))
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
        self.rows = []
        self.worker = None
        self.cancel_requested = False
        self.proc = None
        self.settings_panel = None

        self.build_ui()
        self.on_format_change()
        self.bind("<Escape>", lambda e: self.close_settings())
        self.after(10, self.style_title_bar)

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
            for attr, value in ((20, 1),            # immersive dark mode
                                (34, 0x001C1C1C),   # border color
                                (35, 0x00000000),   # caption color
                                (36, 0x00F2F2F2)):  # caption text color
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
        return ctk.CTkButton(master, text=text, command=command, width=width, height=32,
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
        return ctk.CTkOptionMenu(master, values=values, variable=variable, width=width,
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
        ctk.CTkLabel(header, text=APP_NAME, font=font(30, "Light"),
                     text_color=TEXT).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(header, text="Convert audio and extract sound from video. Everything stays "
                     "on your computer.", font=font(13, "Light"),
                     text_color=MUTED).grid(row=1, column=0, sticky="w")
        gear = None
        if os.path.isfile(resource(SETTINGS_ICON_FILE)):
            img = Image.open(resource(SETTINGS_ICON_FILE))
            img.size = max(img.info.get("sizes", {(64, 64)}))
            gear = ctk.CTkImage(dark_image=img.convert("RGBA"), size=(28, 28))
        self.settings_btn = ctk.CTkButton(header, text="" if gear else "Settings", image=gear,
                                          width=44, height=44, corner_radius=12,
                                          fg_color="transparent", hover_color=CONTROL_HOVER,
                                          text_color=TEXT, font=font(13),
                                          command=self.toggle_settings)
        self.settings_btn.grid(row=0, column=1, rowspan=2, sticky="e")

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

        # Footer on the translucent backdrop: status, thin progress line, actions.
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=3, column=0, sticky="ew", padx=28, pady=(2, 22))
        foot.grid_columnconfigure(0, weight=1)
        self.status_var = ctk.StringVar(value="Ready")
        ctk.CTkLabel(foot, textvariable=self.status_var, font=font(13, "Light"),
                     text_color=MUTED, anchor="w").grid(row=0, column=0, sticky="ew")
        self.progress = ctk.CTkProgressBar(foot, height=3, corner_radius=2, fg_color="#262626",
                                           progress_color=SELECTED)
        self.progress.set(0)
        self.progress.grid(row=1, column=0, sticky="ew", pady=(8, 0), padx=(0, 20))
        self.cancel_btn = self.ghost_button(foot, "Cancel", self.cancel, width=90)
        self.cancel_btn.grid(row=0, column=1, rowspan=2, padx=(0, 10))
        self.convert_btn = ctk.CTkButton(foot, text="Convert", command=self.start, width=140,
                                         height=40, corner_radius=12, fg_color=SELECTED,
                                         hover_color="#d4d4d4", text_color=TEXT_ON_LIGHT,
                                         text_color_disabled="#6a6a6a", font=font(14, ""))
        self.convert_btn.grid(row=0, column=2, rowspan=2)
        self.set_busy(False)
        self.refresh_list()

    # ---------- Settings ----------
    def build_settings_panel(self):
        panel = ctk.CTkFrame(self, fg_color=CARD, corner_radius=RADIUS, border_width=1,
                             border_color="#2a2a2a", width=330)
        panel.grid_propagate(False)
        panel.grid_columnconfigure(0, weight=1)
        pad = {"padx": 22, "sticky": "w"}

        top = ctk.CTkFrame(panel, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=(22, 12), pady=(18, 4))
        ctk.CTkLabel(top, text="Settings", font=font(22, "Light"),
                     text_color=TEXT).pack(side="left")
        ctk.CTkButton(top, text="✕", width=32, height=32, corner_radius=10,
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
        return panel

    def toggle_settings(self):
        if self.settings_panel and self.settings_panel.winfo_ismapped():
            self.close_settings()
            return
        if self.settings_panel is None:
            self.settings_panel = self.build_settings_panel()
            self.bind("<Configure>", lambda e: self.fit_settings(), add="+")
        self.fit_settings()
        self.settings_panel.place(relx=1.0, x=-24, y=92, anchor="ne")
        self.settings_panel.lift()

    def fit_settings(self):
        """Stretch the panel from below the header to just above the window's bottom edge."""
        scale = ctk.ScalingTracker.get_window_scaling(self)
        height = int(self.winfo_height() / scale) - 116
        if height > 100 and self.settings_panel.cget("height") != height:
            self.settings_panel.configure(height=height)

    def close_settings(self):
        if self.settings_panel:
            self.settings_panel.place_forget()

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
        idle = "disabled" if busy else "normal"
        for w in (self.convert_btn, self.add_btn, self.folder_btn, self.clear_btn):
            w.configure(state=idle)
        for r in self.rows:
            r.remove_btn.configure(state=idle)
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
        settings = {
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

    def build_cmd(self, src, dst, s):
        _, codec, has_bitrate = FORMATS[s["format"]]
        cmd = [self.ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", src, "-vn"]
        cmd += codec
        if has_bitrate:
            cmd += ["-b:a", s["bitrate"]]
        if s["rate"] != SAMPLE_RATES[0]:
            cmd += ["-ar", s["rate"]]
        elif s["format"] == "OPUS":
            cmd += ["-ar", "48000"]  # opus only supports specific rates
        if s["channels"] == "Mono":
            cmd += ["-ac", "1"]
        elif s["channels"] == "Stereo":
            cmd += ["-ac", "2"]
        cmd += ["-map_metadata", "0", "-progress", "pipe:1", "-nostats", dst]
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
            try:
                success, err = self.convert_one(src, dst, s, i, total, row)
            except Exception as e:  # e.g. ffmpeg missing
                success, err = False, str(e)
            if success:
                ok += 1
                self.ui(row.set_status, "Done", OK)
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
        duration = get_duration(self.ffmpeg, src)
        self.proc = subprocess.Popen(self.build_cmd(src, dst, s), stdout=subprocess.PIPE,
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
    App().mainloop()
