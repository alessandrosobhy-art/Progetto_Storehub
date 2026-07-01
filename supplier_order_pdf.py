from __future__ import annotations

from io import BytesIO
from typing import Any, Dict, List

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _money(v: Any) -> str:
    try:
        n = float(v or 0)
    except Exception:
        n = 0.0
    s = f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} €"


def _qty(v: Any) -> str:
    try:
        n = float(v or 0)
    except Exception:
        return ""
    if abs(n - round(n)) < 1e-9:
        return str(int(round(n)))
    return f"{n:.2f}".replace(".", ",")


def _escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _p(text: str, st: ParagraphStyle) -> Paragraph:
    return Paragraph(_escape(text or ""), st)


def build_supplier_order_pdf(detail: Dict[str, Any]) -> bytes:
    header = (detail or {}).get("header") or {}
    rows = (detail or {}).get("rows") or []

    styles = getSampleStyleSheet()
    st_title = ParagraphStyle("title", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=15, leading=18, spaceAfter=8)
    st_meta = ParagraphStyle("meta", parent=styles["BodyText"], fontSize=9, leading=11, spaceAfter=3)
    st_head = ParagraphStyle("head", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=8.5, alignment=1)
    st_cell = ParagraphStyle("cell", parent=styles["BodyText"], fontSize=8.5, leading=10)

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title=f"Ordine_{header.get('numero_ordine') or ''}",
    )

    elems: List[Any] = []
    elems.append(Paragraph(f"Ordine fornitore {header.get('numero_ordine') or ''}", st_title))
    elems.append(Paragraph(f"Fornitore: {header.get('supplier_name') or '-'}", st_meta))
    elems.append(Paragraph(f"Store ordinante: {header.get('site') or '-'} {(' - ' + str(header.get('store_name') or '')) if header.get('store_name') else ''}", st_meta))
    elems.append(Paragraph(f"Data ordine: {header.get('order_date') or '-'}", st_meta))
    elems.append(Paragraph(f"Data consegna richiesta: {header.get('requested_delivery_date') or '-'}", st_meta))
    if header.get("note_ordine"):
        elems.append(Paragraph(f"Note: {header.get('note_ordine')}", st_meta))
    elems.append(Spacer(1, 2 * mm))

    table_data: List[List[Any]] = [[
        _p("Descrizione", st_head),
        _p("Q.tà", st_head),
        _p("Prezzo stim.", st_head),
        _p("Subtotale", st_head),
    ]]

    for row in rows:
        table_data.append([
            _p(str(row.get("descrizione") or ""), st_cell),
            _p(_qty(row.get("qty_ordered")), st_cell),
            _p(_money(row.get("estimated_price")), st_cell),
            _p(_money(row.get("subtotal")), st_cell),
        ])

    tbl = Table(table_data, colWidths=[100 * mm, 20 * mm, 30 * mm, 30 * mm], repeatRows=1)
    tbl.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dfe8f7")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#b8c3d9")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ])
    )
    elems.append(tbl)
    elems.append(Spacer(1, 4 * mm))
    elems.append(Paragraph(f"Totale stimato: {_money(header.get('total_estimated'))}", st_meta))
    elems.append(Paragraph("Totale stimato salvo verifica prezzi finali/DDT/fattura.", st_meta))

    doc.build(elems)
    return buf.getvalue()

