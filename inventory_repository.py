from __future__ import annotations

from app_logging import log_swallowed
from datetime import datetime, timedelta
import os
import uuid
from typing import Dict, Tuple, Any, List, Optional

from app_db import get_backend, get_connection
from delivery_repository import get_delivery_table_name, _detect_delivery_layout


def _norm_key(s: str) -> str:
    return (s or "").strip().lower()


def _parse_number_any(v: Any) -> float:
    """Parse numeric values coming from Access or strings (EU/US)."""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)

    s = str(v).strip()
    if not s:
        return 0.0

    s = s.replace("€", "").replace(" ", "")
    # EU thousands '.' and decimal ','
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        # only ',' => decimal
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _list_access_table_names(conn) -> list[str]:
    """Return list of table names from Access via ODBC cursor.tables()."""
    cur = conn.cursor()
    names: list[str] = []
    try:
        for row in cur.tables():
            # pyodbc: TABLE_NAME at index 2 (Catalog, Schema, Name, Type, ...)
            try:
                tname = row.table_name  # type: ignore[attr-defined]
            except Exception:
                tname = row[2] if len(row) > 2 else None
            if tname:
                names.append(str(tname))
    except Exception:
        # If cursor.tables isn't available for some reason, fallback to empty.
        return []
    return names


def _resolve_conv_table_name(conn) -> str:
    """Resolve conversion table name (default prefers 'FP CONV').

    You can override with env: ACCESS_CONV_TABLE.
    If not found, tries a case-insensitive match and then any table containing 'conv'.
    """
    preferred = (os.getenv("ACCESS_CONV_TABLE") or "").strip()
    if preferred:
        return preferred

    preferred_candidates = ["FP CONV", "FB CONV", "FoodPaperConv", "FP_CONV", "FB_CONV"]
    tables = _list_access_table_names(conn)

    def norm_table(n: str) -> str:
        return _norm_key(n).replace("_", " ")

    tables_norm = {norm_table(t): t for t in tables}

    for cand in preferred_candidates:
        key = norm_table(cand)
        if key in tables_norm:
            return tables_norm[key]

    # fallback: pick first containing 'conv'
    conv_like = [t for t in tables if "conv" in _norm_key(t)]
    if conv_like:
        return conv_like[0]

    raise ValueError(
        f"Tabella conversioni non trovata. Imposta ACCESS_CONV_TABLE con il nome esatto. " 
        f"Preferito='FP CONV'. Tabelle disponibili con 'conv': {conv_like}"
    )


def _detect_conv_layout(conn, table_name: str) -> Tuple[str, str, str]:
    """Return (supplier_col, descr_col, conv_col) for conversion table."""
    cur = conn.cursor()
    cur.execute(f"SELECT TOP 1 * FROM [{table_name}]")
    cols = [d[0] for d in cur.description]
    cols_norm = [_norm_key(c) for c in cols]

    # Keywords-based selection (robusto)
    def find_col(keywords: list[str]) -> str:
        for kw in keywords:
            for i, cn in enumerate(cols_norm):
                if kw in cn:
                    return cols[i]
        return ""

    supplier_col = (
        os.getenv("ACCESS_CONV_SUPPLIER_COL") or ""
    ).strip() or find_col(["fornit", "supplier", "vendor", "forn"])

    descr_col = (
        os.getenv("ACCESS_CONV_DESC_COL") or ""
    ).strip() or find_col(["descr", "description", "articol", "prodotto"])

    conv_col = (
        os.getenv("ACCESS_CONV_VALUE_COL") or ""
    ).strip() or find_col(["conv", "kg", "peso"])

    if not supplier_col or not descr_col or not conv_col:
        raise ValueError(f"Colonne conversione non trovate in '{table_name}'. Colonne presenti: {cols}")

    return supplier_col, descr_col, conv_col


def get_conversions_for_supplier(store_code: str, supplier_name: str) -> Dict[str, float]:
    """Return mapping descr_norm -> convKgPerPezzo for given supplier.

    Chiave univoca: incrocio Descrizione + Fornitore (filtriamo per fornitore, mappiamo per descrizione).
    """
    try:
        return _get_conversions_for_supplier_from_pricelist(store_code, supplier_name)
    except Exception as exc:
        # Su SQL Server i tenant devono usare solo il listino StoreHub.
        # Il vecchio fallback FP/FB CONV e' solo per installazioni Access legacy.
        if get_backend() == "sqlserver":
            return {}

    conn = get_connection(store_code)
    try:
        table = _resolve_conv_table_name(conn)
        supplier_col, descr_col, conv_col = _detect_conv_layout(conn, table)

        cur = conn.cursor()
        cur.execute(
            f"SELECT [{descr_col}], [{conv_col}] FROM [{table}] WHERE [{supplier_col}] = ?",
            (supplier_name,),
        )
        out: Dict[str, float] = {}
        for descr, conv in cur.fetchall():
            key = _norm_key(descr)
            if not key:
                continue
            out[key] = _parse_number_any(conv)
        return out
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('inventory_repository:160')


def _get_conversions_for_supplier_from_pricelist(store_code: str, supplier_name: str) -> Dict[str, float]:
    """Read KG->piece conversions from tenant pricelist instead of legacy FP/FB CONV."""
    from supplier_orders_repository import ensure_price_lists_schema, get_price_list_for_store

    ensure_price_lists_schema()
    assigned = get_price_list_for_store(store_code)
    price_list_uuid = str((assigned or {}).get("row_uuid") or "").strip()
    if not price_list_uuid:
        return {}

    supplier = str(supplier_name or "").strip()
    if not supplier:
        return {}

    conn = get_connection(store_code)
    try:
        cur = conn.cursor()
        cur.execute(
            """
SELECT Descrizione, CONV
FROM dbo.ListiniPrezzi
WHERE listino_uuid = ?
  AND tipo_listino = ?
  AND FORNITORE = ?
  AND CONV IS NOT NULL
  AND TRY_CONVERT(decimal(18,4), CONV) <> 0
""",
            (price_list_uuid, "FoodPaper", supplier),
        )
        out: Dict[str, float] = {}
        for descr, conv in cur.fetchall():
            key = _norm_key(descr)
            if not key:
                continue
            out[key] = _parse_number_any(conv)
        return out
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('inventory_repository:203')


