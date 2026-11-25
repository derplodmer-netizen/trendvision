"""Avatar with the full name + an indicator. python tools/render_avatar_name.py -> media/avatar_d.jpg, avatar_e.jpg"""
from pathlib import Path

from playwright.sync_api import sync_playwright

from render_cards import FONTS, GRAIN, LIME, RED

OUT = Path(__file__).resolve().parents[1] / "media"


def page(bg: str, ink: str, accent: str, down: str, glow: str) -> str:
    # mini chart: candles with a rising line and a signal dot — the "indicator"
    candles = "".join(
        f'<rect x="{x+9}" y="{wt}" width="4" height="{wb-wt}" rx="2" fill="{c}"/><rect x="{x}" y="{t}" width="22" height="{h}" rx="5" fill="{c}"/>'
        for x, t, h, wt, wb, c in ((0, 70, 34, 60, 114, down), (34, 52, 40, 40, 100, accent), (68, 58, 26, 48, 92, down),
                                   (102, 30, 44, 18, 82, accent), (136, 8, 46, 0, 62, accent)))
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{FONTS}
*{{margin:0;padding:0}}body{{width:640px;height:640px;overflow:hidden;position:relative;font-family:IT;background:{bg}}}
.grain{{position:absolute;inset:0;opacity:.14;mix-blend-mode:overlay}}
.name{{position:absolute;left:0;right:0;top:118px;text-align:center;font:800 130px/.92 IT;letter-spacing:-5px;color:{ink}}}
.name span{{color:{accent}}}
.glow{{position:absolute;left:170px;top:-60px;width:300px;height:260px;border-radius:50%;background:{glow};filter:blur(70px)}}
</style></head><body><div class="glow"></div>
<div class="name">TREND<br>RADAR<span>.</span></div>
<svg style="position:absolute;left:186px;top:392px" width="268" height="171" viewBox="0 -2 182 118">{candles}
<path d="M4 100 L46 78 L80 84 L114 50 L170 18" fill="none" stroke="{ink}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round" opacity=".85"/>
<circle cx="170" cy="18" r="9" fill="{ink}"/></svg>
{GRAIN}</body></html>"""


with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 640, "height": 640})
    for name, html in (("d", page("#0a0b0d", "#ffffff", LIME, RED, "rgba(198,255,61,.25)")),
                       ("e", page(LIME, "#0a0b0d", "#0a0b0d", "rgba(10,11,13,.35)", "rgba(255,255,255,.35)"))):
        pg.set_content(html)
        pg.wait_for_timeout(200)
        pg.screenshot(path=str(OUT / f"avatar_{name}.jpg"), type="jpeg", quality=92)
    b.close()
print("ok")
