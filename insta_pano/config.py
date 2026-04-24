"""Constants and default configuration for insta_pano."""

from typing import Dict, Set, Tuple

FORMATS: Dict[str, Tuple[int, int]] = {
    "portrait": (1080, 1350),
    "square": (1080, 1080),
}

DEFAULT_SLIDES: int = 3
DEFAULT_FORMAT: str = "portrait"
DEFAULT_QUALITY: int = 95
DEFAULT_OUTPUT_DIR: str = "./output"

MIN_SLIDES: int = 2
MAX_SLIDES: int = 10

SUPPORTED_INPUT_EXTENSIONS: Set[str] = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
