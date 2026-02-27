"""Generate the tray icon PNG for the HOMEPAGE Server build.

Run once:  python resources/generate_icon.py
Produces:  resources/icon.png  (64×64 RGBA)
"""
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Pillow is required: pip install Pillow")
    raise SystemExit(1)

HERE = Path(__file__).resolve().parent
SIZE = 64

img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# Background circle — indigo-600
draw.ellipse([2, 2, SIZE - 2, SIZE - 2], fill=(79, 70, 229))

# "H" letter in white
try:
    font = ImageFont.truetype("arial", 36)
except Exception:
    font = ImageFont.load_default()

bbox = draw.textbbox((0, 0), "H", font=font)
tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
draw.text(((SIZE - tw) / 2, (SIZE - th) / 2 - 2), "H", fill="white", font=font)

out = HERE / "icon.png"
img.save(out)
print(f"Saved {out}  ({SIZE}x{SIZE})")
