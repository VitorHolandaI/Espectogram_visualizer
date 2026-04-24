"""Top-level Tk app: toolbar, scrollable panel list, playback wiring."""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .audio_player import AudioPlayer
from .palette import PALETTE
from .panel import SpectrogramPanel


class SpectrogramComparatorApp:
    def __init__(self, root: tk.Tk, initial_files: list[Path]) -> None:
        self.root: tk.Tk = root
        self.root.title("Spectrogram Comparator")
        self.root.geometry("1280x860")
        self.root.configure(bg=PALETTE["bg"])

        _setup_ttk_style()

        self.panels: list[SpectrogramPanel] = []
        self.pinned_panel: SpectrogramPanel | None = None
        self.selected_panel: SpectrogramPanel | None = None
        self.playback_panel: SpectrogramPanel | None = None
        self.player: AudioPlayer = AudioPlayer()

        self._build_layout()
        if initial_files:
            self.add_files(initial_files)

        self.root.bind("<space>", self._handle_space)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def add_files_dialog(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Choose WAV files — Ctrl+click or Shift+click to select multiple",
            filetypes=[("WAV files", "*.wav"), ("All files", "*.*")],
        )
        self.add_files([Path(p) for p in paths])

    def add_files(self, paths: list[Path]) -> None:
        for path in paths:
            self._add_single_file(path)

    def clear_panels(self) -> None:
        self.stop_audio()
        if self.pinned_panel:
            self.pinned_panel.destroy()
            self.pinned_panel = None
            self._hide_pinned_area()
        for panel in self.panels:
            panel.destroy()
        self.panels.clear()
        self.selected_panel = None
        self.playback_panel = None
        self._sink_add_row()

    def stop_audio(self) -> None:
        self.player.stop()

    def seek_and_play(self, panel: SpectrogramPanel, position_sec: float) -> None:
        self._select(panel)
        frame = max(0, min(int(position_sec * panel.sample_rate), len(panel.data)))
        if self.player.playing and self.playback_panel is panel:
            self._start_playback(panel, frame)
            return
        if self.player.playing:
            self.stop_audio()
        self.playback_panel = panel
        self.player.set_frame(frame)
        panel.set_playhead(frame / panel.sample_rate)

    def toggle_play_pause(self) -> None:
        panel = self.selected_panel or self.playback_panel
        if panel is None:
            return
        if self.player.playing:
            self.stop_audio()
            return
        start = self.player.current_frame if self.playback_panel is panel else 0
        if start >= len(panel.data):
            start = 0
        self._start_playback(panel, start)

    def on_close(self) -> None:
        self.stop_audio()
        self.scroll_canvas.unbind_all("<MouseWheel>")
        self.root.destroy()

    # ── Panel management ─────────────────────────────────────────────

    def _add_single_file(self, path: Path) -> None:
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            messagebox.showerror("Missing file", f"File not found:\n{resolved}")
            return
        if any(panel.wav_path == resolved for panel in self.panels):
            return
        try:
            panel = SpectrogramPanel(
                self.inner, resolved,
                on_seek=self.seek_and_play,
                on_replace=self._replace_panel,
                on_remove=self._remove_panel,
                on_move_up=self._move_panel_up,
                on_move_down=self._move_panel_down,
                on_pin=self._pin_panel,
            )
        except Exception as exc:
            messagebox.showerror("Load failed", f"Could not open {resolved.name}:\n{exc}")
            return
        self.panels.append(panel)
        self._sink_add_row()

    def _replace_panel(self, panel: SpectrogramPanel) -> None:
        paths = filedialog.askopenfilenames(
            title="Replace with WAV",
            filetypes=[("WAV files", "*.wav"), ("All files", "*.*")],
        )
        if not paths:
            return
        new_path = Path(paths[0]).expanduser().resolve()
        if not new_path.is_file():
            messagebox.showerror("Missing file", f"File not found:\n{new_path}")
            return
        if self.playback_panel is panel:
            self.stop_audio()
            self.playback_panel = None
        try:
            panel.reload(new_path)
        except Exception as exc:
            messagebox.showerror("Load failed", f"Could not open {new_path.name}:\n{exc}")

    def _remove_panel(self, panel: SpectrogramPanel) -> None:
        if self.playback_panel is panel:
            self.stop_audio()
            self.playback_panel = None
        if self.selected_panel is panel:
            self.selected_panel = None
        self.panels.remove(panel)
        panel.destroy()

    def _pin_panel(self, panel: SpectrogramPanel) -> None:
        # If this panel is already pinned, unpin it.
        if self.pinned_panel is not None and self.pinned_panel.wav_path == panel.wav_path:
            self._unpin_panel()
            return
        # Unpin any existing pinned panel first.
        if self.pinned_panel is not None:
            self._unpin_panel()
        wav_path = panel.wav_path
        if self.playback_panel is panel:
            self.stop_audio()
            self.playback_panel = None
        if self.selected_panel is panel:
            self.selected_panel = None
        self.panels.remove(panel)
        panel.destroy()
        self._repack_panels()
        self._show_pinned_area()
        pinned = SpectrogramPanel(
            self.pinned_container, wav_path,
            on_seek=self.seek_and_play,
            on_replace=self._replace_panel,
            on_remove=self._remove_panel,
            on_pin=self._pin_panel,
        )
        pinned.set_pinned(True)
        self.pinned_panel = pinned

    def _unpin_panel(self) -> None:
        if self.pinned_panel is None:
            return
        wav_path = self.pinned_panel.wav_path
        if self.playback_panel is self.pinned_panel:
            self.stop_audio()
            self.playback_panel = None
        self.pinned_panel.destroy()
        self.pinned_panel = None
        self._hide_pinned_area()
        try:
            panel = SpectrogramPanel(
                self.inner, wav_path,
                on_seek=self.seek_and_play,
                on_replace=self._replace_panel,
                on_remove=self._remove_panel,
                on_move_up=self._move_panel_up,
                on_move_down=self._move_panel_down,
                on_pin=self._pin_panel,
            )
        except Exception as exc:
            from tkinter import messagebox
            messagebox.showerror("Load failed", f"Could not reload {wav_path.name}:\n{exc}")
            return
        self.panels.insert(0, panel)
        self._repack_panels()

    def _show_pinned_area(self) -> None:
        self.pinned_area.pack(fill="x", padx=10, pady=(0, 4), before=self.scroll_outer)

    def _hide_pinned_area(self) -> None:
        self.pinned_area.pack_forget()

    def _move_panel_up(self, panel: SpectrogramPanel) -> None:
        idx = self.panels.index(panel)
        if idx == 0:
            return
        self.panels[idx], self.panels[idx - 1] = self.panels[idx - 1], self.panels[idx]
        self._repack_panels()

    def _move_panel_down(self, panel: SpectrogramPanel) -> None:
        idx = self.panels.index(panel)
        if idx == len(self.panels) - 1:
            return
        self.panels[idx], self.panels[idx + 1] = self.panels[idx + 1], self.panels[idx]
        self._repack_panels()

    def _repack_panels(self) -> None:
        for panel in self.panels:
            panel.frame.pack_forget()
        for panel in self.panels:
            panel.frame.pack(fill="x", padx=12, pady=6)
        self._sink_add_row()

    def _sink_add_row(self) -> None:
        """Keep the + add row always below all panels."""
        self.add_row.pack_forget()
        self.add_row.pack(fill="x", padx=12, pady=(4, 14))

    # ── Playback ─────────────────────────────────────────────────────

    def _select(self, panel: SpectrogramPanel) -> None:
        for item in self.panels:
            item.set_selected(item is panel)
        self.selected_panel = panel

    def _start_playback(self, panel: SpectrogramPanel, start_frame: int) -> None:
        self.playback_panel = panel
        panel.set_playhead(start_frame / panel.sample_rate)
        self.player.start(
            data=panel.data,
            sample_rate=panel.sample_rate,
            start_frame=start_frame,
            on_tick=lambda _frame: None,
            on_finish=self._on_playback_finished,
        )
        self._schedule_playhead_update()

    def _schedule_playhead_update(self) -> None:
        panel = self.playback_panel
        if panel is not None and panel.sample_rate > 0:
            panel.set_playhead(self.player.current_frame / panel.sample_rate)
        if self.player.playing:
            self.root.after(40, self._schedule_playhead_update)

    def _on_playback_finished(self) -> None:
        self.root.after(0, self._finalize_playback_ui)

    def _finalize_playback_ui(self) -> None:
        panel = self.playback_panel
        if panel is not None and panel.sample_rate > 0:
            end = min(self.player.current_frame / panel.sample_rate, panel.duration)
            panel.set_playhead(end)

    # ── Layout ───────────────────────────────────────────────────────

    def _build_layout(self) -> None:
        self._build_toolbar()
        self._build_hint()
        self._build_pinned_area()
        self._build_scroll_area()

    def _build_toolbar(self) -> None:
        toolbar = tk.Frame(self.root, bg=PALETTE["bg"])
        toolbar.pack(fill="x", padx=18, pady=(16, 6))
        tk.Label(
            toolbar, text="Spectrogram Comparator",
            bg=PALETTE["bg"], fg=PALETTE["text"],
            font=("TkDefaultFont", 14, "bold"),
        ).pack(side="left")
        for text, cmd in (
            ("Clear", self.clear_panels),
            ("Stop", self.stop_audio),
        ):
            ttk.Button(toolbar, text=text, command=cmd, style="Modern.TButton").pack(
                side="right", padx=(8, 0)
            )

    def _build_hint(self) -> None:
        tk.Label(
            self.root,
            text="Click spectrogram or ruler to seek. ▲▼ to reorder. Right-click to replace/remove. Space = play/pause.",
            bg=PALETTE["bg"], fg=PALETTE["text_dim"],
            anchor="w", font=("TkDefaultFont", 9),
        ).pack(fill="x", padx=18, pady=(0, 10))

    def _build_pinned_area(self) -> None:
        self.pinned_area = tk.Frame(self.root, bg=PALETTE["bg"])
        # starts hidden — shown when a panel is pinned
        header = tk.Frame(self.pinned_area, bg=PALETTE["bg"])
        header.pack(fill="x", padx=18, pady=(0, 2))
        tk.Label(
            header, text="📌  Fixado",
            bg=PALETTE["bg"], fg=PALETTE["selected"],
            font=("TkDefaultFont", 9, "bold"), anchor="w",
        ).pack(side="left")
        ttk.Button(
            header, text="Desafixar", command=self._unpin_panel,
            style="Modern.TButton",
        ).pack(side="right")
        self.pinned_container = tk.Frame(self.pinned_area, bg=PALETTE["bg"])
        self.pinned_container.pack(fill="x")

    def _build_scroll_area(self) -> None:
        self.scroll_outer = tk.Frame(self.root, bg=PALETTE["bg"])
        outer = self.scroll_outer
        outer.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.scroll_canvas = tk.Canvas(outer, bg=PALETTE["bg"], highlightthickness=0)
        self.scroll_canvas.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(
            outer, orient="vertical", command=self.scroll_canvas.yview,
            style="Modern.Vertical.TScrollbar",
        )
        scrollbar.pack(side="right", fill="y")
        self.scroll_canvas.configure(yscrollcommand=scrollbar.set)
        self.inner = tk.Frame(self.scroll_canvas, bg=PALETTE["bg"])
        self.window_id = self.scroll_canvas.create_window(
            (0, 0), window=self.inner, anchor="nw"
        )
        self.inner.bind("<Configure>", self._sync_scroll_region)
        self.scroll_canvas.bind("<Configure>", self._on_canvas_resize)
        self.scroll_canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        self.add_row = self._build_add_row()
        self.add_row.pack(fill="x", padx=12, pady=(4, 14))

    def _build_add_row(self) -> tk.Frame:
        row = tk.Frame(self.inner, bg=PALETTE["bg"])
        btn = tk.Button(
            row, text="＋  Add WAVs",
            bg=PALETTE["button_bg"], fg=PALETTE["text"],
            activebackground=PALETTE["button_active"], activeforeground=PALETTE["selected"],
            relief="flat", bd=0, padx=20, pady=8,
            font=("TkDefaultFont", 10, "bold"),
            command=self.add_files_dialog,
        )
        btn.pack(side="left")
        return row

    def _sync_scroll_region(self, _event: tk.Event) -> None:
        self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox("all"))

    def _on_canvas_resize(self, event: tk.Event) -> None:
        self.scroll_canvas.itemconfigure(self.window_id, width=event.width)
        for panel in self.panels:
            panel.request_ruler_realign()

    def _on_mousewheel(self, event: tk.Event) -> None:
        if event.delta:
            self.scroll_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _handle_space(self, _event: tk.Event) -> str:
        widget = self.root.focus_get()
        if isinstance(widget, (tk.Entry, tk.Text)):
            return "break"
        self.toggle_play_pause()
        return "break"


def _setup_ttk_style() -> None:
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(
        "Modern.TButton",
        background=PALETTE["button_bg"], foreground=PALETTE["text"],
        borderwidth=0, focusthickness=0, padding=(14, 6),
        font=("TkDefaultFont", 9, "bold"),
    )
    style.map(
        "Modern.TButton",
        background=[("active", PALETTE["button_active"])],
        foreground=[("active", PALETTE["selected"])],
    )
    style.configure(
        "Modern.Vertical.TScrollbar",
        background=PALETTE["panel_bg"], troughcolor=PALETTE["bg"],
        bordercolor=PALETTE["bg"], arrowcolor=PALETTE["text_dim"],
    )
