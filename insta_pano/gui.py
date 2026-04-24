"""Tkinter GUI for insta_pano — live preview + carousel generation."""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from insta_pano.config import (
    DEFAULT_FORMAT,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_QUALITY,
    DEFAULT_SLIDES,
    FORMATS,
    MAX_SLIDES,
    MIN_SLIDES,
)
from insta_pano.processor import (
    add_debug_overlay,
    load_image,
    process_image,
    slice_canvas,
)
from insta_pano.photo_picker import (
    LocalFolderPicker,
    PhotosAppImporter,
    apple_photos_available,
)
from insta_pano.utils import (
    ensure_output_dir,
    suggest_slide_count,
    validate_quality,
    validate_slide_count,
)

SIDEBAR_W = 300
TILE_H = 100
PREVIEW_BG = "#111111"
TILE_BG = "#1a1a1a"
_ZOOM_MIN = 1.0
_ZOOM_MAX = 3.0


def _fit(img: Image.Image, max_w: int, max_h: int, fast: bool = False) -> Image.Image:
    scale = min(max_w / img.width, max_h / img.height, 1.0)
    resample = Image.BILINEAR if fast else Image.LANCZOS
    return img.resize(
        (max(1, int(img.width * scale)), max(1, int(img.height * scale))), resample
    )


def _cover_resize(img: Image.Image, canvas_w: int, canvas_h: int, zoom: float) -> Image.Image:
    scale = max(canvas_w / img.width, canvas_h / img.height) * max(1.0, zoom)
    return img.resize(
        (max(canvas_w, round(img.width * scale)), max(canvas_h, round(img.height * scale))),
        Image.LANCZOS,
    )


