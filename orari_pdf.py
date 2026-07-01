from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from io import BytesIO
from typing import Any, Dict, Iterable, List, Optional, Tuple

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet


def _parse_hhmm(s: str | None) -> Optional[int]:
    s = (s or "").strip()
    if not s:
        return None
    if ":" not in s:
        return None
    try:
        h, m = s.split(":", 1)
        h = int(h)
        m = int(m)
        if h < 0 or h > 23:
            return None
        if m < 0 or m > 59:
            return None
        return h * 60 + m
    except Exception:
        return None


def _mins_diff(inizio: str | None, fine: str | None, *, overnight_start_min: int = 12 * 60, overnight_end_max: int = 8 * 60) -> Tuple[int, Optional[str]]:
    a = _parse_hhmm(inizio)
    b = _parse_hhmm(fine)
    if a is None or b is None:
        return 0, None
    if b == a:
        return 0, None
    if b > a:
        return b - a, None
    if a >= overnight_start_min and b <= overnight_end_max:
        return (24 * 60 - a) + b, None
    return 0, "end_before_start"


def _fmt_mins(mins: int) -> str:
    h = mins // 60
    m = mins % 60
    return f"{h:02d}:{m:02d}"


_NON_PROD_CAUSALI = {"ferie", "permesso", "allattamento", "off", "prestito", "malattia", "training", "riposo festivo"}


def _is_prod_shift(causale: str | None) -> bool:
    low = str(causale or "").strip().lower()
    if not low:
        return True
    return low not in _NON_PROD_CAUSALI


