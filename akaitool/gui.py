"""Tk desktop GUI: build, edit, audition and export Akai S1000/S2000/S3000 sampler CDs."""
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import numpy as np

from . import __version__, akai, audio, export, image, mapping, project
from .akai import Keygroup, Loop, Program, VolumeModel, Zone

CD_MB = {'74 min (650 MB)': 650, '80 min (700 MB)': 700}
PLAYBACK = ['As sample', 'Loop in release', 'Loop until release', 'No loop', 'Play to end']
LOOPMODES = ['Loop in release', 'Loop until release', 'No loop', 'Play to end']
TITLE = 'Akai CD ISO Maker'


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(TITLE)
        self.geometry('1180x760')
        self.minsize(900, 560)
        self.disc = []                      # list[VolumeModel]
        self.nodes = {}                     # tree iid -> (kind, volume, obj)
        self.cur = None                     # currently selected sample (for the editor)
        self.q = queue.Queue()
        self.project_path = None
        self.dirty = False
        self._build_menu()
        self._build_ui()
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self.after(100, self._poll)
        self.refresh()

    # ------------------------------------------------------------ layout
    def _build_menu(self):
        m = tk.Menu(self)
        f = tk.Menu(m, tearoff=0)
        f.add_command(label='New disc', command=self.new_disc, accelerator='Ctrl+N')
        f.add_command(label='Open project...', command=self.open_project)
        f.add_command(label='Save project', command=self.save_project, accelerator='Ctrl+S')
        f.add_command(label='Save project as...', command=lambda: self.save_project(True))
        f.add_separator()
        f.add_command(label='Open Akai CD / disk image to edit...', command=self.open_image)
        f.add_separator()
        f.add_command(label='Build Akai disc image...', command=self.build_image, accelerator='Ctrl+B')
        f.add_separator()
        f.add_command(label='Exit', command=self.on_close)
        m.add_cascade(label='File', menu=f)
        a = tk.Menu(m, tearoff=0)
        a.add_command(label='Add audio files...', command=self.add_audio, accelerator='Ctrl+O')
        a.add_command(label='Add folder of audio...', command=self.add_folder)
        a.add_separator()
        a.add_command(label='New volume', command=self.new_volume)
        a.add_command(label='Rename...', command=self.rename)
        a.add_command(label='Delete selected', command=self.delete_selected, accelerator='Del')
        m.add_cascade(label='Edit', menu=a)
        e = tk.Menu(m, tearoff=0)
        e.add_command(label='Selected sample(s) to WAV...', command=lambda: self.export_selection('wav'))
        e.add_command(label='Selected program(s) / volume to SFZ...', command=lambda: self.export_selection('sfz'))
        e.add_command(label='Selected program(s) / volume to SoundFont (SF2)...', command=lambda: self.export_selection('sf2'))
        m.add_cascade(label='Export', menu=e)
        h = tk.Menu(m, tearoff=0)
        h.add_command(label='Burning the disc', command=self.help_burn)
        h.add_command(label='About', command=self.about)
        m.add_cascade(label='Help', menu=h)
        self.config(menu=m)
        self.bind('<Control-n>', lambda _e: self.new_disc())
        self.bind('<Control-s>', lambda _e: self.save_project())
        self.bind('<Control-o>', lambda _e: self.add_audio())
        self.bind('<Control-b>', lambda _e: self.build_image())

    def _build_ui(self):
        tb = ttk.Frame(self, padding=(6, 4))
        tb.pack(fill='x')
        for text, cmd in [('Add audio...', self.add_audio), ('Add folder...', self.add_folder),
                          ('New volume', self.new_volume), ('Auto-map selected', self.automap_dialog),
                          ('Delete', self.delete_selected)]:
            ttk.Button(tb, text=text, command=cmd).pack(side='left', padx=2)
        ttk.Separator(tb, orient='vertical').pack(side='left', fill='y', padx=6)
        ttk.Button(tb, text='Build disc image...', command=self.build_image).pack(side='left', padx=2)
        ttk.Button(tb, text='Open Akai CD...', command=self.open_image).pack(side='left', padx=2)
        pw = ttk.PanedWindow(self, orient='horizontal')
        pw.pack(fill='both', expand=True, padx=6, pady=2)
        left = ttk.Frame(pw)
        pw.add(left, weight=1)
        self.tree = ttk.Treeview(left, show='tree', selectmode='extended')
        sb = ttk.Scrollbar(left, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='left', fill='y')
        self.tree.bind('<<TreeviewSelect>>', self.on_select)
        self.tree.bind('<Delete>', lambda _e: self.delete_selected())
        self.tree.bind('<Double-1>', lambda _e: self.rename())
        self.right = ttk.Frame(pw)
        pw.add(self.right, weight=3)
        self.panel = ttk.Frame(self.right)
        self.panel.pack(fill='both', expand=True)
        # status / capacity
        bot = ttk.Frame(self, padding=(6, 3))
        bot.pack(fill='x')
        self.status = tk.StringVar(value='Add audio files to start a disc, or open an existing Akai CD image.')
        ttk.Label(bot, textvariable=self.status).pack(side='left')
        self.cd = tk.StringVar(value='80 min (700 MB)')
        ttk.Combobox(bot, textvariable=self.cd, values=list(CD_MB), width=16, state='readonly').pack(side='right')
        self.cd.trace_add('write', lambda *_: self.update_capacity())
        self.cap = ttk.Progressbar(bot, length=220, maximum=100)
        self.cap.pack(side='right', padx=6)
        self.capl = tk.StringVar()
        ttk.Label(bot, textvariable=self.capl).pack(side='right')

    # ------------------------------------------------------------ helpers
    def busy(self, msg):
        self.status.set(msg)
        self.update_idletasks()

    def _poll(self):
        try:
            while True:
                fn = self.q.get_nowait()
                fn()
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def run_bg(self, work, done, msg):
        """work() runs in a thread, done(result) on the UI thread"""
        self.busy(msg)
        self.config(cursor='watch')

        def target():
            try:
                res = work()
                err = None
            except Exception as ex:                      # noqa: BLE001 - shown to the user
                res, err = None, ex
            self.q.put(lambda: self._finish(done, res, err))
        threading.Thread(target=target, daemon=True).start()

    def _finish(self, done, res, err):
        self.config(cursor='')
        if err is not None:
            self.status.set('Error: %s' % err)
            messagebox.showerror(TITLE, str(err))
            return
        done(res)

    def mark(self):
        self.dirty = True
        self.refresh()

    def selection(self):
        return [self.nodes[i] for i in self.tree.selection() if i in self.nodes]

    def current_volume(self, create=True):
        sel = self.selection()
        if sel:
            return sel[0][1]
        if self.disc:
            return self.disc[0]
        if create:
            vm = VolumeModel('NEW VOLUME')
            self.disc.append(vm)
            return vm
        return None

    # ------------------------------------------------------------ tree
    def refresh(self, keep=None):
        sel = [self.nodes[i][2] for i in self.tree.selection() if i in self.nodes]
        self.tree.delete(*self.tree.get_children())
        self.nodes = {}
        reselect = []
        for vm in self.disc:
            vi = self.tree.insert('', 'end', text='%s  (volume)' % vm.name, open=True)
            self.nodes[vi] = ('volume', vm, vm)
            pi = self.tree.insert(vi, 'end', text='Programs (%d)' % len(vm.programs), open=True)
            self.nodes[pi] = ('programs', vm, vm)
            for p in vm.programs:
                i = self.tree.insert(pi, 'end', text='%s   [%d keygroups]' % (p.name, len(p.keygroups)))
                self.nodes[i] = ('program', vm, p)
                if p in sel:
                    reselect.append(i)
            si = self.tree.insert(vi, 'end', text='Samples (%d, %.1f MB)' % (
                len(vm.samples), sum(s.n for s in vm.samples.values()) * 2 / 1048576.0), open=False)
            self.nodes[si] = ('samples', vm, vm)
            for s in vm.samples.values():
                lp = '  loop' if s.loops and s.loop_mode < 2 else ''
                i = self.tree.insert(si, 'end', text='%s   %s  %.2fs%s' % (s.name, audio.note_name(s.root), s.seconds, lp))
                self.nodes[i] = ('sample', vm, s)
                if s in sel:
                    reselect.append(i)
                    self.tree.item(si, open=True)
            if vm in sel:
                reselect.append(vi)
        if reselect:
            self.tree.selection_set(reselect)
        self.update_capacity()
        self.title('%s%s - %s' % (os.path.basename(self.project_path) if self.project_path else 'untitled',
                                 ' *' if self.dirty else '', TITLE))

    def update_capacity(self):
        try:
            size = image.image_size(self.disc) if self.disc else 0
        except ValueError:
            size = sum(s.n * 2 for v in self.disc for s in v.samples.values())
        cap = CD_MB[self.cd.get()] * 1e6
        self.cap['value'] = min(100, 100.0 * size / cap)
        self.capl.set('%.1f / %d MB' % (size / 1e6, CD_MB[self.cd.get()]))

    # ------------------------------------------------------------ right panel
    def clear_panel(self):
        for w in self.panel.winfo_children():
            w.destroy()
        self.stop_play()

    def on_select(self, _e=None):
        sel = self.selection()
        self.clear_panel()
        if len(sel) == 1 and sel[0][0] == 'sample':
            self.show_sample(sel[0][2], sel[0][1])
        elif len(sel) == 1 and sel[0][0] == 'program':
            self.show_program(sel[0][2], sel[0][1])
        elif len(sel) >= 1:
            n_s = sum(1 for k, _, _ in sel if k == 'sample')
            ttk.Label(self.panel, text='%d item(s) selected.\nUse Auto-map to build programs from selected samples,\n'
                      'Delete to remove, or the Export menu.' % len(sel), padding=20).pack(anchor='nw')
        else:
            ttk.Label(self.panel, text=self.intro(), padding=20, justify='left').pack(anchor='nw')

    def intro(self):
        return ('Akai CD ISO Maker\n\n'
                '1. Add audio (wav, mp3, flac, ogg, aiff ...). Root notes come from the file name (e.g. Piano_C3),\n'
                '    the WAV smpl chunk, or are detected from the sound.\n'
                '2. Auto-map the samples into programs (multisample keyboard, drum kit, or one program each).\n'
                '3. Select a sample to set root, tuning and loop points (loop finder + crossfade).\n'
                '4. Build disc image, then burn it as an image (see Help > Burning the disc).\n\n'
                'S1000 / S2000 / S3000 all read the S1000 format this tool writes by default.')

    # ---- sample editor
    def show_sample(self, s, vm):
        self.cur = s
        p = self.panel
        top = ttk.Frame(p, padding=6)
        top.pack(fill='x')
        self.v_name = tk.StringVar(value=s.name)
        self.v_root = tk.IntVar(value=s.root)
        self.v_cents = tk.DoubleVar(value=round(s.cents, 2))
        self.v_ls = tk.IntVar(value=s.loops[0].start if s.loops else 0)
        self.v_le = tk.IntVar(value=s.loops[0].end if s.loops else 0)
        self.v_mode = tk.StringVar(value=LOOPMODES[s.loop_mode])
        self.v_xf = tk.IntVar(value=20)
        r = 0
        ttk.Label(top, text='Name').grid(row=r, column=0, sticky='w')
        ttk.Entry(top, textvariable=self.v_name, width=16).grid(row=r, column=1, sticky='w')
        ttk.Label(top, text='Root key').grid(row=r, column=2, padx=(12, 2))
        sp = ttk.Spinbox(top, from_=24, to=127, textvariable=self.v_root, width=5, command=self.sample_apply)
        sp.grid(row=r, column=3)
        self.root_lbl = ttk.Label(top, text=audio.note_name(s.root))
        self.root_lbl.grid(row=r, column=4, padx=4)
        ttk.Button(top, text='Detect', command=self.detect_root).grid(row=r, column=5, padx=2)
        ttk.Label(top, text='Tune (cents)').grid(row=r, column=6, padx=(12, 2))
        ttk.Spinbox(top, from_=-1200, to=1200, increment=1, textvariable=self.v_cents, width=7,
                    command=self.sample_apply).grid(row=r, column=7)
        r = 1
        ttk.Label(top, text='Loop start').grid(row=r, column=0, sticky='w', pady=(6, 0))
        ttk.Entry(top, textvariable=self.v_ls, width=10).grid(row=r, column=1, sticky='w', pady=(6, 0))
        ttk.Label(top, text='Loop end').grid(row=r, column=2, pady=(6, 0))
        ttk.Entry(top, textvariable=self.v_le, width=10).grid(row=r, column=3, columnspan=2, sticky='w', pady=(6, 0))
        ttk.Label(top, text='Mode').grid(row=r, column=5, pady=(6, 0))
        cb = ttk.Combobox(top, textvariable=self.v_mode, values=LOOPMODES, width=16, state='readonly')
        cb.grid(row=r, column=6, columnspan=2, pady=(6, 0))
        r = 2
        bar = ttk.Frame(p, padding=(6, 0))
        bar.pack(fill='x')
        ttk.Button(bar, text='Apply', command=self.sample_apply).pack(side='left', padx=2)
        ttk.Button(bar, text='Find loop', command=self.find_loop).pack(side='left', padx=2)
        ttk.Label(bar, text='Crossfade ms').pack(side='left', padx=(10, 2))
        ttk.Spinbox(bar, from_=0, to=2000, increment=10, textvariable=self.v_xf, width=6).pack(side='left')
        ttk.Button(bar, text='Bake crossfade', command=self.bake_xfade).pack(side='left', padx=2)
        ttk.Button(bar, text='Clear loop', command=self.clear_loop).pack(side='left', padx=2)
        ttk.Button(bar, text='Play', command=self.play).pack(side='left', padx=(14, 2))
        ttk.Button(bar, text='Play loop', command=lambda: self.play(True)).pack(side='left', padx=2)
        ttk.Button(bar, text='Stop', command=self.stop_play).pack(side='left', padx=2)
        ttk.Button(bar, text='Normalize', command=self.normalize).pack(side='left', padx=(14, 2))
        ttk.Button(bar, text='Trim silence', command=self.trim).pack(side='left', padx=2)
        info = '%d samples, %.1f kB, plays at %.0f Hz equivalent.   Left click = loop start, right click = loop end.' % (
            s.n, s.n * 2 / 1024.0, s.play_rate)
        ttk.Label(p, text=info, padding=(8, 4)).pack(anchor='w')
        self.wave = tk.Canvas(p, bg='#101820', height=300, highlightthickness=0)
        self.wave.pack(fill='both', expand=True, padx=6, pady=4)
        self.wave.bind('<Configure>', lambda _e: self.draw_wave())
        self.wave.bind('<Button-1>', lambda e: self.wave_click(e, 0))
        self.wave.bind('<Button-3>', lambda e: self.wave_click(e, 1))
        self.root_trace = self.v_root.trace_add('write', lambda *_: self._root_changed())

    def _root_changed(self):
        try:
            self.root_lbl.config(text=audio.note_name(int(self.v_root.get())))
        except (tk.TclError, ValueError):
            pass

    def draw_wave(self):
        s = self.cur
        c = getattr(self, 'wave', None)
        if s is None or c is None or not c.winfo_exists():
            return
        c.delete('all')
        w, h = c.winfo_width(), c.winfo_height()
        if w < 10:
            return
        n = s.n
        x = s.pcm.astype(np.float32) / 32768.0
        step = max(1, n // w)
        m = (n // step) * step
        blk = x[:m].reshape(-1, step)
        mx, mn = blk.max(axis=1), blk.min(axis=1)
        mid = h / 2
        c.create_line(0, mid, w, mid, fill='#2a3a4a')
        xs = np.linspace(0, w - 1, len(mx))
        for i in range(len(mx)):
            c.create_line(xs[i], mid - mx[i] * mid * 0.95, xs[i], mid - mn[i] * mid * 0.95, fill='#58d68d')
        if s.loops:
            lp = s.loops[0]
            for pos, col in ((lp.start, '#f4d03f'), (lp.end, '#ec7063')):
                px = pos / float(n) * w
                c.create_line(px, 0, px, h, fill=col, width=2)
            c.create_rectangle(lp.start / float(n) * w, 0, lp.end / float(n) * w, 6, fill='#f4d03f', outline='')

    def wave_click(self, e, which):
        s = self.cur
        w = self.wave.winfo_width()
        pos = int(max(0, min(s.n - 1, e.x / float(w) * s.n)))
        # snap to the nearest upward zero crossing within ~5 ms
        r = int(s.play_rate * 0.005)
        a, b = max(0, pos - r), min(s.n, pos + r)
        z = audio._zero_cross_up(s.pcm.astype(np.float32), a, b) if b - a > 2 else []
        if len(z):
            pos = int(z[np.argmin(np.abs(z - pos))])
        if which == 0:
            self.v_ls.set(pos)
            if self.v_le.get() <= pos:
                self.v_le.set(s.n)
        else:
            self.v_le.set(pos)
        self.sample_apply()

    def sample_apply(self):
        s = self.cur
        if s is None:
            return
        vm = self.selection()[0][1]
        try:
            s.root = int(min(127, max(24, self.v_root.get())))
            s.cents = float(self.v_cents.get())
            ls, le = int(self.v_ls.get()), int(self.v_le.get())
            s.loop_mode = LOOPMODES.index(self.v_mode.get())
        except (tk.TclError, ValueError):
            return
        if 0 <= ls < le <= s.n and le - ls >= 2:
            s.loops = [Loop(ls, le, 9999)]
        else:
            s.loops = []
        newname = akai.clean_name(self.v_name.get())
        if newname != s.name:
            self.rename_sample(vm, s, newname)
        self.dirty = True
        self.draw_wave()
        self.update_tree_text()

    def update_tree_text(self):
        for iid, (k, vm, o) in self.nodes.items():
            if k == 'sample' and o is self.cur:
                lp = '  loop' if o.loops and o.loop_mode < 2 else ''
                self.tree.item(iid, text='%s   %s  %.2fs%s' % (o.name, audio.note_name(o.root), o.seconds, lp))

    def rename_sample(self, vm, s, newname):
        if newname in vm.samples:
            newname = mapping.unique_name(newname, vm.samples)
        old = s.name
        vm.samples = {(newname if k == old else k): v for k, v in vm.samples.items()}
        s.name = newname
        for p in vm.programs:
            for kg in p.keygroups:
                for z in kg.zones:
                    if z.sample == old:
                        z.sample = newname

    def detect_root(self):
        s = self.cur
        m, conf = audio.detect_pitch(s.pcm.astype(np.float32) / 32768.0, s.play_rate)
        if m is None or conf < 0.5:
            messagebox.showinfo(TITLE, 'No clear pitch found (noise or percussion?).')
            return
        n = int(round(m))
        self.v_root.set(n)
        self.v_cents.set(round(s.cents, 2))
        self.sample_apply()
        self.status.set('Detected %s (%.0f cents off), confidence %.2f' % (audio.note_name(n), (m - n) * 100, conf))

    def find_loop(self):
        s = self.cur
        lp, score = audio.find_loop(s.pcm, s.play_rate)
        if lp is None:
            messagebox.showinfo(TITLE, 'No good loop found: the sample may be too short or too noisy. '
                                'Set loop points by clicking in the waveform.')
            return
        self.v_ls.set(lp.start)
        self.v_le.set(lp.end)
        self.v_mode.set(LOOPMODES[1])
        self.sample_apply()
        self.status.set('Loop found %d - %d (match %.0f%%). Bake a crossfade to smooth it.' % (lp.start, lp.end, score * 100))

    def bake_xfade(self):
        s = self.cur
        if not s.loops:
            messagebox.showinfo(TITLE, 'Set or find a loop first.')
            return
        xf = int(self.v_xf.get() * s.play_rate / 1000.0)
        s.pcm = audio.crossfade_loop(s.pcm, s.loops[0], xf)
        self.dirty = True
        self.draw_wave()
        self.status.set('Crossfade baked into the audio before the loop end.')

    def clear_loop(self):
        s = self.cur
        s.loops = []
        s.loop_mode = akai.LM_NOLOOP
        self.v_mode.set(LOOPMODES[akai.LM_NOLOOP])
        self.v_ls.set(0)
        self.v_le.set(0)
        self.draw_wave()
        self.update_tree_text()

    def normalize(self):
        s = self.cur
        pk = int(np.max(np.abs(s.pcm.astype(np.int32)))) if s.n else 0
        if pk:
            s.pcm = np.clip(np.round(s.pcm.astype(np.float32) * (0.89 * 32767 / pk)), -32768, 32767).astype(np.int16)
        self.draw_wave()

    def trim(self):
        s = self.cur
        a = np.abs(s.pcm.astype(np.int32))
        idx = np.where(a > 64)[0]
        if len(idx) == 0:
            return
        i0, i1 = int(idx[0]), int(idx[-1]) + 1
        s.pcm = s.pcm[i0:i1].copy()
        s.loops = [Loop(l.start - i0, l.end - i0, l.time) for l in s.loops if l.start >= i0 and l.end - i0 <= len(s.pcm)]
        self.refresh()
        self.status.set('Trimmed %d samples of silence.' % (i0 + (a.size - i1)))

    # ---- playback
    def play(self, loop=False):
        try:
            import sounddevice as sd
        except Exception:                                  # noqa: BLE001
            messagebox.showinfo(TITLE, 'Playback needs the sounddevice package (pip install sounddevice).')
            return
        s = self.cur
        x = s.pcm.astype(np.float32) / 32768.0
        if loop and s.loops:
            lp = s.loops[0]
            body = x[lp.start:lp.end]
            x = np.concatenate([x[:lp.end]] + [body] * 5 + [x[lp.end:]])
        rate = s.play_rate
        x = audio.resample(x.reshape(-1, 1), rate, 44100)[:, 0] if abs(rate - 44100) > 1 else x
        sd.stop()
        sd.play(x, 44100)

    def stop_play(self):
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:                                  # noqa: BLE001
            pass

    # ---- program editor
    def show_program(self, prog, vm):
        self.cur = None
        p = self.panel
        top = ttk.Frame(p, padding=6)
        top.pack(fill='x')
        ttk.Label(top, text='Program').pack(side='left')
        nm = tk.StringVar(value=prog.name)
        e = ttk.Entry(top, textvariable=nm, width=16)
        e.pack(side='left', padx=4)
        ttk.Label(top, text='MIDI program').pack(side='left', padx=(12, 2))
        mp = tk.IntVar(value=prog.midi_prog)
        ttk.Spinbox(top, from_=0, to=127, textvariable=mp, width=5).pack(side='left')

        def apply():
            prog.name = akai.clean_name(nm.get())
            try:
                prog.midi_prog = int(mp.get())
            except (tk.TclError, ValueError):
                pass
            self.mark()
        ttk.Button(top, text='Apply', command=apply).pack(side='left', padx=8)
        bar = ttk.Frame(p, padding=(6, 0))
        bar.pack(fill='x')
        ttk.Button(bar, text='Add keygroup from selected sample...', command=lambda: self.add_keygroup(prog, vm)).pack(side='left', padx=2)
        ttk.Button(bar, text='Edit keygroup...', command=lambda: self.edit_keygroup(prog, vm)).pack(side='left', padx=2)
        ttk.Button(bar, text='Remove keygroup', command=lambda: self.remove_keygroup(prog)).pack(side='left', padx=2)
        cols = ('lo', 'hi', 'zones')
        self.kgt = ttk.Treeview(p, columns=cols, show='headings', height=12, selectmode='browse')
        for c, t, w in (('lo', 'From', 70), ('hi', 'To', 70), ('zones', 'Samples (velocity, pan)', 600)):
            self.kgt.heading(c, text=t)
            self.kgt.column(c, width=w, anchor='w')
        self.kgt.pack(fill='both', expand=True, padx=6, pady=6)
        self.kgt.bind('<Double-1>', lambda _e: self.edit_keygroup(prog, vm))
        self.kb = tk.Canvas(p, height=64, bg='#202020', highlightthickness=0)
        self.kb.pack(fill='x', padx=6, pady=(0, 6))
        self.kb.bind('<Configure>', lambda _e: self.draw_keys(prog))
        self.fill_kg(prog)
        issues = [m for lv, m in image.validate([vm]) if '/ %s:' % prog.name in m]
        if issues:
            ttk.Label(p, text='\n'.join(issues), foreground='#b9770e', padding=(8, 0)).pack(anchor='w')

    def fill_kg(self, prog):
        self.kgt.delete(*self.kgt.get_children())
        for i, kg in enumerate(prog.keygroups):
            zs = ',  '.join('%s v%d-%d%s' % (z.sample.strip(), z.lovel, z.hivel, ' pan%+d' % z.pan if z.pan else '')
                            for z in kg.zones)
            self.kgt.insert('', 'end', iid=str(i), values=(audio.note_name(kg.lo), audio.note_name(kg.hi), zs))

    def draw_keys(self, prog):
        c = self.kb
        if not c.winfo_exists():
            return
        c.delete('all')
        w = c.winfo_width()
        kw = w / 128.0
        for k in range(128):
            black = (k % 12) in (1, 3, 6, 8, 10)
            c.create_rectangle(k * kw, 0, (k + 1) * kw, 40 if black else 64, fill='#444' if black else '#ddd', outline='#888')
        cols = ['#e74c3c', '#3498db', '#2ecc71', '#f39c12', '#9b59b6', '#1abc9c']
        for i, kg in enumerate(prog.keygroups):
            c.create_rectangle(kg.lo * kw, 44, (kg.hi + 1) * kw, 62, fill=cols[i % 6], outline='')

    def add_keygroup(self, prog, vm):
        smp = [o for k, _, o in self.selection() if k == 'sample']
        if not smp:
            messagebox.showinfo(TITLE, 'Select a sample in the tree first (ctrl+click for a stereo pair).')
            return
        vs = mapping.voices(smp)
        for v in vs:
            r = v[0].root
            prog.keygroups.append(Keygroup(max(24, r - 2), min(127, r + 2), 0, mapping.zones_for(v), mapping._env_for(v)))
        prog.keygroups.sort(key=lambda k: k.lo)
        self.mark()

    def remove_keygroup(self, prog):
        sel = self.kgt.selection()
        if sel:
            del prog.keygroups[int(sel[0])]
            self.mark()

    def edit_keygroup(self, prog, vm):
        sel = self.kgt.selection()
        if not sel:
            return
        kg = prog.keygroups[int(sel[0])]
        d = tk.Toplevel(self)
        d.title('Keygroup')
        d.transient(self)
        d.grab_set()
        f = ttk.Frame(d, padding=10)
        f.pack()
        lo, hi, tn = tk.IntVar(value=kg.lo), tk.IntVar(value=kg.hi), tk.IntVar(value=kg.tune)
        xf = tk.IntVar(value=kg.xfade)
        ttk.Label(f, text='Key range').grid(row=0, column=0, sticky='w')
        ttk.Spinbox(f, from_=24, to=127, textvariable=lo, width=5).grid(row=0, column=1)
        ttk.Label(f, text='to').grid(row=0, column=2)
        ttk.Spinbox(f, from_=24, to=127, textvariable=hi, width=5).grid(row=0, column=3)
        ttk.Label(f, text='Tune (cents)').grid(row=0, column=4, padx=(12, 2))
        ttk.Spinbox(f, from_=-3600, to=3600, textvariable=tn, width=6).grid(row=0, column=5)
        ttk.Checkbutton(f, text='Velocity crossfade', variable=xf).grid(row=0, column=6, padx=10)
        heads = ['Sample', 'Vel lo', 'Vel hi', 'Pan', 'Tune', 'Playback']
        for i, h in enumerate(heads):
            ttk.Label(f, text=h).grid(row=1, column=i, pady=(10, 0))
        names = [''] + list(vm.samples)
        rows = []
        for r in range(4):
            z = kg.zones[r] if r < len(kg.zones) else Zone('', 0, 127)
            v = dict(s=tk.StringVar(value=z.sample), a=tk.IntVar(value=z.lovel), b=tk.IntVar(value=z.hivel),
                     p=tk.IntVar(value=z.pan), t=tk.IntVar(value=z.tune), m=tk.StringVar(value=PLAYBACK[z.playback]))
            ttk.Combobox(f, textvariable=v['s'], values=names, width=16).grid(row=2 + r, column=0, pady=1)
            ttk.Spinbox(f, from_=0, to=127, textvariable=v['a'], width=5).grid(row=2 + r, column=1)
            ttk.Spinbox(f, from_=0, to=127, textvariable=v['b'], width=5).grid(row=2 + r, column=2)
            ttk.Spinbox(f, from_=-50, to=50, textvariable=v['p'], width=5).grid(row=2 + r, column=3)
            ttk.Spinbox(f, from_=-3600, to=3600, textvariable=v['t'], width=6).grid(row=2 + r, column=4)
            ttk.Combobox(f, textvariable=v['m'], values=PLAYBACK, width=16, state='readonly').grid(row=2 + r, column=5)
            rows.append(v)
        env = dict(kg.env)
        ef = ttk.LabelFrame(f, text='Amp envelope (0-99)', padding=6)
        ef.grid(row=6, column=0, columnspan=7, sticky='we', pady=10)
        ev = {}
        for i, (k, t) in enumerate((('amp_att', 'Attack'), ('amp_dec', 'Decay'), ('amp_sus', 'Sustain'), ('amp_rel', 'Release'))):
            ev[k] = tk.IntVar(value=env.get(k, 0))
            ttk.Label(ef, text=t).grid(row=0, column=2 * i, padx=(8, 2))
            ttk.Spinbox(ef, from_=0, to=99, textvariable=ev[k], width=5).grid(row=0, column=2 * i + 1)

        def ok():
            try:
                kg.lo, kg.hi, kg.tune = int(lo.get()), int(hi.get()), int(tn.get())
                kg.xfade = int(xf.get())
                zs = []
                for v in rows:
                    nm = v['s'].get().strip('\x00')
                    if nm in vm.samples:
                        zs.append(Zone(nm, int(v['a'].get()), int(v['b'].get()), int(v['t'].get()), 0, 0,
                                       int(v['p'].get()), PLAYBACK.index(v['m'].get())))
                kg.zones = zs
                for k, var in ev.items():
                    kg.env[k] = int(var.get())
            except (tk.TclError, ValueError):
                return
            d.destroy()
            prog.keygroups.sort(key=lambda k: k.lo)
            self.mark()
        ttk.Button(f, text='OK', command=ok).grid(row=7, column=5, sticky='e')
        ttk.Button(f, text='Cancel', command=d.destroy).grid(row=7, column=6)

    # ------------------------------------------------------------ actions
    def new_disc(self):
        if self.dirty and not messagebox.askyesno(TITLE, 'Discard the current disc?'):
            return
        self.disc, self.project_path, self.dirty = [], None, False
        self.refresh()
        self.on_select()

    def new_volume(self):
        n = simpledialog.askstring(TITLE, 'Volume name (max 12 characters):', parent=self)
        if n:
            self.disc.append(VolumeModel(akai.clean_name(n)))
            self.mark()

    def rename(self):
        sel = self.selection()
        if len(sel) != 1:
            return
        k, vm, o = sel[0]
        if k in ('volume', 'program', 'sample'):
            n = simpledialog.askstring(TITLE, 'New name (max 12 characters):', initialvalue=o.name, parent=self)
            if n:
                if k == 'sample':
                    self.rename_sample(vm, o, akai.clean_name(n))
                else:
                    o.name = akai.clean_name(n)
                self.mark()

    def delete_selected(self):
        sel = self.selection()
        if not sel:
            return
        if not messagebox.askyesno(TITLE, 'Delete %d selected item(s)?' % len(sel)):
            return
        for k, vm, o in sel:
            if k == 'sample':
                vm.samples.pop(o.name, None)
                for p in vm.programs:
                    for kg in p.keygroups:
                        kg.zones = [z for z in kg.zones if z.sample != o.name]
                    p.keygroups = [kg for kg in p.keygroups if kg.zones]
            elif k == 'program' and o in vm.programs:
                vm.programs.remove(o)
            elif k == 'volume' and o in self.disc:
                self.disc.remove(o)
        self.mark()
        self.on_select()

    def add_folder(self):
        d = filedialog.askdirectory(title='Folder with audio files')
        if d:
            files = []
            for root, _, names in os.walk(d):
                files += [os.path.join(root, n) for n in sorted(names) if n.lower().endswith(audio.AUDIO_EXT)]
            if files:
                self.add_audio(files, os.path.basename(os.path.abspath(d)))
            else:
                messagebox.showinfo(TITLE, 'No audio files found in that folder.')

    def add_audio(self, files=None, progname=None):
        if not files:
            files = filedialog.askopenfilenames(title='Add audio', filetypes=[
                ('Audio', ' '.join('*' + e for e in audio.AUDIO_EXT)), ('All files', '*.*')])
        if not files:
            return
        opts = self.import_options(len(files), progname)
        if opts is None:
            return
        vm = self.current_volume()
        files = list(files)

        def work():
            res, errs = [], []
            for i, f in enumerate(files):
                self.q.put(lambda i=i, f=f: self.status.set('Importing %d/%d  %s' % (i + 1, len(files), os.path.basename(f))))
                try:
                    res += audio.make_samples(f, target_rate=opts['rate'], stereo=opts['stereo'], normalize=opts['norm'])
                except Exception as ex:                    # noqa: BLE001
                    errs.append('%s: %s' % (os.path.basename(f), ex))
            return res, errs

        def done(r):
            smp, errs = r
            smp = mapping.add_samples(vm, smp)
            lay = opts['layout']
            if lay == 'multi' and smp:
                vm.programs.append(mapping.multisample(opts['name'], smp))
            elif lay == 'drums' and smp:
                vm.programs.append(mapping.drumkit(opts['name'], smp))
            elif lay == 'each' and smp:
                vm.programs += mapping.one_each(smp)
            self.mark()
            self.status.set('Added %d samples.' % len(smp))
            if errs:
                messagebox.showwarning(TITLE, 'Some files could not be imported:\n\n' + '\n'.join(errs[:12]))
        self.run_bg(work, done, 'Importing audio...')

    def import_options(self, n, progname=None):
        d = tk.Toplevel(self)
        d.title('Import %d file(s)' % n)
        d.transient(self)
        d.grab_set()
        f = ttk.Frame(d, padding=12)
        f.pack()
        rate = tk.IntVar(value=44100)
        st = tk.StringVar(value='split')
        nm = tk.BooleanVar(value=False)
        lay = tk.StringVar(value='multi')
        name = tk.StringVar(value=progname or 'NEW PROGRAM')
        res = {}
        ttk.Label(f, text='Sample rate').grid(row=0, column=0, sticky='w')
        ttk.Radiobutton(f, text='44.1 kHz (full quality)', variable=rate, value=44100).grid(row=0, column=1, sticky='w')
        ttk.Radiobutton(f, text='22.05 kHz (half the memory)', variable=rate, value=22050).grid(row=1, column=1, sticky='w')
        ttk.Label(f, text='Stereo files').grid(row=2, column=0, sticky='w', pady=(8, 0))
        ttk.Radiobutton(f, text='Split to L + R samples (panned pair)', variable=st, value='split').grid(row=2, column=1, sticky='w', pady=(8, 0))
        ttk.Radiobutton(f, text='Mix to mono', variable=st, value='mono').grid(row=3, column=1, sticky='w')
        ttk.Checkbutton(f, text='Normalize each sample', variable=nm).grid(row=4, column=1, sticky='w', pady=(8, 0))
        ttk.Label(f, text='Then create').grid(row=5, column=0, sticky='w', pady=(8, 0))
        for i, (t, v) in enumerate((('Multisample program (keys from root notes)', 'multi'), ('Drum kit (one key each from C2)', 'drums'),
                                    ('One program per sample', 'each'), ('Nothing, samples only', 'none'))):
            ttk.Radiobutton(f, text=t, variable=lay, value=v).grid(row=5 + i, column=1, sticky='w', pady=(8, 0) if i == 0 else 0)
        ttk.Label(f, text='Program name').grid(row=9, column=0, sticky='w', pady=(8, 0))
        ttk.Entry(f, textvariable=name, width=16).grid(row=9, column=1, sticky='w', pady=(8, 0))

        def ok():
            res.update(rate=rate.get(), stereo=st.get(), norm=nm.get(), layout=lay.get(), name=name.get())
            d.destroy()
        bf = ttk.Frame(f)
        bf.grid(row=10, column=0, columnspan=2, sticky='e', pady=(12, 0))
        ttk.Button(bf, text='Import', command=ok).pack(side='right')
        ttk.Button(bf, text='Cancel', command=d.destroy).pack(side='right', padx=6)
        self.wait_window(d)
        return res or None

    def automap_dialog(self):
        smp = [(vm, o) for k, vm, o in self.selection() if k == 'sample']
        if not smp:
            messagebox.showinfo(TITLE, 'Select samples in the tree first.')
            return
        vm = smp[0][0]
        s = [o for v, o in smp if v is vm]
        kind = simpledialog.askstring(TITLE, 'Create from %d sample(s):\n  multi = multisample keyboard map\n  drums = drum kit\n'
                                      '  each = one program per sample\n\nType multi, drums or each:' % len(s),
                                      initialvalue='multi', parent=self)
        if kind not in ('multi', 'drums', 'each'):
            return
        name = vm.name if kind != 'each' else ''
        if kind != 'each':
            name = simpledialog.askstring(TITLE, 'Program name:', initialvalue='NEW PROGRAM', parent=self) or 'NEW PROGRAM'
        if kind == 'multi':
            vm.programs.append(mapping.multisample(name, s))
        elif kind == 'drums':
            vm.programs.append(mapping.drumkit(name, s))
        else:
            vm.programs += mapping.one_each(s)
        self.mark()

    # ------------------------------------------------------------ files
    def build_image(self):
        mode = self.ask_mode()
        if not mode:
            return
        probs = image.validate(self.disc, mode)
        errs = [m for lv, m in probs if lv == 'error']
        warns = [m for lv, m in probs if lv == 'warn']
        if errs:
            messagebox.showerror(TITLE, 'Fix these first:\n\n' + '\n'.join(errs[:15]))
            return
        if warns and not messagebox.askyesno(TITLE, 'Warnings:\n\n' + '\n'.join(warns[:15]) + '\n\nBuild anyway?'):
            return
        path = filedialog.asksaveasfilename(title='Save Akai disc image', defaultextension='.iso',
                                            filetypes=[('Disc image', '*.iso'), ('All', '*.*')],
                                            initialfile=(self.disc[0].name.strip() or 'AKAI') + '.iso')
        if not path:
            return
        self.run_bg(lambda: image.write_image(path, self.disc, mode),
                    lambda r: self._built(path, r), 'Building disc image...')

    def _built(self, path, r):
        size, parts = r
        self.status.set('Wrote %s  (%.1f MB, %d partition(s))' % (path, size / 1e6, parts))
        if messagebox.askyesno(TITLE, 'Disc image written:\n%s\n%.1f MB, %d partition(s).\n\nShow burning instructions?' % (path, size / 1e6, parts)):
            self.help_burn()

    def ask_mode(self):
        d = tk.Toplevel(self)
        d.title('Disc format')
        d.transient(self)
        d.grab_set()
        f = ttk.Frame(d, padding=12)
        f.pack()
        v = tk.StringVar(value='S1000')
        ttk.Radiobutton(f, text='S1000 format  (S1000 / S1100 / S2000 / S2800 / S3000 / CD3000 all read it)  - recommended',
                        variable=v, value='S1000').pack(anchor='w')
        ttk.Radiobutton(f, text='S3000 format  (S2000 and newer only, experimental: untested on hardware)',
                        variable=v, value='S3000').pack(anchor='w', pady=4)
        r = {}

        def ok():
            r['m'] = v.get()
            d.destroy()
        ttk.Button(f, text='Continue', command=ok).pack(anchor='e', pady=(10, 0))
        self.wait_window(d)
        return r.get('m')

    def open_image(self):
        path = filedialog.askopenfilename(title='Akai CD / disk image', filetypes=[
            ('Akai images', '*.iso *.img *.bin *.tao *.nrg'), ('All', '*.*')])
        if not path:
            return

        def work():
            img = akai.Image(path)
            return img, [(i, v.name, len(v.program_files), len(v.sample_files)) for i, v in enumerate(img.volumes)]

        def done(r):
            img, rows = r
            pick = self.pick_volumes(rows, os.path.basename(path))
            if not pick:
                img.close()
                return

            def load():
                out = []
                for i in pick:
                    out.append(img.volumes[i].load())
                img.close()
                return out
            self.run_bg(load, lambda vols: self._loaded(vols), 'Loading volumes (this decodes every sample)...')
        self.run_bg(work, done, 'Reading disc image...')

    def _loaded(self, vols):
        for vm in vols:
            names = {v.name for v in self.disc}
            vm.name = mapping.unique_name(vm.name, names)
            self.disc.append(vm)
        self.mark()
        self.status.set('Loaded %d volume(s).' % len(vols))

    def pick_volumes(self, rows, title):
        d = tk.Toplevel(self)
        d.title(title)
        d.transient(self)
        d.grab_set()
        f = ttk.Frame(d, padding=10)
        f.pack(fill='both', expand=True)
        ttk.Label(f, text='Pick volumes to load for editing / export:').pack(anchor='w')
        lb = tk.Listbox(f, selectmode='extended', width=60, height=16, exportselection=False)
        for i, nm, npg, ns in rows:
            lb.insert('end', '%-14s %3d programs  %4d samples' % (nm, npg, ns))
        lb.pack(fill='both', expand=True, pady=6)
        lb.select_set(0, 'end' if len(rows) <= 4 else 0)
        out = []

        def ok():
            out.extend(lb.curselection())
            d.destroy()
        ttk.Button(f, text='Load', command=ok).pack(side='right')
        ttk.Button(f, text='Cancel', command=d.destroy).pack(side='right', padx=6)
        self.wait_window(d)
        return out

    def save_project(self, as_new=False):
        path = self.project_path
        if as_new or not path:
            path = filedialog.asksaveasfilename(title='Save project', defaultextension='.akproj',
                                                filetypes=[('Akai tool project', '*.akproj')])
        if not path:
            return
        self.run_bg(lambda: project.save_project(path, self.disc), lambda _r: self._saved(path), 'Saving project...')

    def _saved(self, path):
        self.project_path, self.dirty = path, False
        self.refresh()
        self.status.set('Saved %s' % path)

    def open_project(self):
        path = filedialog.askopenfilename(title='Open project', filetypes=[('Akai tool project', '*.akproj')])
        if not path:
            return

        def done(vols):
            self.disc, self.project_path, self.dirty = vols, path, False
            self.refresh()
            self.on_select()
        self.run_bg(lambda: project.load_project(path), done, 'Opening project...')

    def export_selection(self, fmt):
        sel = self.selection()
        if not sel:
            messagebox.showinfo(TITLE, 'Select samples, programs or a volume in the tree first.')
            return
        out = filedialog.askdirectory(title='Export to folder')
        if not out:
            return

        def work():
            n = 0
            groups = {}
            for k, vm, o in sel:
                groups.setdefault(id(vm), (vm, [], []))
                if k == 'sample':
                    groups[id(vm)][1].append(o)
                elif k == 'program':
                    groups[id(vm)][2].append(o)
                elif k in ('volume', 'programs', 'samples'):
                    groups[id(vm)][1].extend(vm.samples.values())
                    groups[id(vm)][2].extend(vm.programs)
            for vm, smp, progs in groups.values():
                seen = []
                smp = [s for s in smp if not (id(s) in seen or seen.append(id(s)))]
                progs = list(dict.fromkeys(progs))
                d = os.path.join(out, export.safe_name(vm.name))
                os.makedirs(d, exist_ok=True)
                if fmt == 'wav':
                    for s in smp:
                        export.export_wav(s, os.path.join(d, export.safe_name(s.name) + '.wav'))
                        n += 1
                elif fmt == 'sfz':
                    for p in progs:
                        export.export_sfz(vm, p, d)
                        n += 1
                else:
                    if progs:
                        export.export_sf2(vm, progs, os.path.join(d, export.safe_name(vm.name) + '.sf2'))
                        n += len(progs)
            return n
        self.run_bg(work, lambda n: self.status.set('Exported %d item(s) to %s' % (n, out)), 'Exporting...')

    # ------------------------------------------------------------ help
    def help_burn(self):
        messagebox.showinfo('Burning the disc', (
            'The image is a raw Akai sampler disc (no ISO 9660 file system), like a ripped commercial sample CD.\n\n'
            'Burn it as an image, data CD, mode 1 / 2048 bytes per sector, disc-at-once, finalised:\n'
            '  - ImgBurn: Write image file to disc (choose the .iso)\n'
            '  - CDBurnerXP: Burn ISO image\n'
            '  - Linux: cdrecord -dao -data image.iso\n\n'
            'Do not drag the .iso into a normal "data disc" project: that would just store the file.\n\n'
            'Use a CD-R written at a low speed; old SCSI CD-ROM drives in samplers can be picky about media.\n\n'
            'If a sampler will not read it, try: another brand of CD-R, a lower burn speed, or a CD-RW.'))

    def about(self):
        messagebox.showinfo('About', '%s %s\n\nBuild Akai S1000 / S2000 / S3000 sampler CDs from audio files, '
                            'and read, edit and export existing ones (WAV, SFZ, SF2).\n\n'
                            'Made by JunkSmithWizard (JSW) together with Claude (Anthropic).\n'
                            'Format knowledge from reverse engineering real discs and the public akaitools notes.' % (TITLE, __version__))

    def on_close(self):
        if self.dirty and not messagebox.askyesno(TITLE, 'Unsaved changes will be lost. Exit anyway?'):
            return
        self.stop_play()
        self.destroy()


def main():
    App().mainloop()


if __name__ == '__main__':
    main()
