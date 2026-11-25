"""Animated description picture (shown before the Start button) and the bot avatar, fintech style.

    python tools/render_intro.py

media/intro.mp4  960x540, 5 s loop, H.264  -> @BotFather /setdescriptionpic (send as a video / GIF)
media/intro.gif  640x360, 5 s loop         -> the same as a GIF, if the MP4 is not accepted
media/avatar.jpg 640x640                   -> /setuserpic (send as a photo)

Frames are drawn in Chrome (Playwright) with the Web Animations clock stepped frame by frame, then ffmpeg.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

from render_cards import FONTS, GRAIN, LIME, RED, TEAL, UP_PATH, chart

T = Path(__file__).resolve().parent
OUT = T.parent / "media"
FPS, SECONDS = 25, 5.0
W, H = 960, 540

INTRO = f"""<!doctype html><html><head><meta charset="utf-8"><style>{FONTS}
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:{W}px;height:{H}px;overflow:hidden;background:#0a0b0d}}
.stage{{position:absolute;left:0;top:0;width:1280px;height:720px;transform:scale({W/1280});transform-origin:0 0;
 font-family:IT;color:#fff;background:#0a0b0d;overflow:hidden}}
.blob{{position:absolute;border-radius:50%;filter:blur(90px)}}
.grain{{position:absolute;inset:0;opacity:.16;mix-blend-mode:overlay}}
.k{{position:absolute;left:84px;top:150px;display:flex;gap:10px;align-items:center;font:600 22px IT;color:#c9cdd4;
 padding:10px 18px;border:1px solid #2a2d33;border-radius:40px;background:rgba(255,255,255,.03)}}
.k i{{width:10px;height:10px;border-radius:50%;background:{LIME};box-shadow:0 0 12px {LIME}}}
h1{{position:absolute;left:78px;top:218px;font:800 136px/.9 IT;letter-spacing:-6px}}
h1 span{{color:{LIME}}}
.sub{{position:absolute;left:84px;top:478px;width:520px;font:500 28px/1.35 IT;color:#9aa0a8}}
.sub b{{color:#fff;font-weight:600}}
/* radar */
.radar{{position:absolute;left:960px;top:360px;width:560px;height:560px;transform:translate(-50%,-50%)}}
.ring{{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);border-radius:50%;border:1.5px solid rgba(198,255,61,.22)}}
.sweep{{position:absolute;inset:0;border-radius:50%;
 background:conic-gradient(from 0deg,rgba(198,255,61,0) 0deg,rgba(198,255,61,0) 290deg,rgba(198,255,61,.35) 355deg,rgba(198,255,61,.75) 360deg);
 animation:spin 2.5s linear infinite}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
.blip{{position:absolute;width:14px;height:14px;border-radius:50%;transform:translate(-50%,-50%);opacity:0}}
.b1{{left:1080px;top:230px;background:{LIME};box-shadow:0 0 18px {LIME};animation:blip 2.5s .55s infinite}}
.b2{{left:860px;top:470px;background:{RED};box-shadow:0 0 18px {RED};animation:blip 2.5s 1.55s infinite}}
.b3{{left:1070px;top:500px;background:{TEAL};box-shadow:0 0 18px {TEAL};animation:blip 2.5s 1.95s infinite}}
@keyframes blip{{0%{{opacity:0;transform:translate(-50%,-50%) scale(.4)}}8%{{opacity:1;transform:translate(-50%,-50%) scale(1.3)}}
 30%{{opacity:.9;transform:translate(-50%,-50%) scale(1)}}80%,100%{{opacity:0}}}}
/* signal card */
.card{{position:absolute;left:770px;top:150px;width:400px;border-radius:28px;padding:24px 26px;
 background:linear-gradient(180deg,rgba(28,30,35,.96),rgba(16,17,20,.97));border:1px solid rgba(255,255,255,.1);
 box-shadow:0 40px 80px rgba(0,0,0,.65),inset 0 1px 0 rgba(255,255,255,.08);opacity:0;transform:translateY(40px) rotate(-3deg) scale(.96);
 animation:card 5s 1 both}}
@keyframes card{{0%,16%{{opacity:0;transform:translateY(40px) rotate(-3deg) scale(.96)}}
 24%{{opacity:1;transform:translateY(0) rotate(-3deg) scale(1)}}88%{{opacity:1;transform:translateY(0) rotate(-3deg) scale(1)}}
 100%{{opacity:0;transform:translateY(-20px) rotate(-3deg) scale(.98)}}}}
.row{{display:flex;justify-content:space-between;align-items:center}}
.pair{{font:700 26px IT}} .muted{{color:#7d838c;font:500 17px IT}}
.pill{{font:700 17px JB;padding:6px 10px;border-radius:10px;background:rgba(198,255,61,.12);color:{LIME}}}
.dir{{font:800 54px IT;letter-spacing:-2px;color:{LIME};margin:12px 0 2px}}
.line path:last-of-type{{stroke-dasharray:700;stroke-dashoffset:700;animation:draw 5s 1 both}}
.line circle{{opacity:0;animation:dot 5s 1 both}}
@keyframes draw{{0%,26%{{stroke-dashoffset:700}}50%,100%{{stroke-dashoffset:0}}}}
@keyframes dot{{0%,49%{{opacity:0}}52%,100%{{opacity:1}}}}
.area{{opacity:0;animation:area 5s 1 both}}
@keyframes area{{0%,40%{{opacity:0}}55%,100%{{opacity:1}}}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px}}
.cell{{background:rgba(255,255,255,.04);border-radius:14px;padding:11px 14px}}
.cell b{{display:block;font:700 21px JB;margin-top:3px}}
.res{{position:absolute;left:720px;top:520px;width:350px;border-radius:22px;padding:16px 20px;display:flex;gap:12px;align-items:center;
 background:linear-gradient(180deg,rgba(28,30,35,.97),rgba(16,17,20,.98));border:1px solid rgba(198,255,61,.35);
 box-shadow:0 30px 60px rgba(0,0,0,.6),0 0 30px rgba(198,255,61,.12);font:600 21px IT;opacity:0;animation:res 5s 1 both}}
@keyframes res{{0%,58%{{opacity:0;transform:translateY(30px) rotate(2deg) scale(.9)}}66%{{opacity:1;transform:translateY(0) rotate(2deg) scale(1.04)}}
 70%,88%{{opacity:1;transform:translateY(0) rotate(2deg) scale(1)}}100%{{opacity:0;transform:translateY(-10px) rotate(2deg)}}}}
.ok{{width:36px;height:36px;border-radius:50%;display:grid;place-items:center;background:rgba(198,255,61,.15);color:{LIME};font:800 20px IT}}
</style></head><body><div class="stage">
<div class="blob" style="left:760px;top:-120px;width:520px;height:420px;background:{LIME};opacity:.2"></div>
<div class="blob" style="left:900px;top:380px;width:420px;height:420px;background:{TEAL};opacity:.16"></div>
<div class="radar"><div class="sweep"></div><div class="ring" style="width:560px;height:560px"></div>
<div class="ring" style="width:400px;height:400px"></div><div class="ring" style="width:240px;height:240px"></div></div>
<div class="blip b1"></div><div class="blip b2"></div><div class="blip b3"></div>
<div class="k"><i></i>Сигналы для BinoDex</div>
<h1>Trend<br>Radar<span>.</span></h1>
<div class="sub">Вход по индикаторам и <b>честный итог</b> каждого сигнала.</div>
<div class="card"><div class="row"><div><div class="pair">EUR/USD OTC</div><div class="muted">пример сигнала</div></div><div class="pill">88%</div></div>
<div class="dir">↑ Вверх</div><div class="line">{chart(UP_PATH, LIME, 348, 124)}</div>
<div class="grid"><div class="cell"><span class="muted">Экспирация</span><b>1 мин</b></div><div class="cell"><span class="muted">Вход до</span><b>14:32:10</b></div></div></div>
<div class="res"><div class="ok">✓</div><div>EUR/USD ↑ · <span style="color:{LIME}">в плюс</span><div class="muted">итог через 1 мин</div></div></div>
{GRAIN}</div></body></html>"""

AVATAR = f"""<!doctype html><html><head><meta charset="utf-8"><style>{FONTS}
*{{margin:0;padding:0}}body{{width:640px;height:640px;overflow:hidden;position:relative;background:#0a0b0d;font-family:IT}}
.blob{{position:absolute;border-radius:50%;filter:blur(70px)}}
.c{{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);border-radius:50%}}
.ring{{border:3px solid rgba(198,255,61,.28)}}
.sweep{{width:430px;height:430px;background:conic-gradient(from 30deg,rgba(198,255,61,0) 0deg,rgba(198,255,61,0) 270deg,rgba(198,255,61,.45) 355deg,rgba(198,255,61,.95) 360deg)}}
.cross{{position:absolute;background:rgba(198,255,61,.18)}}
.dot{{position:absolute;border-radius:50%;transform:translate(-50%,-50%)}}
.grain{{position:absolute;inset:0;opacity:.16;mix-blend-mode:overlay}}
</style></head><body>
<div class="blob" style="left:300px;top:-40px;width:360px;height:300px;background:{LIME};opacity:.25"></div>
<div class="blob" style="left:-40px;top:380px;width:320px;height:300px;background:{TEAL};opacity:.2"></div>
<div class="c sweep"></div>
<div class="c ring" style="width:430px;height:430px"></div><div class="c ring" style="width:300px;height:300px"></div>
<div class="c ring" style="width:170px;height:170px"></div>
<div class="cross" style="left:105px;right:105px;top:319px;height:2px"></div><div class="cross" style="top:105px;bottom:105px;left:319px;width:2px"></div>
<svg style="position:absolute;left:190px;top:200px" width="260" height="240" viewBox="0 0 260 240">
<path d="M10 200 L80 140 L130 165 L240 40" fill="none" stroke="#fff" stroke-width="26" stroke-linecap="round" stroke-linejoin="round"/>
<path d="M175 38 L245 34 L242 104" fill="none" stroke="#fff" stroke-width="26" stroke-linecap="round" stroke-linejoin="round"/></svg>
<div class="dot" style="left:448px;top:232px;width:30px;height:30px;background:{LIME};box-shadow:0 0 26px {LIME}"></div>
<div class="dot" style="left:210px;top:420px;width:22px;height:22px;background:{RED};box-shadow:0 0 20px {RED}"></div>
{GRAIN}</body></html>"""


def main() -> None:
    OUT.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp())
    n = int(FPS * SECONDS)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": W, "height": H})
        pg.set_content(INTRO)
        pg.wait_for_timeout(300)
        for i in range(n):
            ms = i * 1000 / FPS
            pg.evaluate("ms => document.getAnimations().forEach(a => { a.pause(); a.currentTime = ms; })", ms)
            pg.screenshot(path=str(tmp / f"f{i:04d}.png"))
        pg.set_viewport_size({"width": 640, "height": 640})
        pg.set_content(AVATAR)
        pg.wait_for_timeout(200)
        pg.screenshot(path=str(OUT / "avatar.jpg"), type="jpeg", quality=92)
        b.close()
    ff = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(tmp / "f%04d.png")]
    subprocess.run(ff + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                         "-movflags", "+faststart", "-an", str(OUT / "intro.mp4")], check=True)
    subprocess.run(ff + ["-vf", "fps=15,scale=640:360:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];"
                         "[b][p]paletteuse=dither=bayer:bayer_scale=4", "-loop", "0", str(OUT / "intro.gif")], check=True)
    shutil.rmtree(tmp)
    for f in ("intro.mp4", "intro.gif", "avatar.jpg"):
        print(f"media/{f}", (OUT / f).stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
