"""Генератор PWA-иконок.

Создаёт:
- icon-{size}.png для всех нужных PWA-размеров (72, 96, 128, 144, 152, 180, 192, 384, 512)
- icon-maskable-{size}.png для maskable-покрытия (Android adaptive icons)
- favicon.png (32×32)

Стиль: тёмный брендовый круг с буквой S в акцентном зелёном.

Запуск:
    .venv/Scripts/python.exe -m app.generate_icons
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).resolve().parent / "static" / "icons"
OUT_DIR.mkdir(parents=True, exist_ok=True)

BRAND = (31, 41, 55)      # #1f2937
ACCENT = (16, 185, 129)   # #10b981
WHITE = (255, 255, 255)

STANDARD_SIZES = [72, 96, 128, 144, 152, 180, 192, 384, 512]
MASKABLE_SIZES = [192, 512]


def _best_font(size: int) -> ImageFont.ImageFont:
    """Пробуем взять красивый системный TTF, иначе дефолтный."""
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for p in candidates:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _render_icon(size: int, maskable: bool = False) -> Image.Image:
    """Рендерит одну иконку.

    maskable=True — рисуем с большим «safe zone» (padding 20%) чтобы Android
    мог подрезать углы под маску не повредив логотип.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if maskable:
        # Для maskable — заливка всего квадрата фоном, логотип в центре занимает 60%
        draw.rectangle([0, 0, size, size], fill=BRAND)
        inner_pad = size * 0.2
        logo_size = size - inner_pad * 2
    else:
        # Обычная — круг на всю иконку
        draw.ellipse([2, 2, size - 2, size - 2], fill=BRAND)
        inner_pad = 0
        logo_size = size

    # Акцентный маленький кружок (бренд-марк) сверху слева
    mark_r = logo_size * 0.07
    mark_cx = size / 2 - logo_size * 0.22
    mark_cy = size / 2 - logo_size * 0.22
    draw.ellipse(
        [mark_cx - mark_r, mark_cy - mark_r, mark_cx + mark_r, mark_cy + mark_r],
        fill=ACCENT,
    )

    # Буква S
    letter = "S"
    font_size = int(logo_size * 0.55)
    font = _best_font(font_size)

    # Центрируем букву
    bbox = draw.textbbox((0, 0), letter, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (size - tw) / 2 - bbox[0]
    ty = (size - th) / 2 - bbox[1] + logo_size * 0.02  # чуть ниже центра оптически

    draw.text((tx, ty), letter, font=font, fill=WHITE)

    return img


def generate_all() -> dict:
    created = []

    for size in STANDARD_SIZES:
        img = _render_icon(size, maskable=False)
        path = OUT_DIR / f"icon-{size}.png"
        img.save(path, "PNG", optimize=True)
        created.append(path.name)

    for size in MASKABLE_SIZES:
        img = _render_icon(size, maskable=True)
        path = OUT_DIR / f"icon-maskable-{size}.png"
        img.save(path, "PNG", optimize=True)
        created.append(path.name)

    # Favicon 32×32
    fav = _render_icon(32, maskable=False)
    fav_path = OUT_DIR / "favicon.png"
    fav.save(fav_path, "PNG", optimize=True)
    created.append(fav_path.name)

    return {"count": len(created), "files": created, "dir": str(OUT_DIR)}


if __name__ == "__main__":
    result = generate_all()
    print(f"[ok] Сгенерировано {result['count']} иконок в {result['dir']}")
    for f in result["files"]:
        print(f"  - {f}")
