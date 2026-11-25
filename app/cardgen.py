"""Live signal / result pictures: a static background (media/bg/*.jpg, drawn by tools/render_cards.py) plus a product
card drawn here with the real data — pair, BinoDex payout, direction, expiry, "entry until" time, and a chart of the
pair's actual recent BinoDex prices (results: the path from entry to expiry with the entry level).

Pillow only, no browser on the server. ~30 ms per picture.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONTS = Path(__file__).resolve().parent / "fonts"
BG = ROOT / "media" / "bg"

LIME, RED = (198, 255, 61), (255, 92, 92)
WHITE, MUTED, SUB = (255, 255, 255), (125, 131, 140), (154, 160, 168)
CARD_TOP, CARD_BOT = (28, 30, 35), (16, 17, 20)
S = 2                                    # draw the card at 2x, then downscale: smooth edges


@lru_cache(maxsize=None)
def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / f"{name}.ttf"), size * S)


def _gradient(w: int, h: int, top, bottom) -> Image.Image:
    g = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        g.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return g.resize((w, h))


def _rounded(w: int, h: int, r: int) -> Image.Image:
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w - 1, h - 1), r, fill=255)
    return m


def _chart(draw: ImageDraw.ImageDraw, img: Image.Image, prices: list[float], box, color, entry: float | None = None):
    x0, y0, x1, y1 = box
    pts = [p for p in prices if p is not None]
    if len(pts) < 2:
        return
    lo, hi = min(pts + ([entry] if entry is not None else [])), max(pts + ([entry] if entry is not None else []))
    pad = (hi - lo) * 0.12 or abs(hi) * 1e-5 or 1e-5
    lo, hi = lo - pad, hi + pad
    X = lambda i: x0 + (x1 - x0) * i / (len(pts) - 1)               # noqa: E731
    Y = lambda v: y1 - (y1 - y0) * (v - lo) / (hi - lo)            # noqa: E731
    line = [(X(i), Y(v)) for i, v in enumerate(pts)]
    # area under the line: the line colour fading to transparent downwards
    poly = Image.new("L", img.size, 0)
    ImageDraw.Draw(poly).polygon(line + [(x1, y1), (x0, y1)], fill=255)
    fade = Image.new("L", img.size, 0)
    fade.paste(_gradient(1, int(y1 - y0), (95, 95, 95), (0, 0, 0)).convert("L").resize((img.size[0], int(y1 - y0))),
               (0, int(y0)))
    layer = Image.new("RGBA", img.size, (*color, 0))
    layer.putalpha(ImageChops.multiply(poly, fade))
    img.alpha_composite(layer)
    if entry is not None:
        ey = Y(entry)
        x = x0
        while x < x1:
            draw.line((x, ey, min(x + 12 * S / 2, x1), ey), fill=MUTED, width=2 * S)
            x += 26 * S / 2
        draw.text((x1, ey - 8 * S), "уровень входа", font=font("JetBrainsMono-500", 14), fill=MUTED, anchor="rs")
    draw.line(line, fill=color, width=4 * S, joint="curve")
    ex, ey2 = line[-1]
    r = 7 * S
    draw.ellipse((ex - r, ey2 - r, ex + r, ey2 + r), fill=color)


def _cell(draw, x, y, w, h, label, value):
    draw.rounded_rectangle((x, y, x + w, y + h), 14 * S, fill=(255, 255, 255, 10))
    draw.text((x + 14 * S, y + 12 * S), label, font=font("InterTight-500", 17), fill=MUTED)
    draw.text((x + 14 * S, y + 38 * S), value, font=font("JetBrainsMono-700", 22), fill=WHITE)


def _card(width: int, height: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    w, h = width * S, height * S
    card = _gradient(w, h, CARD_TOP, CARD_BOT).convert("RGBA")
    card.putalpha(_rounded(w, h, 30 * S))
    d = ImageDraw.Draw(card)
    d.rounded_rectangle((0, 0, w - 1, h - 1), 30 * S, outline=(255, 255, 255, 24), width=S)
    return card, d


def _place(bg: Image.Image, card: Image.Image, left: int, top: int, angle: float) -> Image.Image:
    card = card.resize((card.width // S, card.height // S), Image.LANCZOS).rotate(angle, resample=Image.BICUBIC, expand=True)
    shadow = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    sh = Image.new("RGBA", card.size, (0, 0, 0, 150))
    sh.putalpha(Image.eval(card.getchannel("A"), lambda a: int(a * 0.6)))
    shadow.alpha_composite(sh, (left, top + 34))
    shadow = shadow.filter(ImageFilter.GaussianBlur(34))
    out = bg.convert("RGBA")
    out.alpha_composite(shadow)
    out.alpha_composite(card, (left, top))
    return out


def _label(symbol: str, is_otc: bool) -> str:
    return symbol + (" OTC" if is_otc and "OTC" not in symbol.upper() else "")


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=90, optimize=True)
    return buf.getvalue()


def _fmt(price: float, digits: int | None) -> str:
    return f"{price:.{digits}f}" if digits is not None else f"{price:.6g}"


def signal_picture(*, symbol: str, is_otc: bool, direction: str, payout: int, expiry_sec: int, until_ts: float,
                   tz: str, prices: list[float], strong: bool = False, strength_word: str = "") -> bytes:
    up = direction == "up"
    color = LIME if up else RED
    bg = Image.open(BG / ("strong.jpg" if strong else f"signal_{direction}.jpg"))
    W, H = 420, 470
    card, d = _card(W, H)
    p = 28 * S
    d.text((p, 26 * S), _label(symbol, is_otc), font=font("InterTight-700", 26), fill=WHITE)
    d.text((p, 60 * S), f"согласие индикаторов: {strength_word}" if strength_word else "сигнал Trend Radar",
           font=font("InterTight-500", 17), fill=MUTED)
    pill = f"сильный · {payout}%" if strong else f"{payout}%"
    pf = font("JetBrainsMono-700", 17)
    pw = d.textlength(pill, font=pf) + 20 * S
    d.rounded_rectangle((W * S - p - pw, 26 * S, W * S - p, 58 * S), 10 * S, fill=(198, 255, 61, 30))
    d.text((W * S - p - pw / 2, 42 * S), pill, font=pf, fill=LIME, anchor="mm")
    d.text((p, 92 * S), "↑ Вверх" if up else "↓ Вниз", font=font("InterTight-800", 56), fill=color)
    _chart(d, card, prices[-60:], (p, 178 * S, W * S - p, 318 * S), color)
    until = datetime.fromtimestamp(until_ts, timezone.utc).astimezone(ZoneInfo(tz)).strftime("%H:%M:%S")
    exp = f"{expiry_sec // 60} мин" if expiry_sec % 60 == 0 else f"{expiry_sec} с"
    cw = (W * S - 2 * p - 10 * S) // 2
    _cell(d, p, 344 * S, cw, 76 * S, "Экспирация", exp)
    _cell(d, p + cw + 10 * S, 344 * S, cw, 76 * S, "Вход до", until)
    return _jpeg(_place(bg, card, 760, 90 if not strong else 80, 3))


def result_picture(*, symbol: str, is_otc: bool, direction: str, result: str, entry: float, close: float,
                   prices: list[float], digits: int | None) -> bytes:
    win = result == "win"
    color = LIME if win else RED
    bg = Image.open(BG / ("win.jpg" if win else "loss.jpg"))
    W, H = 430, 400
    card, d = _card(W, H)
    p = 28 * S
    d.text((p, 26 * S), _label(symbol, is_otc) + (" ↑" if direction == "up" else " ↓"),
           font=font("InterTight-700", 26), fill=WHITE)
    d.text((p, 60 * S), "итог по цене BinoDex", font=font("InterTight-500", 17), fill=MUTED)
    cx, cy, r = W * S - p - 17 * S, 43 * S, 17 * S
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*color, 38))
    k = 7 * S                                       # the fonts have no ✓/✕ glyphs: draw them
    if win:
        d.line([(cx - k, cy), (cx - k / 3, cy + k * 0.7), (cx + k, cy - k * 0.7)], fill=color, width=3 * S, joint="curve")
    else:
        d.line([(cx - k * .7, cy - k * .7), (cx + k * .7, cy + k * .7)], fill=color, width=3 * S)
        d.line([(cx - k * .7, cy + k * .7), (cx + k * .7, cy - k * .7)], fill=color, width=3 * S)
    _chart(d, card, prices or [entry, close], (p, 104 * S, W * S - p, 250 * S), color, entry=entry)
    cw = (W * S - 2 * p - 10 * S) // 2
    _cell(d, p, 276 * S, cw, 76 * S, "Вход", _fmt(entry, digits))
    _cell(d, p + cw + 10 * S, 276 * S, cw, 76 * S, "Экспирация", _fmt(close, digits))
    return _jpeg(_place(bg, card, 770, 160, 2))


def day_picture(*, day: str, win: int, loss: int, refund: int, pairs: list[tuple[str, int, int]]) -> bytes:
    """Results of the day for the channel: counts, win rate and the most used pairs (wins / losses each)."""
    bg = Image.open(BG / "day.jpg")
    W, H = 430, 470
    card, d = _card(W, H)
    p = 28 * S
    d.text((p, 26 * S), f"Итоги за {day}", font=font("InterTight-700", 26), fill=WHITE)
    d.text((p, 60 * S), "сигналы Trend Radar · цена BinoDex", font=font("InterTight-500", 17), fill=MUTED)
    n = win + loss
    rate = f"{round(win / n * 100)}%" if n else "—"
    d.text((p, 96 * S), rate, font=font("InterTight-800", 64), fill=LIME if n and win >= loss else (RED if n else WHITE))
    d.text((p, 172 * S), f"в плюс: {win} из {n}" + (f" · возвратов: {refund}" if refund else ""),
           font=font("InterTight-600", 20), fill=SUB)
    cw = (W * S - 2 * p - 10 * S) // 2
    _cell(d, p, 212 * S, cw, 76 * S, "В плюс", str(win))
    _cell(d, p + cw + 10 * S, 212 * S, cw, 76 * S, "В минус", str(loss))
    y = 312 * S
    d.text((p, y), "Пары дня", font=font("InterTight-600", 18), fill=MUTED)
    y += 30 * S
    for name, w, l in pairs[:3]:
        d.text((p, y), name, font=font("InterTight-600", 20), fill=WHITE)
        d.text((W * S - p, y), f"+{w} / −{l}", font=font("JetBrainsMono-700", 18),
               fill=LIME if w >= l else RED, anchor="ra")
        y += 34 * S
    return _jpeg(_place(bg, card, 770, 110, -2))
