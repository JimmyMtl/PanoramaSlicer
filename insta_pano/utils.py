"""Utility helpers: validation, logging setup, path management."""

import logging
from pathlib import Path

from insta_pano.config import MAX_SLIDES, MIN_SLIDES, SUPPORTED_INPUT_EXTENSIONS


def setup_logging(verbose: bool = False) -> logging.Logger:
    """Configure root logger and return the package logger.

    Args:
        verbose: If True, set level to DEBUG; otherwise INFO.

    Returns:
        Configured logger for the insta_pano package.
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(format="%(levelname)s: %(message)s", level=level)
    return logging.getLogger("insta_pano")


def validate_input_path(path: str) -> Path:
    """Validate that the input path exists, is a file, and has a supported extension.

    Args:
        path: Raw path string from CLI.

    Returns:
        Resolved Path object.

    Raises:
        FileNotFoundError: If the path does not exist.
        ValueError: If the path is not a file or has an unsupported extension.
    """
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    if not p.is_file():
        raise ValueError(f"Input path is not a file: {path}")
    if p.suffix.lower() not in SUPPORTED_INPUT_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_INPUT_EXTENSIONS))
        raise ValueError(
            f"Unsupported file extension '{p.suffix}'. Supported: {supported}"
        )
    return p


def validate_slide_count(slides: int) -> None:
    """Assert slide count is within allowed range.

    Args:
        slides: Requested number of slides.

    Raises:
        ValueError: If slides is outside [MIN_SLIDES, MAX_SLIDES].
    """
    if not (MIN_SLIDES <= slides <= MAX_SLIDES):
        raise ValueError(
            f"Slide count must be between {MIN_SLIDES} and {MAX_SLIDES}, got {slides}."
        )


def validate_quality(quality: int) -> None:
    """Assert JPEG quality is in [1, 100].

    Args:
        quality: Requested JPEG quality.

    Raises:
        ValueError: If quality is outside [1, 100].
    """
    if not (1 <= quality <= 100):
        raise ValueError(f"Quality must be between 1 and 100, got {quality}.")


def ensure_output_dir(path: str) -> Path:
    """Create output directory (and parents) if it does not exist.

    Args:
        path: Desired output directory path.

    Returns:
        Resolved Path to the output directory.
    """
    p = Path(path).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def validate_zoom(zoom: float) -> None:
    """Assert zoom factor is in [1.0, 5.0].

    Args:
        zoom: Requested zoom multiplier.

    Raises:
        ValueError: If zoom is outside [1.0, 5.0].
    """
    if not (1.0 <= zoom <= 5.0):
        raise ValueError(f"Zoom must be between 1.0 and 5.0, got {zoom}.")


def validate_crop_offset(value: float, name: str = "crop offset") -> None:
    """Assert a crop offset is in [0.0, 1.0].

    Args:
        value: Crop offset (0.0 = left/top, 1.0 = right/bottom).
        name: Human-readable field name for error messages.

    Raises:
        ValueError: If value is outside [0.0, 1.0].
    """
    if not (0.0 <= value <= 1.0):
        raise ValueError(f"{name} must be between 0.0 and 1.0, got {value}.")


def suggest_slide_count(
    img_width: int,
    img_height: int,
    slide_width: int,
    slide_height: int,
) -> int:
    """Suggest the optimal number of slides based on image aspect ratio.

    The heuristic finds how many slide-sized tiles best fill the image width
    at the target slide height, clamped to [MIN_SLIDES, MAX_SLIDES].

    Args:
        img_width: Source image width in pixels.
        img_height: Source image height in pixels.
        slide_width: Target slide width in pixels.
        slide_height: Target slide height in pixels.

    Returns:
        Suggested slide count.
    """
    img_ratio = img_width / img_height
    slide_ratio = slide_width / slide_height
    suggested = round(img_ratio / slide_ratio)
    return max(MIN_SLIDES, min(MAX_SLIDES, suggested))
