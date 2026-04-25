"""Core image processing pipeline for panorama carousel generation."""

import logging
import math
import random
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw

try:
    from pillow_heif import register_heif_opener as _reg_heif
    _reg_heif()
except ImportError:
    pass

logger = logging.getLogger("insta_pano")


def load_image(path: Path) -> Image.Image:
    """Load an image from disk and convert to RGB.

    Args:
        path: Path to the source image.

    Returns:
        RGB PIL Image.

    Raises:
        IOError: If the file cannot be opened or is corrupt.
    """
    try:
        img = Image.open(path)
        img.load()  # Force decode to catch corrupt files immediately.
    except Exception as exc:
        raise IOError(f"Failed to open image '{path}': {exc}") from exc
    return img.convert("RGB")


def resize_cover(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Scale image to cover (target_w, target_h) while preserving aspect ratio.

    The image is scaled up or down so that both dimensions are at least as
    large as the target, analogous to CSS ``background-size: cover``.

    Args:
        img: Source PIL Image.
        target_w: Desired minimum width in pixels.
        target_h: Desired minimum height in pixels.

    Returns:
        Resized PIL Image with size >= (target_w, target_h).
    """
    img_w, img_h = img.size
    scale = max(target_w / img_w, target_h / img_h)
    new_w = round(img_w * scale)
    new_h = round(img_h * scale)
    return img.resize((new_w, new_h), Image.LANCZOS)


def center_crop(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Crop the center of an image to exactly (target_w, target_h).

    Args:
        img: Source PIL Image (must be >= target dimensions).
        target_w: Output width in pixels.
        target_h: Output height in pixels.

    Returns:
        Cropped PIL Image of size (target_w, target_h).
    """
    img_w, img_h = img.size
    left = (img_w - target_w) // 2
    top = (img_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def prepare_canvas(
    img: Image.Image,
    slides: int,
    slide_w: int,
    slide_h: int,
    crop_x: float = 0.5,
    crop_y: float = 0.5,
    zoom: float = 1.0,
) -> Image.Image:
    """Resize and crop a source image into the full panorama canvas.

    Args:
        img: Source PIL Image (any size / aspect ratio).
        slides: Number of slide tiles.
        slide_w: Width of each tile in pixels.
        slide_h: Height of each tile in pixels.
        crop_x: Horizontal anchor — 0.0 = left edge, 0.5 = center, 1.0 = right edge.
        crop_y: Vertical anchor — 0.0 = top edge, 0.5 = center, 1.0 = bottom edge.
        zoom: Extra zoom multiplier on top of cover-scale (≥ 1.0).

    Returns:
        PIL Image of size (slides * slide_w, slide_h).
    """
    canvas_w = slides * slide_w
    canvas_h = slide_h
    base_scale = max(canvas_w / img.width, canvas_h / img.height) * max(1.0, zoom)
    new_w = max(canvas_w, round(img.width * base_scale))
    new_h = max(canvas_h, round(img.height * base_scale))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    excess_x = resized.width - canvas_w
    excess_y = resized.height - canvas_h
    left = round(excess_x * max(0.0, min(1.0, crop_x)))
    top = round(excess_y * max(0.0, min(1.0, crop_y)))
    logger.debug(
        "Canvas: %dx%d  src: %dx%d → scaled: %dx%d  excess: %d×%d  crop: %.2f,%.2f  zoom: %.2f",
        canvas_w, canvas_h, img.width, img.height, new_w, new_h,
        excess_x, excess_y, crop_x, crop_y, zoom,
    )
    return resized.crop((left, top, left + canvas_w, top + canvas_h))


def get_crop_excess(
    img: Image.Image, canvas_w: int, canvas_h: int, zoom: float = 1.0
) -> Tuple[int, int]:
    """Return (excess_x, excess_y) pixels available for crop adjustment.

    Args:
        img: Source PIL Image.
        canvas_w: Target canvas width in pixels.
        canvas_h: Target canvas height in pixels.
        zoom: Additional zoom factor (≥ 1.0).

    Returns:
        (excess_x, excess_y) — pixels of slack on each axis after cover-resize.
        The constrained axis will be 0.
    """
    base_scale = max(canvas_w / img.width, canvas_h / img.height) * max(1.0, zoom)
    new_w = max(canvas_w, round(img.width * base_scale))
    new_h = max(canvas_h, round(img.height * base_scale))
    return new_w - canvas_w, new_h - canvas_h


def add_debug_overlay(canvas: Image.Image, slides: int, slide_w: int) -> Image.Image:
    """Draw red vertical lines at each slice boundary for visual debugging.

    Args:
        canvas: Full panorama canvas image.
        slides: Number of slide tiles.
        slide_w: Width of each tile in pixels.

    Returns:
        New image with boundary lines drawn on top.
    """
    overlay = canvas.copy()
    draw = ImageDraw.Draw(overlay)
    for i in range(1, slides):
        x = i * slide_w
        draw.line([(x, 0), (x, canvas.height - 1)], fill=(255, 0, 0), width=6)
    return overlay


def slice_canvas(
    canvas: Image.Image, slides: int, slide_w: int
) -> List[Image.Image]:
    """Divide the panorama canvas into equal vertical tiles.

    Args:
        canvas: Full panorama canvas image of width (slides * slide_w).
        slides: Number of tiles to produce.
        slide_w: Width of each tile in pixels.

    Returns:
        List of tile images in left-to-right order (index 0 = leftmost / first slide).
    """
    tiles: List[Image.Image] = []
    for i in range(slides):
        left = i * slide_w
        tile = canvas.crop((left, 0, left + slide_w, canvas.height))
        tiles.append(tile)
    return tiles


def _boundary_wave(y: int, amplitude: float, slide_h: int, direction: int) -> float:
    """Single-bump wave for a puzzle cut: 0 at top/bottom, ±amplitude at mid-height."""
    return amplitude * math.sin(math.pi * y / max(1, slide_h)) * direction


def add_puzzle_overlay(
    canvas: Image.Image, slides: int, slide_w: int, amplitude: int
) -> Image.Image:
    """Draw wave cut curves on the panorama canvas for the live preview."""
    overlay = canvas.copy()
    draw = ImageDraw.Draw(overlay)
    slide_h = canvas.height
    step = max(1, slide_h // 300)
    directions = [1 if b % 2 == 0 else -1 for b in range(slides - 1)]

    for b, direction in enumerate(directions):
        x_b = (b + 1) * slide_w
        pts = [
            (x_b + int(_boundary_wave(y, amplitude, slide_h, direction)), y)
            for y in range(0, slide_h, step)
        ]
        if len(pts) > 1:
            draw.line(pts, fill=(255, 50, 50), width=4)
    return overlay


def slice_canvas_puzzle(
    canvas: Image.Image,
    slides: int,
    slide_w: int,
    amplitude: int,
    bg_color: Tuple[int, int, int] = (255, 255, 255),
) -> List[Image.Image]:
    """Divide the panorama canvas into puzzle-shaped tiles.

    Each internal cut follows a single-bump wave so adjacent tiles
    have interlocking tabs and sockets.  Tiles remain (slide_w × slide_h)
    rectangles; socket areas are filled with *bg_color*.
    """
    slide_h = canvas.height
    amplitude = min(amplitude, slide_w // 4)  # cap at 25 % of width
    pad = amplitude
    directions = [1 if b % 2 == 0 else -1 for b in range(slides - 1)]
    result: List[Image.Image] = []

    for idx in range(slides):
        ext_w = slide_w + 2 * pad

        # Extended source — canvas pixels plus a pad-wide buffer on each side
        ext_source = Image.new("RGB", (ext_w, slide_h), bg_color)
        canvas_x_left = idx * slide_w - pad
        canvas_x_right = idx * slide_w + slide_w + pad
        src_x_left = max(0, canvas_x_left)
        src_x_right = min(canvas.width, canvas_x_right)
        if src_x_left < src_x_right:
            dst_x = src_x_left - canvas_x_left
            ext_source.paste(canvas.crop((src_x_left, 0, src_x_right, slide_h)), (dst_x, 0))

        # Build the polygon that encloses the visible region in ext_source space:
        #   left edge  = pad + wave_of_left_boundary  (top → bottom)
        #   right edge = pad + slide_w + wave_of_right_boundary  (bottom → top)
        left_pts = []
        right_pts = []
        for y in range(slide_h):
            lw = 0.0 if idx == 0 else _boundary_wave(y, amplitude, slide_h, directions[idx - 1])
            rw = 0.0 if idx == slides - 1 else _boundary_wave(y, amplitude, slide_h, directions[idx])
            left_pts.append((int(pad + lw), y))
            right_pts.append((int(pad + slide_w + rw), y))

        mask = Image.new("L", (ext_w, slide_h), 0)
        ImageDraw.Draw(mask).polygon(left_pts + list(reversed(right_pts)), fill=255)

        bg_layer = Image.new("RGB", (ext_w, slide_h), bg_color)
        tile_ext = Image.composite(ext_source, bg_layer, mask)
        result.append(tile_ext.crop((pad, 0, pad + slide_w, slide_h)))

    return result


def best_grid(n_pieces: int, img_w: int, img_h: int) -> Tuple[int, int]:
    """Return (rows, cols) whose product is closest to *n_pieces* while
    matching the image aspect ratio (cols/rows ≈ img_w/img_h)."""
    target = img_w / img_h
    best: Tuple[int, int] = (1, n_pieces)
    best_score = float("inf")
    for rows in range(1, n_pieces + 1):
        for cols in {max(1, n_pieces // rows), max(1, round(n_pieces / rows)),
                     max(1, -(-n_pieces // rows))}:
            score = (
                abs(cols / rows - target) / max(1e-9, target)
                + abs(rows * cols - n_pieces) * 0.05
            )
            if score < best_score:
                best_score = score
                best = (rows, cols)
    return best


def _make_piece_mask(
    cell_w: int,
    cell_h: int,
    top: int,
    right: int,
    bottom: int,
    left: int,
    tab_r: int,
) -> Image.Image:
    """Build a grayscale mask for one jigsaw piece.

    Edge convention — +1 tab (knob protrudes outward), -1 socket (knob
    indented inward), 0 flat (border edge).  The returned mask is
    (cell_w + 2*tab_r, cell_h + 2*tab_r) so tab pixels from adjacent
    cells can be sampled via an extended crop.
    """
    pad = tab_r
    w, h = cell_w + 2 * pad, cell_h + 2 * pad
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rectangle([pad, pad, pad + cell_w, pad + cell_h], fill=255)

    mid_x = pad + cell_w // 2
    mid_y = pad + cell_h // 2
    r2 = tab_r * 2  # knob diameter

    # TOP  (y = pad  →  outward = upward)
    if top == 1:
        draw.ellipse([mid_x - tab_r, pad - r2, mid_x + tab_r, pad], fill=255)
    elif top == -1:
        draw.ellipse([mid_x - tab_r, pad, mid_x + tab_r, pad + r2], fill=0)

    # BOTTOM  (y = pad + cell_h  →  outward = downward)
    if bottom == 1:
        draw.ellipse([mid_x - tab_r, pad + cell_h, mid_x + tab_r, pad + cell_h + r2], fill=255)
    elif bottom == -1:
        draw.ellipse([mid_x - tab_r, pad + cell_h - r2, mid_x + tab_r, pad + cell_h], fill=0)

    # LEFT  (x = pad  →  outward = leftward)
    if left == 1:
        draw.ellipse([pad - r2, mid_y - tab_r, pad, mid_y + tab_r], fill=255)
    elif left == -1:
        draw.ellipse([pad, mid_y - tab_r, pad + r2, mid_y + tab_r], fill=0)

    # RIGHT  (x = pad + cell_w  →  outward = rightward)
    if right == 1:
        draw.ellipse([pad + cell_w, mid_y - tab_r, pad + cell_w + r2, mid_y + tab_r], fill=255)
    elif right == -1:
        draw.ellipse([pad + cell_w - r2, mid_y - tab_r, pad + cell_w, mid_y + tab_r], fill=0)

    return mask


def add_jigsaw_overlay(
    canvas: Image.Image, rows: int, cols: int
) -> Image.Image:
    """Draw the jigsaw grid on the canvas for the live preview."""
    overlay = canvas.copy()
    draw = ImageDraw.Draw(overlay)
    cell_w = canvas.width // cols
    cell_h = canvas.height // rows
    for c in range(1, cols):
        x = c * cell_w
        draw.line([(x, 0), (x, canvas.height)], fill=(255, 50, 50), width=2)
    for r in range(1, rows):
        y = r * cell_h
        draw.line([(0, y), (canvas.width, y)], fill=(255, 50, 50), width=2)
    return overlay


def generate_puzzle(
    canvas: Image.Image,
    rows: int,
    cols: int,
    bg_color: Tuple[int, int, int] = (255, 255, 255),
) -> List[Tuple[int, int, Image.Image]]:
    """Cut *canvas* into a *rows* × *cols* jigsaw puzzle.

    Each internal edge carries a circular knob whose direction is
    determined by a seeded RNG (reproducible for the same grid size).
    Adjacent pieces interlock: if piece A has a tab on its right, piece B
    to the right has the matching socket on its left.

    Returns a list of ``(row, col, piece_image)`` in row-major order.
    Piece images are ``(cell_w + 2*tab_r) × (cell_h + 2*tab_r)`` — larger
    than the base cell to include outward tab pixels from adjacent cells.
    """
    img_w, img_h = canvas.size
    cell_w = img_w // cols
    cell_h = img_h // rows
    tab_r = max(6, int(min(cell_w, cell_h) * 0.20))
    pad = tab_r

    rng = random.Random(img_w * 31337 + img_h * 13337 + rows * 7919 + cols * 6271)
    # h_edges[r][c]: +1 → piece(r,c) has TAB on bottom, piece(r+1,c) has SOCKET on top
    h_edges = [[rng.choice([1, -1]) for _ in range(cols)] for _ in range(rows - 1)]
    # v_edges[r][c]: +1 → piece(r,c) has TAB on right, piece(r,c+1) has SOCKET on left
    v_edges = [[rng.choice([1, -1]) for _ in range(cols - 1)] for _ in range(rows)]

    result: List[Tuple[int, int, Image.Image]] = []
    ext_w, ext_h = cell_w + 2 * pad, cell_h + 2 * pad

    for r in range(rows):
        for c in range(cols):
            top_d    = 0 if r == 0         else -h_edges[r - 1][c]
            bottom_d = 0 if r == rows - 1  else  h_edges[r][c]
            left_d   = 0 if c == 0         else -v_edges[r][c - 1]
            right_d  = 0 if c == cols - 1  else  v_edges[r][c]

            mask = _make_piece_mask(cell_w, cell_h, top_d, right_d, bottom_d, left_d, tab_r)

            # Extended canvas crop (may reach into adjacent cells for tab pixels)
            x0, y0 = c * cell_w - pad, r * cell_h - pad
            sx0 = max(0, x0);  sy0 = max(0, y0)
            sx1 = min(img_w, x0 + ext_w);  sy1 = min(img_h, y0 + ext_h)

            ext_src = Image.new("RGB", (ext_w, ext_h), bg_color)
            if sx0 < sx1 and sy0 < sy1:
                ext_src.paste(canvas.crop((sx0, sy0, sx1, sy1)),
                              (sx0 - x0, sy0 - y0))

            bg_layer = Image.new("RGB", (ext_w, ext_h), bg_color)
            piece = Image.composite(ext_src, bg_layer, mask)
            result.append((r, c, piece))
            logger.debug("Piece (%d,%d) top=%d right=%d bottom=%d left=%d", r, c, top_d, right_d, bottom_d, left_d)

    return result


def export_puzzle_pieces(
    pieces: List[Tuple[int, int, Image.Image]],
    output_dir: Path,
    prefix: str,
    quality: int,
    use_png: bool = False,
) -> List[Path]:
    """Save each jigsaw piece as ``{prefix}_r{row:02d}_c{col:02d}.{ext}``."""
    ext = ".png" if use_png else ".jpg"
    saved: List[Path] = []
    for r, c, piece_img in pieces:
        dest = output_dir / f"{prefix}_r{r + 1:02d}_c{c + 1:02d}{ext}"
        kwargs: dict = {"optimize": True}
        if use_png:
            kwargs["compress_level"] = 6
        else:
            kwargs["quality"] = quality
            kwargs["subsampling"] = 0
        piece_img.save(dest, **kwargs)
        logger.info("  Saved piece (%d,%d): %s", r, c, dest)
        saved.append(dest)
    return saved


def build_output_paths(
    output_dir: Path,
    prefix: str,
    count: int,
    use_png: bool,
) -> List[Path]:
    """Build ordered output file paths without writing anything.

    Args:
        output_dir: Destination directory.
        prefix: Filename prefix (no extension).
        count: Number of tiles.
        use_png: If True use .png, otherwise .jpg.

    Returns:
        List of Path objects in upload order.
    """
    ext = ".png" if use_png else ".jpg"
    return [output_dir / f"{prefix}_{i:02d}{ext}" for i in range(1, count + 1)]


def export_tiles(
    tiles: List[Image.Image],
    output_dir: Path,
    prefix: str,
    quality: int,
    use_png: bool = False,
) -> List[Path]:
    """Save each tile to disk with sequential two-digit numbering.

    Args:
        tiles: Ordered list of tile images (index 0 = slide 1).
        output_dir: Destination directory (must already exist).
        prefix: Output filename prefix.
        quality: JPEG quality (1–95); ignored when use_png is True.
        use_png: Export as lossless PNG instead of JPEG.

    Returns:
        List of Paths to written files, in upload order.
    """
    paths = build_output_paths(output_dir, prefix, len(tiles), use_png)
    for tile, dest in zip(tiles, paths):
        save_kwargs: dict = {"optimize": True}
        if use_png:
            save_kwargs["compress_level"] = 6
        else:
            save_kwargs["quality"] = quality
            save_kwargs["subsampling"] = 0  # 4:4:4 — no chroma downsampling
        tile.save(dest, **save_kwargs)
        logger.info("  Saved: %s", dest)
    return paths


def process_image(
    input_path: Path,
    output_dir: Path,
    slides: int,
    slide_dims: Tuple[int, int],
    quality: int,
    prefix: Optional[str] = None,
    dry_run: bool = False,
    debug_overlay: bool = False,
    use_png: bool = False,
    source_image: Optional[Image.Image] = None,
    crop_x: float = 0.5,
    crop_y: float = 0.5,
    zoom: float = 1.0,
    puzzle_mode: bool = False,
    puzzle_amplitude: int = 0,
    puzzle_bg_color: Tuple[int, int, int] = (255, 255, 255),
) -> List[Path]:
    """End-to-end pipeline: load → resize → crop → slice → export.

    Args:
        input_path: Path to the source image (used for naming and fallback load).
        output_dir: Directory to write output tiles.
        slides: Number of output tiles.
        slide_dims: (width, height) of each tile in pixels.
        quality: JPEG export quality (1–100).
        prefix: Output filename prefix; defaults to input stem.
        dry_run: If True, log planned output paths but write nothing.
        debug_overlay: If True, draw slice-boundary lines before exporting.
        use_png: Export tiles as PNG instead of JPEG.
        source_image: Pre-loaded image to skip the disk read (e.g. from GUI cache).
        crop_x: Horizontal crop anchor (0.0 left → 1.0 right).
        crop_y: Vertical crop anchor (0.0 top → 1.0 bottom).
        zoom: Additional zoom multiplier (≥ 1.0).

    Returns:
        List of Paths to written files, or empty list on dry-run.
    """
    slide_w, slide_h = slide_dims
    out_prefix = prefix or input_path.stem

    if source_image is not None:
        img = source_image
        logger.info("Using cached image  (%d × %d px)", img.width, img.height)
    else:
        img = load_image(input_path)
        logger.info("Loaded '%s'  (%d × %d px)", input_path.name, img.width, img.height)

    canvas = prepare_canvas(img, slides, slide_w, slide_h, crop_x, crop_y, zoom)

    if debug_overlay:
        canvas = add_debug_overlay(canvas, slides, slide_w)
        logger.debug("Debug overlay applied")

    if puzzle_mode and puzzle_amplitude > 0:
        tiles = slice_canvas_puzzle(canvas, slides, slide_w, puzzle_amplitude, puzzle_bg_color)
        logger.info("Puzzle-sliced into %d tiles (%d × %d px each, depth %dpx)",
                    slides, slide_w, slide_h, puzzle_amplitude)
    else:
        tiles = slice_canvas(canvas, slides, slide_w)
        logger.info("Sliced into %d tiles (%d × %d px each)", slides, slide_w, slide_h)

    if dry_run:
        planned = build_output_paths(output_dir, out_prefix, slides, use_png)
        logger.info("Dry-run — no files written. Planned output:")
        for p in planned:
            logger.info("  %s", p)
        return []

    saved = export_tiles(tiles, output_dir, out_prefix, quality, use_png)
    return saved
