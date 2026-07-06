from __future__ import annotations

from app_logging import log_swallowed
import os
import smtplib
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app_db import get_connection_sqlserver_database, get_storehub_database_name
from inventory_repository import get_delivery_avg_prices_last_weeks
from supplier_orders_repository import (
    ensure_supplier_orders_schema,
    list_fornitore_contacts,
    list_fornitori,
    list_prices_for_supplier_all_types,
)


TABLE_ORDERS = "OrdiniFornitori"
TABLE_ORDER_ROWS = "OrdiniFornitoriRighe"
TABLE_ORDER_LOG = "OrdiniFornitoriLog"
TABLE_SUPPLIER_USERS = "FornitoriUtenti"

ORDER_STATUS_INVITED = "Invitato"
ORDER_STATUS_VIEWED = "Visualizzato"
ORDER_STATUS_COMPLETED = "Caricato"
ORDER_STATUS_SENT = "Inviato"

ORDER_MODE_ONLINE = "Online"
ORDER_MODE_MAIL = "Mail"

PRICE_SOURCE_LISTINO = "Listino"
PRICE_SOURCE_STORICO = "Storico"


def _conn(read_only: bool = False):
    return get_connection_sqlserver_database(get_storehub_database_name(), read_only=read_only)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _clean(v: Any) -> str:
    return str(v or "").strip()


def normalize_order_mode(v: Any) -> str:
    s = _clean(v).strip().lower()
    if s == "online":
        return ORDER_MODE_ONLINE
    return ORDER_MODE_MAIL


def _safe_decimal(v: Any) -> Decimal:
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    try:
        s = str(v).strip()
    except Exception:
        return Decimal("0")
    if not s:
        return Decimal("0")
    s = s.replace("€", "").replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return Decimal(s)
    except Exception:
        return Decimal("0")


def _quantize_money(v: Any) -> Decimal:
    return _safe_decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _int_or_none(v: Any) -> Optional[int]:
    s = _clean(v)
    if not s:
        return None
    try:
        return int(float(s.replace(",", ".")))
    except Exception:
        return None


def _date_only(v: Any) -> Optional[str]:
    s = _clean(v)
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except Exception:
            continue
    try:
        return datetime.fromisoformat(s).date().isoformat()
    except Exception:
        return None


def _parse_iso_date(v: Any) -> Optional[datetime]:
    s = _date_only(v)
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d")


def _slug_part(v: str) -> str:
    out = []
    for ch in _clean(v).upper():
        if ch.isalnum():
            out.append(ch)
        elif ch in {" ", "-", "_", "/"}:
            out.append("-")
    txt = "".join(out).strip("-")
    while "--" in txt:
        txt = txt.replace("--", "-")
    return txt or "STORE"


def _rows_to_dict(cur) -> List[Dict[str, Any]]:
    cols = [c[0] for c in (cur.description or [])]
    out: List[Dict[str, Any]] = []
    for row in cur.fetchall():
        item = {}
        for idx, col in enumerate(cols):
            val = row[idx]
            if isinstance(val, datetime):
                item[col] = val.isoformat()
            elif isinstance(val, Decimal):
                item[col] = float(val)
            else:
                item[col] = val
        out.append(item)
    return out


def ensure_supplier_order_flow_schema() -> None:
    ensure_supplier_orders_schema()
    conn = _conn(False)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
