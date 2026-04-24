"""SpectrogramPanel: left arrows + header + matplotlib specgram + ruler.

Owns its own widget tree. Notifies the app via callbacks for seek,
replace, remove, and move up/down.
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import soundfile as sf
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from .palette import PALETTE
from .time_ruler import TimeRuler


SeekHandler = Callable[["SpectrogramPanel", float], None]
PanelCallback = Callable[["SpectrogramPanel"], None]


class SpectrogramPanel:
    def __init__(
        self,
        parent: tk.Widget,
        wav_path: Path,
        on_seek: SeekHandler,
        on_replace: Optional[PanelCallback] = None,
        on_remove: Optional[PanelCallback] = None,
        on_move_up: Optional[PanelCallback] = None,
        on_move_down: Optional[PanelCallback] = None,
        on_pin: Optional[PanelCallback] = None,
    ) -> None:
        self.wav_path: Path = wav_path
        self._on_seek: SeekHandler = on_seek
        self._on_replace: Optional[PanelCallback] = on_replace
        self._on_remove: Optional[PanelCallback] = on_remove
        self._on_move_up: Optional[PanelCallback] = on_move_up
        self._on_move_down: Optional[PanelCallback] = on_move_down
        self._on_pin: Optional[PanelCallback] = on_pin
        self.data, self.sample_rate = _load_mono(wav_path)
        self.duration: float = _compute_duration(self.data, self.sample_rate)

        self.frame: tk.Frame = _build_outer_frame(parent)

        # Left strip: ▲ ▼ 📌
        self._left_strip = tk.Frame(self.frame, bg=PALETTE["panel_bg"], width=36)
        self._left_strip.pack(side="left", fill="y", padx=(8, 0), pady=8)
        self._left_strip.pack_propagate(False)
        self._btn_up = _make_arrow_btn(
            self._left_strip, "▲", lambda: self._on_move_up and self._on_move_up(self)
        )
        self._btn_up.pack(side="top", fill="x", pady=(0, 6))
        self._btn_down = _make_arrow_btn(
            self._left_strip, "▼", lambda: self._on_move_down and self._on_move_down(self)
        )
        self._btn_down.pack(side="top", fill="x", pady=(0, 6))
        self._btn_pin = _make_arrow_btn(
            self._left_strip, "📌", lambda: self._on_pin and self._on_pin(self)
        )
        self._btn_pin.pack(side="top", fill="x")

        # Right content area
        right = tk.Frame(self.frame, bg=PALETTE["panel_bg"])
        right.pack(side="left", fill="both", expand=True)

        self.name_label, self.time_label = _build_header(right, wav_path.name)
        self.meta_label = _build_meta_line(right, self._format_meta())

        self.figure: Figure = Figure(figsize=(9, 2.2), dpi=100)
        self.figure.patch.set_facecolor(PALETTE["plot_bg"])
        self.ax = self.figure.add_subplot(111)
        self.playhead = None
        self._render_spectrogram()

        self.canvas: FigureCanvasTkAgg = FigureCanvasTkAgg(self.figure, master=right)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=(0, 8))
        self.canvas.mpl_connect("button_press_event", self._on_plot_click)
        self.canvas.get_tk_widget().bind("<Button-3>", self._show_context_menu)

        self.ruler: TimeRuler = TimeRuler(right, self.duration, on_seek=self._emit_seek)
        self.ruler.pack(fill="x", padx=(0, 8), pady=(2, 10))
        self.frame.after(50, self._align_ruler_with_plot)

        for widget in (self.frame, self._left_strip, self._btn_up, self._btn_down,
                       self._btn_pin, self.name_label, self.time_label, self.meta_label, right):
            widget.bind("<Button-3>", self._show_context_menu)

    def set_selected(self, selected: bool) -> None:
        color = PALETTE["selected"] if selected else PALETTE["panel_border"]
        self.frame.configure(highlightbackground=color, highlightcolor=color)

    def set_pinned(self, pinned: bool) -> None:
        """Hide ▲▼ and highlight 📌 when panel lives in the pinned area."""
        if pinned:
            self._btn_up.pack_forget()
            self._btn_down.pack_forget()
            self._btn_pin.configure(fg=PALETTE["selected"])
        else:
            self._btn_up.pack(side="top", fill="x", pady=(0, 6))
            self._btn_down.pack(side="top", fill="x", pady=(0, 6))
            self._btn_pin.configure(fg=PALETTE["text_dim"])

    def set_playhead(self, position_sec: float) -> None:
        if self.playhead is None:
            return
        safe = _clamp(position_sec, 0.0, self.duration)
        self.playhead.set_xdata([safe, safe])
        self.canvas.draw_idle()
        self.ruler.set_playhead(safe)
        self.time_label.configure(text=_format_time(safe))

    def reload(self, new_path: Path) -> None:
        self.wav_path = new_path
        self.data, self.sample_rate = _load_mono(new_path)
        self.duration = _compute_duration(self.data, self.sample_rate)
        self.name_label.configure(text=new_path.name)
        self.time_label.configure(text=_format_time(0.0))
        self.meta_label.configure(text=self._format_meta())
        self.playhead = None
        self._render_spectrogram()
        self.canvas.draw()
        self.ruler.update_duration(self.duration)
        self.frame.after(50, self._align_ruler_with_plot)

    def destroy(self) -> None:
        self.canvas.get_tk_widget().destroy()
        self.figure.clear()
        self.ruler.destroy()
        self.frame.destroy()

    def request_ruler_realign(self) -> None:
        self.frame.after(20, self._align_ruler_with_plot)

    def _render_spectrogram(self) -> None:
        self.ax.clear()
        self.ax.set_facecolor(PALETTE["plot_bg"])
        self.ax.specgram(
            self.data, NFFT=1024, Fs=self.sample_rate, noverlap=512,
            scale="dB", cmap="magma", vmin=-100, vmax=-20,
        )
        self.ax.set_ylim(0, min(8000, self.sample_rate / 2))
        self.ax.set_ylabel("Hz", color=PALETTE["text_dim"], fontsize=8)
        self.ax.set_xticks([])
        self.ax.tick_params(colors=PALETTE["text_dim"], labelsize=8)
        for spine in self.ax.spines.values():
            spine.set_color(PALETTE["axis"])
        self.ax.margins(x=0)
        self.ax.set_xlim(0, self.duration)
        self.ax.set_title(
            self.wav_path.name,
            color=PALETTE["text"], fontsize=8, pad=4, loc="left",
        )
        self.playhead = self.ax.axvline(
            0.0, color=PALETTE["playhead"], linewidth=1.5, alpha=0.95
        )
        self.figure.subplots_adjust(left=0.05, right=0.995, top=0.88, bottom=0.05)

    def _align_ruler_with_plot(self) -> None:
        widget_width = self.canvas.get_tk_widget().winfo_width()
        if widget_width <= 1:
            self.frame.after(80, self._align_ruler_with_plot)
            return
        bbox = self.ax.get_position()
        left = int(bbox.x0 * widget_width)
        right = int((1 - bbox.x1) * widget_width) + 8
        self.ruler.set_padding(left, right)

    def _show_context_menu(self, event: tk.Event) -> None:
        menu = tk.Menu(
            self.frame, tearoff=0,
            bg=PALETTE["panel_bg"], fg=PALETTE["text"],
            activebackground=PALETTE["button_active"],
            activeforeground=PALETTE["selected"],
            bd=0,
        )
        if self._on_replace:
            menu.add_command(label="Replace…", command=lambda: self._on_replace(self))  # type: ignore[misc]
            menu.add_separator()
        if self._on_remove:
            menu.add_command(label="Remove", command=lambda: self._on_remove(self))  # type: ignore[misc]
        if menu.index("end") is None:
            return
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _on_plot_click(self, event: object) -> None:
        if getattr(event, "button", 1) != 1:
            return
        if getattr(event, "inaxes", None) is not self.ax:
            return
        position_sec = float(getattr(event, "xdata", 0.0) or 0.0)
        self._emit_seek(position_sec)

    def _emit_seek(self, position_sec: float) -> None:
        self._on_seek(self, position_sec)

    def _format_meta(self) -> str:
        peak = float(np.max(np.abs(self.data))) if len(self.data) else 0.0
        rms = float(np.sqrt(np.mean(self.data ** 2))) if len(self.data) else 0.0
        return (
            f"{self.sample_rate} Hz   {self.duration:.2f} s   "
            f"peak {peak:.3f}   rms {rms:.3f}"
        )


def _make_arrow_btn(parent: tk.Widget, text: str, command: Callable) -> tk.Button:
    return tk.Button(
        parent, text=text,
        bg=PALETTE["button_bg"], fg=PALETTE["text_dim"],
        activebackground=PALETTE["button_active"], activeforeground=PALETTE["selected"],
        relief="flat", bd=0, cursor="arrow",
        font=("TkDefaultFont", 11),
        command=command,
    )


def _build_outer_frame(parent: tk.Widget) -> tk.Frame:
    frame = tk.Frame(
        parent,
        bg=PALETTE["panel_bg"],
        highlightthickness=1,
        highlightbackground=PALETTE["panel_border"],
        highlightcolor=PALETTE["panel_border"],
    )
    frame.pack(fill="x", padx=12, pady=6)
    return frame


def _build_header(parent: tk.Frame, filename: str) -> tuple[tk.Label, tk.Label]:
    header = tk.Frame(parent, bg=PALETTE["panel_bg"])
    header.pack(fill="x", padx=(8, 12), pady=(10, 4))
    name = tk.Label(
        header, text=filename, bg=PALETTE["panel_bg"], fg=PALETTE["text"],
        font=("TkDefaultFont", 10, "bold"), anchor="w",
    )
    name.pack(side="left")
    time_label = tk.Label(
        header, text=_format_time(0.0), bg=PALETTE["panel_bg"],
        fg=PALETTE["selected"], font=("TkFixedFont", 10, "bold"), anchor="e",
    )
    time_label.pack(side="right")
    return name, time_label


def _build_meta_line(parent: tk.Frame, text: str) -> tk.Label:
    label = tk.Label(
        parent, text=text, bg=PALETTE["panel_bg"], fg=PALETTE["text_dim"],
        font=("TkDefaultFont", 8), anchor="w",
    )
    label.pack(fill="x", padx=(8, 12), pady=(0, 6))
    return label


def _load_mono(wav_path: Path) -> tuple[np.ndarray, int]:
    data, sample_rate = sf.read(str(wav_path), dtype="float32", always_2d=False)
    mono = data if data.ndim == 1 else data.mean(axis=1)
    return mono, sample_rate


def _compute_duration(data: np.ndarray, sample_rate: int) -> float:
    if sample_rate <= 0:
        raise ValueError(
            f"panel: sample_rate must be > 0, got {sample_rate!r} "
            f"(expected positive int from soundfile.read)"
        )
    dur = len(data) / float(sample_rate)
    return max(dur, 0.01)


def _format_time(pos: float) -> str:
    return f"{pos:6.2f} s"


def _clamp(value: float, lo: float, hi: float) -> float:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value