def get_delivery_avg_prices_last_weeks(store_code: str, supplier_name: str, weeks: int = 4) -> Dict[str, float]:
    """Avg price per 'CAR' (cartoni) from DatiDelivery over the last N weeks.

    Formula richiesta:
      prezzo_medio = (totale EURO inseriti nei DDT) / (totale CAR inseriti)

    Nota: in DatiDelivery il campo quantità che usiamo è quello già scritto come 'colli totali' (CAR totali).
    """
    conn = get_connection(store_code)
    table = get_delivery_table_name()
    try:
        layout = _detect_delivery_layout(conn, table)
        if layout.get("error"):
            raise ValueError(layout["error"])

        site_col = layout.get("site_col")
        supplier_col = layout.get("supplier_col")
        desc_col = layout.get("desc_col") or layout.get("descr_col")
        car_col = layout.get("qta_col") or layout.get("car_col")
        euro_col = layout.get("val_col") or layout.get("value_col")
        date_col = layout.get("deliv_date_col") or layout.get("date_col") or layout.get("doc_date_col")

        # fallback descrizione cercata dentro anag_cols se non rilevata
        if not desc_col:
            anag_cols = layout.get("anag_cols") or []
            for c in anag_cols:
                cn = _norm_key(c)
                if cn in ("descrizione", "descr", "descrizioneprodotto", "description"):
                    desc_col = c
                    break

        if not (site_col and supplier_col and desc_col and car_col and euro_col):
            raise ValueError(
                "Layout DatiDelivery incompleto: "
                f"site_col={site_col}, supplier_col={supplier_col}, desc_col={desc_col}, qta_col={car_col}, val_col={euro_col}"
            )

        since_dt = datetime.now() - timedelta(days=7 * int(weeks))
        cur = conn.cursor()

        # Se abbiamo una colonna data, proviamo a filtrare in SQL
        if date_col:
            sql = (
                f"SELECT [{desc_col}] AS d, SUM([{euro_col}]) AS s_euro, SUM([{car_col}]) AS s_car "
                f"FROM [{table}] "
                f"WHERE [{site_col}] = ? AND [{supplier_col}] = ? AND [{date_col}] >= ? "
                f"GROUP BY [{desc_col}]"
            )
            try:
                cur.execute(sql, (store_code, supplier_name, since_dt))
                rows = cur.fetchall()

                out: Dict[str, float] = {}
                for descr, s_euro, s_car in rows:
                    key = _norm_key(descr)
                    if not key:
                        continue
                    car = _parse_number_any(s_car)
                    if car <= 0:
                        continue
                    euro = _parse_number_any(s_euro)
                    out[key] = euro / car
                return out
            except Exception:
                # fallback python filtering
                log_swallowed('inventory_repository:272')

        # Fallback: nessun filtro SQL (o date_col mancante). Filtriamo (se possibile) in Python.
        if date_col:
            cur.execute(
                f"SELECT [{desc_col}] AS d, [{euro_col}] AS euro, [{car_col}] AS car, [{date_col}] AS dt "
                f"FROM [{table}] WHERE [{site_col}] = ? AND [{supplier_col}] = ?",
                (store_code, supplier_name),
            )
            raw = cur.fetchall()

            agg: Dict[str, Dict[str, float]] = {}
            for descr, euro, car, dt in raw:
                dt_ok = None
                if isinstance(dt, datetime):
                    dt_ok = dt
                else:
                    s = str(dt).strip() if dt is not None else ""
                    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                        try:
                            dt_ok = datetime.strptime(s[:10], fmt)
                            break
                        except Exception:
                            log_swallowed('inventory_repository:294')

                if dt_ok is None or dt_ok < since_dt:
                    continue

                key = _norm_key(descr)
                if not key:
                    continue
                agg.setdefault(key, {"euro": 0.0, "car": 0.0})
                agg[key]["euro"] += _parse_number_any(euro)
                agg[key]["car"] += _parse_number_any(car)

            out: Dict[str, float] = {}
            for k, v in agg.items():
                if v["car"] > 0:
                    out[k] = v["euro"] / v["car"]
            return out

        # Ultimo fallback: senza colonna data calcoliamo su tutto lo storico (per non bloccare l'inventario)
        cur.execute(
            f"SELECT [{desc_col}] AS d, SUM([{euro_col}]) AS s_euro, SUM([{car_col}]) AS s_car "
            f"FROM [{table}] WHERE [{site_col}] = ? AND [{supplier_col}] = ? "
            f"GROUP BY [{desc_col}]",
            (store_code, supplier_name),
        )
        rows = cur.fetchall()

        out: Dict[str, float] = {}
        for descr, s_euro, s_car in rows:
            key = _norm_key(descr)
            if not key:
                continue
            car = _parse_number_any(s_car)
            if car <= 0:
                continue
            euro = _parse_number_any(s_euro)
            out[key] = euro / car
        return out
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('inventory_repository:336')


# ----------------------- INVENTORY / TX SAVE -----------------------


def get_inventory_table_name() -> str:
    """Nome tabella Access per movimenti inventario.

    Override con env: ACCESS_INVENTORY_TABLE
    Default: datiinventario
    """
    return (os.getenv("ACCESS_INVENTORY_TABLE") or "datiinventario").strip()


def get_tx_table_name() -> str:
    """Nome tabella Access per movimenti trasferimenti (TX).

    Override con env: ACCESS_TX_TABLE
    Default: DatiTX
    """
    return (os.getenv("ACCESS_TX_TABLE") or "DatiTX").strip()


def _resolve_table_name(conn, preferred: str, extra_candidates: Optional[List[str]] = None) -> str:
    """Resolve table name in Access with case-insensitive match.

    Raises ValueError if no candidate is found in database.
    """
    tables = _list_access_table_names(conn)
    cand: List[str] = []
    if preferred:
        cand.append(preferred)
    if extra_candidates:
        cand.extend([c for c in extra_candidates if c])

    def norm(s: str) -> str:
        return _norm_key(s).replace("_", " ")

    table_map = {norm(t): t for t in tables}

    for c in cand:
        key = norm(c)
        if key in table_map:
            return table_map[key]

    # fallback: try any table containing preferred token
    if preferred:
        token = norm(preferred)
        for t in tables:
            if token and token in norm(t):
                return t

    raise ValueError(
        f"Tabella '{preferred}' non trovata nel database Access. Tabelle disponibili: {', '.join(sorted(set(tables)))}"
    )


def _get_table_columns(conn, table_name: str) -> List[str]:
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT TOP 1 * FROM [{table_name}]")
        return [d[0] for d in (cur.description or [])]
    finally:
        try:
            cur.close()
        except Exception:
            log_swallowed('inventory_repository:403')


def _cols_norm_map(cols: List[str]) -> Dict[str, str]:
    """Return mapping norm_col -> actual_col."""
    out: Dict[str, str] = {}
    for c in cols:
        k = _norm_key(c).replace("_", " ")
        if k and k not in out:
            out[k] = c
    return out


def _find_col(cols_map: Dict[str, str], preferred_keys: List[str]) -> str:
    for k in preferred_keys:
        kk = _norm_key(k).replace("_", " ")
        if kk in cols_map:
            return cols_map[kk]
    return ""


def _detect_inventory_layout(cols: List[str], require_site2: bool = False) -> Dict[str, str]:
    """Detect required column names for inventory/TX tables."""
    cols_map = _cols_norm_map(cols)

    site_col = _find_col(cols_map, ["site", "store", "negozio", "pv", "punto vendita"])
    date_col = _find_col(cols_map, ["data", "date", "giorno", "datamov", "data mov", "data_mov"])
    mov_col = _find_col(cols_map, ["tipotrans", "tipo trans", "tipo_mov", "tipo mov", "tipo movimentazione", "causale", "causale2", "movtype", "mov type", "mov_type"])
    car_col = _find_col(cols_map, ["car"])
    # 'INT' può esistere come colonna, ma non deve confondersi con QTAINT
    interno_col = _find_col(cols_map, ["interno", "int"])
    pez_col = _find_col(cols_map, ["pez"])
    totpz_col = _find_col(cols_map, ["totpz", "tot pz", "tot_pz", "tot pez", "totpez"])
    toteuro_col = _find_col(cols_map, ["toteuro", "tot euro", "tot_euro", "tot€", "tot €", "totale euro"])

    site2_col = ""
    if require_site2:
        site2_col = _find_col(cols_map, ["site2", "site 2", "store2", "store 2", "negozio2", "negozio 2"])

    missing = []
    for name, col in (
        ("SITE", site_col),
        ("DATA", date_col),
        ("TIPOTRANS", mov_col),
        ("CAR", car_col),
        ("INTERNO", interno_col),
        ("PEZ", pez_col),
        ("TOTPZ", totpz_col),
        ("TOTEURO", toteuro_col),
    ):
        if not col:
            missing.append(name)

    if require_site2 and not site2_col:
        missing.append("SITE2")

    if missing:
        raise ValueError(
            "Colonne mancanti nella tabella inventario/TX: " + ", ".join(missing) +
            ". Imposta i nomi corretti nel DB Access o usa variabili d'ambiente per i nomi tabella."
        )

    return {
        "site_col": site_col,
        "date_col": date_col,
        "mov_col": mov_col,
        "car_col": car_col,
        "interno_col": interno_col,
        "pez_col": pez_col,
        "totpz_col": totpz_col,
        "toteuro_col": toteuro_col,
        "site2_col": site2_col,
    }