IF OBJECT_ID('dbo.{TABLE_ORDERS}','U') IS NULL
BEGIN
  CREATE TABLE dbo.{TABLE_ORDERS} (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    numero_ordine NVARCHAR(50) NOT NULL,
    site NVARCHAR(50) NOT NULL,
    store_name NVARCHAR(255) NULL,
    supplier_row_uuid UNIQUEIDENTIFIER NOT NULL,
    supplier_name NVARCHAR(255) NOT NULL,
    supplier_contact_row_uuid UNIQUEIDENTIFIER NULL,
    order_date DATE NOT NULL,
    requested_delivery_date DATE NOT NULL,
    order_mode NVARCHAR(20) NOT NULL,
    status NVARCHAR(30) NOT NULL,
    note_ordine NVARCHAR(MAX) NULL,
    total_estimated DECIMAL(18,2) NOT NULL DEFAULT 0,
    viewed_at DATETIME2 NULL,
    completed_at DATETIME2 NULL,
    sent_email_at DATETIME2 NULL,
    pdf_rel_path NVARCHAR(500) NULL,
    pdf_filename NVARCHAR(255) NULL,
    ddt_supplier_code NVARCHAR(100) NULL,
    ddt_data_doc NVARCHAR(20) NULL,
    ddt_data_rif NVARCHAR(20) NULL,
    ddt_numero NVARCHAR(100) NULL,
    ddt_created_at DATETIME2 NULL,
    created_by NVARCHAR(100) NULL,
    created_by_name NVARCHAR(255) NULL,
    created_by_email NVARCHAR(255) NULL,
    updated_by NVARCHAR(100) NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_{TABLE_ORDERS}_numero ON dbo.{TABLE_ORDERS}(numero_ordine);
  CREATE INDEX IX_{TABLE_ORDERS}_site ON dbo.{TABLE_ORDERS}(site, order_date DESC);
  CREATE INDEX IX_{TABLE_ORDERS}_supplier ON dbo.{TABLE_ORDERS}(supplier_row_uuid, status, requested_delivery_date);
END

IF OBJECT_ID('dbo.{TABLE_ORDER_ROWS}','U') IS NULL
BEGIN
  CREATE TABLE dbo.{TABLE_ORDER_ROWS} (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    order_row_uuid UNIQUEIDENTIFIER NOT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    tipo_listino NVARCHAR(100) NULL,
    product_code NVARCHAR(100) NULL,
    descrizione NVARCHAR(255) NOT NULL,
    gruppo NVARCHAR(100) NULL,
    unita NVARCHAR(50) NULL,
    qta_car INT NULL,
    qta_int INT NULL,
    conv DECIMAL(18,4) NULL,
    qty_colli DECIMAL(18,4) NULL,
    qty_pezzi DECIMAL(18,4) NULL,
    qty_ordered DECIMAL(18,4) NOT NULL DEFAULT 0,
    estimated_price DECIMAL(18,4) NULL,
    price_source NVARCHAR(20) NULL,
    subtotal DECIMAL(18,2) NOT NULL DEFAULT 0,
    picked BIT NOT NULL DEFAULT 0,
    lotto NVARCHAR(100) NULL,
    scadenza DATE NULL,
    note_fornitore NVARCHAR(MAX) NULL,
    picked_at DATETIME2 NULL,
    updated_by_supplier_at DATETIME2 NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE INDEX IX_{TABLE_ORDER_ROWS}_order ON dbo.{TABLE_ORDER_ROWS}(order_row_uuid, sort_order, created_at);
END

IF OBJECT_ID('dbo.{TABLE_ORDER_LOG}','U') IS NULL
BEGIN
  CREATE TABLE dbo.{TABLE_ORDER_LOG} (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    order_row_uuid UNIQUEIDENTIFIER NOT NULL,
    old_status NVARCHAR(30) NULL,
    new_status NVARCHAR(30) NOT NULL,
    event_type NVARCHAR(50) NOT NULL,
    note NVARCHAR(MAX) NULL,
    actor_uid NVARCHAR(100) NULL,
    actor_name NVARCHAR(255) NULL,
    actor_email NVARCHAR(255) NULL,
    actor_role NVARCHAR(50) NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE INDEX IX_{TABLE_ORDER_LOG}_order ON dbo.{TABLE_ORDER_LOG}(order_row_uuid, created_at DESC);
END

IF OBJECT_ID('dbo.{TABLE_SUPPLIER_USERS}','U') IS NULL
BEGIN
  CREATE TABLE dbo.{TABLE_SUPPLIER_USERS} (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    user_uid NVARCHAR(100) NOT NULL,
    supplier_row_uuid UNIQUEIDENTIFIER NOT NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_{TABLE_SUPPLIER_USERS}_pair ON dbo.{TABLE_SUPPLIER_USERS}(user_uid, supplier_row_uuid);
  CREATE INDEX IX_{TABLE_SUPPLIER_USERS}_user ON dbo.{TABLE_SUPPLIER_USERS}(user_uid);
END

IF COL_LENGTH('dbo.{TABLE_ORDERS}', 'archived_at') IS NULL
BEGIN
  ALTER TABLE dbo.{TABLE_ORDERS} ADD archived_at DATETIME2 NULL;
END

IF COL_LENGTH('dbo.{TABLE_ORDERS}', 'reopened_at') IS NULL
BEGIN
  ALTER TABLE dbo.{TABLE_ORDERS} ADD reopened_at DATETIME2 NULL;
END

IF COL_LENGTH('dbo.{TABLE_ORDER_ROWS}', 'supplier_added') IS NULL
BEGIN
  ALTER TABLE dbo.{TABLE_ORDER_ROWS} ADD supplier_added BIT NOT NULL CONSTRAINT DF_{TABLE_ORDER_ROWS}_supplier_added DEFAULT 0;
END

IF COL_LENGTH('dbo.{TABLE_ORDER_ROWS}', 'replacement_for_row_uuid') IS NULL
BEGIN
  ALTER TABLE dbo.{TABLE_ORDER_ROWS} ADD replacement_for_row_uuid UNIQUEIDENTIFIER NULL;
END
"""
        )
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('supplier_order_flow_repository:280')


def list_supplier_contacts_resolved() -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for supplier in list_fornitori():
        sid = _clean(supplier.get("row_uuid"))
        if sid:
            out[sid] = list_fornitore_contacts(sid)
    return out


def get_supplier_by_uuid(row_uuid: str) -> Optional[Dict[str, Any]]:
    rid = _clean(row_uuid)
    if not rid:
        return None
    for item in list_fornitori():
        if _clean(item.get("row_uuid")) == rid:
            return item
    return None


def resolve_supplier_contact(fornitore_row_uuid: str, store_code: str, order_mode: str) -> Dict[str, Any]:
    supplier = get_supplier_by_uuid(fornitore_row_uuid)
    if not supplier:
        raise ValueError("Fornitore non trovato.")
    contacts = list_fornitore_contacts(fornitore_row_uuid)
    mode = normalize_order_mode(order_mode)
    store_code = _clean(store_code)

    preferred = [
        c for c in contacts
        if (_clean(c.get("TipoOrdine")) or ORDER_MODE_MAIL) == mode and store_code in (c.get("store_codes") or [])
    ]
    fallback_mode = [
        c for c in contacts
        if (_clean(c.get("TipoOrdine")) or ORDER_MODE_MAIL) == mode and not (c.get("store_codes") or [])
    ]
    fallback_store = [c for c in contacts if store_code in (c.get("store_codes") or [])]
    generic = [c for c in contacts if not (c.get("store_codes") or [])]
    chosen = (preferred or fallback_mode or fallback_store or generic or [None])[0]

    if chosen:
        return {
            "row_uuid": _clean(chosen.get("row_uuid")),
            "Referente": _clean(chosen.get("Referente")),
            "Email": _clean(chosen.get("Email")),
            "Telefono1": _clean(chosen.get("Telefono1")),
            "Telefono2": _clean(chosen.get("Telefono2")),
            "TipoOrdine": mode,
            "store_codes": chosen.get("store_codes") or [],
        }

    return {
        "row_uuid": "",
        "Referente": _clean(supplier.get("Referente")),
        "Email": _clean(supplier.get("Email")),
        "Telefono1": _clean(supplier.get("Telefono1")),
        "Telefono2": _clean(supplier.get("Telefono2")),
        "TipoOrdine": mode,
        "store_codes": [],
    }


def load_supplier_pricelist_for_order(store_code: str, supplier_name: str) -> List[Dict[str, Any]]:
    source = list_prices_for_supplier_all_types(supplier_name, max_rows=5000, store_code=store_code) or {}
    rows = source.get("rows") or []
    hist = get_delivery_avg_prices_last_weeks(store_code, supplier_name, weeks=4) or {}
    out: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows or []):
        if not isinstance(row, dict):
            continue
        descr = _clean(row.get("Descrizione"))
        key = descr.strip().lower()
        listino_price = _safe_decimal(row.get("PREZZO"))
        storico_price = _safe_decimal(hist.get(key))
        if listino_price > 0:
            price = listino_price
            price_source = PRICE_SOURCE_LISTINO
        else:
            price = storico_price
            price_source = PRICE_SOURCE_STORICO if storico_price > 0 else ""
        out.append(
            {
                "sort_order": idx,
                "tipo_listino": _clean(row.get("_TipoListino") or row.get("TipoListino")),
                "product_code": _clean(row.get("CODICE")),
                "descrizione": descr,
                "gruppo": _clean(row.get("GRUPPO")),
                "unita": _clean(row.get("UNITA")),
                "qta_car": _int_or_none(row.get("QTACAR")),
                "qta_int": _int_or_none(row.get("QTAINT")),
                "conv": float(_safe_decimal(row.get("CONV"))) if _safe_decimal(row.get("CONV")) else None,
                "estimated_price": float(price) if price else 0.0,
                "price_source": price_source,
                "qty_colli": "",
                "qty_pezzi": "",
                "qty_ordered": 0.0,
                "subtotal": 0.0,
            }
        )
    return out


def _build_order_no(site: str) -> str:
    now = datetime.now()
    return f"OF-{_slug_part(site)}-{now.strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:6].upper()}"


def _insert_status_log(cur, order_row_uuid: str, old_status: Optional[str], new_status: str, event_type: str, actor: Dict[str, Any], note: str | None = None) -> None:
    cur.execute(
        f"""
INSERT INTO dbo.{TABLE_ORDER_LOG} (order_row_uuid, old_status, new_status, event_type, note, actor_uid, actor_name, actor_email, actor_role)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
""",
        (
            order_row_uuid,
            old_status,
            new_status,
            event_type,
            note,
            _clean(actor.get("uid")),
            _clean(actor.get("name")),
            _clean(actor.get("email")),
            _clean(actor.get("role")),
        ),
    )


def create_supplier_order(
    *,
    site: str,
    store_name: str,
    supplier_row_uuid: str,
    supplier_name: str,
    supplier_contact: Dict[str, Any],
    requested_delivery_date: str,
    order_mode: str,
    note_ordine: str,
    rows: List[Dict[str, Any]],
    actor: Dict[str, Any],
) -> Dict[str, Any]:
    ensure_supplier_order_flow_schema()
    order_mode = normalize_order_mode(order_mode)
    status = ORDER_STATUS_INVITED if order_mode == ORDER_MODE_ONLINE else ORDER_STATUS_SENT
    order_no = _build_order_no(site)
    order_date = datetime.now().date().isoformat()
    total_estimated = sum((_quantize_money(r.get("subtotal")) for r in rows), Decimal("0"))
    conn = _conn(False)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
INSERT INTO dbo.{TABLE_ORDERS}
  (numero_ordine, site, store_name, supplier_row_uuid, supplier_name, supplier_contact_row_uuid, order_date, requested_delivery_date,
   order_mode, status, note_ordine, total_estimated, sent_email_at, created_by, created_by_name, created_by_email, updated_by)
OUTPUT inserted.row_uuid
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""",
            (
                order_no,
                site,
                store_name or None,
                supplier_row_uuid,
                supplier_name,
                _clean(supplier_contact.get("row_uuid")) or None,
                order_date,
                requested_delivery_date,
                order_mode,
                status,
                note_ordine or None,
                total_estimated,
                _utcnow() if order_mode == ORDER_MODE_MAIL else None,
                _clean(actor.get("uid")) or None,
                _clean(actor.get("name")) or None,
                _clean(actor.get("email")) or None,
                _clean(actor.get("uid")) or None,
            ),
        )
        order_row_uuid = str(cur.fetchone()[0])
        for idx, row in enumerate(rows):
            cur.execute(
                f"""
INSERT INTO dbo.{TABLE_ORDER_ROWS}
  (order_row_uuid, sort_order, tipo_listino, product_code, descrizione, gruppo, unita, qta_car, qta_int, conv,
   qty_colli, qty_pezzi, qty_ordered, estimated_price, price_source, subtotal)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""",
                (
                    order_row_uuid,
                    int(row.get("sort_order") or idx),
                    _clean(row.get("tipo_listino")) or None,
                    _clean(row.get("product_code")) or None,
                    _clean(row.get("descrizione")),
                    _clean(row.get("gruppo")) or None,
                    _clean(row.get("unita")) or None,
                    row.get("qta_car"),
                    row.get("qta_int"),
                    _safe_decimal(row.get("conv")) if row.get("conv") not in (None, "") else None,
                    _safe_decimal(row.get("qty_colli")) if _clean(row.get("qty_colli")) else None,
                    _safe_decimal(row.get("qty_pezzi")) if _clean(row.get("qty_pezzi")) else None,
                    _safe_decimal(row.get("qty_ordered")),
                    _safe_decimal(row.get("estimated_price")) if row.get("estimated_price") not in (None, "") else None,
                    _clean(row.get("price_source")) or None,
                    _quantize_money(row.get("subtotal")),
                ),
            )
        _insert_status_log(cur, order_row_uuid, None, status, "create", actor, note_ordine or None)
        conn.commit()
        return {"ok": True, "row_uuid": order_row_uuid, "numero_ordine": order_no, "status": status}
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('supplier_order_flow_repository:494')


def delete_supplier_order(order_row_uuid: str) -> None:
    rid = _clean(order_row_uuid)
    if not rid:
        return
    conn = _conn(False)
    try:
        cur = conn.cursor()
        cur.execute(f"DELETE FROM dbo.{TABLE_ORDER_LOG} WHERE order_row_uuid=?", (rid,))
        cur.execute(f"DELETE FROM dbo.{TABLE_ORDER_ROWS} WHERE order_row_uuid=?", (rid,))
        cur.execute(f"DELETE FROM dbo.{TABLE_ORDERS} WHERE row_uuid=?", (rid,))
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('supplier_order_flow_repository:512')


def update_order_pdf_and_email(order_row_uuid: str, *, pdf_rel_path: str, pdf_filename: str, sent_email_at: bool) -> None:
    conn = _conn(False)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
UPDATE dbo.{TABLE_ORDERS}
SET pdf_rel_path=?, pdf_filename=?, sent_email_at=?, updated_at=SYSUTCDATETIME()
WHERE row_uuid=?
""",
            (pdf_rel_path, pdf_filename, _utcnow() if sent_email_at else None, order_row_uuid),
        )
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('supplier_order_flow_repository:532')


def list_supplier_orders(
    *,
    allowed_sites: Optional[List[str]] = None,
    supplier_row_uuids: Optional[List[str]] = None,
    viewer_role: str = "internal",
    filters: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    ensure_supplier_order_flow_schema()
    filters = filters or {}
    where = ["1=1"]
    params: List[Any] = []

    if viewer_role == "supplier":
        ids = [_clean(x) for x in (supplier_row_uuids or []) if _clean(x)]
        if not ids:
            return []
        placeholders = ",".join(["?"] * len(ids))
        where.append(f"o.supplier_row_uuid IN ({placeholders})")
        params.extend(ids)
        where.append("o.order_mode = ?")
        params.append(ORDER_MODE_ONLINE)
    else:
        sites = [_clean(x) for x in (allowed_sites or []) if _clean(x)]
        if sites:
            placeholders = ",".join(["?"] * len(sites))
            where.append(f"o.site IN ({placeholders})")
            params.extend(sites)

    store_filter = _clean(filters.get("store"))
    if store_filter:
        where.append("o.site = ?")
        params.append(store_filter)
    supplier_filter = _clean(filters.get("supplier"))
    if supplier_filter:
        where.append("o.supplier_row_uuid = ?")
        params.append(supplier_filter)
    status_filter = _clean(filters.get("status"))
    if status_filter:
        where.append("o.status = ?")
        params.append(status_filter)
    mode_filter = _clean(filters.get("order_mode"))
    if mode_filter:
        where.append("o.order_mode = ?")
        params.append(mode_filter)
    date_from = _date_only(filters.get("order_date_from"))
    if date_from:
        where.append("o.order_date >= ?")
        params.append(date_from)
    date_to = _date_only(filters.get("order_date_to"))
    if date_to:
        where.append("o.order_date <= ?")
        params.append(date_to)
    deliv_from = _date_only(filters.get("delivery_date_from"))
    if deliv_from:
        where.append("o.requested_delivery_date >= ?")
        params.append(deliv_from)
    deliv_to = _date_only(filters.get("delivery_date_to"))
    if deliv_to:
        where.append("o.requested_delivery_date <= ?")
        params.append(deliv_to)

    conn = _conn(True)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
SELECT
  o.row_uuid,
  o.numero_ordine,
  o.site,
  o.store_name,
  o.supplier_row_uuid,
  o.supplier_name,
  o.order_date,
  o.requested_delivery_date,
  o.order_mode,
  o.status,
  o.note_ordine,
  o.total_estimated,
  o.pdf_rel_path,
  o.pdf_filename,
  o.ddt_supplier_code,
  o.ddt_data_doc,
  o.ddt_data_rif,
  o.ddt_numero,
  o.viewed_at,
  o.completed_at,
  o.archived_at,
  o.reopened_at,
  o.sent_email_at,
  o.created_by_name,
  o.created_by_email
FROM dbo.{TABLE_ORDERS} o
WHERE {' AND '.join(where)}
ORDER BY o.order_date DESC, o.created_at DESC
""",
            params,
        )
        return _rows_to_dict(cur)
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('supplier_order_flow_repository:638')


def get_supplier_order_detail(order_row_uuid: str) -> Optional[Dict[str, Any]]:
    rid = _clean(order_row_uuid)
    if not rid:
        return None
    conn = _conn(True)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
SELECT row_uuid, numero_ordine, site, store_name, supplier_row_uuid, supplier_name, supplier_contact_row_uuid,
       order_date, requested_delivery_date, order_mode, status, note_ordine, total_estimated, viewed_at, completed_at,
       archived_at, reopened_at,
       sent_email_at, pdf_rel_path, pdf_filename, ddt_supplier_code, ddt_data_doc, ddt_data_rif, ddt_numero, ddt_created_at,
       created_by, created_by_name, created_by_email, created_at, updated_at
FROM dbo.{TABLE_ORDERS}
WHERE row_uuid = ?
""",
            (rid,),
        )
        header_rows = _rows_to_dict(cur)
        if not header_rows:
            return None
        header = header_rows[0]

        cur.execute(
            f"""
SELECT row_uuid, sort_order, tipo_listino, product_code, descrizione, gruppo, unita, qta_car, qta_int, conv,
       qty_colli, qty_pezzi, qty_ordered, estimated_price, price_source, subtotal, picked, lotto, scadenza,
       note_fornitore, picked_at, updated_by_supplier_at, supplier_added, replacement_for_row_uuid
FROM dbo.{TABLE_ORDER_ROWS}
WHERE order_row_uuid = ?
ORDER BY sort_order, created_at
""",
            (rid,),
        )
        rows = _rows_to_dict(cur)

        cur.execute(
            f"""
SELECT old_status, new_status, event_type, note, actor_uid, actor_name, actor_email, actor_role, created_at
FROM dbo.{TABLE_ORDER_LOG}
WHERE order_row_uuid = ?
ORDER BY created_at DESC
""",
            (rid,),
        )
        logs = _rows_to_dict(cur)
        return {"header": header, "rows": rows, "logs": logs}
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('supplier_order_flow_repository:693')


def mark_order_viewed(order_row_uuid: str, actor: Dict[str, Any]) -> None:
    detail = get_supplier_order_detail(order_row_uuid)
    if not detail:
        return
    header = detail["header"]
    if _clean(header.get("status")) != ORDER_STATUS_INVITED:
        return
    conn = _conn(False)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
UPDATE dbo.{TABLE_ORDERS}
SET status=?, viewed_at=COALESCE(viewed_at, SYSUTCDATETIME()), updated_at=SYSUTCDATETIME(), updated_by=?
WHERE row_uuid=?
""",
            (ORDER_STATUS_VIEWED, _clean(actor.get("uid")) or None, order_row_uuid),
        )
        _insert_status_log(cur, order_row_uuid, ORDER_STATUS_INVITED, ORDER_STATUS_VIEWED, "view", actor)
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('supplier_order_flow_repository:720')


def save_supplier_order_line(
    order_row_uuid: str,
    line_row_uuid: str,
    *,
    picked: bool,
    lotto: str,
    scadenza: str,
    note_fornitore: str,
) -> None:
    scadenza_iso = _date_only(scadenza)
    if _clean(scadenza) and not scadenza_iso:
        raise ValueError("Formato scadenza non valido.")
    conn = _conn(False)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
UPDATE dbo.{TABLE_ORDER_ROWS}
SET picked=?, lotto=?, scadenza=?, note_fornitore=?, picked_at=?, updated_by_supplier_at=SYSUTCDATETIME(), updated_at=SYSUTCDATETIME()
WHERE row_uuid=? AND order_row_uuid=?
""",
            (
                1 if picked else 0,
                _clean(lotto) or None,
                scadenza_iso,
                _clean(note_fornitore) or None,
                _utcnow() if picked else None,
                _clean(line_row_uuid),
                _clean(order_row_uuid),
            ),
        )
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('supplier_order_flow_repository:759')


def complete_supplier_order(order_row_uuid: str, actor: Dict[str, Any]) -> None:
    detail = get_supplier_order_detail(order_row_uuid)
    if not detail:
        raise ValueError("Ordine non trovato.")
    header = detail["header"]
    rows = detail["rows"] or []
    if not rows:
        raise ValueError("Ordine senza righe.")
    if not any(bool(r.get("picked")) for r in rows):
        raise ValueError("Per completare l'ordine serve almeno una riga preparata.")
    old_status = _clean(header.get("status")) or ORDER_STATUS_INVITED
    conn = _conn(False)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
UPDATE dbo.{TABLE_ORDERS}
SET status=?, completed_at=SYSUTCDATETIME(), archived_at=SYSUTCDATETIME(), updated_at=SYSUTCDATETIME(), updated_by=?
WHERE row_uuid=?
""",
            (ORDER_STATUS_COMPLETED, _clean(actor.get("uid")) or None, order_row_uuid),
        )
        _insert_status_log(cur, order_row_uuid, old_status, ORDER_STATUS_COMPLETED, "complete", actor)
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('supplier_order_flow_repository:790')


