"""สร้างรายงาน PDF ด้วย reportlab"""
from __future__ import annotations

import base64
import io
import os
import re
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)
from xml.sax.saxutils import escape

HERE = os.path.dirname(__file__)
# ฟอนต์ที่รองรับภาษาไทย (ต้องเป็น TrueType outline) — วางไฟล์ของตัวเองไว้ใน wifi_survey/fonts/ ได้
THAI_FONTS = [
    os.path.join(HERE, "fonts", "Thai.ttf"),
    "C:/Windows/Fonts/tahoma.ttf", "C:/Windows/Fonts/LeelawUI.ttf",
    "/System/Library/Fonts/Supplemental/Tahoma.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/tlwg/Loma.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
]

T = {
    "en": dict(title="Wi-Fi Site Survey Report", date="Date", surveyor="Surveyor",
               summary="Summary", points="Measurement points", pts_ok="Points meeting target",
               rssi_stats="Best-AP RSSI (min / avg / max)", target="Target RSSI",
               cover="Estimated area coverage", heatmap="Signal heatmap",
               table="Measurement details", label="Point", ssid="Best SSID",
               bssid="BSSID", rssi="RSSI", ch="Ch", band="Band", aps="APs seen",
               channels="Channel usage", chan="Channel", nap="APs", maxrssi="Strongest",
               planned="Planned access points", name="Name", power="TX (dBm)",
               none="(none)", legend="Legend", page="Page"),
    "th": dict(title="รายงานการสำรวจ Wi-Fi (Site Survey)", date="วันที่", surveyor="ผู้สำรวจ",
               summary="สรุปผล", points="จำนวนจุดวัด", pts_ok="จุดที่ผ่านเกณฑ์",
               rssi_stats="RSSI ของ AP ที่แรงที่สุด (ต่ำสุด / เฉลี่ย / สูงสุด)", target="เกณฑ์ RSSI",
               cover="ประมาณพื้นที่ที่ครอบคลุม", heatmap="Heatmap ความแรงสัญญาณ",
               table="รายละเอียดจุดวัด", label="จุด", ssid="SSID ที่แรงสุด",
               bssid="BSSID", rssi="RSSI", ch="ช่อง", band="ย่าน", aps="จำนวน AP",
               channels="การใช้ช่องสัญญาณ", chan="ช่อง", nap="จำนวน AP", maxrssi="แรงสุด",
               planned="ตำแหน่ง AP ที่วางแผนไว้", name="ชื่อ", power="กำลังส่ง (dBm)",
               none="(ไม่มี)", legend="คำอธิบายสี", page="หน้า"),
}


def find_thai_font() -> str | None:
    for p in THAI_FONTS:
        if os.path.exists(p):
            try:
                TTFont("ThaiProbe", p)
                return p
            except Exception:
                continue
    return None


def _fonts(lang: str) -> tuple[str, str, str]:
    """คืน (ชื่อฟอนต์, ภาษาที่ใช้จริง, ...) — ถ้าไม่มีฟอนต์ไทยจะถอยไปอังกฤษ"""
    if lang == "th":
        path = find_thai_font()
        if path:
            if "ThaiFont" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("ThaiFont", path))
            return "ThaiFont", "th", ""
    return "Helvetica", "en", ""


def _txt(s, latin_only: bool) -> str:
    s = "" if s is None else str(s)
    if latin_only:
        s = re.sub(r"[^\x20-\x7e\u00a0-\u00ff]", "?", s)
    return escape(s)


def stats(project: dict, target: float) -> dict:
    best = []
    for p in project.get("points", []):
        aps = p.get("aps") or []
        if aps:
            best.append(max(a["rssi"] for a in aps))
    n = len(project.get("points", []))
    return {
        "n": n,
        "n_ok": sum(1 for b in best if b >= target),
        "min": min(best) if best else None,
        "avg": sum(best) / len(best) if best else None,
        "max": max(best) if best else None,
    }


def channel_table(project: dict) -> list[tuple]:
    seen: dict[tuple, dict] = {}
    for p in project.get("points", []):
        for a in p.get("aps") or []:
            key = (a.get("band") or "?", a.get("channel"))
            seen.setdefault(key, {})
            seen[key][a["bssid"]] = max(seen[key].get(a["bssid"], -200), a["rssi"])
    rows = []
    for (band, ch), aps in seen.items():
        rows.append((band, ch, len(aps), max(aps.values())))
    rows.sort(key=lambda r: (str(r[0]), r[1] if r[1] is not None else 0))
    return rows