def _is_row_empty(row: Dict[str, Any]) -> bool:
    car = _parse_number_any(row.get("car"))
    interno = _parse_number_any(row.get("interno"))
    pez = _parse_number_any(row.get("pez"))
    kg = _parse_number_any(row.get("kg"))
    # riga vuota se nessuna quantità inserita
    return (car == 0 and interno == 0 and pez == 0 and kg == 0)


def save_inventory_movement(
    store_code: str,
    data_mov: str,
    mov_type: str,
    rows: List[Dict[str, Any]],
    site2: Optional[str] = None,
) -> Dict[str, Any]:
    """Scrive i movimenti in datiinventario e, per TXIN/TXOUT, anche in DatiTX.

    rows: lista di righe con chiavi:
      - anag: dict colonne listino (tutta l'anagrafica)
      - car, interno, pez, kg
      - totpz, toteuro
    """
    mov_type = (mov_type or "").strip()
    if not mov_type:
        return {"success": False, "error": "Tipo movimentazione mancante."}

    try:
        data_dt = datetime.strptime((data_mov or "").strip(), "%Y-%m-%d")
    except Exception:
        return {"success": False, "error": "Data movimentazione non valida (atteso YYYY-MM-DD)."}

    is_tx = mov_type.upper() in ("TXIN", "TXOUT")
    if is_tx and not (site2 and str(site2).strip()):
        return {"success": False, "error": "Per TXIN/TXOUT è obbligatorio SITE2."}

    try:
        conn = get_connection(store_code)
    except Exception as e:  # pragma: no cover
        return {"success": False, "error": f"Errore connessione Access: {e}"}

    inserted_inv = 0
    inserted_tx = 0
    skipped = 0

    try:
        inv_table = _resolve_table_name(
            conn,
            get_inventory_table_name(),
            extra_candidates=["DatiInventario", "DATIINVENTARIO", "dati inventario", "inventario", "Inventario"],
        )
        tx_table = None
        if is_tx:
            tx_table = _resolve_table_name(
                conn,
                get_tx_table_name(),
                extra_candidates=["DATITX", "dati tx", "tx", "DatiTx"],
            )

        inv_cols = _get_table_columns(conn, inv_table)
        inv_layout = _detect_inventory_layout(inv_cols, require_site2=False)
        inv_cols_map = _cols_norm_map(inv_cols)
        inv_rowuuid_col = _detect_row_uuid_col(inv_cols)
        inv_rowuuid_col = _detect_row_uuid_col(inv_cols)

        tx_cols = []
        tx_layout = {}
        tx_cols_map = {}
        tx_rowuuid_col = ""
        if is_tx and tx_table:
            tx_cols = _get_table_columns(conn, tx_table)
            tx_layout = _detect_inventory_layout(tx_cols, require_site2=True)
            tx_cols_map = _cols_norm_map(tx_cols)
            tx_rowuuid_col = _detect_row_uuid_col(tx_cols)

        cur = conn.cursor()

        def _insert(table_name: str, col_values: Dict[str, Any]) -> None:
            cols_order = list(col_values.keys())
            placeholders = ",".join(["?"] * len(cols_order))
            cols_sql = ",".join([f"[{c}]" for c in cols_order])
            sql = f"INSERT INTO [{table_name}] ({cols_sql}) VALUES ({placeholders})"
            cur.execute(sql, [col_values[c] for c in cols_order])

        for r in rows or []:
            if not isinstance(r, dict):
                skipped += 1
                continue

            if _is_row_empty(r):
                skipped += 1
                continue

            anag = r.get("anag") if isinstance(r.get("anag"), dict) else {}

            base_vals: Dict[str, Any] = {}
            base_vals[inv_layout["site_col"]] = str(store_code)
            base_vals[inv_layout["date_col"]] = data_dt
            base_vals[inv_layout["mov_col"]] = mov_type

            if inv_rowuuid_col and inv_rowuuid_col not in base_vals:
                base_vals[inv_rowuuid_col] = str(uuid.uuid4())

            base_vals[inv_layout["car_col"]] = _parse_number_any(r.get("car"))
            base_vals[inv_layout["interno_col"]] = _parse_number_any(r.get("interno"))
            base_vals[inv_layout["pez_col"]] = _parse_number_any(r.get("pez"))
            base_vals[inv_layout["totpz_col"]] = _parse_number_any(r.get("totpz"))
            base_vals[inv_layout["toteuro_col"]] = _parse_number_any(r.get("toteuro"))

            # Aggiungi anagrafica: solo colonne presenti nella tabella, senza sovrascrivere base_vals
            for k, v in (anag or {}).items():
                kk = _norm_key(k).replace("_", " ")
                if not kk:
                    continue
                actual_col = inv_cols_map.get(kk)
                if not actual_col:
                    continue
                if inv_rowuuid_col and actual_col == inv_rowuuid_col:
                    continue
                if actual_col in base_vals:
                    continue
                # normalizza empty string -> NULL
                if isinstance(v, str) and not v.strip():
                    v = None
                base_vals[actual_col] = v

            _insert(inv_table, base_vals)
            inserted_inv += 1

            if is_tx and tx_table:
                tx_vals: Dict[str, Any] = {}
                # mappa i campi base sulla tabella TX (colonne potrebbero avere case diverso)
                tx_vals[tx_layout["site_col"]] = str(store_code)
                tx_vals[tx_layout["date_col"]] = data_dt
                tx_vals[tx_layout["mov_col"]] = mov_type

                if tx_rowuuid_col and tx_rowuuid_col not in tx_vals:
                    tx_vals[tx_rowuuid_col] = str(uuid.uuid4())
                tx_vals[tx_layout["site2_col"]] = str(site2).strip()

                tx_vals[tx_layout["car_col"]] = _parse_number_any(r.get("car"))
                tx_vals[tx_layout["interno_col"]] = _parse_number_any(r.get("interno"))
                tx_vals[tx_layout["pez_col"]] = _parse_number_any(r.get("pez"))
                tx_vals[tx_layout["totpz_col"]] = _parse_number_any(r.get("totpz"))
                tx_vals[tx_layout["toteuro_col"]] = _parse_number_any(r.get("toteuro"))

                for k, v in (anag or {}).items():
                    kk = _norm_key(k).replace("_", " ")
                    if not kk:
                        continue
                    actual_col = tx_cols_map.get(kk)
                    if not actual_col:
                        continue
                    if tx_rowuuid_col and actual_col == tx_rowuuid_col:
                        continue
                    if actual_col in tx_vals:
                        continue
                    if isinstance(v, str) and not v.strip():
                        v = None
                    tx_vals[actual_col] = v

                _insert(tx_table, tx_vals)
                inserted_tx += 1

        conn.commit()
        return {
            "success": True,
            "inventory_table": inv_table,
            "tx_table": tx_table,
            "inserted_inventory": inserted_inv,
            "inserted_tx": inserted_tx,
            "skipped": skipped,
        }

    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            log_swallowed('inventory_repository:656')
        return {"success": False, "error": str(e)}
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('inventory_repository:662')


# -----------------------------
#  Lettura / Modifica movimenti
# -----------------------------

def _detect_supplier_col(cols: List[str]) -> str:
    """Try to detect supplier column in inventory/TX tables."""
    cols_map = _cols_norm_map(cols)
    return _find_col(
        cols_map,
        [
            "fornitore",
            "fornitore nome",
            "nome fornitore",
            "supplier",
            "supplier name",
            "suppl",
            "vendor",
            "vendor name",
            "ragione sociale",
            "ragionesociale",
        ],
    )



