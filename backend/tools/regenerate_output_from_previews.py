#!/usr/bin/env python3
from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

import config as cfg  # noqa: E402

OUT_DIR = ROOT_DIR / str(getattr(cfg, "BIN_OUTPUT_DIR", "output") or "output")
LCD_W = int(getattr(cfg, "LCD_WIDTH", 800) or 800)
LCD_H = int(getattr(cfg, "LCD_HEIGHT", 480) or 480)


def render_landscape_from_preview(src: Path) -> Image.Image:
    img = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    img_w, img_h = img.size

    canvas = Image.new("RGB", (LCD_W, LCD_H), (0, 0, 0))

    bg_scale = max(LCD_W / img_w, LCD_H / img_h)
    bg_w, bg_h = max(1, int(img_w * bg_scale)), max(1, int(img_h * bg_scale))
    bg = img.resize((bg_w, bg_h), Image.LANCZOS)
    bg_left = (bg_w - LCD_W) // 2
    bg_top = (bg_h - LCD_H) // 2
    bg = bg.crop((bg_left, bg_top, bg_left + LCD_W, bg_top + LCD_H))
    canvas.paste(bg.filter(ImageFilter.GaussianBlur(radius=30)), (0, 0))

    fg_scale = min(LCD_W / img_w, LCD_H / img_h)
    fg_w, fg_h = max(1, int(img_w * fg_scale)), max(1, int(img_h * fg_scale))
    fg = img.resize((fg_w, fg_h), Image.LANCZOS)
    canvas.paste(fg, ((LCD_W - fg_w) // 2, (LCD_H - fg_h) // 2))
    return canvas


def image_to_rgb565(img: Image.Image) -> bytes:
    img = img.convert("RGB")
    if img.size != (LCD_W, LCD_H):
        raise RuntimeError(f"unexpected image size: {img.size}, expected {(LCD_W, LCD_H)}")

    data = bytearray(LCD_W * LCD_H * 2)
    idx = 0
    pixels = img.load()
    for y in range(LCD_H):
        for x in range(LCD_W):
            r, g, b = pixels[x, y]
            val = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
            data[idx] = val & 0xFF
            data[idx + 1] = (val >> 8) & 0xFF
            idx += 2
    return bytes(data)


def main() -> None:
    previews = sorted(OUT_DIR.glob("preview_*.jpg"))
    if not previews:
        raise SystemExit(f"No preview_*.jpg files found in {OUT_DIR}")

    first_rgb565: Path | None = None
    first_preview: Path | None = None

    for preview in previews:
        photo_id = preview.stem.removeprefix("preview_")
        img = render_landscape_from_preview(preview)

        rgb565_path = OUT_DIR / f"photo_{photo_id}.rgb565"
        preview_path = OUT_DIR / f"preview_{photo_id}.jpg"

        rgb565_path.write_bytes(image_to_rgb565(img))
        img.save(preview_path, "JPEG", quality=90)

        if first_rgb565 is None:
            first_rgb565 = rgb565_path
            first_preview = preview_path

        print(f"[OK] {rgb565_path.name} -> {LCD_W}x{LCD_H}, {rgb565_path.stat().st_size} bytes")

    if first_rgb565 and first_preview:
        shutil.copyfile(first_rgb565, OUT_DIR / "latest.rgb565")
        shutil.copyfile(first_preview, OUT_DIR / "preview.jpg")
        print("[OK] latest.rgb565 / preview.jpg updated")


if __name__ == "__main__":
    main()
