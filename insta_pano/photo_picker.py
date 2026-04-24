"""Photo source helpers: native Apple Photos integration and local folder picker."""

from __future__ import annotations

import queue
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, List, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

# Register HEIC/HEIF support so Pillow can open Photos originals
try:
    from pillow_heif import register_heif_opener as _reg
    _reg()
except ImportError:
    pass

THUMB_SIZE = 150
GRID_COLS = 5
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".heic", ".heif"}


def apple_photos_available() -> bool:
    """True on macOS (Photos.app is always present)."""
    import sys
    return sys.platform == "darwin"


# ── Apple Photos — native app integration ────────────────────────────────────

_EXPORT_SCRIPT = """\
tell application "Photos"
    set sel to selection
    if (count of sel) = 0 then
        error "NO_SELECTION"
    end if
    set thePhoto to item 1 of sel
    set tmpDir to "{tmp_dir}"
    do shell script "mkdir -p " & quoted form of tmpDir
    export {{thePhoto}} to (POSIX file tmpDir) with using originals
    return tmpDir & (filename of thePhoto)
end tell
"""


class PhotosAppImporter(tk.Toplevel):
    """Minimal dialog that opens Photos.app and imports the user's selection.

    Workflow:
      1. Photos.app opens automatically.
      2. User selects any photo there.
      3. User clicks "Import Selected" — AppleScript exports the original to
         a temp directory and hands the path back to on_select.
    """

    def __init__(self, parent: tk.Tk,
                 on_select: Callable[[Path], None]) -> None:
        super().__init__(parent)
        self.title("Import from Photos")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._on_select = on_select
        self._ui_q: queue.Queue[Callable[[], None]] = queue.Queue()
        self._tmp_dir = tempfile.mkdtemp(prefix="insta_pano_")

        self._build_ui()
        self._start_poll()

        # Open Photos immediately so the user can start selecting
        subprocess.Popen(["open", "-a", "Photos"])

    # ── queue drain ───────────────────────────────────────────────────────────

    def _start_poll(self) -> None:
        try:
            while True:
                self._ui_q.get_nowait()()
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(50, self._start_poll)

    def _ui(self, fn: Callable) -> None:
        self._ui_q.put(fn)

    # ── layout ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        pad = dict(padx=24, pady=6)

        tk.Label(self, text="Import from Apple Photos",
                 font=("", 14, "bold")).pack(pady=(20, 4))

        tk.Label(
            self,
            text=(
                "Photos has opened.\n\n"
                "  1. Select any photo in Photos\n"
                "  2. Come back here and click  Import Selected"
            ),
            font=("", 11),
            justify="left",
        ).pack(**pad)

        self._status_var = tk.StringVar()
        tk.Label(self, textvariable=self._status_var,
                 fg="gray", font=("", 9)).pack(pady=(0, 4))

        btn_row = tk.Frame(self)
        btn_row.pack(pady=(4, 20), **{k: v for k, v in pad.items() if k == "padx"})
        ttk.Button(btn_row, text="Cancel",
                   command=self.destroy).pack(side=tk.LEFT, padx=(0, 8))
        self._import_btn = ttk.Button(
            btn_row, text="Import Selected",
            command=self._do_import,
        )
        self._import_btn.pack(side=tk.LEFT)

        self.update_idletasks()
        self.geometry("")   # shrink-wrap to content

    # ── import ────────────────────────────────────────────────────────────────

    def _do_import(self) -> None:
        self._import_btn.state(["disabled"])
        self._status_var.set("Exporting from Photos…")
        threading.Thread(target=self._run_script, daemon=True).start()

    def _run_script(self) -> None:
        script = _EXPORT_SCRIPT.format(tmp_dir=self._tmp_dir + "/")
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "NO_SELECTION" in stderr:
                msg = "No photo selected — please select one in Photos first."
            else:
                msg = stderr or "Export failed."
            self._ui(lambda m=msg: self._error(m))
            return

        path = Path(result.stdout.strip())
        if not path.exists():
            self._ui(lambda: self._error(f"Exported file not found: {path.name}"))
            return

        self._ui(lambda p=path: self._finish(p))

    def _finish(self, path: Path) -> None:
        self._on_select(path)
        self.destroy()

    def _error(self, msg: str) -> None:
        self._status_var.set(msg)
        self._import_btn.state(["!disabled"])


# ── Local folder visual picker ────────────────────────────────────────────────

