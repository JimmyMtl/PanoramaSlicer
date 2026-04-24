"""Unit tests for processor.py image logic."""

import tempfile
from pathlib import Path

import pytest
from PIL import Image

from insta_pano.processor import (
    add_debug_overlay,
    center_crop,
    export_tiles,
    prepare_canvas,
    resize_cover,
    slice_canvas,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def solid_image(w: int, h: int, color: tuple = (100, 150, 200)) -> Image.Image:
    return Image.new("RGB", (w, h), color=color)


def gradient_image(w: int, h: int) -> Image.Image:
    """Image where pixel (x, y) = (x % 256, y % 256, 0)."""
    img = Image.new("RGB", (w, h))
    pixels = img.load()
    for x in range(w):
        for y in range(h):
            pixels[x, y] = (x % 256, y % 256, 0)
    return img


# ---------------------------------------------------------------------------
# resize_cover
# ---------------------------------------------------------------------------

class TestResizeCover:
    def test_covers_target_dimensions(self):
        img = solid_image(800, 600)
        result = resize_cover(img, 1080, 1350)
        assert result.width >= 1080
        assert result.height >= 1350

    def test_wide_source_covers_wide_canvas(self):
        img = solid_image(4000, 800)
        result = resize_cover(img, 4320, 1350)
        assert result.width >= 4320
        assert result.height >= 1350

    def test_small_source_scaled_up(self):
        img = solid_image(100, 100)
        result = resize_cover(img, 1080, 1080)
        assert result.size == (1080, 1080)

    def test_aspect_ratio_preserved(self):
        img = solid_image(1000, 500)
        result = resize_cover(img, 2000, 600)
        original_ratio = img.width / img.height
        result_ratio = result.width / result.height
        assert abs(result_ratio - original_ratio) < 0.02

    def test_no_distortion_portrait(self):
        img = solid_image(3000, 4000)
        result = resize_cover(img, 1080, 1350)
        assert result.width >= 1080 and result.height >= 1350
        original_ratio = img.width / img.height
        result_ratio = result.width / result.height
        assert abs(result_ratio - original_ratio) < 0.02


# ---------------------------------------------------------------------------
# center_crop
# ---------------------------------------------------------------------------

class TestCenterCrop:
    def test_output_size_exact(self):
        img = solid_image(2000, 2000)
        result = center_crop(img, 1080, 1080)
        assert result.size == (1080, 1080)

    def test_portrait_crop(self):
        img = solid_image(3000, 2000)
        result = center_crop(img, 1080, 1350)
        assert result.size == (1080, 1350)

    def test_identity_when_same_size(self):
        img = solid_image(1080, 1350)
        result = center_crop(img, 1080, 1350)
        assert result.size == (1080, 1350)

    def test_center_pixel_matches(self):
        img = gradient_image(300, 200)
        result = center_crop(img, 100, 100)
        # Center of 300×200 = (150, 100); crop top-left = (100, 50)
        assert result.getpixel((0, 0)) == img.getpixel((100, 50))


# ---------------------------------------------------------------------------
# prepare_canvas
# ---------------------------------------------------------------------------

class TestPrepareCanvas:
    def test_canvas_size_portrait_3_slides(self):
        img = solid_image(5000, 1000)
        canvas = prepare_canvas(img, 3, 1080, 1350)
        assert canvas.size == (3 * 1080, 1350)

    def test_canvas_size_square_4_slides(self):
        img = solid_image(800, 600)
        canvas = prepare_canvas(img, 4, 1080, 1080)
        assert canvas.size == (4 * 1080, 1080)

    def test_canvas_size_portrait_2_slides(self):
        img = solid_image(400, 400)
        canvas = prepare_canvas(img, 2, 1080, 1350)
        assert canvas.size == (2 * 1080, 1350)

    def test_canvas_size_max_slides(self):
        img = solid_image(10000, 800)
        canvas = prepare_canvas(img, 10, 1080, 1350)
        assert canvas.size == (10 * 1080, 1350)


# ---------------------------------------------------------------------------
# slice_canvas
# ---------------------------------------------------------------------------

class TestSliceCanvas:
    def test_correct_tile_count(self):
        canvas = solid_image(3 * 1080, 1350)
        tiles = slice_canvas(canvas, 3, 1080)
        assert len(tiles) == 3

    def test_tile_dimensions_portrait(self):
        canvas = solid_image(4 * 1080, 1350)
        tiles = slice_canvas(canvas, 4, 1080)
        for tile in tiles:
            assert tile.size == (1080, 1350)

    def test_tile_dimensions_square(self):
        canvas = solid_image(5 * 1080, 1080)
        tiles = slice_canvas(canvas, 5, 1080)
        for tile in tiles:
            assert tile.size == (1080, 1080)

    def test_tiles_cover_full_width(self):
        w, h = 200, 50
        canvas = gradient_image(w, h)
        tiles = slice_canvas(canvas, 2, 100)
        # tile[0] left edge == canvas x=0
        assert tiles[0].getpixel((0, 0)) == canvas.getpixel((0, 0))
        # tile[1] left edge == canvas x=100
        assert tiles[1].getpixel((0, 0)) == canvas.getpixel((100, 0))

    def test_tile_boundary_continuity(self):
        """Right edge of tile N must be adjacent to left edge of tile N+1."""
        canvas = gradient_image(300, 50)
        tiles = slice_canvas(canvas, 3, 100)
        # Rightmost col of tile 0 (x=99 in canvas) == tile 1's leftmost (x=100 in canvas)
        assert tiles[0].getpixel((99, 10)) == canvas.getpixel((99, 10))
        assert tiles[1].getpixel((0, 10)) == canvas.getpixel((100, 10))
        assert tiles[2].getpixel((0, 10)) == canvas.getpixel((200, 10))

    def test_no_overlap_or_gap(self):
        """All pixels from original canvas appear exactly once across tiles."""
        canvas = gradient_image(300, 10)
        tiles = slice_canvas(canvas, 3, 100)
        reconstructed = Image.new("RGB", (300, 10))
        for i, tile in enumerate(tiles):
            reconstructed.paste(tile, (i * 100, 0))
        assert list(reconstructed.get_flattened_data()) == list(canvas.get_flattened_data())


# ---------------------------------------------------------------------------
# add_debug_overlay
# ---------------------------------------------------------------------------

class TestAddDebugOverlay:
    def test_output_size_unchanged(self):
        canvas = solid_image(3 * 1080, 1350)
        result = add_debug_overlay(canvas, 3, 1080)
        assert result.size == canvas.size

    def test_boundary_pixel_is_red(self):
        canvas = solid_image(2 * 100, 50, color=(0, 0, 0))
        result = add_debug_overlay(canvas, 2, 100)
        r, g, b = result.getpixel((100, 25))
        assert r == 255 and g == 0 and b == 0

    def test_original_not_mutated(self):
        canvas = solid_image(200, 50, color=(50, 50, 50))
        _ = add_debug_overlay(canvas, 2, 100)
        assert canvas.getpixel((100, 25)) == (50, 50, 50)


# ---------------------------------------------------------------------------
# export_tiles (integration: write to temp dir)
# ---------------------------------------------------------------------------

class TestExportTiles:
    def test_files_created(self):
        tiles = [solid_image(1080, 1350) for _ in range(3)]
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = export_tiles(tiles, Path(tmpdir), "test", quality=85)
            assert len(paths) == 3
            for p in paths:
                assert p.exists()

    def test_filenames_sequential(self):
        tiles = [solid_image(1080, 1350) for _ in range(3)]
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = export_tiles(tiles, Path(tmpdir), "slide", quality=85)
            names = [p.name for p in paths]
            assert names == ["slide_01.jpg", "slide_02.jpg", "slide_03.jpg"]

    def test_png_export(self):
        tiles = [solid_image(1080, 1080) for _ in range(2)]
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = export_tiles(tiles, Path(tmpdir), "tile", quality=95, use_png=True)
            for p in paths:
                assert p.suffix == ".png"
                assert p.exists()

    def test_jpeg_dimensions_preserved(self):
        tiles = [solid_image(1080, 1350)]
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = export_tiles(tiles, Path(tmpdir), "check", quality=95)
            reopened = Image.open(paths[0])
            assert reopened.size == (1080, 1350)


# ---------------------------------------------------------------------------
# Full pipeline smoke test with a real temporary image
# ---------------------------------------------------------------------------

class TestFullPipeline:
    def test_end_to_end_portrait(self):
        """Process a synthetic wide image and verify output tile dimensions."""
        from insta_pano.processor import process_image

        src = gradient_image(3840, 720)
        with tempfile.TemporaryDirectory() as tmpdir:
            src_path = Path(tmpdir) / "source.jpg"
            src.save(src_path, quality=95)

            out_dir = Path(tmpdir) / "out"
            out_dir.mkdir()

            saved = process_image(
                input_path=src_path,
                output_dir=out_dir,
                slides=3,
                slide_dims=(1080, 1350),
                quality=90,
            )

            assert len(saved) == 3
            for p in saved:
                img = Image.open(p)
                assert img.size == (1080, 1350)

    def test_dry_run_writes_nothing(self):
        from insta_pano.processor import process_image

        src = solid_image(2160, 1080)
        with tempfile.TemporaryDirectory() as tmpdir:
            src_path = Path(tmpdir) / "wide.png"
            src.save(src_path)

            out_dir = Path(tmpdir) / "out"
            out_dir.mkdir()

            result = process_image(
                input_path=src_path,
                output_dir=out_dir,
                slides=2,
                slide_dims=(1080, 1080),
                quality=90,
                dry_run=True,
            )

            assert result == []
            assert list(out_dir.iterdir()) == []
