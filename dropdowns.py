"""Borderless, themed dropdown panels inside the app window."""
import customtkinter as ctk


class ResonateDropdown(ctk.CTkOptionMenu):
    active_popup = None

    def __init__(self, *args, **kwargs):
        kwargs.update(height=34, corner_radius=9,
                      fg_color=('#ffffff', '#000000'),
                      button_color=('#ffffff', '#000000'),
                      button_hover_color=('#000000', '#ffffff'),
                      text_color=('#000000', '#ffffff'),
                      text_color_disabled=('#92929b', '#686874'))
        super().__init__(*args, **kwargs)
        self.panel = None
        self.root_bindings = []
        self.bind('<Destroy>', self._disposed, add=True)

    def _on_enter(self,event=0):
        if self.cget('state')!='disabled':
            self.configure(fg_color=('#000000','#ffffff'),button_color=('#000000','#ffffff'),text_color=('#ffffff','#000000'))

    def _on_leave(self,event=0):
        self.configure(fg_color=('#ffffff','#000000'),button_color=('#ffffff','#000000'),text_color=('#000000','#ffffff'))

    def style_rows(self,hover=None):
        if self.panel is None:return
        for index,row in enumerate(self.rows):
            highlighted=index==hover or self.values_snapshot[index]==self.get()
            row.configure(fg_color=('#000000','#ffffff') if highlighted else 'transparent',
                          text_color=('#ffffff','#000000') if highlighted else ('#000000','#ffffff'))

    def _open_dropdown_menu(self):
        if self.cget('state') == 'disabled':
            return
        if self.panel is not None:
            self.close_panel()
            return
        if ResonateDropdown.active_popup is not None:
            ResonateDropdown.active_popup.close_panel()
        values = list(self.cget('values'))
        if not values:
            return
        self.host = self.winfo_toplevel()
        self.previous_focus=self.host.focus_get()
        self.host.update_idletasks()
        scale = ctk.ScalingTracker.get_widget_scaling(self)
        root_width = self.host.winfo_width()/scale
        root_height = self.host.winfo_height()/scale
        width = min(root_width-16, max(self._current_width, max(len(v) for v in values)*7+48))
        height = min(len(values)*34+12, 292, root_height-24)
        x = (self.winfo_rootx()-self.host.winfo_rootx())/scale
        y = (self.winfo_rooty()-self.host.winfo_rooty()+self.winfo_height())/scale+5
        x = max(8, min(x, root_width-width-8))
        if y+height>root_height-8:
            y = max(8, (self.winfo_rooty()-self.host.winfo_rooty())/scale-height-5)
        self.panel = ctk.CTkFrame(self.host, width=width, height=height, corner_radius=12,
                                 border_width=0, fg_color=('#ffffff', '#000000'))
        self.panel.pack_propagate(False)
        self.panel.place(x=x, y=y)
        self.panel.lift()
        self.pane = ctk.CTkScrollableFrame(self.panel, fg_color='transparent', corner_radius=0,
                                          scrollbar_button_color=('#cccccc', '#333333'),
                                          scrollbar_button_hover_color=('#000000', '#ffffff'))
        self.pane.pack(fill='both', expand=True, padx=5, pady=5)
        self.rows = []
        self.values_snapshot = values
        self.active = values.index(self.get()) if self.get() in values else 0
        for index, value in enumerate(values):
            selected = value == self.get()
            row = ctk.CTkButton(self.pane, text=('✓  ' if selected else '    ')+value,
                                anchor='w', height=32, corner_radius=7, border_width=0,
                                fg_color=('#000000', '#ffffff') if selected else 'transparent',
                                hover_color=('#000000', '#ffffff'),
                                text_color=('#ffffff', '#000000') if selected else ('#000000', '#ffffff'),
                                font=self.cget('font'),
                                command=lambda choice=value: self.choose(choice))
            row.bind('<Enter>',lambda e,i=index:self.style_rows(i),add=True)
            row.bind('<Leave>',lambda e:self.style_rows(),add=True)
            row.pack(fill='x', pady=1)
            self.rows.append(row)
        self.panel._canvas.bind("<Escape>",self.escape)
        self.panel._canvas.bind("<Down>",lambda e:self.navigate(1))
        self.panel._canvas.bind("<Up>",lambda e:self.navigate(-1))
        self.panel._canvas.bind("<Return>",self.enter)
        self.host.after_idle(lambda:self.panel._canvas.focus_set() if self.panel is not None else None)
        ResonateDropdown.active_popup = self
        for sequence, callback in [('<Button-1>', self.outside), ('<Escape>', self.escape),
                                    ('<Down>', lambda e:self.navigate(1)), ('<Up>', lambda e:self.navigate(-1)),
                                    ('<Return>', self.enter), ('<Configure>', self.window_changed)]:
            binding = self.host.bind(sequence, callback, add='+')
            self.root_bindings.append((sequence, binding))

    def outside(self, event):
        widget = event.widget
        while widget is not None:
            if widget is self or widget is self.panel:
                return
            widget = getattr(widget, 'master', None)
        self.close_panel()

    def window_changed(self, event):
        if event.widget is self.host:
            self.close_panel()

    def navigate(self, direction):
        if self.panel is None:
            return
        self.active = (self.active+direction)%len(self.rows)
        self.style_rows(self.active)
        self.pane._parent_canvas.yview_moveto(max(0, self.active-3)/max(1, len(self.rows)))
        return 'break'

    def enter(self, event):
        if self.panel is not None:
            self.choose(self.values_snapshot[self.active])
            return 'break'

    def escape(self, event):
        self.close_panel()
        return 'break'

    def choose(self, value):
        self.close_panel()
        root = self.winfo_toplevel()
        self._dropdown_callback(value)
        if hasattr(root, 'play_click'):
            root.play_click()

    def close_panel(self):
        if self.panel is not None:
            self.panel.destroy()
            self.panel = None
            if getattr(self,"previous_focus",None) is not None and self.previous_focus.winfo_exists():self.previous_focus.focus_set()
        if hasattr(self, 'host'):
            for sequence, binding in self.root_bindings:
                self.host.unbind(sequence, binding)
        self.root_bindings = []
        if ResonateDropdown.active_popup is self:
            ResonateDropdown.active_popup = None

    def destroy(self):
        self.close_panel()
        super().destroy()

    def _disposed(self, event):
        if event.widget is self or str(event.widget)==str(self):
            self.close_panel()
