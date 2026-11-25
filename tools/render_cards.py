"""Trend Radar pictures, fintech style (1280x720 JPG into media/, plus avatar and description picture).

Look: near-black background, one acid-lime accent (red for "down" / "loss"), big Inter Tight headline with a
coloured full stop, grey body text with white emphasis, a lime button, soft colour blobs and film grain.
The right half of every card is a piece of product UI (signal card, results list, status widget, menu) —
the bot shows what it does instead of clipart. Every number on the pictures is marked as an example.

    python tools/render_cards.py            # all cards + avatar + description
    python tools/render_cards.py welcome    # some cards

Needs Playwright + Chrome (designer machine only; the bot sends the JPGs).
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

T = Path(__file__).resolve().parent
OUT = T.parent / "media"
BRAND = "Trend Radar"

LIME, RED, TEAL = "#c6ff3d", "#ff5c5c", "#1ec8b4"


def b64(p: Path) -> str:
    return base64.b64encode(p.read_bytes()).decode()


def fonts() -> str:
    out = []
    for fam, pkg, ws in (("IT", "inter-tight", (500, 600, 700, 800)), ("JB", "jetbrains-mono", (500, 700))):
        for w in ws:
            for sub, rng in (("latin", "U+0000-00FF,U+2000-206F,U+2190-21FF,U+2212,U+2713,U+2715"),
                             ("cyrillic", "U+0400-04FF")):
                out.append(f"@font-face{{font-family:{fam};font-weight:{w};src:url(data:font/woff2;base64,"
                           f"{b64(T / f'{pkg}-{sub}-{w}-normal.woff2')});unicode-range:{rng}}}")
    return "".join(out)


FONTS = fonts()
GRAIN = ("<svg class='grain' width='1280' height='720'><filter id='n'><feTurbulence type='fractalNoise' "
         "baseFrequency='.85' numOctaves='3' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 .5 0 0 0 0 .5 "
         "0 0 0 0 .5 0 0 0 .6 0'/></filter><rect width='100%' height='100%' filter='url(#n)'/></svg>")

UP_PATH = "M0 120 C40 112 60 128 90 104 S140 96 170 84 S220 98 250 70 S300 52 330 58 S380 30 420 22"
DOWN_PATH = "M0 26 C40 34 60 18 90 42 S140 50 170 62 S220 48 250 76 S300 94 330 88 S380 116 420 124"


def css(accent: str) -> str:
    return f"""{FONTS}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:1280px;height:720px;overflow:hidden;position:relative;background:#0a0b0d;font-family:IT;color:#fff}}