def _detect_row_uuid_col(cols: List[str]) -> str:
    """Detect row_uuid/rowid column if present (SQL migration)."""
    for c in cols or []:
        nk = _norm_key(str(c)).replace(" ", "").replace("_", "")
        if "rowuuid" in nk or nk in ("rowid", "uuid"):
            return str(c)
    return ""



def _detect_desc_col(cols: List[str]) -> str:
    """Try to detect description column."""
    cols_map = _cols_norm_map(cols)
    return _find_col(
        cols_map,
        [
            "descrizione",
            "descr",
            "description",
            "articolo",
            "prodotto",
            "nome articolo",
            "item",
        ],
    )




def _detect_code_col(cols: List[str]) -> str:
    """Try to detect a product code/SKU column."""
    cols_map = _cols_norm_map(cols)
    return _find_col(
        cols_map,
        [
            "codice",
            "code",
            "sku",
            "articolo",
            "item code",
            "cod articolo",
            "codarticolo",
            "id articolo",
        ],
    )

def get_inventory_document_rows(
    store_code: str,
    supplier_name: str,
    data_mov: str,
    mov_type: str,
    site2: Optional[str] = None,
) -> Dict[str, Any]:
    """Load existing inventory/TX rows for given header key.

    For TXIN/TXOUT it loads from DatiTX (so it can filter on SITE2).
    Returns:
      - success: bool
      - cols: list of anagrafica columns (in DB order)
      - rows: list of dicts {anag: {...}, car, interno, pez, totpz, toteuro}
      - error: optional
    """
    mov_type = (mov_type or "").strip()
    supplier_name = (supplier_name or "").strip()
    if not store_code:
        return {"success": False, "error": "Store mancante."}
    if not mov_type:
        return {"success": False, "error": "Tipo movimentazione mancante."}
    if not data_mov:
        return {"success": False, "error": "Data movimentazione mancante."}
    if not supplier_name:
        return {"success": False, "error": "Fornitore mancante."}

    try:
        data_dt = datetime.strptime((data_mov or "").strip(), "%Y-%m-%d")
    except Exception:
        return {"success": False, "error": "Data movimentazione non valida (atteso YYYY-MM-DD)."}

    is_tx = mov_type.upper() in ("TXIN", "TXOUT")

    conn = None
    try:
        # connection helper requires store_code
        conn = get_connection(store_code)
        inv_table = _resolve_table_name(conn, get_inventory_table_name(), extra_candidates=["datiinventario"])
        tx_table = None
        if is_tx:
            tx_table = _resolve_table_name(conn, get_tx_table_name(), extra_candidates=["DatiTX", "datitx"])

        table = tx_table if is_tx else inv_table

        cols = _get_table_columns(conn, table)
        layout = _detect_inventory_layout(cols, require_site2=is_tx)
        cols_map = _cols_norm_map(cols)

        supplier_col = _detect_supplier_col(cols)
        if not supplier_col:
            return {"success": False, "error": f"Colonna fornitore non trovata in tabella {table}."}

        desc_col = _detect_desc_col(cols)

        where_clauses = [f"[{layout['site_col']}] = ?", f"[{layout['mov_col']}] = ?", f"[{layout['date_col']}] = ?", f"[{supplier_col}] = ?"]
        params: List[Any] = [str(store_code), mov_type, data_dt, supplier_name]

        if is_tx:
            if not site2:
                return {"success": False, "error": "SITE2 mancante per TXIN/TXOUT."}
            site2_col = layout.get("site2_col") or ""
            if not site2_col:
                return {"success": False, "error": f"Colonna SITE2 non trovata in tabella {table}."}
            where_clauses.append(f"[{site2_col}] = ?")
            params.append(str(site2).strip())

        order_sql = f" ORDER BY [{desc_col}]" if desc_col else ""
        sql = f"SELECT * FROM [{table}] WHERE " + " AND ".join(where_clauses) + order_sql

        cur = conn.cursor()
        cur.execute(sql, params)
        fetched = cur.fetchall()

        colnames = [d[0] for d in cur.description] if cur.description else []

        rows_out: List[Dict[str, Any]] = []
        for rec in fetched:
            row_dict = {colnames[i]: rec[i] for i in range(len(colnames))}
            out_row: Dict[str, Any] = {
                "anag": {},
                "car": _parse_number_any(row_dict.get(layout["car_col"])),
                "interno": _parse_number_any(row_dict.get(layout["interno_col"])),
                "pez": _parse_number_any(row_dict.get(layout["pez_col"])),
                "totpz": _parse_number_any(row_dict.get(layout["totpz_col"])),
                "toteuro": _parse_number_any(row_dict.get(layout["toteuro_col"])),
            }

            # anagrafica: everything except tech/qty columns
            skip_cols = set(
                [
                    layout["site_col"],
                    layout["date_col"],
                    layout["mov_col"],
                    layout["car_col"],
                    layout["interno_col"],
                    layout["pez_col"],
                    layout["totpz_col"],
                    layout["toteuro_col"],
                    supplier_col,
                ]
            )
            if is_tx and layout.get("site2_col"):
                skip_cols.add(layout["site2_col"])

            for c in colnames:
                if c in skip_cols:
                    continue
                out_row["anag"][c] = row_dict.get(c)

            # include supplier as anag (useful if you want to show it later)
            out_row["anag"][supplier_col] = row_dict.get(supplier_col)

            rows_out.append(out_row)

        # build cols list (db order) from anag keys
        anag_cols: List[str] = []
        if rows_out:
            # use DB column order, but only those present in anag
            keys = set(rows_out[0].get("anag", {}).keys())
            for c in colnames:
                if c in keys:
                    anag_cols.append(c)
            # add any remaining keys (unlikely)
            for k in keys:
                if k not in anag_cols:
                    anag_cols.append(k)

        return {"success": True, "cols": anag_cols, "rows": rows_out}

    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            log_swallowed('inventory_repository:873')


