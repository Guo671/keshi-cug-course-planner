"""Generate the deterministic Keshi PNG and multi-resolution Windows icon."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _draw_master(size: int = 1024) -> Image.Image:
    scale = size / 1024

    def box(left: int, top: int, right: int, bottom: int) -> tuple[int, int, int, int]:
        return (
            round(left * scale),
            round(top * scale),
            round(right * scale),
            round(bottom * scale),
        )

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # A rounded, deliberately asymmetric ore silhouette stays legible at 16 px.
    ore_points = [
        (224, 82),
        (365, 47),
        (512, 62),
        (664, 48),
        (789, 105),
        (886, 192),
        (943, 319),
        (951, 466),
        (928, 620),
        (875, 766),
        (782, 875),
        (650, 939),
        (500, 961),
        (345, 940),
        (214, 884),
        (119, 792),
        (67, 663),
        (51, 513),
        (71, 363),
        (117, 222),
        (163, 134),
    ]
    scaled_ore = [tuple(round(value * scale) for value in point) for point in ore_points]
    draw.polygon(scaled_ore, fill="#2d7666")
    draw.line(
        [*scaled_ore, scaled_ore[0]],
        fill="#2d7666",
        width=max(12, round(54 * scale)),
        joint="curve",
    )

    draw.rounded_rectangle(box(164, 190, 861, 828), radius=round(88 * scale), fill="#fbf7ef")
    draw.rounded_rectangle(box(164, 190, 861, 347), radius=round(76 * scale), fill="#173f37")
    draw.rectangle(box(164, 270, 861, 347), fill="#173f37")
    for x in (313, 712):
        draw.rounded_rectangle(
            box(x - 34, 128, x + 34, 255),
            radius=round(33 * scale),
            fill="#efb36b",
        )

    # Course-grid motif: three neutral periods and one selected orange class.
    grid_color = "#d9e9e4"
    for row in range(2):
        for column in range(3):
            left = 226 + column * 193
            top = 420 + row * 164
            draw.rounded_rectangle(
                box(left, top, left + 134, top + 105),
                radius=round(25 * scale),
                fill=grid_color,
            )
    draw.rounded_rectangle(box(419, 584, 553, 689), radius=round(25 * scale), fill="#efb36b")

    # Check mark doubles as a geological pick-like diagonal without tiny lettering.
    check_points = ((664, 681), (718, 737), (824, 597))
    draw.line(
        [tuple(round(value * scale) for value in point) for point in check_points],
        fill="#173f37",
        width=max(10, round(42 * scale)),
        joint="curve",
    )
    return image


def generate(output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    master = _draw_master()
    png_path = output_dir / "app.png"
    ico_path = output_dir / "app.ico"
    master.save(png_path, format="PNG", optimize=True)
    master.save(ico_path, format="ICO", sizes=[(size, size) for size in SIZES])
    return png_path, ico_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "assets",
    )
    arguments = parser.parse_args()
    png_path, ico_path = generate(arguments.output_dir)
    print(f"generated {png_path}")
    print(f"generated {ico_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
