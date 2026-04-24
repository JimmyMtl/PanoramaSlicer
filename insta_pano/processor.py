"""Core image processing pipeline for panorama carousel generation."""

import logging
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