def replace_inventory_movement(
    store_code: str,
    supplier_name: str,
    data_mov: str,
    mov_type: str,
    rows: List[Dict[str, Any]],
    site2: Optional[str] = None,
) -> Dict[str, Any]:
    """Replace a movement set (delete + insert) atomically.

    Deletes existing rows by key and reinserts the provided list.
    For TXIN/TXOUT it replaces both datiinventario and DatiTX.
    """
    mov_type = (mov_type or "").strip()
    supplier_name = (supplier_name or "").strip()
    if not store_code:
        return {"success": False, "error": "Store mancante."}
    if not mov_type:
        return {"success": False, "error": "Tipo movimentazione mancante."}
    if not data_mov:
        return {"success": False, "error": "Data movimentazione mancante."}
    if not supplier_name:
        return {"success": False, "error": "Fornitore mancante."}

    try:
        data_dt = datetime.strptime((data_mov or "").strip(), "%Y-%m-%d")
    except Exception:
        return {"success": False, "error": "Data movimentazione non valida (atteso YYYY-MM-DD)."}

    is_tx = mov_type.upper() in ("TXIN", "TXOUT")

    conn = None
    try:
        # connection helper requires store_code
        conn = get_connection(store_code)
        inv_table = _resolve_table_name(conn, get_inventory_table_name(), extra_candidates=["datiinventario"])
        tx_table = None
        if is_tx:
            tx_table = _resolve_table_name(conn, get_tx_table_name(), extra_candidates=["DatiTX", "datitx"])

        inv_cols = _get_table_columns(conn, inv_table)
        inv_layout = _detect_inventory_layout(inv_cols, require_site2=False)
        inv_cols_map = _cols_norm_map(inv_cols)
        inv_rowuuid_col = _detect_row_uuid_col(inv_cols)

        inv_supplier_col = _detect_supplier_col(inv_cols)
        if not inv_supplier_col:
            return {"success": False, "error": f"Colonna fornitore non trovata in tabella {inv_table}."}

        tx_cols = []
        tx_layout = {}
        tx_cols_map = {}
        tx_rowuuid_col = ""
        tx_supplier_col = ""
        if is_tx and tx_table:
            tx_cols = _get_table_columns(conn, tx_table)
            tx_layout = _detect_inventory_layout(tx_cols, require_site2=True)
            tx_cols_map = _cols_norm_map(tx_cols)
            tx_rowuuid_col = _detect_row_uuid_col(tx_cols)
            tx_supplier_col = _detect_supplier_col(tx_cols)
            if not tx_supplier_col:
                return {"success": False, "error": f"Colonna fornitore non trovata in tabella {tx_table}."}

        cur = conn.cursor()

        deleted_inv = 0
        deleted_tx = 0

        # --- DELETE existing set ---
        inv_where = [f"[{inv_layout['site_col']}] = ?", f"[{inv_layout['mov_col']}] = ?", f"[{inv_layout['date_col']}] = ?", f"[{inv_supplier_col}] = ?"]
        inv_params: List[Any] = [str(store_code), mov_type, data_dt, supplier_name]

        # if inventory table has site2, include it for TX
        inv_site2_col = inv_layout.get("site2_col") or ""
        if is_tx and site2 and inv_site2_col:
            inv_where.append(f"[{inv_site2_col}] = ?")
            inv_params.append(str(site2).strip())

        sql_del_inv = f"DELETE FROM [{inv_table}] WHERE " + " AND ".join(inv_where)
        cur.execute(sql_del_inv, inv_params)
        deleted_inv = cur.rowcount if cur.rowcount is not None else 0

        if is_tx and tx_table:
            if not site2:
                return {"success": False, "error": "SITE2 mancante per TXIN/TXOUT."}
            tx_where = [f"[{tx_layout['site_col']}] = ?", f"[{tx_layout['mov_col']}] = ?", f"[{tx_layout['date_col']}] = ?", f"[{tx_supplier_col}] = ?", f"[{tx_layout['site2_col']}] = ?"]
            tx_params: List[Any] = [str(store_code), mov_type, data_dt, supplier_name, str(site2).strip()]

            sql_del_tx = f"DELETE FROM [{tx_table}] WHERE " + " AND ".join(tx_where)
            cur.execute(sql_del_tx, tx_params)
            deleted_tx = cur.rowcount if cur.rowcount is not None else 0

        # --- INSERT new rows (same logic as save_inventory_movement) ---
        inserted_inv = 0
        inserted_tx = 0
        skipped = 0

        def _insert(table_name: str, col_values: Dict[str, Any]) -> None:
            cols_order = list(col_values.keys())
            placeholders = ",".join(["?"] * len(cols_order))
            cols_sql = ",".join([f"[{c}]" for c in cols_order])
            sql = f"INSERT INTO [{table_name}] ({cols_sql}) VALUES ({placeholders})"
            cur.execute(sql, [col_values[c] for c in cols_order])

        for r in rows or []:
            if not isinstance(r, dict):
                skipped += 1
                continue

            if _is_row_empty(r):
                skipped += 1
                continue

            anag = r.get("anag") if isinstance(r.get("anag"), dict) else {}

            base_vals: Dict[str, Any] = {}
            base_vals[inv_layout["site_col"]] = str(store_code)
            base_vals[inv_layout["date_col"]] = data_dt
            base_vals[inv_layout["mov_col"]] = mov_type

            if inv_rowuuid_col and inv_rowuuid_col not in base_vals:
                base_vals[inv_rowuuid_col] = str(uuid.uuid4())

            base_vals[inv_layout["car_col"]] = _parse_number_any(r.get("car"))
            base_vals[inv_layout["interno_col"]] = _parse_number_any(r.get("interno"))
            base_vals[inv_layout["pez_col"]] = _parse_number_any(r.get("pez"))
            base_vals[inv_layout["totpz_col"]] = _parse_number_any(r.get("totpz"))
            base_vals[inv_layout["toteuro_col"]] = _parse_number_any(r.get("toteuro"))

            # For TX, also write site2 if inventory table supports it
            if is_tx and site2 and inv_site2_col:
                base_vals[inv_site2_col] = str(site2).strip()

            for k, v in (anag or {}).items():
                kk = _norm_key(k).replace("_", " ")
                if not kk:
                    continue
                actual_col = inv_cols_map.get(kk)
                if not actual_col:
                    continue
                if inv_rowuuid_col and actual_col == inv_rowuuid_col:
                    continue
                if actual_col in base_vals:
                    continue
                if isinstance(v, str) and not v.strip():
                    v = None
                base_vals[actual_col] = v

            _insert(inv_table, base_vals)
            inserted_inv += 1

            if is_tx and tx_table:
                tx_vals: Dict[str, Any] = {}
                tx_vals[tx_layout["site_col"]] = str(store_code)
                tx_vals[tx_layout["date_col"]] = data_dt
                tx_vals[tx_layout["mov_col"]] = mov_type

                if tx_rowuuid_col and tx_rowuuid_col not in tx_vals:
                    tx_vals[tx_rowuuid_col] = str(uuid.uuid4())
                tx_vals[tx_layout["site2_col"]] = str(site2).strip()

                tx_vals[tx_layout["car_col"]] = _parse_number_any(r.get("car"))
                tx_vals[tx_layout["interno_col"]] = _parse_number_any(r.get("interno"))
                tx_vals[tx_layout["pez_col"]] = _parse_number_any(r.get("pez"))
                tx_vals[tx_layout["totpz_col"]] = _parse_number_any(r.get("totpz"))
                tx_vals[tx_layout["toteuro_col"]] = _parse_number_any(r.get("toteuro"))

                for k, v in (anag or {}).items():
                    kk = _norm_key(k).replace("_", " ")
                    if not kk:
                        continue
                    actual_col = tx_cols_map.get(kk)
                    if not actual_col:
                        continue
                    if tx_rowuuid_col and actual_col == tx_rowuuid_col:
                        continue
                    if actual_col in tx_vals:
                        continue
                    if isinstance(v, str) and not v.strip():
                        v = None
                    tx_vals[actual_col] = v

                _insert(tx_table, tx_vals)
                inserted_tx += 1

        conn.commit()
        return {
            "success": True,
            "deleted_inventory": deleted_inv,
            "deleted_tx": deleted_tx,
            "inserted_inventory": inserted_inv,
            "inserted_tx": inserted_tx,
            "skipped": skipped,
        }

    except Exception as e:
        try:
            if conn:
                conn.rollback()
        except Exception:
            log_swallowed('inventory_repository:1076')
        return {"success": False, "error": str(e)}
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            log_swallowed('inventory_repository:1083')