.grain{{position:absolute;inset:0;opacity:.16;mix-blend-mode:overlay;pointer-events:none}}
.blob{{position:absolute;border-radius:50%;filter:blur(90px)}}
.k{{position:absolute;left:84px;top:96px;display:flex;gap:10px;align-items:center;font:600 20px IT;color:#c9cdd4;
 padding:9px 16px;border:1px solid #2a2d33;border-radius:40px;background:rgba(255,255,255,.03)}}
.k i{{width:9px;height:9px;border-radius:50%;background:{accent};box-shadow:0 0 12px {accent}}}
h1{{position:absolute;left:80px;top:158px;font:800 var(--hs,120px)/0.92 IT;letter-spacing:-5px}}
h1 span{{color:{accent}}}
p.sub{{position:absolute;left:84px;top:var(--pt,410px);width:540px;font:500 27px/1.35 IT;color:#9aa0a8}}
p.sub b{{color:#fff;font-weight:600}}
.btn{{position:absolute;left:84px;top:var(--bt,548px);padding:18px 30px;border-radius:16px;background:{accent};color:#0a0b0d;font:700 24px IT}}
.btn.ghost{{background:transparent;color:#fff;border:1px solid #2f3238}}
.card{{position:absolute;border-radius:30px;background:linear-gradient(180deg,rgba(28,30,35,.94),rgba(16,17,20,.96));
 border:1px solid rgba(255,255,255,.09);box-shadow:0 40px 80px rgba(0,0,0,.6),inset 0 1px 0 rgba(255,255,255,.08);padding:26px 28px}}
.row{{display:flex;justify-content:space-between;align-items:center}}
.pair{{font:700 26px IT;letter-spacing:-.5px}} .muted{{color:#7d838c;font:500 17px IT}}
.pill{{font:700 17px JB;padding:6px 10px;border-radius:10px;background:rgba(198,255,61,.12);color:{LIME}}}
.dir{{font:800 56px IT;letter-spacing:-2px;margin:14px 0 4px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:16px}}
.cell{{background:rgba(255,255,255,.04);border-radius:14px;padding:12px 14px}}
.cell b{{display:block;font:700 22px JB;color:#fff;margin-top:4px}}
.res{{display:flex;gap:12px;align-items:center;font:600 21px IT}}
.dot{{width:34px;height:34px;border-radius:50%;display:grid;place-items:center;font:800 19px IT;flex:none}}
.ok{{background:rgba(198,255,61,.15);color:{LIME}}} .bad{{background:rgba(255,92,92,.15);color:{RED}}}
.li{{display:flex;justify-content:space-between;align-items:center;padding:13px 0;border-bottom:1px solid rgba(255,255,255,.06);font:600 21px IT}}
.li:last-child{{border-bottom:0}}
.li .l{{display:flex;gap:12px;align-items:center}}
.tag{{font:700 15px JB;color:#7d838c}}
.mbtn{{display:flex;gap:12px;align-items:center;padding:16px 18px;border-radius:16px;background:rgba(255,255,255,.05);font:600 22px IT;margin-top:10px}}
.mbtn.main{{background:{LIME};color:#0a0b0d}}
.ic{{width:30px;text-align:center;font:700 22px IT}}
.bar{{height:10px;border-radius:6px;background:rgba(255,255,255,.07);overflow:hidden;margin-top:10px}}
.bar i{{display:block;height:100%;border-radius:6px;background:{accent}}}
.field{{margin-top:14px;padding:16px 18px;border-radius:14px;background:rgba(255,255,255,.05);border:1px solid rgba(198,255,61,.5);
 font:700 26px JB;display:flex;justify-content:space-between;align-items:center}}
.copy{{font:600 16px IT;color:#0a0b0d;background:{LIME};padding:6px 10px;border-radius:8px}}
.step{{display:flex;gap:14px;align-items:flex-start;margin-top:16px;font:600 21px/1.3 IT}}
.num{{width:34px;height:34px;border-radius:10px;display:grid;place-items:center;font:700 17px JB;flex:none}}
.num.on{{background:{LIME};color:#0a0b0d}} .num.off{{background:rgba(255,255,255,.06);color:#9aa0a8}}
.live{{display:inline-block;width:10px;height:10px;border-radius:50%;background:{LIME};box-shadow:0 0 0 6px rgba(198,255,61,.15);margin-right:10px}}
"""


def chart(path: str, color: str, w: int = 364, h: int = 130, entry: bool = False) -> str:
    end = (420, 22) if path == UP_PATH else (420, 124)
    gid = "g" + color.strip("#")
    extra = ""
    if entry:                       # dashed entry level at the start of the price path
        y0 = 120 if path == UP_PATH else 26
        ty = y0 - 8 if path == UP_PATH else y0 + 22
        extra = (f'<line x1="0" y1="{y0}" x2="420" y2="{y0}" stroke="#7d838c" stroke-width="2" stroke-dasharray="6 7"/>'
                 f'<text x="300" y="{ty}" fill="#7d838c" font-family="JB" font-size="15">уровень входа</text>')
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 420 140"><defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{color}" stop-opacity=".35"/><stop offset="1" stop-color="{color}" stop-opacity="0"/>'
            f'</linearGradient></defs>{extra}<path d="{path} L420 140 L0 140Z" fill="url(#{gid})"/>'
            f'<path d="{path}" fill="none" stroke="{color}" stroke-width="4" stroke-linecap="round"/>'
            f'<circle cx="{end[0]}" cy="{end[1]}" r="7" fill="{color}"/></svg>')


def signal_card(direction: str, left=780, top=86, rot=-3, strong=False) -> str:
    up = direction == "up"
    col = LIME if up else RED
    head = '<div class="pill" style="background:rgba(198,255,61,.12)">⚡ сильный</div>' if strong else '<div class="pill">88%</div>'
    return f"""<div class="card" style="left:{left}px;top:{top}px;width:420px;transform:rotate({rot}deg)">
 <div class="row"><div><div class="pair">EUR/USD OTC</div><div class="muted">пример сигнала</div></div>{head}</div>
 <div class="dir" style="color:{col}">{'↑ Вверх' if up else '↓ Вниз'}</div>{chart(UP_PATH if up else DOWN_PATH, col)}
 <div class="grid"><div class="cell"><span class="muted">Экспирация</span><b>1 мин</b></div>
 <div class="cell"><span class="muted">Вход до</span><b>14:32:10</b></div></div></div>"""


def result_chip(ok: bool, left: int, top: int, rot: float = 2) -> str:
    return f"""<div class="card" style="left:{left}px;top:{top}px;width:360px;padding:18px 22px;transform:rotate({rot}deg)">
 <div class="res"><div class="dot {'ok' if ok else 'bad'}">{'✓' if ok else '✕'}</div><div>GBP/USD ↓ · <span style="color:{LIME if ok else RED}">{'в плюс' if ok else 'в минус'}</span>
 <div class="muted">итог пришёл через 1 мин</div></div></div></div>"""


def page(accent: str, body: str, hs: int = 120, pt: int = 410, bt: int = 548, blobs: str = "", extra: str = "") -> str:
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{css(accent)}'
            f'body{{--hs:{hs}px;--pt:{pt}px;--bt:{bt}px}}{extra}</style></head><body>{blobs}{body}{GRAIN}</body></html>')


ONE_LINE = ".k{top:176px}h1{top:238px}"
PHONE_CSS = """
.phone{position:absolute;left:800px;top:40px;width:360px;height:700px;border-radius:52px;background:#050506;
 border:2px solid #2a2d33;box-shadow:0 50px 100px rgba(0,0,0,.7),inset 0 0 0 9px #111214;transform:rotate(-4deg);overflow:hidden}
.notch{position:absolute;left:50%;top:16px;width:110px;height:30px;border-radius:20px;background:#000;transform:translateX(-50%)}
.tgh{position:absolute;left:0;right:0;top:58px;height:64px;display:flex;align-items:center;gap:12px;padding:0 22px;
 background:rgba(255,255,255,.04);border-bottom:1px solid rgba(255,255,255,.06)}
.av{width:40px;height:40px;border-radius:50%;background:#0a0b0d;border:1px solid #2a2d33;display:grid;place-items:center;font:800 15px IT}
.av span{color:#c6ff3d}
.tgh b{font:700 18px IT;display:block}.tgh i{font:500 14px IT;color:#7d838c;font-style:normal}
.msg{position:absolute;left:18px;width:300px;border-radius:18px 18px 18px 6px;background:#1a1c21;padding:10px;font:500 14px IT;color:#d7dbe0}
.mini{border-radius:14px;background:#0f1013;padding:12px 14px;margin-bottom:8px}
.mini .p{font:700 15px IT;color:#fff}.mini .d{font:800 28px IT;color:#c6ff3d;letter-spacing:-1px;margin:4px 0}
.mini .m{display:flex;gap:8px;margin-top:6px}.mini .m span{flex:1;background:rgba(255,255,255,.05);border-radius:8px;padding:6px 8px;font:700 12px JB;color:#fff}
.reply{border-left:3px solid #c6ff3d;padding-left:8px;margin-bottom:6px;font:500 12px IT;color:#7d838c}
"""      # one-line headline: the text block sits lower, centred


def blobs(c1: str = LIME, c2: str = TEAL, o1: float = .22, o2: float = .18) -> str:
    return (f'<div class="blob" style="left:760px;top:-120px;width:520px;height:420px;background:{c1};opacity:{o1}"></div>'
            f'<div class="blob" style="left:900px;top:380px;width:420px;height:420px;background:{c2};opacity:{o2}"></div>')


def text(kicker: str, head: str, sub: str, btn: str | None, ghost: bool = False) -> str:
    b = f'<div class="btn{" ghost" if ghost else ""}">{btn}</div>' if btn else ""
    return f'<div class="k"><i></i>{kicker}</div><h1>{head}</h1><p class="sub">{sub}</p>{b}'


def cards() -> dict[str, str]:
    c = {}
    phone = ("<div class='phone'><div class='notch'></div><div class='tgh'><div class='av'>TR<span>.</span></div>"
             "<div><b>Trend Radar</b><i>бот · пример переписки</i></div></div>"
             "<div class='msg' style='top:140px'><div class='mini'><div class='p'>EUR/USD OTC · 88%</div>"
             "<div class='d'>↑ Вверх</div>" + chart(UP_PATH, LIME, 250, 70) +
             "<div class='m'><span>1 мин</span><span>до 14:32:10</span></div></div>"
             "Вход вверх, экспирация 1 мин. Итог пришлю сюда же.</div>"
             "<div class='msg' style='top:440px'><div class='reply'>EUR/USD OTC ↑</div>"
             "<b style='color:#c6ff3d'>✓ В плюс.</b> Цена пошла в сторону сигнала: EMA 8 выше EMA 21, RSI 58, "
             "импульс вверх.</div>"
             "</div>")
    c["welcome"] = page(LIME, text("Сигналы для BinoDex", "Trend<br>Radar<span>.</span>",
                                   "Вход по индикаторам и <b>честный итог</b> каждого сигнала — плюс и минус, с разбором.",
                                   "Подписаться на канал  →") + phone, blobs=blobs(), extra=PHONE_CSS)
    steps = [("1", "Пара или «Авто»", "бот сам найдёт, где вход чётче"), ("2", "Время сделки", "от 30 секунд до 5 минут"),
             ("3", "Вход по котировкам BinoDex", "или честное «входа нет»"), ("4", "Итог с разбором", "почему плюс или минус")]
    st = "".join(f'<div class="step" style="margin-top:{0 if i == 0 else 18}px"><div class="num on">{n}</div>'
                 f'<div>{h}<div class="muted" style="margin-top:3px">{d}</div></div></div>'
                 for i, (n, h, d) in enumerate(steps))
    c["value"] = page(LIME, text("Бот открыт", "Как это<br>работает<span>.</span>",
                                 "Сигналы по любой паре — после <b>двух шагов</b>: регистрация на BinoDex и пополнение.",
                                 "Получить доступ  →") +
                      f'<div class="card" style="left:780px;top:120px;width:420px;transform:rotate(-2deg)">'
                      f'<div class="pair" style="margin-bottom:18px">Trend Radar</div>{st}</div>', hs=108, blobs=blobs())
    c["register"] = page(LIME, text("Шаг 1 из 2", "Регистрация<span>.</span>",
                                    "Создай аккаунт BinoDex <b>по кнопке</b> и пришли свой <b>ID из профиля</b>.",
                                    "Зарегистрироваться  →") +
                         f'<div class="card" style="left:780px;top:190px;width:420px;transform:rotate(-2deg)">'
                         f'<div class="pair">Профиль BinoDex</div><div class="muted">твой ID — пример</div>'
                         f'<div class="field">12345678<span class="copy">Копировать</span></div>'
                         f'<div class="step"><div class="num on">1</div>Аккаунт по нашей ссылке</div>'
                         f'<div class="step"><div class="num on">2</div>ID — сообщением в бот</div>'
                         f'<div class="step"><div class="num off">3</div><span style="color:#9aa0a8">Бот проверит сам</span></div></div>',
                         hs=96, pt=372, bt=500, blobs=blobs(), extra=ONE_LINE)
    c["deposit"] = page(LIME, text("Шаг 2 из 2", "Пополнение<span>.</span>",
                                   "От <b>$10</b>. Доступ откроется сам — бот проверяет <b>каждые 10 минут</b>.",
                                   "Открыть BinoDex  →") +
                        f'<div class="card" style="left:780px;top:210px;width:420px;transform:rotate(-2deg)">'
                        f'<div class="pair">Статус доступа</div><div class="muted">проверка идёт автоматически</div>'
                        f'<div class="li" style="margin-top:12px"><div class="l"><div class="dot ok">✓</div>Аккаунт подтверждён</div></div>'
                        f'<div class="li"><div class="l"><span class="live"></span>Ждём пополнение</div><span class="tag">10 мин</span></div>'
                        f'<div class="li"><div class="l"><div class="dot" style="background:rgba(255,255,255,.06);color:#7d838c">→</div>'
                        f'<span style="color:#9aa0a8">Доступ к сигналам</span></div></div></div>', hs=96, pt=372, bt=500, blobs=blobs(), extra=ONE_LINE)
    c["access"] = page(LIME, text("Trend Radar", "Доступ<br>открыт<span>.</span>",
                                  "Сигнал по любой паре <b>в любой момент</b>. Совет: 1–3% счёта на сделку.",
                                  "Получить сигнал  →") +
                       f'<div class="card" style="left:790px;top:120px;width:400px;transform:rotate(-2deg)">'
                       f'<div class="pair">Меню</div><div class="muted">Trend Radar</div>'
                       f'<div class="mbtn main"><span class="ic">↗</span>Получить сигнал</div>'
                       f'<div class="mbtn"><span class="ic">≡</span>Статистика</div>'
                       f'<div class="mbtn"><span class="ic">◉</span>Уведомления: вкл</div>'
                       f'<div class="mbtn"><span class="ic">→</span>Открыть BinoDex</div></div>', blobs=blobs())
    c["signal_up"] = page(LIME, text("Trend Radar", "Вход<br>вверх<span>.</span>",
                                     "Пара, экспирация и время входа — <b>в сообщении</b>. Итог придёт <b>ответом под сигналом</b>.",
                                     None) + signal_card("up", 760, 110), pt=420, blobs=blobs())
    c["signal_down"] = page(RED, text("Trend Radar", "Вход<br>вниз<span>.</span>",
                                      "Пара, экспирация и время входа — <b>в сообщении</b>. Итог придёт <b>ответом под сигналом</b>.",
                                      None) + signal_card("down", 760, 110), pt=420, blobs=blobs(RED, "#ff9a3d", .2, .12))
    c["strong"] = page(LIME, text("Trend Radar", "Сильный<br>вход<span>.</span>",
                                  "Индикаторы сошлись <b>сильнее обычного</b>. Итог придёт сюда же.", None) +
                       signal_card("up", 760, 80, strong=True) +
                       f'<div class="card" style="left:690px;top:520px;width:440px;padding:18px 22px;transform:rotate(2deg)">'
                       f'<div class="row"><span style="font:600 19px IT;white-space:nowrap">Согласие индикаторов</span><span class="tag" style="white-space:nowrap">EMA · RSI · импульс</span></div>'
                       f'<div class="bar"><i style="width:86%"></i></div></div>', pt=420, blobs=blobs())
    c["win"] = page(LIME, text("Итог сигнала", "В плюс<span>.</span>",
                               "Цена пошла в сторону сигнала. <b>Разбор — в сообщении</b>: на чём построен вход и что сработало.",
                               None) +
                    f'<div class="card" style="left:770px;top:170px;width:430px;transform:rotate(-2deg)">'
                    f'<div class="row"><div><div class="pair">EUR/USD OTC ↑</div><div class="muted">пример итога</div></div>'
                    f'<div class="dot ok">✓</div></div><div style="margin-top:14px">{chart(UP_PATH, LIME, 374, 134, True)}</div>'
                    f'<div class="grid"><div class="cell"><span class="muted">Вход</span><b>1.08412</b></div>'
                    f'<div class="cell"><span class="muted">Экспирация</span><b>1.08447</b></div></div></div>',
                    hs=120, pt=380, blobs=blobs(), extra=ONE_LINE)
    c["loss"] = page(RED, text("Итог сигнала", "В минус<span>.</span>",
                               "Минусы тоже показываем. <b>Почему так вышло — в сообщении</b>: что изменилось на рынке.",
                               None) +
                     f'<div class="card" style="left:770px;top:170px;width:430px;transform:rotate(-2deg)">'
                     f'<div class="row"><div><div class="pair">EUR/USD OTC ↑</div><div class="muted">пример итога</div></div>'
                     f'<div class="dot bad">✕</div></div><div style="margin-top:14px">{chart(DOWN_PATH, RED, 374, 134, True)}</div>'
                     f'<div class="grid"><div class="cell"><span class="muted">Вход</span><b>1.08412</b></div>'
                     f'<div class="cell"><span class="muted">Экспирация</span><b>1.08390</b></div></div></div>',
                     hs=120, pt=380, blobs=blobs(RED, "#ff9a3d", .2, .12), extra=ONE_LINE)
    return c


def backgrounds() -> dict[str, str]:
    """Same cards without the product card: the bot draws a live card with real data on the right (app/cardgen.py)."""
    sub = "Пара, экспирация и время входа — <b>на карточке</b>. Итог придёт <b>ответом под сигналом</b>."
    return {
        "bg/signal_up": page(LIME, text("Trend Radar", "Вход<br>вверх<span>.</span>", sub, None), pt=420, blobs=blobs()),
        "bg/signal_down": page(RED, text("Trend Radar", "Вход<br>вниз<span>.</span>", sub, None), pt=420,
                               blobs=blobs(RED, "#ff9a3d", .2, .12)),
        "bg/strong": page(LIME, text("Trend Radar", "Сильный<br>вход<span>.</span>",
                                     "Индикаторы сошлись <b>сильнее обычного</b>. Итог придёт сюда же.", None),
                          pt=420, blobs=blobs()),
        "bg/win": page(LIME, text("Итог сигнала", "В плюс<span>.</span>",
                                  "Цена пошла в сторону сигнала. <b>Разбор — в сообщении</b>: на чём построен вход и что сработало.",
                                  None), hs=120, pt=380, blobs=blobs(), extra=ONE_LINE),
        "bg/day": page(LIME, text("Trend Radar", "Итоги<br>дня<span>.</span>",
                                  "Все сигналы за день — <b>и плюсы, и минусы</b>. Итог по цене BinoDex.", None),
                       pt=420, blobs=blobs()),
        "bg/loss": page(RED, text("Итог сигнала", "В минус<span>.</span>",
                                  "Минусы тоже показываем. <b>Почему так вышло — в сообщении</b>: что изменилось на рынке.",
                                  None), hs=120, pt=380, blobs=blobs(RED, "#ff9a3d", .2, .12), extra=ONE_LINE),
    }


AVATAR = f"""<!doctype html><html><head><meta charset="utf-8"><style>{FONTS}
*{{margin:0;padding:0}}body{{width:640px;height:640px;overflow:hidden;position:relative;background:#0a0b0d;font-family:IT}}
.blob{{position:absolute;border-radius:50%;filter:blur(70px)}}
.r{{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);border-radius:50%;border:2px solid rgba(198,255,61,.22)}}
.m{{position:absolute;inset:0;display:grid;place-items:center;font:800 250px IT;letter-spacing:-14px;color:#fff}}
.m span{{color:{LIME}}}
.grain{{position:absolute;inset:0;opacity:.16;mix-blend-mode:overlay}}
</style></head><body>
<div class="blob" style="left:300px;top:-60px;width:380px;height:320px;background:{LIME};opacity:.28"></div>
<div class="blob" style="left:-60px;top:380px;width:320px;height:320px;background:{TEAL};opacity:.22"></div>
<div class="r" style="width:560px;height:560px"></div><div class="r" style="width:420px;height:420px"></div>
<div class="m"><div>TR<span>.</span></div></div>{GRAIN}</body></html>"""


def main() -> None:
    all_cards = cards() | backgrounds()
    only = sys.argv[1:] or [*all_cards, "avatar"]
    (OUT / "bg").mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": 1280, "height": 720})
        for name in only:
            if name == "avatar":
                pg.set_viewport_size({"width": 640, "height": 640})
                pg.set_content(AVATAR)
                pg.wait_for_timeout(200)
                pg.screenshot(path=str(OUT / "avatar.jpg"), type="jpeg", quality=92)
                pg.set_viewport_size({"width": 1280, "height": 720})
            else:
                pg.set_content(all_cards[name])
                pg.wait_for_timeout(200)
                pg.screenshot(path=str(OUT / f"{name}.jpg"), type="jpeg", quality=90)
                if name == "welcome":
                    pg.set_viewport_size({"width": 1280, "height": 720})
                    from PIL import Image
                    Image.open(OUT / "welcome.jpg").resize((640, 360)).save(OUT / "description.jpg", quality=90)
            print(f"media/{name}.jpg")
        b.close()


if __name__ == "__main__":
    main()