def link_order_to_ddt(order_row_uuid: str, *, supplier_code: str, data_doc: str, data_rif: str, numero_ddt: str | None = None) -> None:
    conn = _conn(False)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
UPDATE dbo.{TABLE_ORDERS}
SET ddt_supplier_code=?, ddt_data_doc=?, ddt_data_rif=?, ddt_numero=?, ddt_created_at=SYSUTCDATETIME(), updated_at=SYSUTCDATETIME()
WHERE row_uuid=?
""",
            (_clean(supplier_code), _clean(data_doc), _clean(data_rif), _clean(numero_ddt) or None, _clean(order_row_uuid)),
        )
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('supplier_order_flow_repository:810')


def get_supplier_user_supplier_ids(user_email: str) -> List[str]:
    email = _clean(user_email).lower()
    if not email:
        return []
    conn = _conn(True)
    try:
        cur = conn.cursor()
        cur.execute(
            """
SELECT DISTINCT f.row_uuid
FROM dbo.Fornitori f
LEFT JOIN dbo.FornitoriContatti c ON c.fornitore_row_uuid = f.row_uuid
WHERE LOWER(ISNULL(c.Email, '')) = ? OR LOWER(ISNULL(f.Email, '')) = ?
""",
            (email, email),
        )
        return [str(r[0]) for r in cur.fetchall() if r[0]]
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('supplier_order_flow_repository:834')


def get_supplier_user_assignments(user_uid: str) -> List[str]:
    uid = _clean(user_uid)
    if not uid:
        return []
    ensure_supplier_order_flow_schema()
    conn = _conn(True)
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT supplier_row_uuid FROM dbo.{TABLE_SUPPLIER_USERS} WHERE user_uid=? ORDER BY supplier_row_uuid",
            (uid,),
        )
        return [str(r[0]) for r in cur.fetchall() if r[0]]
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('supplier_order_flow_repository:854')


def replace_supplier_user_assignments(user_uid: str, supplier_row_uuids: List[str]) -> None:
    uid = _clean(user_uid)
    if not uid:
        return
    ensure_supplier_order_flow_schema()
    clean_ids = sorted({_clean(x) for x in (supplier_row_uuids or []) if _clean(x)})
    conn = _conn(False)
    try:
        cur = conn.cursor()
        cur.execute(f"DELETE FROM dbo.{TABLE_SUPPLIER_USERS} WHERE user_uid=?", (uid,))
        for sid in clean_ids:
            cur.execute(
                f"INSERT INTO dbo.{TABLE_SUPPLIER_USERS} (user_uid, supplier_row_uuid) VALUES (?, ?)",
                (uid, sid),
            )
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('supplier_order_flow_repository:877')


def resolve_supplier_user_supplier_ids(*, user_uid: str, user_email: str) -> List[str]:
    mapped = get_supplier_user_assignments(user_uid)
    if mapped:
        return mapped
    return get_supplier_user_supplier_ids(user_email)


def reopen_supplier_order(order_row_uuid: str, actor: Dict[str, Any]) -> None:
    detail = get_supplier_order_detail(order_row_uuid)
    if not detail:
        raise ValueError("Ordine non trovato.")
    header = detail["header"]
    old_status = _clean(header.get("status")) or ORDER_STATUS_COMPLETED
    conn = _conn(False)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
UPDATE dbo.{TABLE_ORDERS}
SET status=?, archived_at=NULL, reopened_at=SYSUTCDATETIME(), updated_at=SYSUTCDATETIME(), updated_by=?
WHERE row_uuid=?
""",
            (ORDER_STATUS_VIEWED, _clean(actor.get("uid")) or None, order_row_uuid),
        )
        _insert_status_log(cur, order_row_uuid, old_status, ORDER_STATUS_VIEWED, "reopen", actor)
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('supplier_order_flow_repository:910')


