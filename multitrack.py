"""Same-window, non-destructive multi-clip audio composition."""
import copy
import ctypes
import json
import math
import os
import queue
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk
from dropdowns import ResonateDropdown
import numpy as np


def clip_length(clip):
    return (clip['out']-clip['in'])/clip.get('speed',1.)


def tempo_filters(speed):
    if not math.isfinite(speed) or not .25<=speed<=4:raise ValueError('Speed must be between 0.25× and 4×')
    filters=[]
    while speed<.5:filters.append('atempo=0.5');speed/=.5
    while speed>2:filters.append('atempo=2.0');speed/=2
    filters.append(f'atempo={speed:.8f}')
    return filters


def mix_command(ffmpeg, clips, destination, start=0, codec=None):
    """Trim each input accurately, apply its fades, then place it on the timeline."""
    if not clips:
        raise ValueError("Add at least one audio clip first.")
    cmd = [ffmpeg, '-v', 'error', '-nostdin', '-y']
    # Seek up to 1 s early and trim that lead-in off: compressed decoders (MP3)
    # need a few frames to warm up, so seeking straight to 'in' shifts the audio.
    prerolls = [min(clip['in'], 1.) for clip in clips]
    for clip, preroll in zip(clips, prerolls):
        cmd += ['-ss', str(clip['in']-preroll), '-t', str(clip['out']-clip['in']+preroll), '-i', clip['path']]
    filters = []
    for i, clip in enumerate(clips):
        duration = clip_length(clip)
        chain = f'[{i}:a:0]atrim=start={prerolls[i]},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo'
        if clip.get('reverse'):chain+=',areverse'
        chain+=','+','.join(tempo_filters(clip.get('speed',1.)))
        for name in ('fade_in', 'fade_out'):
            length = min(max(0, clip.get(name, 0)), duration)
            if length:
                begin = 0 if name == 'fade_in' else duration-length
                chain += f",afade=t={'in' if name=='fade_in' else 'out'}:st={begin}:d={length}"
        chain += f",adelay={round(clip['offset']*1000)}:all=1[a{i}]"
        filters.append(chain)
    labels = ''.join(f'[a{i}]' for i in range(len(clips)))
    filters.append(f'{labels}amix=inputs={len(clips)}:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95:latency=1[mix]')
    output = '[mix]'
    if start:
        filters.append(f'[mix]atrim=start={start},asetpts=PTS-STARTPTS[preview]')
        output = '[preview]'
    cmd += ['-filter_complex', ';'.join(filters), '-map', output]
    return cmd + (codec or ['-c:a', 'pcm_s16le']) + [destination]