def _crop_from_resized(
    resized: Image.Image, canvas_w: int, canvas_h: int, crop_x: float, crop_y: float
) -> Image.Image:
    ex = resized.width - canvas_w
    ey = resized.height - canvas_h
    left = round(ex * max(0.0, min(1.0, crop_x)))
    top = round(ey * max(0.0, min(1.0, crop_y)))
    return resized.crop((left, top, left + canvas_w, top + canvas_h))


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("panoramaSlice — Carousel Generator")
        self.geometry("1160x820")
        self.minsize(900, 660)

        self._source: Optional[Image.Image] = None
        self._display_img: Optional[Image.Image] = None
        self._preview_job: Optional[str] = None
        self._tile_job: Optional[str] = None
        self._preview_lock = threading.Lock()
        self._tk_preview: Optional[ImageTk.PhotoImage] = None
        self._tk_tiles: List[ImageTk.PhotoImage] = []

        # Cached LANCZOS-resized image (pre-crop) — reused for instant crop updates
        self._resized_full: Optional[Image.Image] = None
        self._resized_key: Tuple = ()   # (zoom_round4, slides, fmt)

        self._crop_excess_x: int = 0
        self._crop_excess_y: int = 0
        self._preview_scale: float = 1.0
        self._drag_last_x: Optional[int] = None
        self._drag_last_y: Optional[int] = None
        self._suppress_fast_preview: bool = False

        self._ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()

        self._build_ui()
        self._setup_keyboard_shortcuts()
        self._poll_ui_queue()

    # ── queue drain ───────────────────────────────────────────────────────────

    def _poll_ui_queue(self) -> None:
        try:
            while True:
                self._ui_queue.get_nowait()()
        except queue.Empty:
            pass
        self.after(50, self._poll_ui_queue)

    def _ui(self, fn: Callable[[], None]) -> None:
        self._ui_queue.put(fn)

    # ── layout ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._status_var = tk.StringVar(value="Ready — open an image to begin.")
        tk.Label(self, textvariable=self._status_var, anchor="w",
                 relief="sunken", padx=8, pady=3,
                 font=("", 10)).pack(side=tk.BOTTOM, fill=tk.X)

        self._sidebar = tk.Frame(self, width=SIDEBAR_W, relief="flat", bd=0)
        self._sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self._sidebar.pack_propagate(False)
        ttk.Separator(self, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y)

        right = tk.Frame(self)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._build_sidebar()
        self._build_preview(right)

    # ── sidebar ───────────────────────────────────────────────────────────────

    def _build_sidebar(self) -> None:
        f = self._sidebar
        FULL = dict(padx=10, pady=2, fill=tk.X)

        self._section(f, "INPUT IMAGE")
        src_row = tk.Frame(f)
        src_row.pack(**FULL)
        ttk.Button(src_row, text="File…",
                   command=self._browse_input).pack(side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Button(src_row, text="Folder…",
                   command=self._open_folder_picker).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0))
        if apple_photos_available():
            ttk.Button(src_row, text="Apple Photos",
                       command=self._open_apple_photos).pack(
                side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0))

        self._input_var = tk.StringVar()
        self._input_var.trace_add("write", lambda *_: self._on_input_changed())
        tk.Entry(f, textvariable=self._input_var, state="readonly").pack(**FULL)
        self._img_info_var = tk.StringVar()
        tk.Label(f, textvariable=self._img_info_var,
                 fg="gray", font=("", 9), anchor="w").pack(padx=10, fill=tk.X)

        self._section(f, "OUTPUT")
        self._output_var = tk.StringVar(value=DEFAULT_OUTPUT_DIR)
        row2 = tk.Frame(f)
        row2.pack(**FULL)
        tk.Entry(row2, textvariable=self._output_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row2, text="Browse",
                   command=self._browse_output).pack(side=tk.RIGHT, padx=(4, 0))
        tk.Label(f, text="Filename prefix (optional)", anchor="w").pack(padx=10, anchor="w")
        self._prefix_var = tk.StringVar()
        tk.Entry(f, textvariable=self._prefix_var).pack(**FULL)

        self._section(f, "SLIDES")
        slides_row = tk.Frame(f)
        slides_row.pack(**FULL)
        tk.Label(slides_row, text="Count").pack(side=tk.LEFT)
        self._slides_var = tk.IntVar(value=DEFAULT_SLIDES)
        self._slides_var.trace_add("write", lambda *_: self._schedule_preview())
        ttk.Spinbox(slides_row, from_=MIN_SLIDES, to=MAX_SLIDES,
                    width=5, textvariable=self._slides_var).pack(side=tk.RIGHT)
        ttk.Button(f, text="Auto-suggest from image ratio",
                   command=self._suggest_slides).pack(**FULL)
        tk.Label(f, text="Format", anchor="w").pack(padx=10, anchor="w")
        self._format_var = tk.StringVar(value=DEFAULT_FORMAT)
        self._format_var.trace_add("write", lambda *_: self._schedule_preview())
        ttk.Combobox(f, textvariable=self._format_var,
                     values=list(FORMATS.keys()), state="readonly").pack(**FULL)

        self._section(f, "CROP & ZOOM")
        tk.Label(f, text="Drag preview to pan  •  ← → ↑ ↓ to nudge",
                 fg="gray", font=("", 8), anchor="w").pack(padx=10, fill=tk.X)

        hrow = tk.Frame(f)
        hrow.pack(**FULL)
        tk.Label(hrow, text="Horizontal").pack(side=tk.LEFT)
        self._crop_x_lbl = tk.Label(hrow, text="50%", font=("", 9, "bold"))
        self._crop_x_lbl.pack(side=tk.RIGHT)
        self._crop_x_var = tk.DoubleVar(value=0.5)
        self._crop_x_var.trace_add("write", lambda *_: self._on_crop_x_changed())
        ttk.Scale(f, from_=0.0, to=1.0, variable=self._crop_x_var,
                  orient=tk.HORIZONTAL).pack(**FULL)

        vrow = tk.Frame(f)
        vrow.pack(**FULL)
        tk.Label(vrow, text="Vertical").pack(side=tk.LEFT)
        self._crop_y_lbl = tk.Label(vrow, text="50%", font=("", 9, "bold"))
        self._crop_y_lbl.pack(side=tk.RIGHT)
        self._crop_y_var = tk.DoubleVar(value=0.5)
        self._crop_y_var.trace_add("write", lambda *_: self._on_crop_y_changed())
        ttk.Scale(f, from_=0.0, to=1.0, variable=self._crop_y_var,
                  orient=tk.HORIZONTAL).pack(**FULL)

        zrow = tk.Frame(f)
        zrow.pack(**FULL)
        tk.Label(zrow, text="Zoom").pack(side=tk.LEFT)
        self._zoom_lbl = tk.Label(zrow, text="1.00×", font=("", 9, "bold"))
        self._zoom_lbl.pack(side=tk.RIGHT)
        self._zoom_var = tk.DoubleVar(value=1.0)
        self._zoom_var.trace_add("write", lambda *_: self._on_zoom_changed())
        ttk.Scale(f, from_=_ZOOM_MIN, to=_ZOOM_MAX, variable=self._zoom_var,
                  orient=tk.HORIZONTAL).pack(**FULL)

        ttk.Button(f, text="Reset Crop & Zoom",
                   command=self._reset_crop).pack(**FULL)

        self._section(f, "EXPORT")
        qrow = tk.Frame(f)
        qrow.pack(**FULL)
        tk.Label(qrow, text="JPEG Quality").pack(side=tk.LEFT)
        self._quality_lbl = tk.Label(qrow, text=str(DEFAULT_QUALITY),
                                     font=("", 10, "bold"))
        self._quality_lbl.pack(side=tk.RIGHT)
        self._quality_var = tk.IntVar(value=DEFAULT_QUALITY)
        self._quality_var.trace_add(
            "write",
            lambda *_: self._quality_lbl.config(text=str(self._quality_var.get())),
        )
        ttk.Scale(f, from_=1, to=100, variable=self._quality_var,
                  orient=tk.HORIZONTAL).pack(**FULL)
        self._png_var = tk.BooleanVar()
        tk.Checkbutton(f, text="Export as PNG (lossless)",
                       variable=self._png_var, anchor="w").pack(padx=10, fill=tk.X)
        self._debug_var = tk.BooleanVar()
        tk.Checkbutton(f, text="Burn slice markers into exported tiles",
                       variable=self._debug_var, anchor="w").pack(padx=10, fill=tk.X)
        self._dry_run_var = tk.BooleanVar()
        tk.Checkbutton(f, text="Dry run (preview only, no files written)",
                       variable=self._dry_run_var, anchor="w").pack(padx=10, fill=tk.X)

        ttk.Separator(f, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=10, pady=10)
        ttk.Button(f, text="Refresh Preview",
                   command=self._schedule_preview).pack(**FULL)
        self._gen_btn = ttk.Button(f, text="Generate Carousel  (⌘G)",
                                   command=self._generate)
        self._gen_btn.pack(**FULL)
        self._progress = ttk.Progressbar(f, mode="indeterminate", length=200)
        self._progress.pack(**FULL)
        ttk.Button(f, text="Open Output Folder",
                   command=self._open_output).pack(**FULL)

    def _section(self, parent: tk.Frame, title: str) -> None:
        tk.Label(parent, text=title, fg="gray",
                 font=("", 8, "bold"), anchor="w").pack(
            padx=10, pady=(8, 2), fill=tk.X)

    # ── preview canvas ────────────────────────────────────────────────────────

    def _build_preview(self, parent: tk.Frame) -> None:
        self._pv_canvas = tk.Canvas(parent, bg=PREVIEW_BG, highlightthickness=0)
        self._pv_canvas.pack(fill=tk.BOTH, expand=True)
        self._pv_canvas.bind("<Configure>", lambda _: self._rescale_preview())
        self._pv_canvas.bind("<Button-1>", self._on_drag_start)
        self._pv_canvas.bind("<B1-Motion>", self._on_drag_motion)
        self._pv_canvas.bind("<ButtonRelease-1>", self._on_drag_end)
        self._pv_canvas.create_text(
            400, 200, text="Open an image to see the preview",
            fill="#3a3a3a", font=("", 14),
        )
        strip = tk.Frame(parent, bg=TILE_BG, height=TILE_H + 24)
        strip.pack(fill=tk.X)
        strip.pack_propagate(False)
        ttk.Separator(strip, orient=tk.HORIZONTAL).pack(fill=tk.X)
        self._tiles_row = tk.Frame(strip, bg=TILE_BG)
        self._tiles_row.pack(expand=True)

    # ── drag-to-pan ───────────────────────────────────────────────────────────

    def _on_drag_start(self, event: tk.Event) -> None:
        if self._source is None:
            return
        self._drag_last_x = event.x
        self._drag_last_y = event.y
        self._pv_canvas.config(cursor="fleur")

    def _on_drag_motion(self, event: tk.Event) -> None:
        if self._drag_last_x is None or self._source is None:
            return
        dx = event.x - self._drag_last_x
        dy = event.y - self._drag_last_y
        self._drag_last_x = event.x
        self._drag_last_y = event.y

        scale = self._preview_scale or 1.0
        updated = False
        self._suppress_fast_preview = True
        try:
            if self._crop_excess_x > 0:
                delta = -(dx / scale) / self._crop_excess_x
                self._crop_x_var.set(max(0.0, min(1.0, self._crop_x_var.get() + delta)))
                updated = True
            if self._crop_excess_y > 0:
                delta = -(dy / scale) / self._crop_excess_y
                self._crop_y_var.set(max(0.0, min(1.0, self._crop_y_var.get() + delta)))
                updated = True
        finally:
            self._suppress_fast_preview = False

        if updated:
            self._fast_crop_preview()
            self._schedule_tile_update()

    def _on_drag_end(self, event: tk.Event) -> None:
        self._drag_last_x = None
        self._drag_last_y = None
        self._pv_canvas.config(cursor="fleur" if self._source else "")

    # ── fast crop preview (synchronous, main thread) ──────────────────────────

    def _resized_full_valid(self) -> bool:
        if self._resized_full is None:
            return False
        slides = int(self._slides_var.get())
        fmt = self._format_var.get()
        zoom = self._zoom_var.get()
        slide_w, slide_h = FORMATS[fmt]
        return (
            self._resized_key == (round(zoom, 4), slides, fmt)
            and self._resized_full.width >= slides * slide_w
            and self._resized_full.height >= slide_h
        )

    def _fast_crop_preview(self) -> None:
        """Re-crop the cached resized image — runs on main thread, no lag."""
        if not self._resized_full_valid():
            return
        slides = int(self._slides_var.get())
        slide_w, slide_h = FORMATS[self._format_var.get()]
        canvas_w = slides * slide_w
        crop_x = self._crop_x_var.get()
        crop_y = self._crop_y_var.get()

        canvas_img = _crop_from_resized(
            self._resized_full, canvas_w, slide_h, crop_x, crop_y
        )
        self._crop_excess_x = self._resized_full.width - canvas_w
        self._crop_excess_y = self._resized_full.height - slide_h
        display_img = add_debug_overlay(canvas_img, slides, slide_w)
        self._display_img = display_img
        self._render_panorama(display_img, fast=True)

    def _schedule_tile_update(self) -> None:
        if self._tile_job:
            self.after_cancel(self._tile_job)
        self._tile_job = self.after(150, self._update_tiles_from_cache)

    def _update_tiles_from_cache(self) -> None:
        self._tile_job = None
        if not self._resized_full_valid():
            return
        slides = int(self._slides_var.get())
        slide_w, slide_h = FORMATS[self._format_var.get()]
        canvas_img = _crop_from_resized(
            self._resized_full, slides * slide_w, slide_h,
            self._crop_x_var.get(), self._crop_y_var.get(),
        )
        self._render_tile_strip(slice_canvas(canvas_img, slides, slide_w))

    # ── keyboard shortcuts ────────────────────────────────────────────────────

    def _setup_keyboard_shortcuts(self) -> None:
        self.bind("<KeyPress>", self._on_key)
        self.bind("<Command-g>", lambda _: self._generate())
        self.bind("<Control-g>", lambda _: self._generate())

    def _on_key(self, event: tk.Event) -> None:
        if isinstance(self.focus_get(), (tk.Entry, ttk.Entry, ttk.Spinbox, ttk.Combobox)):
            return
        k = event.keysym
        if k == "Left":
            self._nudge_crop(-0.05, "x")
        elif k == "Right":
            self._nudge_crop(0.05, "x")
        elif k == "Up":
            self._nudge_crop(-0.05, "y")
        elif k == "Down":
            self._nudge_crop(0.05, "y")
        elif k in ("plus", "equal"):
            self._nudge_slides(1)
        elif k == "minus":
            self._nudge_slides(-1)

    def _nudge_crop(self, delta: float, axis: str) -> None:
        if axis == "x":
            self._crop_x_var.set(max(0.0, min(1.0, self._crop_x_var.get() + delta)))
        else:
            self._crop_y_var.set(max(0.0, min(1.0, self._crop_y_var.get() + delta)))

    def _nudge_slides(self, delta: int) -> None:
        self._slides_var.set(max(MIN_SLIDES, min(MAX_SLIDES, self._slides_var.get() + delta)))

    # ── crop / zoom traces ────────────────────────────────────────────────────

    def _on_crop_x_changed(self) -> None:
        self._crop_x_lbl.config(text=f"{int(round(self._crop_x_var.get() * 100))}%")
        if not self._suppress_fast_preview:
            self._fast_crop_preview()
            self._schedule_tile_update()

    def _on_crop_y_changed(self) -> None:
        self._crop_y_lbl.config(text=f"{int(round(self._crop_y_var.get() * 100))}%")
        if not self._suppress_fast_preview:
            self._fast_crop_preview()
            self._schedule_tile_update()

    def _on_zoom_changed(self) -> None:
        self._zoom_lbl.config(text=f"{self._zoom_var.get():.2f}×")
        self._schedule_preview()

    def _reset_crop(self) -> None:
        self._crop_x_var.set(0.5)
        self._crop_y_var.set(0.5)
        self._zoom_var.set(1.0)

    # ── image loading ─────────────────────────────────────────────────────────

    def _on_input_changed(self) -> None:
        path_str = self._input_var.get().strip()
        if not path_str:
            return
        p = Path(path_str)
        if not p.is_file():
            return
        self._set_status("Loading image…")

        def _load() -> None:
            try:
                img = load_image(p)
                mb = p.stat().st_size / 1_048_576
                info = f"{img.width} × {img.height} px  •  {mb:.1f} MB"
                def _apply() -> None:
                    self._source = img
                    self._resized_full = None
                    self._resized_key = ()
                    self._img_info_var.set(info)
                    self._pv_canvas.config(cursor="fleur")
                    self._reset_crop()
                self._ui(_apply)
            except Exception as exc:
                self._ui(lambda: self._set_status(f"Load error: {exc}"))

        threading.Thread(target=_load, daemon=True).start()

    # ── full preview (LANCZOS, background thread) ─────────────────────────────

    def _schedule_preview(self) -> None:
        if self._preview_job:
            self.after_cancel(self._preview_job)
        if self._tile_job:
            self.after_cancel(self._tile_job)
            self._tile_job = None
        self._preview_job = self.after(200, self._update_preview)

    def _update_preview(self) -> None:
        self._preview_job = None
        if self._source is None:
            return

        source = self._source
        slides = int(self._slides_var.get())
        fmt = self._format_var.get()
        slide_w, slide_h = FORMATS[fmt]
        crop_x = self._crop_x_var.get()
        crop_y = self._crop_y_var.get()
        zoom = self._zoom_var.get()

        if not self._preview_lock.acquire(blocking=False):
            self._preview_job = self.after(150, self._update_preview)
            return

        def _compute() -> None:
            try:
                canvas_w = slides * slide_w
                resized_full = _cover_resize(source, canvas_w, slide_h, zoom)
                canvas_img = _crop_from_resized(
                    resized_full, canvas_w, slide_h, crop_x, crop_y
                )
                excess_x = resized_full.width - canvas_w
                excess_y = resized_full.height - slide_h
                display_img = add_debug_overlay(canvas_img, slides, slide_w)
                tiles = slice_canvas(canvas_img, slides, slide_w)
                rkey = (round(zoom, 4), slides, fmt)
                status = (
                    f"{slides} slides × {slide_w}×{slide_h} px  "
                    f"(canvas: {canvas_img.width}×{canvas_img.height} px)"
                )
                self._ui(lambda: self._apply_preview(
                    display_img, tiles, status, excess_x, excess_y, resized_full, rkey
                ))
            except Exception as exc:
                self._ui(lambda: self._set_status(f"Preview error: {exc}"))
            finally:
                self._preview_lock.release()

        threading.Thread(target=_compute, daemon=True).start()

    def _apply_preview(
        self,
        display_img: Image.Image,
        tiles: List[Image.Image],
        status: str,
        excess_x: int,
        excess_y: int,
        resized_full: Image.Image,
        resized_key: Tuple,
    ) -> None:
        self._display_img = display_img
        self._crop_excess_x = excess_x
        self._crop_excess_y = excess_y
        self._resized_full = resized_full
        self._resized_key = resized_key
        self._render_panorama(display_img)
        self._render_tile_strip(tiles)
        self._set_status(status)

    def _rescale_preview(self) -> None:
        if self._display_img is not None:
            self._render_panorama(self._display_img)

    def _render_panorama(self, img: Image.Image, fast: bool = False) -> None:
        cw = self._pv_canvas.winfo_width() or 800
        ch = self._pv_canvas.winfo_height() or 400
        thumb = _fit(img, cw, ch, fast=fast)
        self._preview_scale = thumb.width / img.width
        self._tk_preview = ImageTk.PhotoImage(thumb)
        self._pv_canvas.delete("all")
        self._pv_canvas.create_image(
            (cw - thumb.width) // 2, (ch - thumb.height) // 2,
            anchor="nw", image=self._tk_preview,
        )

    def _render_tile_strip(self, tiles: List[Image.Image]) -> None:
        for w in self._tiles_row.winfo_children():
            w.destroy()
        self._tk_tiles = []
        for i, tile in enumerate(tiles, start=1):
            scale = TILE_H / tile.height
            thumb = tile.resize(
                (max(1, int(tile.width * scale)), TILE_H), Image.BILINEAR
            )
            tk_img = ImageTk.PhotoImage(thumb)
            self._tk_tiles.append(tk_img)
            cell = tk.Frame(self._tiles_row, bg=TILE_BG)
            cell.pack(side=tk.LEFT, padx=5, pady=4)
            tk.Label(cell, image=tk_img, bg="#111").pack()
            tk.Label(cell, text=f"Slide {i:02d}",
                     bg=TILE_BG, fg="gray", font=("", 8)).pack()

    # ── actions ───────────────────────────────────────────────────────────────

    def _browse_input(self) -> None:
        path = filedialog.askopenfilename(
            title="Select source image",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.webp *.bmp *.tiff"),
                       ("All files", "*.*")],
        )
        if path:
            self._load_from_path(Path(path))

    def _open_folder_picker(self) -> None:
        LocalFolderPicker(self, on_select=self._load_from_path)

    def _open_apple_photos(self) -> None:
        PhotosAppImporter(self, on_select=self._load_from_path)

    def _load_from_path(self, path: Path) -> None:
        self._input_var.set(str(path))

    def _browse_output(self) -> None:
        path = filedialog.askdirectory(title="Select output directory")
        if path:
            self._output_var.set(path)

    def _suggest_slides(self) -> None:
        if self._source is None:
            messagebox.showinfo("No image loaded", "Open an image first.")
            return
        slide_w, slide_h = FORMATS[self._format_var.get()]
        n = suggest_slide_count(
            self._source.width, self._source.height, slide_w, slide_h
        )
        self._slides_var.set(n)
        self._set_status(
            f"Suggested {n} slides for a {self._source.width}×{self._source.height} image."
        )

    def _generate(self) -> None:
        if self._source is None:
            messagebox.showinfo("No image loaded", "Open an image first.")
            return

        input_path = Path(self._input_var.get().strip())
        output_str = self._output_var.get().strip() or DEFAULT_OUTPUT_DIR

        try:
            slides = int(self._slides_var.get())
            quality = int(self._quality_var.get())
            validate_slide_count(slides)
            validate_quality(quality)
        except ValueError as exc:
            messagebox.showerror("Invalid settings", str(exc))
            return

        fmt = self._format_var.get()
        source = self._source
        prefix = self._prefix_var.get().strip() or None
        dry_run = self._dry_run_var.get()
        debug = self._debug_var.get()
        use_png = self._png_var.get()
        crop_x = self._crop_x_var.get()
        crop_y = self._crop_y_var.get()
        zoom = self._zoom_var.get()
        output_dir = ensure_output_dir(output_str)

        self._gen_btn.state(["disabled"])
        self._progress.start(12)
        self._set_status("Generating…")

        def _run() -> None:
            try:
                saved = process_image(
                    input_path=input_path,
                    output_dir=output_dir,
                    slides=slides,
                    slide_dims=FORMATS[fmt],
                    quality=quality,
                    prefix=prefix,
                    dry_run=dry_run,
                    debug_overlay=debug,
                    use_png=use_png,
                    source_image=source,
                    crop_x=crop_x,
                    crop_y=crop_y,
                    zoom=zoom,
                )
                msg = ("Dry run complete — no files written." if dry_run
                       else f"Done — {len(saved)} slide"
                            f"{'s' if len(saved) != 1 else ''} written to {output_dir}")
                self._ui(lambda: self._set_status(msg))
            except Exception as exc:
                err = str(exc)
                self._ui(lambda: self._set_status(f"Error: {err}"))
            finally:
                self._ui(self._progress.stop)
                self._ui(lambda: self._gen_btn.state(["!disabled"]))

        threading.Thread(target=_run, daemon=True).start()

    def _open_output(self) -> None:
        path = Path(self._output_var.get().strip() or DEFAULT_OUTPUT_DIR).resolve()
        if not path.exists():
            messagebox.showinfo("Folder not found",
                                f"Output folder does not exist yet:\n{path}")
            return
        if sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        elif sys.platform == "win32":
            subprocess.run(["explorer", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)

    def _set_status(self, msg: str) -> None:
        self._status_var.set(msg)


def launch() -> None:
    """Launch the GUI application."""
    App().mainloop()


if __name__ == "__main__":
    launch()