def add_supplier_order_selected_product(
    order_row_uuid: str,
    *,
    selected_product: Dict[str, Any],
    qty_ordered: Any,
    replacement_for_row_uuid: str | None,
    picked: bool,
    lotto: str,
    scadenza: str,
    note_fornitore: str,
) -> None:
    detail = get_supplier_order_detail(order_row_uuid)
    if not detail:
        raise ValueError("Ordine non trovato.")
    qty = _safe_decimal(qty_ordered)
    if qty <= 0:
        raise ValueError("Quantità non valida.")
    scadenza_iso = _date_only(scadenza)
    if _clean(scadenza) and not scadenza_iso:
        raise ValueError("Formato scadenza non valido.")
    rows = detail.get("rows") or []
    current_max = max((int(r.get("sort_order") or 0) for r in rows), default=0)
    descr = _clean(selected_product.get("descrizione"))
    if not descr:
        raise ValueError("Prodotto non valido.")
    price = _safe_decimal(selected_product.get("estimated_price"))
    subtotal = _quantize_money(price * qty)
    conn = _conn(False)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
INSERT INTO dbo.{TABLE_ORDER_ROWS}
  (order_row_uuid, sort_order, tipo_listino, product_code, descrizione, gruppo, unita, qta_car, qta_int, conv,
   qty_colli, qty_pezzi, qty_ordered, estimated_price, price_source, subtotal, picked, lotto, scadenza, note_fornitore,
   picked_at, updated_by_supplier_at, supplier_added, replacement_for_row_uuid)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, SYSUTCDATETIME(), 1, ?)