class _ThumbnailGrid(tk.Frame):
    """Scrollable grid of image thumbnails."""

    def __init__(self, parent: tk.Widget,
                 on_select: Optional[Callable[[int], None]] = None,
                 on_confirm: Optional[Callable[[int], None]] = None) -> None:
        super().__init__(parent, bg="#1a1a1a")
        self._on_select = on_select
        self._on_confirm = on_confirm
        self._cells: List[tk.Frame] = []
        self._selected: Optional[int] = None
        self._build()

    def _build(self) -> None:
        canvas = tk.Canvas(self, bg="#1a1a1a", highlightthickness=0)
        vsb = ttk.Scrollbar(self, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._canvas = canvas

        inner = tk.Frame(canvas, bg="#1a1a1a")
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(win, width=e.width))
        canvas.bind("<MouseWheel>",
                    lambda e: canvas.yview_scroll(-(e.delta // 120), "units"))
        self._inner = inner

    def clear(self) -> None:
        for w in self._inner.winfo_children():
            w.destroy()
        self._cells = []
        self._selected = None

    def add_cell(self, label: str) -> int:
        idx = len(self._cells)
        cell = tk.Frame(self._inner, width=THUMB_SIZE + 10,
                        height=THUMB_SIZE + 28, bg="#2b2b2b")
        cell.grid(row=idx // GRID_COLS, column=idx % GRID_COLS,
                  padx=3, pady=3, sticky="nw")
        cell.grid_propagate(False)

        img_lbl = tk.Label(cell, text="·", bg="#2b2b2b", fg="#444", font=("", 22))
        img_lbl.pack(fill=tk.BOTH, expand=True)

        short = label if len(label) <= 16 else label[:13] + "…"
        tk.Label(cell, text=short, bg="#2b2b2b", fg="#999",
                 font=("", 8)).pack(fill=tk.X)

        cell._img_lbl = img_lbl
        for w in cell.winfo_children():
            w.bind("<Button-1>", lambda e, i=idx: self._click(i))
            w.bind("<Double-Button-1>", lambda e, i=idx: self._dbl(i))
        cell.bind("<Button-1>", lambda e, i=idx: self._click(i))
        cell.bind("<Double-Button-1>", lambda e, i=idx: self._dbl(i))

        self._cells.append(cell)
        return idx

    def set_thumb(self, idx: int, pil_img: Image.Image) -> None:
        if idx >= len(self._cells):
            return
        tk_img = ImageTk.PhotoImage(pil_img)
        lbl = self._cells[idx]._img_lbl
        lbl.configure(image=tk_img, text="")
        lbl._ref = tk_img

    def selected(self) -> Optional[int]:
        return self._selected

    def _click(self, idx: int) -> None:
        if self._selected is not None and self._selected < len(self._cells):
            c = self._cells[self._selected]
            c.configure(bg="#2b2b2b", relief="flat", bd=0)
            c._img_lbl.configure(bg="#2b2b2b")
        self._selected = idx
        c = self._cells[idx]
        c.configure(bg="#1a4a8a", relief="solid", bd=2)
        c._img_lbl.configure(bg="#1a4a8a")
        if self._on_select:
            self._on_select(idx)

    def _dbl(self, idx: int) -> None:
        self._click(idx)
        if self._on_confirm:
            self._on_confirm(idx)


class LocalFolderPicker(tk.Toplevel):
    """Modal dialog — browse a local folder and pick one image visually."""

    def __init__(self, parent: tk.Tk,
                 on_select: Callable[[Path], None]) -> None:
        super().__init__(parent)
        self.title("Browse Folder")
        self.geometry("960x680")
        self.minsize(640, 420)
        self.transient(parent)
        self.grab_set()

        self._on_select = on_select
        self._files: List[Path] = []
        self._ui_q: queue.Queue[Callable[[], None]] = queue.Queue()

        self._build_ui()
        self._start_poll()
        self._pick_folder()

    def _start_poll(self) -> None:
        try:
            while True:
                self._ui_q.get_nowait()()
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(50, self._start_poll)

    def _ui(self, fn: Callable) -> None:
        self._ui_q.put(fn)

    def _build_ui(self) -> None:
        top = tk.Frame(self, pady=6)
        top.pack(fill=tk.X, padx=10)
        self._folder_lbl = tk.Label(top, text="No folder selected",
                                     fg="gray", font=("", 9), anchor="w")
        self._folder_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._count_lbl = tk.Label(top, text="", fg="gray", font=("", 9))
        self._count_lbl.pack(side=tk.RIGHT, padx=(0, 8))
        ttk.Button(top, text="Change Folder",
                   command=self._pick_folder).pack(side=tk.RIGHT)
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X)

        self._grid = _ThumbnailGrid(
            self,
            on_select=lambda _: self._ok_btn.state(["!disabled"]),
            on_confirm=lambda _: self._confirm(),
        )
        self._grid.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        self._status_var = tk.StringVar()
        tk.Label(self, textvariable=self._status_var,
                 fg="gray", font=("", 9)).pack(pady=2)

        btn_row = tk.Frame(self)
        btn_row.pack(fill=tk.X, padx=10, pady=8)
        ttk.Button(btn_row, text="Cancel",
                   command=self.destroy).pack(side=tk.RIGHT, padx=(6, 0))
        self._ok_btn = ttk.Button(btn_row, text="Select Photo",
                                   command=self._confirm)
        self._ok_btn.pack(side=tk.RIGHT)
        self._ok_btn.state(["disabled"])

    def _pick_folder(self) -> None:
        folder = filedialog.askdirectory(title="Choose a folder", parent=self)
        if not folder:
            if not self._files:
                self.destroy()
            return
        p = Path(folder)
        self._folder_lbl.configure(text=str(p), fg="black")
        self._status_var.set("Scanning…")
        threading.Thread(target=self._scan, args=(p,), daemon=True).start()

    def _scan(self, folder: Path) -> None:
        files = sorted(
            [f for f in folder.iterdir()
             if f.is_file() and f.suffix.lower() in _IMAGE_EXTS],
            key=lambda f: f.name,
        )
        self._ui(lambda: self._populate(files))

    def _populate(self, files: List[Path]) -> None:
        self._files = files
        self._status_var.set("")
        self._count_lbl.configure(text=f"{len(files)} images")
        self._grid.clear()
        self._ok_btn.state(["disabled"])
        for f in files:
            self._grid.add_cell(f.name)
        threading.Thread(target=self._load_thumbs,
                         args=(list(files),), daemon=True).start()

    def _load_thumbs(self, files: List[Path]) -> None:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for idx, f in enumerate(files):
                pool.submit(self._one_thumb, idx, f)

    def _one_thumb(self, idx: int, path: Path) -> None:
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
            self._ui(lambda i=idx, im=img: self._grid.set_thumb(i, im))
        except Exception:
            pass

    def _confirm(self) -> None:
        idx = self._grid.selected()
        if idx is None:
            return
        self._on_select(self._files[idx])
        self.destroy()
