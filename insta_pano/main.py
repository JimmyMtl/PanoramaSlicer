"""CLI entrypoint for insta_pano."""

import argparse
import logging
import sys
from pathlib import Path

from insta_pano.config import (
    DEFAULT_FORMAT,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_QUALITY,
    DEFAULT_SLIDES,
    FORMATS,
    MAX_SLIDES,
    MIN_SLIDES,
    SUPPORTED_INPUT_EXTENSIONS,
)
from insta_pano.processor import load_image, process_image
from insta_pano.utils import (
    ensure_output_dir,
    setup_logging,
    suggest_slide_count,
    validate_crop_offset,
    validate_input_path,
    validate_quality,
    validate_slide_count,
    validate_zoom,
)


def build_parser() -> argparse.ArgumentParser:
    """Construct and return the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="insta_pano",
        description=(
            "Generate Instagram panorama carousels by splitting a wide image "
            "into perfectly aligned slides."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--input",
        metavar="PATH",
        help="Path to the source image (jpg/png/webp/bmp/tiff).",
    )
    input_group.add_argument(
        "--input-dir",
        metavar="DIR",
        dest="input_dir",
        help="Process every supported image in a directory (batch mode).",
    )

    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_DIR,
        metavar="DIR",
        help="Output directory for slide images.",
    )
    parser.add_argument(
        "--slides",
        type=int,
        default=DEFAULT_SLIDES,
        metavar=f"{MIN_SLIDES}-{MAX_SLIDES}",
        help="Number of carousel slides.",
    )
    parser.add_argument(
        "--format",
        choices=list(FORMATS.keys()),
        default=DEFAULT_FORMAT,
        help="Slide aspect ratio format.",
    )
    parser.add_argument(
        "--quality",
        type=int,
        default=DEFAULT_QUALITY,
        metavar="1-100",
        help="JPEG export quality (ignored with --png).",
    )
    parser.add_argument(
        "--prefix",
        default=None,
        metavar="NAME",
        help="Custom output filename prefix (default: input filename stem).",
    )
    parser.add_argument(
        "--crop-x",
        type=float,
        default=0.5,
        metavar="0.0-1.0",
        dest="crop_x",
        help="Horizontal crop anchor: 0.0 = left edge, 0.5 = center, 1.0 = right edge.",
    )
    parser.add_argument(
        "--crop-y",
        type=float,
        default=0.5,
        metavar="0.0-1.0",
        dest="crop_y",
        help="Vertical crop anchor: 0.0 = top edge, 0.5 = center, 1.0 = bottom edge.",
    )
    parser.add_argument(
        "--zoom",
        type=float,
        default=1.0,
        metavar="1.0-5.0",
        help="Extra zoom multiplier on top of cover-resize (1.0 = no extra zoom).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned operations without writing any files.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Draw red slice-boundary lines on output tiles.",
    )
    parser.add_argument(
        "--png",
        action="store_true",
        help="Export tiles as lossless PNG instead of JPEG.",
    )
    parser.add_argument(
        "--suggest",
        action="store_true",
        help="Print the suggested slide count for the input image and exit.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    return parser


def _process_single(
    input_path: Path,
    output_dir: Path,
    args: argparse.Namespace,
    logger: logging.Logger,
) -> None:
    """Run the pipeline for one image file."""
    slide_dims = FORMATS[args.format]

    if args.suggest:
        img = load_image(input_path)
        suggestion = suggest_slide_count(img.width, img.height, *slide_dims)
        logger.info(
            "Suggested slides for %d×%d image with '%s' format: %d",
            img.width, img.height, args.format, suggestion,
        )
        return

    saved = process_image(
        input_path=input_path,
        output_dir=output_dir,
        slides=args.slides,
        slide_dims=slide_dims,
        quality=args.quality,
        prefix=args.prefix,
        dry_run=args.dry_run,
        debug_overlay=args.debug,
        use_png=args.png,
        crop_x=args.crop_x,
        crop_y=args.crop_y,
        zoom=args.zoom,
    )

    if saved:
        logger.info(
            "Done. %d slide%s written to '%s'.",
            len(saved), "s" if len(saved) != 1 else "", output_dir,
        )


def main() -> None:
    """Parse arguments and run the carousel generation pipeline."""
    parser = build_parser()
    args = parser.parse_args()

    logger = setup_logging(args.verbose)

    try:
        validate_slide_count(args.slides)
        validate_quality(args.quality)
        validate_crop_offset(args.crop_x, "--crop-x")
        validate_crop_offset(args.crop_y, "--crop-y")
        validate_zoom(args.zoom)

        output_dir = ensure_output_dir(args.output)

        if args.input_dir:
            input_dir = Path(args.input_dir).resolve()
            if not input_dir.is_dir():
                logger.error("Input directory not found: %s", args.input_dir)
                sys.exit(1)
            image_paths = sorted(
                p for p in input_dir.iterdir()
                if p.is_file() and p.suffix.lower() in SUPPORTED_INPUT_EXTENSIONS
            )
            if not image_paths:
                logger.error(
                    "No supported images found in '%s'. Supported: %s",
                    input_dir, ", ".join(sorted(SUPPORTED_INPUT_EXTENSIONS)),
                )
                sys.exit(1)
            logger.info("Batch mode: %d image(s) in '%s'", len(image_paths), input_dir)
            errors = 0
            for path in image_paths:
                try:
                    _process_single(path, output_dir, args, logger)
                except (FileNotFoundError, ValueError, IOError) as exc:
                    logger.error("[%s] %s", path.name, exc)
                    errors += 1
            if errors:
                logger.error("%d file(s) failed.", errors)
                sys.exit(1)
        else:
            input_path = validate_input_path(args.input)
            if args.suggest:
                slide_dims = FORMATS[args.format]
                img = load_image(input_path)
                suggestion = suggest_slide_count(img.width, img.height, *slide_dims)
                logger.info(
                    "Suggested slides for %d×%d image with '%s' format: %d",
                    img.width, img.height, args.format, suggestion,
                )
                sys.exit(0)
            _process_single(input_path, output_dir, args, logger)

    except (FileNotFoundError, ValueError, IOError) as exc:
        logger.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