""",
            (
                order_row_uuid,
                current_max + 10,
                _clean(selected_product.get("tipo_listino")) or None,
                _clean(selected_product.get("product_code")) or None,
                descr,
                _clean(selected_product.get("gruppo")) or None,
                _clean(selected_product.get("unita")) or None,
                selected_product.get("qta_car"),
                selected_product.get("qta_int"),
                _safe_decimal(selected_product.get("conv")) if selected_product.get("conv") not in (None, "") else None,
                None,
                None,
                qty,
                price if price else None,
                _clean(selected_product.get("price_source")) or None,
                subtotal,
                1 if picked else 0,
                _clean(lotto) or None,
                scadenza_iso,
                _clean(note_fornitore) or None,
                _utcnow() if picked else None,
                _clean(replacement_for_row_uuid) or None,
            ),
        )
        cur.execute(
            f"""
UPDATE dbo.{TABLE_ORDERS}
SET updated_at=SYSUTCDATETIME(), updated_by=?
WHERE row_uuid=?
""",
            (None, order_row_uuid),
        )
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('supplier_order_flow_repository:989')


def build_order_pdf_bytes(detail: Dict[str, Any]) -> bytes:
    from supplier_order_pdf import build_supplier_order_pdf

    return build_supplier_order_pdf(detail)


def save_order_pdf(detail: Dict[str, Any], pdf_bytes: bytes) -> Dict[str, str]:
    header = detail.get("header") or {}
    order_no = _clean(header.get("numero_ordine")) or _clean(header.get("row_uuid"))
    base_dir = Path(__file__).resolve().parent / "generated" / "supplier_orders"
    base_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{order_no}.pdf"
    out_path = base_dir / filename
    out_path.write_bytes(pdf_bytes)
    rel_path = str(Path("generated") / "supplier_orders" / filename).replace("\\", "/")
    return {"filename": filename, "rel_path": rel_path, "abs_path": str(out_path)}


def send_order_email(*, to_email: str, subject: str, body: str, pdf_bytes: bytes, pdf_filename: str) -> None:
    host = os.getenv("SMTP_HOST") or ""
    port = int(os.getenv("SMTP_PORT") or "587")
    username = os.getenv("SMTP_USERNAME") or ""
    password = os.getenv("SMTP_PASSWORD") or ""
    from_email = os.getenv("SMTP_FROM_EMAIL") or username
    from_name = os.getenv("SMTP_FROM_NAME") or "Store Hub 360"
    use_tls = str(os.getenv("SMTP_USE_TLS") or "1").strip() not in {"0", "false", "False"}

    if not host or not from_email:
        raise RuntimeError("Configurazione SMTP incompleta.")
    if not to_email:
        raise RuntimeError("Email fornitore mancante.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email
    msg.set_content(body)
    msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=pdf_filename)

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        if use_tls:
            smtp.starttls()
        if username:
            smtp.login(username, password)
        smtp.send_message(msg)
