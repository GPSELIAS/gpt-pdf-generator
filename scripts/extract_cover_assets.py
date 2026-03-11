from __future__ import annotations

from pathlib import Path
from PIL import Image


def _bbox_from_mask(mask) -> tuple[int, int, int, int] | None:
    bbox = mask.getbbox()
    if not bbox:
        return None
    x0, y0, x1, y1 = bbox
    if x1 - x0 < 10 or y1 - y0 < 10:
        return None
    return x0, y0, x1, y1


def _pad_bbox(bbox: tuple[int, int, int, int], pad: int, w: int, h: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = bbox
    return max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad)


def _save(im: Image.Image, bbox: tuple[int, int, int, int], out_path: Path) -> None:
    crop = im.crop(bbox)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    crop.save(out_path, format="PNG", optimize=True)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    src = root / "CoverTemplate.png"
    if not src.exists():
        raise SystemExit(f"Missing source image: {src}")

    im = Image.open(src).convert("RGB")
    w, h = im.size

    # 1) GPS logo (dark circular mark) in top-left quadrant
    tl = im.crop((0, 0, int(w * 0.40), int(h * 0.18)))
    gray = tl.convert("L")
    # dark pixels
    mask = gray.point(lambda p: 255 if p < 70 else 0)
    bbox = _bbox_from_mask(mask)
    if not bbox:
        raise SystemExit("Could not detect GPS logo bbox")
    bbox = _pad_bbox(bbox, pad=18, w=tl.size[0], h=tl.size[1])
    gps_bbox = bbox  # relative to tl
    gps_bbox_abs = (gps_bbox[0], gps_bbox[1], gps_bbox[2], gps_bbox[3])
    _save(im, gps_bbox_abs, root / "templates" / "assets" / "gpsholding.png")

    # 2) Tower (dominant orange pixels) in full page
    # Orange-ish: high R, medium-high G, lower B
    r, g, b = im.split()
    orange_mask = Image.eval(r, lambda p: 255 if p > 190 else 0)
    # refine with G and B by combining masks
    g_mask = Image.eval(g, lambda p: 255 if 80 < p < 240 else 0)
    b_mask = Image.eval(b, lambda p: 255 if p < 210 else 0)
    mask = Image.merge("RGB", (orange_mask, g_mask, b_mask)).convert("L").point(lambda p: 255 if p > 20 else 0)
    bbox = _bbox_from_mask(mask)
    if not bbox:
        raise SystemExit("Could not detect tower bbox")
    bbox = _pad_bbox(bbox, pad=10, w=w, h=h)
    _save(im, bbox, root / "templates" / "assets" / "turm.png")

    # 3) Nister header image (navy strokes/text) around center band
    # Use a tighter band-focused region and a dark-pixel mask (more robust with the orange overlay)
    region = (int(w * 0.12), int(h * 0.30), int(w * 0.88), int(h * 0.70))
    mid = im.crop(region)
    gray = mid.convert("L")
    # dark strokes/text
    mask = gray.point(lambda p: 255 if p < 85 else 0)

    bbox = _bbox_from_mask(mask)
    if not bbox:
        raise SystemExit("Could not detect Nister header bbox")
    bbox = _pad_bbox(bbox, pad=36, w=mid.size[0], h=mid.size[1])

    # Convert to absolute bbox on original
    x0, y0, x1, y1 = bbox
    rx0, ry0, _, _ = region
    bbox_abs = (rx0 + x0, ry0 + y0, rx0 + x1, ry0 + y1)
    _save(im, bbox_abs, root / "templates" / "assets" / "Nisterheaderbild.png")

    print("Wrote:")
    print(" - templates/assets/gpsholding.png")
    print(" - templates/assets/turm.png")
    print(" - templates/assets/Nisterheaderbild.png")


if __name__ == "__main__":
    main()