def update_inventory_movement_header(
    *,
    store_code: str,
    supplier_name: str,
    mov_type: str,
    old_data_mov: str,
    new_data_mov: str,
    old_site2: Optional[str] = None,
    new_site2: Optional[str] = None,
    new_mov_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Aggiorna l'intestazione (data e, per TX, SITE2 e/o tipo) di TUTTE le righe del movimento.

    - Per movimenti non-TX aggiorna solo in DatiInventario.
    - Per TXIN/TXOUT aggiorna sia DatiTX che (se presente) la copia in DatiInventario.

    Nota: non consente conversioni tra TX e non-TX (richiederebbe creare/cancellare righe tra tabelle).
    """
    supplier_name = (supplier_name or "").strip()
    mov_type = (mov_type or "").strip().upper()
    new_mov_type_u = ((new_mov_type or mov_type) or "").strip().upper()

    if not store_code:
        return {"success": False, "error": "Store mancante."}
    if not supplier_name:
        return {"success": False, "error": "Fornitore mancante."}
    if not mov_type:
        return {"success": False, "error": "Tipo movimentazione mancante."}
    if not old_data_mov or not new_data_mov:
        return {"success": False, "error": "Date mancanti per cambio intestazione."}

    is_tx_old = mov_type in ("TXIN", "TXOUT")
    is_tx_new = new_mov_type_u in ("TXIN", "TXOUT")
    if is_tx_old != is_tx_new:
        return {"success": False, "error": "Non è consentito convertire un movimento TX in/non-TX durante il cambio intestazione."}

    if is_tx_old and not old_site2:
        return {"success": False, "error": "SITE2 mancante per movimento TX (chiave originale)."}
    if is_tx_old and not (new_site2 or old_site2):
        return {"success": False, "error": "SITE2 mancante per movimento TX (nuovo valore)."}

    try:
        old_dt = datetime.strptime(old_data_mov.strip(), "%Y-%m-%d")
        new_dt = datetime.strptime(new_data_mov.strip(), "%Y-%m-%d")
    except Exception:
        return {"success": False, "error": "Data non valida (atteso YYYY-MM-DD)."}

    conn = None
    try:
        conn = get_connection(store_code)

        inv_table = _resolve_table_name(conn, get_inventory_table_name(), extra_candidates=["datiinventario"])
        inv_cols = _get_table_columns(conn, inv_table)
        inv_layout = _detect_inventory_layout(inv_cols, require_site2=False)
        inv_supplier_col = _detect_supplier_col(inv_cols)
        if not inv_supplier_col:
            return {"success": False, "error": f"Colonna fornitore non trovata in tabella {inv_table}."}

        updated_inv = 0
        updated_tx = 0

        # --- Update DatiInventario (sempre) ---
        set_parts = [f"[{inv_layout['date_col']}] = ?"]
        set_params: List[Any] = [new_dt]

        if new_mov_type_u and new_mov_type_u != mov_type:
            set_parts.append(f"[{inv_layout['mov_col']}] = ?")
            set_params.append(new_mov_type_u)

        inv_site2_col = inv_layout.get("site2_col") or ""
        if is_tx_old and inv_site2_col and (new_site2 is not None):
            set_parts.append(f"[{inv_site2_col}] = ?")
            set_params.append((new_site2 or "").strip())

        where_parts = [
            f"[{inv_layout['site_col']}] = ?",
            f"[{inv_layout['mov_col']}] = ?",
            f"[{inv_layout['date_col']}] = ?",
            f"[{inv_supplier_col}] = ?",
        ]
        where_params: List[Any] = [str(store_code), mov_type, old_dt, supplier_name]

        if is_tx_old and inv_site2_col and old_site2:
            where_parts.append(f"[{inv_site2_col}] = ?")
            where_params.append(str(old_site2).strip())

        sql_inv = (
            f"UPDATE [{inv_table}] SET " + ", ".join(set_parts) +
            " WHERE " + " AND ".join(where_parts)
        )

        cur = conn.cursor()
        cur.execute(sql_inv, tuple(set_params + where_params))
        updated_inv = cur.rowcount if cur.rowcount is not None else 0

        # --- Update DatiTX (solo per TX) ---
        if is_tx_old:
            tx_table = _resolve_table_name(conn, get_tx_table_name(), extra_candidates=["DatiTX", "datitx"])
            tx_cols = _get_table_columns(conn, tx_table)
            tx_layout = _detect_inventory_layout(tx_cols, require_site2=True)
            tx_supplier_col = _detect_supplier_col(tx_cols)
            if not tx_supplier_col:
                return {"success": False, "error": f"Colonna fornitore non trovata in tabella {tx_table}."}
            tx_site2_col = tx_layout.get("site2_col") or ""
            if not tx_site2_col:
                return {"success": False, "error": f"Colonna SITE2 non trovata in tabella {tx_table}."}

            set_parts_tx = [f"[{tx_layout['date_col']}] = ?"]
            set_params_tx: List[Any] = [new_dt]

            if new_mov_type_u and new_mov_type_u != mov_type:
                set_parts_tx.append(f"[{tx_layout['mov_col']}] = ?")
                set_params_tx.append(new_mov_type_u)

            # SITE2: se non cambia, riscrive il vecchio valore (ok)
            new_site2_val = (new_site2 if new_site2 is not None else old_site2) or ""
            set_parts_tx.append(f"[{tx_site2_col}] = ?")
            set_params_tx.append(str(new_site2_val).strip())

            where_parts_tx = [
                f"[{tx_layout['site_col']}] = ?",
                f"[{tx_layout['mov_col']}] = ?",
                f"[{tx_layout['date_col']}] = ?",
                f"[{tx_supplier_col}] = ?",
                f"[{tx_site2_col}] = ?",
            ]
            where_params_tx: List[Any] = [str(store_code), mov_type, old_dt, supplier_name, str(old_site2).strip()]

            sql_tx = (
                f"UPDATE [{tx_table}] SET " + ", ".join(set_parts_tx) +
                " WHERE " + " AND ".join(where_parts_tx)
            )

            cur.execute(sql_tx, tuple(set_params_tx + where_params_tx))
            updated_tx = cur.rowcount if cur.rowcount is not None else 0

        conn.commit()
        try:
            cur.close()
        except Exception:
            log_swallowed('inventory_repository:1227')

        return {
            "success": True,
            "updated_inv": int(updated_inv or 0),
            "updated_tx": int(updated_tx or 0),
            "error": None,
        }

    except Exception as ex:
        try:
            if conn:
                conn.rollback()
        except Exception:
            log_swallowed('inventory_repository:1241')
        return {"success": False, "error": str(ex)}
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            log_swallowed('inventory_repository:1248')



def _get_anag_value(anag: Dict[str, Any], target_col: str) -> Any:
    """Return value from anag matching target_col by exact or normalized match."""
    if not anag or not target_col:
        return None
    if target_col in anag:
        return anag.get(target_col)
    tnorm = _norm_key(target_col).replace("_", " ")
    for k, v in (anag or {}).items():
        if _norm_key(k).replace("_", " ") == tnorm:
            return v
    return None


def delete_inventory_row(
    store_code: str,
    supplier_name: str,
    data_mov: str,
    mov_type: str,
    descrizione: Optional[str] = None,
    codice: Optional[str] = None,
    site2: Optional[str] = None,
) -> Dict[str, Any]:
    """Delete a single row from Inventory and (for TX) from DatiTX.

    Key used:
      - SITE + TIPOTRANS + DATA + FORNITORE + (DESCRIZIONE or CODICE)
      - for TX: + SITE2 on DatiTX, and also on Inventory table if it has SITE2.

    Returns counts for both tables.
    """
    mov_type = (mov_type or "").strip()
    supplier_name = (supplier_name or "").strip()

    if not store_code:
        return {"success": False, "error": "Store mancante."}
    if not mov_type:
        return {"success": False, "error": "Tipo movimentazione mancante."}
    if not data_mov:
        return {"success": False, "error": "Data movimentazione mancante."}
    if not supplier_name:
        return {"success": False, "error": "Fornitore mancante."}

    try:
        data_dt = datetime.strptime((data_mov or "").strip(), "%Y-%m-%d")
    except Exception:
        return {"success": False, "error": "Data movimentazione non valida (atteso YYYY-MM-DD)."}

    is_tx = mov_type.upper() in ("TXIN", "TXOUT")
    if is_tx and not (site2 and str(site2).strip()):
        return {"success": False, "error": "SITE2 mancante per TXIN/TXOUT."}

    if not (descrizione or codice):
        return {"success": False, "error": "Parametri mancanti: descrizione o codice."}

    conn = None
    try:
        conn = get_connection(store_code)

        inv_table = _resolve_table_name(conn, get_inventory_table_name(), extra_candidates=["datiinventario"])
        tx_table = None
        if is_tx:
            tx_table = _resolve_table_name(conn, get_tx_table_name(), extra_candidates=["DatiTX", "datitx"])

        # inventory layout
        inv_cols = _get_table_columns(conn, inv_table)
        inv_layout = _detect_inventory_layout(inv_cols, require_site2=False)
        inv_supplier_col = _detect_supplier_col(inv_cols)
        if not inv_supplier_col:
            return {"success": False, "error": f"Colonna fornitore non trovata in tabella {inv_table}."}

        inv_desc_col = _detect_desc_col(inv_cols)
        inv_code_col = _detect_code_col(inv_cols)

        cur = conn.cursor()

        deleted_inv = 0
        deleted_tx = 0

        # Build base where
        inv_where = [
            f"[{inv_layout['site_col']}] = ?",
            f"[{inv_layout['mov_col']}] = ?",
            f"[{inv_layout['date_col']}] = ?",
            f"[{inv_supplier_col}] = ?",
        ]
        inv_params = [str(store_code), mov_type, data_dt, supplier_name]

        # If inventory table supports site2, also filter on it for TX
        inv_site2_col = inv_layout.get("site2_col") or ""
        if is_tx and inv_site2_col and site2:
            inv_where.append(f"[{inv_site2_col}] = ?")
            inv_params.append(str(site2).strip())

        # Row identifier
        if inv_desc_col and descrizione:
            inv_where.append(f"[{inv_desc_col}] = ?")
            inv_params.append(str(descrizione))
        elif inv_code_col and codice:
            inv_where.append(f"[{inv_code_col}] = ?")
            inv_params.append(str(codice))
        else:
            # if table doesn't have the requested identifier
            return {"success": False, "error": "Impossibile determinare la colonna chiave (descrizione/codice) per cancellazione."}

        sql_del_inv = f"DELETE FROM [{inv_table}] WHERE " + " AND ".join(inv_where)
        cur.execute(sql_del_inv, inv_params)
        deleted_inv = cur.rowcount if cur.rowcount is not None else 0

        if is_tx and tx_table:
            tx_cols = _get_table_columns(conn, tx_table)
            tx_layout = _detect_inventory_layout(tx_cols, require_site2=True)
            tx_supplier_col = _detect_supplier_col(tx_cols)
            if not tx_supplier_col:
                return {"success": False, "error": f"Colonna fornitore non trovata in tabella {tx_table}."}

            tx_desc_col = _detect_desc_col(tx_cols)
            tx_code_col = _detect_code_col(tx_cols)

            tx_where = [
                f"[{tx_layout['site_col']}] = ?",
                f"[{tx_layout['mov_col']}] = ?",
                f"[{tx_layout['date_col']}] = ?",
                f"[{tx_supplier_col}] = ?",
                f"[{tx_layout['site2_col']}] = ?",
            ]
            tx_params = [str(store_code), mov_type, data_dt, supplier_name, str(site2).strip()]

            if tx_desc_col and descrizione:
                tx_where.append(f"[{tx_desc_col}] = ?")
                tx_params.append(str(descrizione))
            elif tx_code_col and codice:
                tx_where.append(f"[{tx_code_col}] = ?")
                tx_params.append(str(codice))
            else:
                return {"success": False, "error": "Impossibile determinare la colonna chiave (descrizione/codice) per cancellazione TX."}

            sql_del_tx = f"DELETE FROM [{tx_table}] WHERE " + " AND ".join(tx_where)
            cur.execute(sql_del_tx, tx_params)
            deleted_tx = cur.rowcount if cur.rowcount is not None else 0

        conn.commit()
        return {
            "success": True,
            "deleted_inventory": int(deleted_inv or 0),
            "deleted_tx": int(deleted_tx or 0),
        }

    except Exception as e:
        try:
            if conn:
                conn.rollback()
        except Exception:
            log_swallowed('inventory_repository:1404')
        return {"success": False, "error": str(e)}
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            log_swallowed('inventory_repository:1411')


def save_inventory_document(
    store_code: str,
    header: Dict[str, Any],
    cols: List[str],
    rows: List[Dict[str, Any]],
    site2: Optional[str] = None,
) -> Dict[str, Any]:
    """Save edited inventory/TX rows.

    Similar to save_delivery_document logic:
      - For each provided row, delete any existing row by key then insert updated values.
      - Only the provided rows are affected (no full replace).
      - For TXIN/TXOUT the same operations are applied to both datiinventario and DatiTX.

    rows items expected:
      { anag: {...}, car, interno, pez, kg, totpz, toteuro }

    Note: KG is not persisted, but can be used to build totpz client-side.
    """
    supplier_name = (header.get("supplier_name") or "").strip()
    mov_type = (header.get("mov_type") or "").strip()
    data_mov = (header.get("data_mov") or "").strip()

    if not store_code:
        return {"success": False, "error": "Store mancante.", "inserted_inventory": 0, "inserted_tx": 0, "skipped": len(rows or [])}
    if not supplier_name:
        return {"success": False, "error": "Fornitore mancante.", "inserted_inventory": 0, "inserted_tx": 0, "skipped": len(rows or [])}
    if not mov_type:
        return {"success": False, "error": "Tipo movimentazione mancante.", "inserted_inventory": 0, "inserted_tx": 0, "skipped": len(rows or [])}
    if not data_mov:
        return {"success": False, "error": "Data movimentazione mancante.", "inserted_inventory": 0, "inserted_tx": 0, "skipped": len(rows or [])}

    try:
        data_dt = datetime.strptime((data_mov or "").strip(), "%Y-%m-%d")
    except Exception:
        return {"success": False, "error": "Data movimentazione non valida (atteso YYYY-MM-DD).", "inserted_inventory": 0, "inserted_tx": 0, "skipped": len(rows or [])}

    is_tx = mov_type.upper() in ("TXIN", "TXOUT")
    if is_tx and not (site2 and str(site2).strip()):
        return {"success": False, "error": "Per TXIN/TXOUT è obbligatorio SITE2.", "inserted_inventory": 0, "inserted_tx": 0, "skipped": len(rows or [])}

    conn = None
    try:
        conn = get_connection(store_code)

        inv_table = _resolve_table_name(conn, get_inventory_table_name(), extra_candidates=["datiinventario"])
        tx_table = None
        if is_tx:
            tx_table = _resolve_table_name(conn, get_tx_table_name(), extra_candidates=["DatiTX", "datitx"])

        inv_cols = _get_table_columns(conn, inv_table)
        inv_layout = _detect_inventory_layout(inv_cols, require_site2=False)
        inv_cols_map = _cols_norm_map(inv_cols)
        inv_rowuuid_col = _detect_row_uuid_col(inv_cols)

        inv_supplier_col = _detect_supplier_col(inv_cols)
        if not inv_supplier_col:
            return {"success": False, "error": f"Colonna fornitore non trovata in tabella {inv_table}.", "inserted_inventory": 0, "inserted_tx": 0, "skipped": len(rows or [])}

        inv_desc_col = _detect_desc_col(inv_cols)
        inv_code_col = _detect_code_col(inv_cols)

        # optional site2 column in inventory table (some DBs might have it)
        inv_site2_col = inv_layout.get("site2_col") or ""

        tx_cols: List[str] = []
        tx_layout: Dict[str, str] = {}
        tx_cols_map: Dict[str, str] = {}
        tx_rowuuid_col = ""
        tx_supplier_col = ""
        tx_desc_col = ""
        tx_code_col = ""

        if is_tx and tx_table:
            tx_cols = _get_table_columns(conn, tx_table)
            tx_layout = _detect_inventory_layout(tx_cols, require_site2=True)
            tx_cols_map = _cols_norm_map(tx_cols)
            tx_rowuuid_col = _detect_row_uuid_col(tx_cols)
            tx_supplier_col = _detect_supplier_col(tx_cols)
            if not tx_supplier_col:
                return {"success": False, "error": f"Colonna fornitore non trovata in tabella {tx_table}.", "inserted_inventory": 0, "inserted_tx": 0, "skipped": len(rows or [])}
            tx_desc_col = _detect_desc_col(tx_cols)
            tx_code_col = _detect_code_col(tx_cols)

        cur = conn.cursor()
        inserted_inv = 0
        inserted_tx = 0
        skipped = 0

        def _insert(table_name: str, col_values: Dict[str, Any]) -> None:
            cols_order = list(col_values.keys())
            placeholders = ",".join(["?"] * len(cols_order))
            cols_sql = ",".join([f"[{c}]" for c in cols_order])
            sql = f"INSERT INTO [{table_name}] ({cols_sql}) VALUES ({placeholders})"
            cur.execute(sql, [col_values[c] for c in cols_order])

        for r in rows or []:
            if not isinstance(r, dict):
                skipped += 1
                continue
            if _is_row_empty(r):
                skipped += 1
                continue

            anag = r.get("anag") if isinstance(r.get("anag"), dict) else {}

            # Ensure supplier is set in anag
            if inv_supplier_col and _get_anag_value(anag, inv_supplier_col) in (None, ""):
                anag[inv_supplier_col] = supplier_name

            # Identify row for delete
            descr_val = None
            code_val = None
            if inv_desc_col:
                descr_val = _get_anag_value(anag, inv_desc_col)
            if inv_code_col:
                code_val = _get_anag_value(anag, inv_code_col)

            # Fallback: try common anag keys if direct match failed
            if descr_val in (None, ""):
                for k in ("descrizione", "descr", "description"):
                    for ak, av in (anag or {}).items():
                        if _norm_key(ak) == k:
                            descr_val = av
                            break
                    if descr_val not in (None, ""):
                        break

            if code_val in (None, ""):
                for k in ("codice", "code", "sku"):
                    for ak, av in (anag or {}).items():
                        if _norm_key(ak) == k:
                            code_val = av
                            break
                    if code_val not in (None, ""):
                        break

            if descr_val in (None, "") and code_val in (None, ""):
                # Without an identifier we can't safely delete old rows
                skipped += 1
                continue

            # --- DELETE existing row in inventory ---
            inv_where = [
                f"[{inv_layout['site_col']}] = ?",
                f"[{inv_layout['mov_col']}] = ?",
                f"[{inv_layout['date_col']}] = ?",
                f"[{inv_supplier_col}] = ?",
            ]
            inv_params: List[Any] = [str(store_code), mov_type, data_dt, supplier_name]

            if is_tx and inv_site2_col and site2:
                inv_where.append(f"[{inv_site2_col}] = ?")
                inv_params.append(str(site2).strip())

            if inv_desc_col and descr_val not in (None, ""):
                inv_where.append(f"[{inv_desc_col}] = ?")
                inv_params.append(descr_val)
            elif inv_code_col and code_val not in (None, ""):
                inv_where.append(f"[{inv_code_col}] = ?")
                inv_params.append(code_val)

            try:
                cur.execute(f"DELETE FROM [{inv_table}] WHERE " + " AND ".join(inv_where), inv_params)
            except Exception:
                log_swallowed('inventory_repository:1579')

            # --- DELETE existing row in TX ---
            if is_tx and tx_table:
                tx_where = [
                    f"[{tx_layout['site_col']}] = ?",
                    f"[{tx_layout['mov_col']}] = ?",
                    f"[{tx_layout['date_col']}] = ?",
                    f"[{tx_supplier_col}] = ?",
                    f"[{tx_layout['site2_col']}] = ?",
                ]
                tx_params: List[Any] = [str(store_code), mov_type, data_dt, supplier_name, str(site2).strip()]

                if tx_desc_col and descr_val not in (None, ""):
                    tx_where.append(f"[{tx_desc_col}] = ?")
                    tx_params.append(descr_val)
                elif tx_code_col and code_val not in (None, ""):
                    tx_where.append(f"[{tx_code_col}] = ?")
                    tx_params.append(code_val)

                try:
                    cur.execute(f"DELETE FROM [{tx_table}] WHERE " + " AND ".join(tx_where), tx_params)
                except Exception:
                    log_swallowed('inventory_repository:1602')

            # --- INSERT inventory row ---
            inv_vals: Dict[str, Any] = {
                inv_layout["site_col"]: str(store_code),
                inv_layout["date_col"]: data_dt,
                inv_layout["mov_col"]: mov_type,
                inv_layout["car_col"]: _parse_number_any(r.get("car")),
                inv_layout["interno_col"]: _parse_number_any(r.get("interno")),
                inv_layout["pez_col"]: _parse_number_any(r.get("pez")),
                inv_layout["totpz_col"]: _parse_number_any(r.get("totpz")),
                inv_layout["toteuro_col"]: _parse_number_any(r.get("toteuro")),
            }

            if is_tx and inv_site2_col and site2:
                inv_vals[inv_site2_col] = str(site2).strip()

            # anag columns
            for k, v in (anag or {}).items():
                kk = _norm_key(k).replace("_", " ")
                if not kk:
                    continue
                actual_col = inv_cols_map.get(kk)
                if not actual_col:
                    continue
                if inv_rowuuid_col and actual_col == inv_rowuuid_col:
                    continue
                if actual_col in inv_vals:
                    continue
                if isinstance(v, str) and not v.strip():
                    v = None
                inv_vals[actual_col] = v

            # force supplier (in case it wasn't in anag)
            if inv_supplier_col and inv_supplier_col not in inv_vals:
                inv_vals[inv_supplier_col] = supplier_name

            _insert(inv_table, inv_vals)
            inserted_inv += 1

            # --- INSERT TX row ---
            if is_tx and tx_table:
                tx_vals: Dict[str, Any] = {
                    tx_layout["site_col"]: str(store_code),
                    tx_layout["date_col"]: data_dt,
                    tx_layout["mov_col"]: mov_type,
                    tx_layout["site2_col"]: str(site2).strip(),
                    tx_layout["car_col"]: _parse_number_any(r.get("car")),
                    tx_layout["interno_col"]: _parse_number_any(r.get("interno")),
                    tx_layout["pez_col"]: _parse_number_any(r.get("pez")),
                    tx_layout["totpz_col"]: _parse_number_any(r.get("totpz")),
                    tx_layout["toteuro_col"]: _parse_number_any(r.get("toteuro")),
                }

                # anag columns
                for k, v in (anag or {}).items():
                    kk = _norm_key(k).replace("_", " ")
                    if not kk:
                        continue
                    actual_col = tx_cols_map.get(kk)
                    if not actual_col:
                        continue
                    if tx_rowuuid_col and actual_col == tx_rowuuid_col:
                        continue
                    if actual_col in tx_vals:
                        continue
                    if isinstance(v, str) and not v.strip():
                        v = None
                    tx_vals[actual_col] = v

                if tx_supplier_col and tx_supplier_col not in tx_vals:
                    tx_vals[tx_supplier_col] = supplier_name

                _insert(tx_table, tx_vals)
                inserted_tx += 1

        conn.commit()
        return {
            "success": True,
            "inserted_inventory": inserted_inv,
            "inserted_tx": inserted_tx,
            "skipped": skipped,
        }

    except Exception as e:
        try:
            if conn:
                conn.rollback()
        except Exception:
            log_swallowed('inventory_repository:1691')
        return {"success": False, "error": str(e), "inserted_inventory": 0, "inserted_tx": 0, "skipped": len(rows or [])}
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            log_swallowed('inventory_repository:1698')
