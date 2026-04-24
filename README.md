# panoramaSlice

> Split any wide image into perfectly aligned Instagram carousel slides — from the command line or a native GUI.

[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)]()

---

## What it does

panoramaSlice takes a single wide (panoramic) image and slices it into **N perfectly aligned tiles** ready to upload as an Instagram carousel. When a viewer swipes through the slides the image flows seamlessly from one to the next.

Key properties:

- **No distortion** — resizes with *cover* mode then center-crops, never stretches.
- **Pixel-perfect alignment** — tiles are contiguous crops of the same canvas; no rounding gaps.
- **Instagram-ready dimensions** — portrait 1080 × 1350 (default) or square 1080 × 1080.
- **HEIC / JPEG / PNG** — reads any format Pillow supports, including HEIC from Apple Photos.

---

## Screenshots

| CLI | GUI |
|-----|-----|
| ![CLI demo](docs/cli_demo.png) | ![GUI demo](docs/gui_demo.png) |

> *Add your own screenshots to `docs/` after cloning.*

---

## Features

- **CLI** — scriptable, composable, CI-friendly.
- **Desktop GUI** (macOS) — live preview with slice markers, tile strip, progress bar.
- **Apple Photos integration** — import directly from Photos.app via AppleScript; no file-manager needed.
- **Local folder browser** — visual thumbnail grid for picking from any directory.
- **Auto-suggest** — recommends the optimal slide count for your image's aspect ratio.
- **Dry-run mode** — shows planned output paths without writing any files.
- **Debug overlay** — burn red slice-boundary lines into the output for QA.
- **PNG or JPEG** — lossless PNG export or JPEG at quality 1–100.

---

## Installation

### Requirements

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip

### With uv (recommended)

```bash
git clone https://github.com/JimmyMtl/panoramaSlice.git
cd panoramaSlice
uv sync
```

### With pip

```bash
git clone https://github.com/JimmyMtl/panoramaSlice.git
cd panoramaSlice
pip install .
```

### macOS extras (Apple Photos + HEIC)

Installed automatically on macOS via the platform markers in `pyproject.toml`:

| Package | Purpose |
|---------|---------|
| `osxphotos` | Read Apple Photos library metadata |
| `pillow-heif` | Open HEIC/HEIF files from Photos |

---

## CLI usage

```bash
# Basic — 3 portrait slides, output to ./output
python -m insta_pano.main --input panorama.jpg

# 4 portrait slides, custom output directory
python -m insta_pano.main --input panorama.jpg --slides 4 --output ./carousel

# Square format, quality 100, PNG output
python -m insta_pano.main --input photo.png --slides 3 --format square --quality 100 --png

# Let panoramaSlice suggest the best slide count
python -m insta_pano.main --input panorama.jpg --suggest

# Preview what would be created without writing anything
python -m insta_pano.main --input panorama.jpg --slides 5 --dry-run

# Installed as a command
panorama_slice --input panorama.jpg --slides 4
```

### All flags

| Flag | Default | Description |
|------|---------|-------------|
| `--input` | *(required)* | Source image (jpg / png / webp / heic / …) |
| `--output` | `./output` | Output directory |
| `--slides` | `3` | Number of slides (2–10) |
| `--format` | `portrait` | `portrait` (1080 × 1350) or `square` (1080 × 1080) |
| `--quality` | `95` | JPEG quality 1–100 |
| `--prefix` | *(input stem)* | Custom output filename prefix |
| `--dry-run` | off | Preview without writing files |
| `--debug` | off | Burn red slice markers into output |
| `--png` | off | Export as lossless PNG |
| `--suggest` | off | Print suggested slide count and exit |
| `--verbose` | off | Enable DEBUG logging |

---

## GUI usage (macOS)

```bash
uv run insta_pano_gui
# or
python -m insta_pano.gui
```

### Input sources

| Button | What happens |
|--------|-------------|
| **File…** | Standard file-open dialog |
| **Folder…** | Visual thumbnail grid of images in any local directory |
| **Apple Photos** | Opens Photos.app — select a photo there, click *Import Selected* |

The preview canvas updates automatically as you adjust slides, format, or quality. Red boundary lines show exactly where each slice will fall. The tile strip at the bottom shows what each individual slide will look like.

---

## How the image math works

```
source image (any size)
        │
        ▼  resize with cover (max scale, aspect ratio preserved)
        │
        ▼  center-crop to (slides × slide_width) × slide_height
        │
        ▼  slice into N equal vertical tiles
        │
   tile_01.jpg … tile_N.jpg
```

- **Cover resize**: `scale = max(target_w / src_w, target_h / src_h)` — guarantees no black bars.
- **Center crop**: removes equal amounts from left/right (and top/bottom if needed).
- **Slice**: `tile_i = canvas.crop((i * slide_w, 0, (i+1) * slide_w, slide_h))` — zero rounding, zero gap.

---

## Upload order

Files are named `<prefix>_01.jpg`, `<prefix>_02.jpg`, … in **left-to-right order**.  
Upload them to Instagram in this exact sequence — `_01` is the first slide (leftmost panel).

### Instagram best practices

- Use **portrait (4:5)** format for maximum feed real estate.
- Upload in **numerical order** — Instagram preserves the sequence.
- Start at **quality 95–100**; Instagram will re-compress on their end.
- For best panorama continuity, use a source with an aspect ratio close to `N × (4/5)`.

---

## Running tests

```bash
uv run python -m pytest tests/ -v
```

---

## Project structure

```
panoramaSlice/
├── insta_pano/
│   ├── config.py        # slide dimensions, defaults, bounds
│   ├── processor.py     # image pipeline
│   ├── utils.py         # validation, logging, path helpers
│   ├── main.py          # CLI (argparse)
│   ├── gui.py           # desktop GUI (tkinter)
│   └── photo_picker.py  # Apple Photos importer + folder browser
├── tests/
│   └── test_processor.py
├── pyproject.toml
├── LICENSE
└── CONTRIBUTING.md
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Issues and pull requests are welcome.

---

## License

[MIT](LICENSE) © 2026 JimmyMtl
