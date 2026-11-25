"""Avatar options (640x640). python tools/render_avatars.py -> media/avatar_a.jpg, avatar_b.jpg, avatar_c.jpg"""
from pathlib import Path

from playwright.sync_api import sync_playwright

from render_cards import FONTS, GRAIN, LIME, RED

OUT = Path(__file__).resolve().parents[1] / "media"
BASE = f"<style>{FONTS}*{{margin:0;padding:0}}body{{width:640px;height:640px;overflow:hidden;position:relative;font-family:IT}}" \
       ".grain{position:absolute;inset:0;opacity:.14;mix-blend-mode:overlay}</style>"

# A: monogram on lime — the brand letters, readable at any size
A = BASE + f"""<body style="background:{LIME}">
<div style="position:absolute;left:0;right:0;top:150px;text-align:center;font:800 290px/1 IT;letter-spacing:-4px;color:#0a0b0d;padding-right:4px">TR</div>
<svg style="position:absolute;left:150px;top:440px" width="340" height="70" viewBox="0 0 340 70">
<path d="M10 58 L110 30 L180 46 L330 8" fill="none" stroke="#0a0b0d" stroke-width="16" stroke-linecap="round" stroke-linejoin="round"/></svg>
{GRAIN}</body>"""

# B: three rising candles — "trading signals" at a glance
def candle(x, top, h, col, wick_top, wick_bot):
    return (f'<rect x="{x+26}" y="{wick_top}" width="10" height="{wick_bot-wick_top}" rx="5" fill="{col}"/>'
            f'<rect x="{x}" y="{top}" width="62" height="{h}" rx="14" fill="{col}"/>')

B = BASE + f"""<body style="background:radial-gradient(circle at 50% 40%,#1b1f17 0%,#0a0b0d 70%)">
<div style="position:absolute;left:330px;top:-60px;width:380px;height:320px;border-radius:50%;background:{LIME};opacity:.22;filter:blur(70px)"></div>
<svg style="position:absolute;left:0;top:0" width="640" height="640" viewBox="0 0 640 640">
{candle(150, 340, 140, RED, 300, 510)}{candle(289, 240, 190, LIME, 195, 470)}{candle(428, 120, 230, LIME, 75, 395)}
<path d="M130 545 H510" stroke="rgba(255,255,255,.25)" stroke-width="6" stroke-linecap="round"/></svg>
{GRAIN}</body>"""

# C: radar screen with one signal blip and an up arrow inside — the name, drawn simply
C = BASE + f"""<body style="background:#0a0b0d">
<svg style="position:absolute;left:0;top:0" width="640" height="640" viewBox="0 0 640 640">
<defs><radialGradient id="g" cx="50%" cy="50%" r="50%"><stop offset="0" stop-color="#1d2412"/><stop offset="1" stop-color="#0a0b0d"/></radialGradient>
<linearGradient id="s" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{LIME}" stop-opacity="0"/><stop offset="1" stop-color="{LIME}" stop-opacity=".55"/></linearGradient></defs>
<circle cx="320" cy="320" r="250" fill="url(#g)" stroke="{LIME}" stroke-width="14"/>
<circle cx="320" cy="320" r="165" fill="none" stroke="{LIME}" stroke-opacity=".35" stroke-width="6"/>
<circle cx="320" cy="320" r="80" fill="none" stroke="{LIME}" stroke-opacity=".35" stroke-width="6"/>
<path d="M320 320 L320 70 A250 250 0 0 1 497 143 Z" fill="url(#s)"/>
<path d="M320 320 L497 143" stroke="{LIME}" stroke-width="10" stroke-linecap="round"/>
<path d="M210 410 L300 320 L350 365 L450 255" fill="none" stroke="#fff" stroke-width="30" stroke-linecap="round" stroke-linejoin="round"/>
<path d="M395 250 H455 V310" fill="none" stroke="#fff" stroke-width="30" stroke-linecap="round" stroke-linejoin="round"/>
</svg>{GRAIN}</body>"""

with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 640, "height": 640})
    for name, html in (("a", A), ("b", B), ("c", C)):
        pg.set_content(f'<!doctype html><html><head><meta charset="utf-8"></head>{html}</html>')
        pg.wait_for_timeout(200)
        pg.screenshot(path=str(OUT / f"avatar_{name}.jpg"), type="jpeg", quality=92)
    b.close()
print("ok")