class Timeline(ctk.CTkFrame):
    ROW = 94
    LEFT = 110

    def __init__(self, app, api):
        super().__init__(app, fg_color=api.BG, corner_radius=0)
        self.app, self.api = app, api
        self.clips = copy.deepcopy(getattr(app, 'timeline_project', []))
        self.selected = None
        self.zoom = 1.0
        self.view = 0.0
        self.position = 0.0
        self.messages = queue.Queue()
        self.closed = False
        self.proc = None
        self.busy = False
        self.cancelled = False
        self.preview = None
        self.alias = f'resonate_mix_{id(self)}'
        self.playing = False
        self.history = []
        self.job = None
        self.zoom_job = None
        self.zoom_target = 1.0
        self.precise_timer=False
        self.region = self.bar = None
        self.animating = False
        self.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.lift()
        app.active_editor = self
        app.activity_wave.animate(False)
        header = ctk.CTkFrame(self, fg_color='transparent')
        header.pack(fill='x', padx=20, pady=(16, 8))
        self.button(header, '← Back', self.destroy, 75).pack(side='left')
        ctk.CTkLabel(header, text='Audio timeline', font=api.font(24), text_color=api.TEXT).pack(side='left', padx=14)
        self.button(header, 'Settings', app.toggle_settings, 78).pack(side='right',padx=4)
        self.button(header, 'Add audio', self.add_audio, 100).pack(side='right')
        self.button(header, 'Add queued files', self.add_queued, 125).pack(side='right', padx=5)
        tools = ctk.CTkFrame(self, fg_color='transparent')
        tools.pack(fill='x', padx=22, pady=6)
        for text, action, width in [('Play', self.play, 65), ('Stop', self.stop, 65), ('Split', self.split, 60), ('Remove', self.remove, 75), ('Undo', self.undo, 65)]:
            self.button(tools, text, action, width).pack(side='left', padx=2)
        for text, action in [('Fit', self.fit), ('+', lambda: self.set_zoom(self.zoom_target*1.5)), ('−', lambda: self.set_zoom(self.zoom_target/1.5))]:
            self.button(tools, text, action, 38).pack(side='right', padx=2)
        area=ctk.CTkFrame(self,fg_color='transparent')
        area.pack(fill='both',expand=True,padx=22,pady=(8,0))
        self.canvas = tk.Canvas(area, bg='#0b0b0e', highlightthickness=0)
        self.canvas.pack(side='left',fill='both', expand=True)
        self.vertical = ctk.CTkScrollbar(area, command=self.canvas.yview)
        self.vertical.pack(side='right',fill='y')
        self.canvas.configure(yscrollcommand=self.vertical.set)
        self.scrollbar = ctk.CTkScrollbar(self, orientation='horizontal', command=self.pan)
        self.scrollbar.pack(fill='x', padx=22)
        self.canvas.bind('<Configure>', lambda e: self.paint())
        self.canvas.bind('<Button-1>', self.press)
        self.canvas.bind('<B1-Motion>', self.drag)
        self.canvas.bind('<ButtonRelease-1>',self.release)
        self.canvas.bind('<MouseWheel>', self.wheel)
        self.canvas.bind('<Delete>', lambda e: self.remove())
        self.canvas.bind('<space>', lambda e: self.play())
        self.canvas.bind('<Control-z>',lambda e:self.undo())
        properties = ctk.CTkFrame(self, fg_color=api.CARD, corner_radius=12)
        properties.pack(fill='x', padx=22, pady=10)
        self.values = {}
        for column, (name, label) in enumerate([('offset', 'Timeline position'), ('in', 'Source in'), ('out', 'Source out')]):
            group = ctk.CTkFrame(properties, fg_color='transparent')
            group.pack(side='left', padx=8, pady=8)
            ctk.CTkLabel(group, text=label+' (s)', text_color=api.MUTED, font=api.font(11)).pack(anchor='w')
            var = ctk.StringVar(value='0')
            self.values[name] = var
            entry = ctk.CTkEntry(group, width=105, textvariable=var)
            entry.pack()
        self.button(properties, 'Apply', self.apply_values, 60).pack(side='left', padx=5)
        effects=ctk.CTkFrame(self,fg_color='transparent')
        effects.pack(fill='x',padx=22,pady=(0,6))
        self.button(effects,'Slow down',lambda:self.change_speed(max(.25,self.clips[self.selected].get('speed',1.)/1.25)) if self.selected is not None else None,88).pack(side='left',padx=3)
        self.speed_var=ctk.StringVar(value='1×')
        ResonateDropdown(effects,variable=self.speed_var,values=['0.25×','0.5×','0.75×','1×','1.25×','1.5×','2×','3×','4×'],width=83,command=lambda value:self.change_speed(float(value[:-1]))).pack(side='left',padx=3)
        self.button(effects,'Speed up',lambda:self.change_speed(min(4,self.clips[self.selected].get('speed',1.)*1.25)) if self.selected is not None else None,83).pack(side='left',padx=3)
        self.reverse_btn=self.button(effects,'Reverse: off',self.toggle_reverse,110);self.reverse_btn.pack(side='left',padx=5)
        separation=ctk.CTkFrame(self,fg_color='transparent')
        separation.pack(fill='x',padx=22,pady=(0,6))
        ctk.CTkLabel(separation,text='AI isolation',text_color=api.MUTED,width=95).pack(side='left')
        self.isolation_mode=ctk.StringVar(value='Vocals / voice')
        ResonateDropdown(separation,variable=self.isolation_mode,values=['Vocals / voice','Instrumental','All 6 stems','Drums','Bass','Guitar','Piano','Other'],width=130).pack(side='left',padx=3)
        self.button(separation,'Isolate',self.isolate,75).pack(side='left',padx=3)
        ctk.CTkLabel(separation,text='Local processing · separates stems, not individual speakers',text_color=api.MUTED,font=api.font(10)).pack(side='left',padx=8)
        self.fade_duration = ctk.StringVar(value='1.0')
        fades = ctk.CTkFrame(self, fg_color='transparent')
        fades.pack(fill='x', padx=22, pady=(0, 6))
        self.fade_in = self.button(fades, 'Fade in: off', lambda: self.toggle_fade('fade_in'), 120)
        self.fade_in.pack(side='left', padx=(0, 6))
        self.fade_out = self.button(fades, 'Fade out: off', lambda: self.toggle_fade('fade_out'), 120)
        self.fade_out.pack(side='left', padx=6)
        ctk.CTkLabel(fades, text='Duration (s)', text_color=api.MUTED).pack(side='left', padx=(12, 6))
        ctk.CTkEntry(fades, textvariable=self.fade_duration, width=75).pack(side='left')
        self.button(fades, 'Save project', self.save_project, 105).pack(side='right')
        self.button(fades, 'Open project', self.open_project, 105).pack(side='right', padx=6)
        footer = ctk.CTkFrame(self, fg_color='transparent')
        footer.pack(fill='x', padx=22, pady=(0, 15))
        self.status = ctk.CTkLabel(footer, text='Add audio, then drag clips to arrange them. Overlapping clips mix together.', text_color=api.MUTED, wraplength=280, width=260, anchor='w')
        self.status.pack(side='left', fill='x', expand=True)
        self.format = ctk.StringVar(value=app.format_var.get())
        ResonateDropdown(footer, variable=self.format, values=list(api.FORMATS), width=85).pack(side='right', padx=6)
        self.button(footer, 'Export mix', self.export, 110).pack(side='right', padx=5)
        self.button(footer, 'Cancel task', self.cancel, 95).pack(side='right')
        self.drag_mode = None
        self.poll()
        self.paint()

    def button(self, parent, text, command, width):
        return self.api.MotionButton(parent, text=text, command=command, width=width, height=32)

    def snapshot(self):
        self.stop()
        self.history.append(copy.deepcopy(self.clips))
        self.history = self.history[-30:]

    def total(self):
        return max([c['offset']+clip_length(c) for c in self.clips]+[10])

    def scale(self):
        return max(1, self.canvas.winfo_width()-self.LEFT)/max(.05, self.total()/self.zoom)

    def x(self, seconds):
        return self.LEFT+(seconds-self.view)*self.scale()

    def at(self, x):
        return max(0, self.view+(x-self.LEFT)/self.scale())

    def add_audio(self):
        self.load(filedialog.askopenfilenames(parent=self.app, title='Add clips to timeline', filetypes=self.api.INPUT_TYPES))

    def add_queued(self):
        self.load([row.path for row in self.app.rows])

    def load(self, paths, offset=0):
        if self.busy:
            self.status.configure(text='Wait for the current task or cancel it first.'); return
        if not paths: return
        self.snapshot(); self.busy = True; self.cancelled = False
        self.status.configure(text='Reading audio clips…')
        def worker():
            for path in paths:
                if self.closed or self.cancelled: break
                try:
                    duration = self.api.get_duration(self.app.ffmpeg, path)
                    if duration <= 0: raise ValueError('No audio duration found')
                    # Read bounded chunks; never retain a full recording in memory.
                    proc = subprocess.Popen([self.app.ffmpeg, '-v', 'error', '-i', path, '-vn', '-ac', '1', '-ar', '8000', '-f', 's16le', '-'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=self.api.NO_WINDOW)
                    self.proc = proc
                    peaks = []
                    block = max(256, math.ceil(duration*8000/900))*2
                    while True:
                        data = proc.stdout.read(block)
                        if not data: break
                        values = np.frombuffer(data[:len(data)//2*2], dtype='<i2').astype(np.int32)
                        peaks.append(float(np.abs(values).max(initial=0))/32768)
                    if proc.wait() != 0: raise ValueError('Audio decoding failed or was cancelled')
                    self.messages.put(('clip', {'path': os.path.abspath(path), 'duration': duration, 'in': 0., 'out': duration, 'offset': offset, 'speed':1., 'reverse':False, 'fade_in': 0., 'fade_out': 0., 'peaks': peaks}))
                except Exception as error: self.messages.put(('notice', os.path.basename(path)+': '+str(error)))
            self.messages.put(('loaded', None))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        if self.closed: return
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == 'clip': self.clips.append(value); self.select(len(self.clips)-1); self.fit()
                elif kind == 'stems':
                    self.busy=False;self.proc=None
                    paths,offset=value;self.load(paths,offset=offset)
                elif kind == 'loaded': self.busy = False; self.proc = None; self.status.configure(text='Clips ready. Drag to move; drag clip edges to trim. Ctrl + wheel zooms.')
                elif kind == 'notice': self.status.configure(text=value)
                elif kind == 'finished': self.busy = False; self.proc = None; self.status.configure(text=value)
                elif kind == 'preview':
                    self.busy = False; self.proc = None
                    if self.mci(f'open "{value}" type waveaudio alias {self.alias}') == 0:
                        self.mci(f'set {self.alias} time format milliseconds'); self.mci(f'play {self.alias}')
                        self.playing = True; self.play_origin = self.position
                    self.status.configure(text='Playing mixed timeline. Stop before making edits.')
        except queue.Empty: pass
        if self.playing:
            buffer = ctypes.create_unicode_buffer(64)
            if self.mci(f'status {self.alias} position', buffer) == 0 and buffer.value.isdigit():
                self.position = self.play_origin+int(buffer.value)/1000
                self.paint()
            self.mci(f'status {self.alias} mode', buffer)
            if buffer.value != 'playing': self.playing = False
        self.job = self.after(30, self.poll)

    def select(self, index):
        self.selected = index
        if index is not None:
            clip = self.clips[index]
            self.speed_var.set(f"{clip.get('speed',1.):g}×")
            self.reverse_btn.configure(text='Reverse: '+('on' if clip.get('reverse') else 'off'))
            for key, var in self.values.items(): var.set(f'{clip[key]:.3f}')
            for key, button in [('fade_in', self.fade_in), ('fade_out', self.fade_out)]:
                value = clip.get(key, 0)
                button.configure(text=key.replace('_', ' ').capitalize()+(': '+f'{value:.2f}s' if value else ': off'))
        self.paint()

    def paint(self):
        self.canvas.delete('all')
        if not self.winfo_exists(): return
        width = max(1, self.canvas.winfo_width())
        height = max(self.canvas.winfo_height(), 38+len(self.clips)*self.ROW)
        # CTk scrollbars redraw synchronously (~3 ms each), so only touch them on change.
        if self.region != (width, height):
            self.region = (width, height); self.canvas.configure(scrollregion=(0, 0, width, height))
        step = next((v for v in [.01, .05, .1, .25, .5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1800, 3600] if v*self.scale() >= 80), 3600)
        for n in range(math.floor(self.view/step), math.ceil((self.view+(width-self.LEFT)/self.scale())/step)+1):
            x = self.x(n*step)
            self.canvas.create_line(x, 0, x, height, fill='#222229')
            self.canvas.create_text(x+4, 13, text=f'{n*step:.2f}s', fill='#9999a6', anchor='w', font=('Segoe UI', 9))
        for i, clip in enumerate(self.clips):
            y = 34+i*self.ROW; a = self.x(clip['offset']); b = self.x(clip['offset']+clip_length(clip))
            self.canvas.create_text(8, y+35, text=f'AUDIO {i+1}', fill='#8e8e9b', anchor='w', font=('Segoe UI', 9))
            if b < self.LEFT or a > width: continue
            left, right = max(self.LEFT, a), min(width, b)
            self.canvas.create_rectangle(left, y, right, y+76, fill='#172340' if i==self.selected else '#141925', outline='#b2a0ff' if i==self.selected else '#354665', width=2)
            self.canvas.create_text(left+9, y+13, text=(os.path.basename(clip['path'])+f"  · {clip.get('speed',1):g}×"+('  · REVERSE' if clip.get('reverse') else ''))[:85], anchor='w', fill='#eeeeff', font=('Segoe UI', 9))
            peaks = clip.get('peaks', [])
            if peaks:
                top=[];bottom=[];scale=self.scale()
                for pixel in range(int(left)+2,int(right),2):
                    source=(clip['out']-(pixel-a)/scale*clip.get('speed',1.)) if clip.get('reverse') else (clip['in']+(pixel-a)/scale*clip.get('speed',1.))
                    index=min(len(peaks)-1,max(0,int(source/clip['duration']*len(peaks))))
                    amp=max(1,peaks[index]*22)
                    top.extend((pixel,y+48-amp));bottom.append((pixel,y+48+amp))
                if top:self.canvas.create_polygon(*(top+[v for pair in reversed(bottom) for v in pair]),fill='#91acff',outline='')
            if i==self.selected:
                for edge in (a,b):
                    if self.LEFT<=edge<=width:self.canvas.create_rectangle(edge-3,y+24,edge+3,y+61,fill='#b2a0ff',outline='')
            for side, length in [('in', clip.get('fade_in', 0)), ('out', clip.get('fade_out', 0))]:
                if length:
                    x1, x2 = (a, a+length*self.scale()) if side=='in' else (b-length*self.scale(), b)
                    self.canvas.create_line(x1, y+72 if side=='in' else y+26, x2, y+26 if side=='in' else y+72, fill='#f5d4ff', width=2)
        self.canvas.create_rectangle(0, 0, self.LEFT-1, 30, fill='#0b0b0e', outline='')
        self.canvas.create_line(self.x(self.position), 0, self.x(self.position), height, fill='#ff727e', width=2)
        span = self.total()/self.zoom
        bar = (round(self.view/self.total(), 4), round(min(1, (self.view+span)/self.total()), 4))
        if bar != self.bar and not self.animating:
            self.bar = bar; self.scrollbar.set(*bar)

    def press(self, event):
        if self.busy: return
        self.canvas.focus_set(); self.stop()
        y = self.canvas.canvasy(event.y)
        if y < 30 or abs(event.x-self.x(self.position))<=8:
            self.drag_mode='scrub'
            self.canvas.grab_set()
            self.position = min(self.at(event.x),self.total()); self.paint(); return
        index = int((y-34)//self.ROW)
        if not 0 <= index < len(self.clips):
            self.drag_mode='scrub';self.canvas.grab_set()
            self.position=min(self.total(),self.at(event.x));self.paint();return
        self.select(index); self.snapshot()
        clip = self.clips[index]; a = self.x(clip['offset']); b = self.x(clip['offset']+clip_length(clip))
        if not a-7 <= event.x <= b+7:
            self.drag_mode='scrub';self.canvas.grab_set()
            self.position=min(self.total(),self.at(event.x));self.paint();return
        self.drag_mode = 'in' if abs(event.x-a)<9 else 'out' if abs(event.x-b)<9 else 'move'
        self.canvas.grab_set()
        self.drag_x = event.x; self.drag_scale=self.scale(); self.original = copy.deepcopy(clip)

    def drag(self, event):
        if self.drag_mode=='scrub':
            self.position=max(0,min(self.total(),self.at(event.x)));self.paint();return
        if not self.drag_mode or self.selected is None or self.busy: return
        clip = self.clips[self.selected]; old = self.original
        delta = (event.x-self.drag_x)/self.drag_scale
        if self.drag_mode == 'move': clip['offset'] = max(0, old['offset']+delta)
        elif self.drag_mode == 'in':
            speed=old.get('speed',1.)
            available=(old['duration']-old['out'])/speed if old.get('reverse') else old['in']/speed
            delta=max(-min(available,old['offset']),min(delta,clip_length(old)-.01))
            if old.get('reverse'):clip['out']=old['out']-delta*speed
            else:clip['in']=old['in']+delta*speed
            clip['offset']=old['offset']+delta
        else:
            speed=old.get('speed',1.)
            length=max(.01,clip_length(old)+delta)
            if old.get('reverse'):clip['in']=max(0,old['out']-length*speed)
            else:clip['out']=min(old['duration'],old['in']+length*speed)
        self.select(self.selected)

    def release(self,event):
        if self.drag_mode=='scrub':self.position=max(0,min(self.total(),self.at(event.x)))
        self.drag_mode=None
        try:self.canvas.grab_release()
        except tk.TclError:pass
        self.paint()

    def apply_values(self):
        if self.selected is None or self.busy: return
        clip = self.clips[self.selected]
        try:
            values = {k: float(v.get()) for k, v in self.values.items()}
            if not all(math.isfinite(v) for v in values.values()) or not (values['offset']>=0 and 0<=values['in']<values['out']<=clip['duration']): raise ValueError()
        except ValueError: self.status.configure(text='Enter valid clip times within the source duration.'); return
        self.snapshot(); clip.update(values); self.select(self.selected)

    def change_speed(self,speed):
        if self.selected is None or self.busy:return
        self.snapshot();self.clips[self.selected]['speed']=max(.25,min(4,speed));self.select(self.selected)

    def toggle_reverse(self):
        if self.selected is None or self.busy:return
        self.snapshot();clip=self.clips[self.selected];clip['reverse']=not clip.get('reverse',False);self.select(self.selected)

    def isolate(self):
        if self.selected is None or self.busy:return
        from isolation import start_isolation
        start_isolation(self)

    def toggle_fade(self, key):
        if self.selected is None or self.busy: return
        try:
            length = float(self.fade_duration.get())
            if not math.isfinite(length) or length<=0: raise ValueError()
        except ValueError: self.status.configure(text='Enter a positive fade duration in seconds.'); return
        self.snapshot(); clip = self.clips[self.selected]
        clip[key] = 0 if clip.get(key) else min(length, clip_length(clip))
        self.select(self.selected)

    def split(self):
        if self.selected is None or self.busy: return
        clip = self.clips[self.selected]; local = self.position-clip['offset']
        if not .001 < local < clip_length(clip)-.001: return
        self.snapshot(); other = copy.deepcopy(clip)
        if clip.get('reverse'):
            cut=clip['out']-local*clip.get('speed',1.)
            clip['in']=cut;other['out']=cut
        else:
            cut=clip['in']+local*clip.get('speed',1.)
            clip['out']=cut;other['in']=cut
        clip['fade_out']=0
        other['offset']=self.position;other['fade_in']=0
        self.clips.insert(self.selected+1, other); self.select(self.selected+1)

    def remove(self):
        if self.selected is None or self.busy: return
        self.snapshot(); self.clips.pop(self.selected); self.selected = None; self.paint()

    def undo(self):
        if self.history and not self.busy:
            self.stop(); self.clips = self.history.pop(); self.selected = None; self.paint()

    def fit(self):
        if self.zoom_job is not None:self.after_cancel(self.zoom_job);self.zoom_job=None
        self.precision_timer(False)
        self.animating = False
        self.zoom = self.zoom_target = 1.; self.view = 0; self.paint()

    def precision_timer(self,active):
        if active==self.precise_timer:return
        if active:self.precise_timer=ctypes.windll.winmm.timeBeginPeriod(1)==0
        else:ctypes.windll.winmm.timeEndPeriod(1);self.precise_timer=False

    def set_zoom(self, value):
        if self.zoom_job is not None: self.after_cancel(self.zoom_job)
        self.zoom_target = max(1, min(value, self.total()/.1))
        self.precision_timer(True)
        original = self.zoom; begin = time.perf_counter(); next_frame=begin; anchor = self.view+self.total()/self.zoom/2
        def tick():
            nonlocal next_frame
            self.zoom_job = None
            t = min(1, (time.perf_counter()-begin)/.2); ease = t*t*(3-2*t)
            self.zoom = math.exp(math.log(original)+(math.log(self.zoom_target)-math.log(original))*ease)
            self.view = max(0, min(self.total()-self.total()/self.zoom, anchor-self.total()/self.zoom/2))
            self.animating = t<1; self.paint()
            if t<1:
                next_frame+=1/60;now=time.perf_counter()
                if next_frame<now:next_frame=now+1/60
                self.zoom_job=self.after(max(1,int((next_frame-now)*1000)),tick)
            else:self.precision_timer(False)
        tick()

    def pan(self, *args):
        span = self.total()/self.zoom
        value = float(args[1])*self.total() if args[0]=='moveto' else self.view+int(args[1])*span*.1
        self.view = max(0, min(self.total()-span, value)); self.paint()

    def wheel(self, event):
        if event.state&4: self.set_zoom(self.zoom_target*(1.2 if event.delta>0 else 1/1.2))
        elif event.state&1: self.pan('scroll', -1 if event.delta>0 else 1, 'units')
        else: self.canvas.yview_scroll(-1 if event.delta>0 else 1, 'units')
        return 'break'

    def mci(self, command, buffer=None):
        return ctypes.windll.winmm.mciSendStringW(command, buffer, len(buffer) if buffer is not None else 0, None)

    def stop(self):
        self.mci(f'close {self.alias}'); self.playing = False
        if self.preview:
            try: os.remove(self.preview)
            except OSError: pass
            self.preview = None

    def play(self):
        if self.playing: self.stop(); return
        if not self.clips or self.busy: return
        end=max(c['offset']+clip_length(c) for c in self.clips)
        if self.position>=end:self.position=0
        handle, self.preview = tempfile.mkstemp(prefix='resonate-mix-', suffix='.wav'); os.close(handle)
        self.render(self.preview, preview=True)

    def export(self):
        if not self.clips or self.busy: return
        ext = self.api.FORMATS[self.format.get()][0]
        path = filedialog.asksaveasfilename(parent=self.app, title='Export mixed timeline', defaultextension='.'+ext, filetypes=[(self.format.get(), '*.'+ext)])
        if not path: return
        if any(os.path.normcase(os.path.abspath(path))==os.path.normcase(c['path']) for c in self.clips):
            self.status.configure(text='Choose a new output file to keep your original audio safe.'); return
        self.render(path)

    def render(self, path, preview=False):
        self.busy = True; self.cancelled = False
        self.status.configure(text='Preparing mixed playback…' if preview else 'Exporting mixed timeline…')
        clips = copy.deepcopy(self.clips)
        fmt = self.format.get()
        settings = {'format': fmt, 'bitrate': self.app.bitrate_var.get(), 'rate': self.app.rate_var.get(), 'channels': self.app.channels_var.get()}
        codec = None if preview else self.api.FORMATS[fmt][1]+self.app.encode_args(settings, self.api.FORMATS[fmt][2])
        staging = path
        if not preview:
            handle, staging = tempfile.mkstemp(prefix='.resonate-export-',suffix=os.path.splitext(path)[1],dir=os.path.dirname(os.path.abspath(path)))
            os.close(handle)
        command = mix_command(self.app.ffmpeg, clips, staging, self.position if preview else 0, codec)
        def worker():
            try:
                self.proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=self.api.NO_WINDOW)
                _, error = self.proc.communicate()
                if self.proc.returncode: raise ValueError(error.decode(errors='replace')[-300:] or 'Task cancelled')
                if not preview: os.replace(staging,path)
                self.messages.put(('preview', path) if preview else ('finished', 'Mix exported: '+os.path.basename(path)))
            except Exception as error:
                self.messages.put(('finished', str(error)))
                try: os.remove(staging)
                except OSError: pass
        threading.Thread(target=worker, daemon=True).start()

    def cancel(self):
        self.cancelled = True
        if self.proc is not None and self.proc.poll() is None: self.proc.terminate()

    def save_project(self):
        path = filedialog.asksaveasfilename(parent=self.app, defaultextension='.resonate.json', filetypes=[('Resonate project', '*.resonate.json')])
        if path:
            with open(path, 'w', encoding='utf-8') as stream: json.dump({'version': 1, 'clips': self.clips}, stream)

    def open_project(self):
        if self.busy: return
        path = filedialog.askopenfilename(parent=self.app, filetypes=[('Resonate project', '*.json')])
        if not path: return
        try:
            with open(path, encoding='utf-8') as stream: data = json.load(stream)
            clips = data['clips']
            if not isinstance(clips, list): raise ValueError('Invalid project')
            for clip in clips:
                clip.setdefault('speed',1.);clip.setdefault('reverse',False)
                tempo_filters(clip['speed'])
                if not isinstance(clip['reverse'],bool):raise ValueError('Invalid reverse setting')
                for key in ('duration', 'in', 'out', 'offset', 'fade_in', 'fade_out'):
                    if not isinstance(clip[key], (int, float)) or not math.isfinite(clip[key]): raise ValueError('Invalid clip time')
                if not (0<=clip['in']<clip['out']<=clip['duration'] and clip['offset']>=0): raise ValueError('Invalid clip range')
                if not os.path.isfile(clip['path']): raise ValueError('Missing audio: '+clip['path'])
                peaks=clip.get('peaks',[])
                if not isinstance(peaks,list) or len(peaks)>100000 or any(not isinstance(v,(float,int)) or not math.isfinite(v) or not 0<=v<=1 for v in peaks):raise ValueError('Invalid waveform data')
            self.snapshot(); self.clips = clips; self.selected = None; self.fit()
        except Exception as error: self.status.configure(text=str(error))

    def destroy(self):
        if self.closed: return
        self.closed = True; self.cancel(); self.stop(); self.precision_timer(False)
        self.app.timeline_project = copy.deepcopy(self.clips)
        self.app.active_editor = None
        for job in (self.job, self.zoom_job):
            if job is not None: self.after_cancel(job)
        if self.app.activity_wave.winfo_exists(): self.app.activity_wave.animate(True)
        super().destroy()