def build_orari_pdf(
    *,
    site: str,
    week_start: date,
    nominativi: List[str],
    turni: List[Dict[str, Any]],
    staff_map: Dict[str, Any] | None = None,
    sales: Dict[str, Any] | None = None,
    prev_year: Dict[str, Any] | None = None,
    legenda: List[Dict[str, Any]] | None = None,
) -> bytes:
    styles = getSampleStyleSheet()

    # Stili più compatti per la tabella
    st_cell = ParagraphStyle(
        "cell",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=7,
        leading=8,
        spaceBefore=0,
        spaceAfter=0,
    )
    st_head = ParagraphStyle(
        "head",
        parent=styles["BodyText"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=9,
        spaceBefore=0,
        spaceAfter=0,
        alignment=1,  # center
    )
    st_name = ParagraphStyle(
        "name",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=7,
        leading=8,
        spaceBefore=0,
        spaceAfter=0,
    )

    buf = BytesIO()

    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title=f"Orari_{site}_{week_start.isoformat()}",
    )

    elems: List[Any] = []
    week_end = week_start + timedelta(days=6)
    title = Paragraph(
        f"<b>Orari</b> - Site {site} - Settimana {week_start.strftime('%d/%m/%Y')} → {week_end.strftime('%d/%m/%Y')}",
        styles["Title"],
    )
    elems.append(title)
    elems.append(Spacer(1, 2.5 * mm))

    # Legenda colori (se presente)
    leg = [x for x in (legenda or []) if (x or {}).get("nomelegenda") and (x or {}).get("colorelegenda")]
    if leg:
        elems.append(Paragraph("<b>Legenda colori</b>", styles["Heading3"]))

        # Impaginazione compatta: 3 voci per riga (box colore + nome)
        per_row = 3
        box_w = 7 * mm
        name_w = 44 * mm
        rows: List[List[Any]] = []
        style_cmds = [
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ]

        def _hex_to_color(v: str):
            try:
                return colors.HexColor(str(v).strip())
            except Exception:
                return colors.white

        r_i = 0
        row: List[Any] = []
        for i, it in enumerate(leg):
            nome = str(it.get("nomelegenda") or "").strip()
            col = str(it.get("colorelegenda") or "").strip()
            row.append("")
            row.append(Paragraph(nome, st_cell))
            # colore cella (colonna box)
            ccol = (i % per_row) * 2
            style_cmds.append(("BACKGROUND", (ccol, r_i), (ccol, r_i), _hex_to_color(col)))
            style_cmds.append(("BOX", (ccol, r_i), (ccol, r_i), 0.25, colors.lightgrey))

            if (i + 1) % per_row == 0:
                rows.append(row)
                row = []
                r_i += 1

        if row:
            # completa colonne mancanti
            while len(row) < per_row * 2:
                row.extend(["", ""])
            rows.append(row)

        col_widths = []
        for _ in range(per_row):
            col_widths.extend([box_w, name_w])

        lt = Table(rows, colWidths=col_widths)
        lt.setStyle(TableStyle(style_cmds))
        elems.append(lt)
        elems.append(Spacer(1, 3 * mm))
    else:
        elems.append(Spacer(1, 4 * mm))

    days = [week_start + timedelta(days=i) for i in range(7)]
    day_labels = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]

    hdr: List[Any] = [Paragraph("<b>Nominativo</b>", st_head)]
    for i in range(7):
        hdr.append(Paragraph(f"{day_labels[i]}<br/>{days[i].strftime('%d/%m')}", st_head))

    # indicizza turni per nominativo/data
    idx: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for r in turni or []:
        nom = str((r or {}).get("nominativo") or "").strip()
        d = str((r or {}).get("data") or "").strip()
        if nom and d:
            idx[(nom, d)] = r

    def _cell_text(nom: str, d: date) -> Tuple[str, int]:
        r = idx.get((nom, d.isoformat()), {}) or {}

        caus1 = str(r.get("causale") or "").strip()
        sp1 = str(r.get("s_prestito") or "").strip()

        caus2 = str(r.get("causale2") or "").strip()
        sp2 = str(r.get("s_prestito2") or "").strip()

        i1 = str(r.get("inizio_1") or "").strip()
        f1 = str(r.get("fine_1") or "").strip()
        i2 = str(r.get("inizio_2") or "").strip()
        f2 = str(r.get("fine_2") or "").strip()

        mins1, _ = _mins_diff(i1, f1)
        mins2, _ = _mins_diff(i2, f2)
        prod_tot = (_is_prod_shift(caus1) and mins1 or 0) + (_is_prod_shift(caus2) and mins2 or 0)

        parts: List[str] = []

        def add_block(a: str, b: str, mins: int, caus: str, sp: str) -> None:
            a = (a or "").strip()
            b = (b or "").strip()
            caus = (caus or "").strip()
            sp = (sp or "").strip()

            if not (a or b or caus or sp):
                return

            line_parts: List[str] = []

            if a or b:
                dur = f"({_fmt_mins(mins)})" if mins else ""
                line_parts.append(f"{a}–{b} {dur}".strip())

                c_norm = caus.lower()
                if c_norm:
                    if c_norm == "prestito":
                        if sp:
                            line_parts.append(f"<i>Prestito</i>: {sp}")
                        else:
                            line_parts.append(f"<i>Prestito</i>")
                    else:
                        line_parts.append(f"<i>{caus}</i>")
                else:
                    if sp:
                        line_parts.append(f"Prestito: {sp}")
            else:
                # Nessun orario: mostra causale (in grassetto) e/o prestito
                c_norm = caus.lower()
                if c_norm:
                    if c_norm == "prestito":
                        if sp:
                            line_parts.append(f"<b>Prestito</b>: {sp}")
                        else:
                            line_parts.append(f"<b>Prestito</b>")
                    else:
                        line_parts.append(f"<b>{caus}</b>")
                if sp and c_norm != "prestito":
                    line_parts.append(f"Prestito: {sp}")

            parts.append(" ".join([x for x in line_parts if x]).strip())

        add_block(i1, f1, mins1, caus1, sp1)
        add_block(i2, f2, mins2, caus2, sp2)

        if prod_tot:
            parts.append(f"<b>Tot: {_fmt_mins(prod_tot)}</b>")

        return "<br/>".join([p for p in parts if p]), prod_tot


    # tabella orari
    data: List[List[Any]] = [hdr]
    for nom in nominativi:
        # Totali settimanali (come sotto il nominativo nella UI)
        week_mins = 0
        for d in days:
            _, dm = _cell_text(nom, d)
            week_mins += int(dm or 0)

        # Ore contrattuali (ore) in base al nominativo
        contr_h = 0
        try:
            raw = (staff_map or {}).get(nom)
            if isinstance(raw, dict):
                raw = raw.get("ore_contrattuali")
            contr_h = int(raw or 0)
        except Exception:
            contr_h = 0
        contr_mins = max(0, contr_h) * 60
        diff = week_mins - contr_mins

        diff_txt = _fmt_mins(abs(diff))
        diff_html = f"<font color='red'>-{diff_txt}</font>" if diff < 0 else diff_txt

        name_html = (
            f"<b>{nom}</b><br/>"
            f"Contr: {_fmt_mins(contr_mins)}<br/>"
            f"Tot: <b>{_fmt_mins(week_mins)}</b><br/>"
            f"Diff: {diff_html}"
        )

        row: List[Any] = [Paragraph(name_html, st_name)]
        for d in days:
            html, _ = _cell_text(nom, d)
            row.append(Paragraph(html or "", st_cell))
        data.append(row)

    # col widths: nome più largo, giorni uguali
    total_w = landscape(A4)[0] - doc.leftMargin - doc.rightMargin
    name_w = 62 * mm
    day_w = (total_w - name_w) / 7.0
    col_widths = [name_w] + [day_w] * 7

    t = Table(data, colWidths=col_widths, repeatRows=1)
    style_cmds: List[Tuple] = [
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]

    # Evidenzia weekend (sab/dom) per leggibilità
    # Colonne: 0 = nominativo, 1..7 = lun..dom
    style_cmds.append(("BACKGROUND", (6, 0), (6, -1), colors.Color(0.97, 0.97, 0.97)))
    style_cmds.append(("BACKGROUND", (7, 0), (7, -1), colors.Color(0.95, 0.95, 0.95)))

    # Strisce alternate sulle righe dati
    for r in range(1, len(data)):
        if r % 2 == 0:
            style_cmds.append(("BACKGROUND", (0, r), (-1, r), colors.Color(0.99, 0.99, 0.99)))

    t.setStyle(TableStyle(style_cmds))
    elems.append(t)

    doc.build(elems)
    return buf.getvalue()
