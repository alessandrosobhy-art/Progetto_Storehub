from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _to_number(v: Any) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        s = str(v).strip()
    except Exception:
        return 0.0
    if not s:
        return 0.0
    s = s.replace("€", "").replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return 0.0


def _is_int(x: float) -> bool:
    return abs(x - round(x)) < 1e-9


def _format_qty_it(v: Any) -> str:
    n = _to_number(v)
    if not n:
        return ""
    if _is_int(n):
        return str(int(round(n)))
    # formato italiano con 2 decimali
    s = f"{n:.2f}"
    return s.replace(".", ",")


def _p(text: str, st: ParagraphStyle) -> Paragraph:
    # Escape minimale: reportlab Paragraph interpreta tag HTML. Qui usiamo solo testo.
    text = (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return Paragraph(text, st)


def build_inventory_pdf(
    *,
    site: str,
    site_name: str = "",
    header: Dict[str, Any] | None = None,
    rows: List[Dict[str, Any]] | None = None,
) -> bytes:
    header = header or {}
    rows = rows or []

    styles = getSampleStyleSheet()

    st_title = ParagraphStyle(
        "inv_title",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=16,
        spaceAfter=6,
    )

    st_meta = ParagraphStyle(
        "inv_meta",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9,
        leading=11,
        spaceAfter=6,
    )

    st_head = ParagraphStyle(
        "inv_head",
        parent=styles["BodyText"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        alignment=1,
        spaceBefore=0,
        spaceAfter=0,
    )

    st_cell = ParagraphStyle(
        "inv_cell",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9,
        leading=11,
        spaceBefore=0,
        spaceAfter=0,
    )

    buf = BytesIO()

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title=f"Inventario_{site}",
    )

    elems: List[Any] = []

    data_mov = str(header.get("data_mov") or "").strip()
    mov_type = str(header.get("mov_type") or "").strip()

    title = f"Inventario" + (f" - Site {site}" if site else "")
    if site_name:
        title += f" ({site_name})"
    elems.append(Paragraph(title, st_title))

    meta_parts = []
    if data_mov:
        try:
            dt = datetime.fromisoformat(data_mov)
            meta_parts.append(f"Data: {dt.strftime('%d/%m/%Y')}")
        except Exception:
            meta_parts.append(f"Data: {data_mov}")
    if mov_type:
        meta_parts.append(f"Tipo: {mov_type}")

    suppliers = header.get("supplier_names")
    if isinstance(suppliers, list):
        clean = [str(s).strip() for s in suppliers if str(s).strip()]
        if clean:
            meta_parts.append("Fornitori: " + ", ".join(clean))
    else:
        sup = str(header.get("supplier_name") or "").strip()
        if sup:
            meta_parts.append(f"Fornitore: {sup}")

    if meta_parts:
        elems.append(Paragraph(" — ".join(meta_parts), st_meta))
    elems.append(Spacer(1, 2 * mm))

    table_data: List[List[Any]] = []
    table_data.append([
        _p("Fornitore", st_head),
        _p("Descrizione", st_head),
        _p("CAR", st_head),
        _p("INT", st_head),
        _p("PEZ", st_head),
        _p("KG", st_head),
    ])

    for r in rows:
        if not isinstance(r, dict):
            continue
        supplier = str(r.get("fornitore") or r.get("supplier") or r.get("supplier_name") or "").strip()
        descr = str(r.get("descrizione") or r.get("description") or r.get("desc") or "").strip()
        if not (supplier or descr):
            continue

        table_data.append([
            _p(supplier, st_cell),
            _p(descr, st_cell),
            _p(_format_qty_it(r.get("car")), st_cell),
            _p(_format_qty_it(r.get("int")) or _format_qty_it(r.get("interno")), st_cell),
            _p(_format_qty_it(r.get("pez")), st_cell),
            _p(_format_qty_it(r.get("kg")), st_cell),
        ])

    # larghezze: A4 (210mm) - margini 20mm = 190mm utili
    col_widths = [35 * mm, 85 * mm, 16 * mm, 16 * mm, 16 * mm, 16 * mm]

    tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                ("ALIGN", (2, 0), (-1, 0), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )

    elems.append(tbl)

    doc.build(elems)
    return buf.getvalue()
