"""Resize the shared Virtus logo into a multi-resolution Windows icon."""
from __future__ import annotations

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "build" / "generated" / "Virtus.ico"
SOURCE = ROOT / "static" / "virtus-logo-dark.png"


def render(size: int) -> Image.Image:
    with Image.open(SOURCE) as image:
        return image.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [render(size) for size in sizes]
    images[-1].save(
        OUTPUT,
        format="ICO",
        append_images=images[:-1],
        sizes=[(size, size) for size in sizes],
    )


if __name__ == "__main__":
    main()