def build_pdf(project: dict, heatmap_png: bytes | None, *, lang: str = "en",
              target: float = -67.0, coverage: float | None = None,
              surveyor: str = "") -> bytes:
    # ถ้าข้อมูลมีตัวอักษรไทย (ชื่อโปรเจกต์/SSID) ให้ใช้ฟอนต์ไทยแม้เลือกรายงานภาษาอังกฤษ
    has_thai = bool(re.search(r"[\u0E00-\u0E7F]", repr(project)))
    font, lang_used, _ = _fonts("th" if has_thai else lang)
    if lang_used == "en" or lang == "en":
        L = T["en"]
    else:
        L = T["th"]
    latin = font == "Helvetica"
    ss = getSampleStyleSheet()
    base = ParagraphStyle("b", parent=ss["Normal"], fontName=font, fontSize=9, leading=12)
    h1 = ParagraphStyle("h1", parent=base, fontSize=20, leading=24, spaceAfter=6)
    h2 = ParagraphStyle("h2", parent=base, fontSize=13, leading=16, spaceBefore=10, spaceAfter=4)
    cell = ParagraphStyle("c", parent=base, fontSize=8, leading=10)

    def P(s, st=base):
        return Paragraph(_txt(s, latin), st)

    def table(rows, widths, head=True):
        t = Table([[P(c, cell) for c in r] for r in rows], colWidths=widths, repeatRows=1 if head else 0)
        style = [("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c8ccd4")),
                 ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                 ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f7fa")])]
        if head:
            style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dfe6f1")))
        t.setStyle(TableStyle(style))
        return t

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm,
                            title=project.get("name", "Wi-Fi Survey"))
    W = A4[0] - 30 * mm
    s = stats(project, target)
    story = [P(L["title"], h1), P(project.get("name", ""), h2),
             P(f'{L["date"]}: {datetime.now():%Y-%m-%d %H:%M}'
               + (f'   |   {L["surveyor"]}: {surveyor}' if surveyor else ""))]

    story.append(P(L["summary"], h2))
    rows = [[L["points"], s["n"]],
            [L["target"], f"{target:g} dBm"],
            [L["pts_ok"], f'{s["n_ok"]} / {s["n"]}' + (f' ({100 * s["n_ok"] / s["n"]:.0f}%)' if s["n"] else "")]]
    if s["min"] is not None:
        rows.append([L["rssi_stats"], f'{s["min"]:.0f} / {s["avg"]:.0f} / {s["max"]:.0f} dBm'])
    if coverage is not None:
        rows.append([L["cover"], f"{coverage:.0f}%"])
    story.append(table(rows, [W * 0.55, W * 0.45], head=False))

    if heatmap_png:
        story.append(P(L["heatmap"], h2))
        from PIL import Image as PILImage
        with PILImage.open(io.BytesIO(heatmap_png)) as im:
            iw, ih = im.size
        maxw, maxh = W, 175 * mm
        sc = min(maxw / iw, maxh / ih)
        story.append(Image(io.BytesIO(heatmap_png), width=iw * sc, height=ih * sc))

    pts = project.get("points", [])
    story.append(P(L["table"], h2))
    rows = [["#", L["label"], L["ssid"], L["bssid"], L["rssi"], L["ch"], L["band"], L["aps"]]]
    for i, p in enumerate(pts, 1):
        aps = sorted(p.get("aps") or [], key=lambda a: a["rssi"], reverse=True)
        top = aps[0] if aps else None
        rows.append([i, p.get("label", ""), top["ssid"] if top else "-", top["bssid"] if top else "-",
                     f'{top["rssi"]:.0f}' if top else "-", top.get("channel") or "-" if top else "-",
                     top.get("band") or "-" if top else "-", len(aps)])
    story.append(table(rows, [8 * mm, 25 * mm, 38 * mm, 38 * mm, 14 * mm, 12 * mm, 14 * mm, W - 149 * mm]))

    ch = channel_table(project)
    if ch:
        story.append(P(L["channels"], h2))
        story.append(table([[L["band"], L["chan"], L["nap"], L["maxrssi"]]] +
                           [[b, c if c is not None else "-", n, f"{m:.0f} dBm"] for b, c, n, m in ch],
                           [W * .2, W * .2, W * .2, W * .2]))

    planned = project.get("aps", [])
    story.append(P(L["planned"], h2))
    if planned:
        story.append(table([[L["name"], L["band"], L["ch"], L["power"], "x,y (m)"]] +
                           [[a.get("name", ""), a.get("band", ""), a.get("channel") or "-", a.get("txPower", ""),
                             a.get("pos_m", "")] for a in planned],
                           [W * .3, W * .15, W * .1, W * .2, W * .25]))
    else:
        story.append(P(L["none"]))

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.grey)
        canvas.drawRightString(A4[0] - 15 * mm, 8 * mm, f'{L["page"]} {d.page}')
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def decode_data_url(url: str | None) -> bytes | None:
    if not url:
        return None
    m = re.match(r"^data:image/(png|jpeg);base64,(.*)$", url, re.S)
    if not m:
        raise ValueError("รูป heatmap ต้องเป็น data URL แบบ PNG/JPEG")
    return base64.b64decode(m.group(2))
