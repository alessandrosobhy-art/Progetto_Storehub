from app_logging import log_swallowed
from datetime import datetime
from date_utils import to_datetime00 as _to_dt00, to_ddmmyyyy as _to_ddmmyyyy, to_iso as _to_iso_date
import os
import uuid
from typing import List, Dict, Any, Optional

from app_db import get_connection, get_backend, sql_trim
from supplier_orders_repository import (
    ensure_supplier_orders_schema,
    migrate_legacy_pricelists,
    list_fornitori as sql_list_fornitori,
    list_prices_for_supplier_all_types,
)

# PATCH: ddt_insert_row_uuid_sql_v1.0.0



def _get_odbc_columns_info(conn, table_name: str) -> Dict[str, Dict[str, Any]]:
    """Ritorna info ODBC per le colonne della tabella (tipo, data_type).
    Serve per distinguere campi DATE/DATETIME da TEXT, evitando problemi di locale.
    """
    info: Dict[str, Dict[str, Any]] = {}
    try:
        cur = conn.cursor()
        for row in cur.columns(table=table_name):
            try:
                col_name = getattr(row, "column_name", None) or getattr(row, "COLUMN_NAME", None)
            except Exception:
                col_name = None
            if not col_name:
                continue
            info[str(col_name)] = {
                "data_type": getattr(row, "data_type", None),
                "type_name": getattr(row, "type_name", None),
            }
        try:
            cur.close()
        except Exception:
            log_swallowed('delivery_repository:40')
    except Exception:
        # se fallisce, ritorna vuoto: useremo solo fallback testuale
        return {}
    return info


def _is_date_like_col(col_info: Optional[Dict[str, Any]]) -> bool:
    if not col_info:
        return False
    dt = col_info.get("data_type")
    tn = str(col_info.get("type_name") or "").upper()
    # ODBC types: DATE=91, TIMESTAMP=93 (alcuni driver usano 9/11)
    if dt in (9, 11, 91, 93):
        return True
    if "DATE" in tn or "TIME" in tn:
        return True
    return False

def _normalize_name(name: str) -> str:
    return (name or "").strip().lower()


def _qname(name: str) -> str:
    return f"[{str(name).replace(']', ']]')}]"


def _is_tech_col(col_name: str) -> bool:
    """Riconosce colonne tecniche (es. row_uuid) da ignorare nei fallback layout."""
    n = _normalize_name(col_name).replace(" ", "")
    if not n:
        return False
    if n in ("row_uuid", "rowuuid", "uuid", "created_at", "updated_at", "inserted_at", "modified_at"):
        return True
    if n.endswith("_uuid"):
        return True
    return False



# ----------------------- DELIVERY TABLE (DATI DTT) -----------------------


def get_delivery_table_name() -> str:
    """Nome della tabella Access che contiene i movimenti di delivery/DDT.

    Può essere personalizzato tramite variabile d'ambiente ACCESS_DELIVERY_TABLE,
    altrimenti usa "DatiDelivery" come default.
    """
    return os.getenv("ACCESS_DELIVERY_TABLE", "DatiDelivery")


def get_recent_deliveries(store_code: str, limit: int = 50) -> Dict[str, Any]:
    """Restituisce alcune righe di esempio dalla tabella delivery dello store."""
    info: Dict[str, Any] = {
        "table": get_delivery_table_name(),
        "columns": [],
        "rows": [],
        "error": None,
    }

    table = info["table"]

    try:
        conn = get_connection(store_code)
    except Exception as e:  # pragma: no cover
        info["error"] = f"Errore di connessione al database Access: {e}"
        return info

    try:
        cursor = conn.cursor()
        sql = f"SELECT TOP {int(limit)} * FROM [{table}]"
        cursor.execute(sql)
        cols = [col[0] for col in cursor.description]
        info["columns"] = cols

        rows = []
        for row in cursor.fetchall():
            rows.append({col: val for col, val in zip(cols, row)})
        info["rows"] = rows
        return info
    except Exception as e:  # pragma: no cover
        info["error"] = f"Errore durante la lettura della tabella {table}: {e}"
        return info
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('delivery_repository:128')


# ---------------------------- SUPPLIERS ----------------------------------


def get_suppliers_for_store(store_code: str) -> Dict[str, Any]:
    """Legge l'elenco fornitori dal database Access dello store.

    Usa le seguenti variabili d'ambiente (case-insensitive sui nomi di colonna):

    - ACCESS_SUPPLIERS_TABLE         (default: FORNITORI)
    - ACCESS_SUPPLIERS_CODE_COL      (default: codice)
    - ACCESS_SUPPLIERS_NAME_COL      (default: nome)

    Ritorna:
    {
      "table": ...,
      "code_column": ...,
      "name_column": ...,
      "available_columns": [...],
      "suppliers": [ {"code": ..., "name": ...}, ... ],
      "error": None | "messaggio"
    }
    """
    try:
        ensure_supplier_orders_schema()
        suppliers = sql_list_fornitori()
        return {
            "table": "dbo.Fornitori",
            "code_column": "Fornitore",
            "name_column": "Fornitore",
            "available_columns": ["Fornitore", "Referente", "Email", "Telefono1", "Telefono2", "TipoOrdine"],
            "suppliers": [
                {
                    "code": str(r.get("Fornitore") or "").strip(),
                    "name": str(r.get("Fornitore") or "").strip(),
                    "order_mode": str(r.get("TipoOrdine") or "Mail").strip() or "Mail",
                }
                for r in (suppliers or [])
                if str(r.get("Fornitore") or "").strip()
            ],
            "error": None,
        }
    except Exception:
        log_swallowed('delivery_repository:173')

    table = os.getenv("ACCESS_SUPPLIERS_TABLE", "FORNITORI")
    code_col_cfg = _normalize_name(os.getenv("ACCESS_SUPPLIERS_CODE_COL", "Fornitore"))
    name_col_cfg = _normalize_name(os.getenv("ACCESS_SUPPLIERS_NAME_COL", "Fornitore"))

    result: Dict[str, Any] = {
        "table": table,
        "code_column": code_col_cfg,
        "name_column": name_col_cfg,
        "available_columns": [],
        "suppliers": [],
        "error": None,
    }

    try:
        conn = get_connection(store_code)
    except Exception as e:  # pragma: no cover
        result["error"] = f"Errore di connessione al database Access: {e}"
        return result

    try:
        cursor = conn.cursor()
        sql = f"SELECT TOP 1 * FROM [{table}]"
        cursor.execute(sql)
        cols_raw = [col[0] for col in cursor.description]
        cols_norm = [_normalize_name(c) for c in cols_raw]
        result["available_columns"] = cols_raw

        # Trova la colonna codice
        if code_col_cfg not in cols_norm:
            result["error"] = (
                f"La colonna codice fornitori '{code_col_cfg}' non è stata trovata nella tabella {table}. "
                f"Colonne disponibili: {', '.join(cols_raw)}. "
                "Verifica la variabile ACCESS_SUPPLIERS_CODE_COL nel file .env."
            )
            return result

        if name_col_cfg not in cols_norm:
            result["error"] = (
                f"La colonna nome fornitori '{name_col_cfg}' non è stata trovata nella tabella {table}. "
                f"Colonne disponibili: {', '.join(cols_raw)}. "
                "Verifica la variabile ACCESS_SUPPLIERS_NAME_COL nel file .env."
            )
            return result

        code_idx = cols_norm.index(code_col_cfg)
        name_idx = cols_norm.index(name_col_cfg)
        code_col_real = cols_raw[code_idx]
        name_col_real = cols_raw[name_idx]

        # Ora leggiamo tutti i fornitori
        sql_all = f"SELECT t.{_qname(code_col_real)} AS supplier_code, t.{_qname(name_col_real)} AS supplier_name FROM {_qname(table)} AS t ORDER BY t.{_qname(code_col_real)}"
        cursor.execute(sql_all)
        suppliers = []
        for row in cursor.fetchall():
            suppliers.append({
                "code": row[0],
                "name": row[1],
            })

        result["suppliers"] = suppliers
        result["code_column"] = code_col_real
        result["name_column"] = name_col_real
        return result
    except Exception as e:  # pragma: no cover
        result["error"] = f"Errore durante la lettura dei fornitori dalla tabella {table}: {e}"
        return result
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('delivery_repository:245')


# ---------------------------- PRICE LIST ---------------------------------


def get_price_list_for_supplier(store_code: str, supplier_code: str, max_rows: int = 500) -> Dict[str, Any]:
    """Legge il listino (FoodPaper + Operating) per un fornitore, importando TUTTE le colonne anagrafiche.

    Usa due tabelle distinte in Access:

    - ACCESS_PRICELIST_FOOD_TABLE   (default: FoodPaper)
    - ACCESS_PRICELIST_OPER_TABLE   (default: Operating)

    Le colonne utilizzate per filtrare e per i calcoli sono condivise per entrambe le tabelle
    e configurabili tramite variabili d'ambiente (il nome è confrontato in modo case-insensitive):

    - ACCESS_PRICELIST_SUPPLIER_COL     (default: Fornitore)
    - ACCESS_PRICELIST_CODE_COL         (default: Codice)
    - ACCESS_PRICELIST_DESC_COL         (default: Descrizione)
    - ACCESS_PRICELIST_CATEGORY_COL     (default: Categoria)        [opzionale]
    - ACCESS_PRICELIST_TYPE_COL         (default: Tipo)             [opzionale]
    - ACCESS_PRICELIST_PRICE_COL        (default: Prezzo)
    - ACCESS_PRICELIST_UNIT_COL         (default: Pezzi_per_collo)  [opzionale]

    Ritorna un unico elenco di righe unendo FoodPaper e Operating, con tutte le colonne anagrafiche.
    """
    try:
        ensure_supplier_orders_schema()
        migrate_legacy_pricelists()
        return list_prices_for_supplier_all_types(supplier_code=supplier_code, max_rows=max_rows, store_code=store_code)
    except Exception:
        log_swallowed('delivery_repository:277')

    food_table = os.getenv("ACCESS_PRICELIST_FOOD_TABLE", "FoodPaper")
    oper_table = os.getenv("ACCESS_PRICELIST_OPER_TABLE", "Operating")

    supplier_col_cfg = _normalize_name(os.getenv("ACCESS_PRICELIST_SUPPLIER_COL", "Fornitore"))
    code_col_cfg = _normalize_name(os.getenv("ACCESS_PRICELIST_CODE_COL", "Codice"))
    desc_col_cfg = _normalize_name(os.getenv("ACCESS_PRICELIST_DESC_COL", "Descrizione"))
    cat_col_cfg = _normalize_name(os.getenv("ACCESS_PRICELIST_CATEGORY_COL", "Categoria"))
    type_col_cfg = _normalize_name(os.getenv("ACCESS_PRICELIST_TYPE_COL", "Tipo"))
    price_col_cfg = _normalize_name(os.getenv("ACCESS_PRICELIST_PRICE_COL", "Prezzo"))
    unit_col_cfg = _normalize_name(os.getenv("ACCESS_PRICELIST_UNIT_COL", "QtaCar"))

    info: Dict[str, Any] = {
        "tables": [food_table, oper_table],
        "supplier_code": supplier_code,
        "columns": [],              # tutte le colonne anagrafiche importate
        "rows": [],                 # lista di dict {colonna: valore}
        "error": None,
        "available_columns": {},    # per diagnostica per tabella
        "code_column": None,
        "desc_column": None,
        "price_column": None,
        "unit_column": None,
    }

    try:
        conn = get_connection(store_code)
    except Exception as e:  # pragma: no cover
        info["error"] = f"Errore di connessione al database Access: {e}"
        return info

    def _load_from_table(table_name: str, default_type_label: str) -> None:
        if not table_name:
            return

        try:
            cursor = conn.cursor()
            # Prima lettura per ottenere le colonne effettive della tabella
            test_sql = f"SELECT TOP 1 * FROM [{table_name}]"
            cursor.execute(test_sql)
            cols_raw = [col[0] for col in cursor.description]
            cols_norm = [_normalize_name(c) for c in cols_raw]
            info["available_columns"][table_name] = cols_raw

            def col_or_none(cfg_name: str) -> str:
                if not cfg_name:
                    return None
                if cfg_name in cols_norm:
                    return cols_raw[cols_norm.index(cfg_name)]
                return None

            supplier_col = col_or_none(supplier_col_cfg)
            code_col = col_or_none(code_col_cfg)
            desc_col = col_or_none(desc_col_cfg)
            cat_col = col_or_none(cat_col_cfg)
            type_col = col_or_none(type_col_cfg)
            price_col = col_or_none(price_col_cfg)
            unit_col = col_or_none(unit_col_cfg)

            missing_required = []
            if supplier_col is None:
                missing_required.append(f"fornitore ('{supplier_col_cfg}')")
            if code_col is None:
                missing_required.append(f"codice ('{code_col_cfg}')")
            if desc_col is None:
                missing_required.append(f"descrizione ('{desc_col_cfg}')")
            if price_col is None:
                missing_required.append(f"prezzo ('{price_col_cfg}')")

            if missing_required:
                msg = (
                    f"Nel listino {table_name} mancano una o più colonne obbligatorie: "
                    + ", ".join(missing_required)
                    + f". Colonne disponibili: {', '.join(cols_raw)}. "
                    "Verifica le variabili ACCESS_PRICELIST_* nel file .env."
                )
                if not info["error"]:
                    info["error"] = msg
                else:
                    info["error"] += " | " + msg
                return

            # Salviamo i nomi reali delle colonne chiave (solo la prima volta)
            if not info["code_column"]:
                info["code_column"] = code_col
            if not info["desc_column"]:
                info["desc_column"] = desc_col
            if not info["price_column"]:
                info["price_column"] = price_col
            if unit_col and not info["unit_column"]:
                info["unit_column"] = unit_col

            # Ora leggiamo TUTTE le colonne della tabella per il fornitore selezionato
            sql = f"SELECT * FROM [{table_name}] WHERE {sql_trim(f'[{supplier_col}]')} = ? ORDER BY [{desc_col}]"
            cursor.execute(sql, str(supplier_code).strip())
            cols_all = [col[0] for col in cursor.description]

            # Impostiamo l'elenco colonne globale solo la prima volta
            if not info["columns"]:
                # aggiungiamo anche una colonna fittizia per indicare il tipo di listino
                cols_final = list(cols_all)
                if "_TipoListino" not in cols_final:
                    cols_final.append("_TipoListino")
                info["columns"] = cols_final

            def _to_float(v):
                try:
                    if v is None or v == "":
                        return None
                    return float(v)
                except Exception:
                    return None

            for db_row in cursor.fetchall():
                if len(info["rows"]) >= max_rows:
                    break
                row_dict: Dict[str, Any] = {col: val for col, val in zip(cols_all, db_row)}
                # aggiungiamo sempre una colonna di servizio per indicare da che listino proviene
                row_dict["_TipoListino"] = default_type_label

                # opzionalmente potremmo normalizzare prezzo/unit_factor qui se serve in futuro
                # price_val = _to_float(row_dict.get(price_col))
                # unit_val = _to_float(row_dict.get(unit_col)) if unit_col else None

                info["rows"].append(row_dict)

        except Exception as e:  # pragma: no cover
            msg = f"Errore durante la lettura del listino dalla tabella {table_name}: {e}"
            if not info["error"]:
                info["error"] = msg
            else:
                info["error"] += " | " + msg

    try:
        _load_from_table(food_table, "FoodPaper")
        _load_from_table(oper_table, "Operating")
        return info
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('delivery_repository:419')



def _parse_number_eu(value):
    """Parse numbers in a way similar to the JS parseNumber (european format).
    Accepts values like '318,00', '1.234,56', '318.00', '318'.
    Returns float. Empty/invalid -> 0.0
    """
    if value is None:
        return 0.0
    s = str(value).strip()
    if not s:
        return 0.0
    # remove spaces
    s = s.replace(" ", "")
    # if comma present, treat as decimal separator in EU style
    if "," in s:
        s = s.replace(".", "")
        s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return 0.0




def _get_price_from_anag(anag: dict) -> float:
    """Return default price from anagrafica dict (listino price).
    Looks for keys normalized as 'prezzo'/'price'.
    """
    if not isinstance(anag, dict):
        return 0.0
    for k, v in anag.items():
        try:
            if _normalize_name(k) in ('prezzo', 'price'):
                return _parse_number_eu(v)
        except Exception:
            continue
    return 0.0




def _format_header_date_for_text(value):
    """Converte la data dell'header in stringa testo dd/mm/yyyy.
    Accetta formati comuni (YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY) e, se non
    riconosce il formato, restituisce la stringa originale.
    """
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%d/%m/%Y")
        except Exception:
            continue
    # formato non riconosciuto, restituisco la stringa così com'è
    return s
def _detect_delivery_layout(conn, table_name):
    """Detect basic DatiDelivery layout by reading a single row.
    Assumes structure: [site] + [anagrafica cols...] + [qta_tot] + [valore].
    Allows overriding via env vars:
      ACCESS_DELIVERY_SITE_COL
      ACCESS_DELIVERY_QTA_COL
      ACCESS_DELIVERY_VAL_COL
      ACCESS_DELIVERY_CODE_COL
    """
    cur = conn.cursor()
    try:
        try:
            cur.execute(f"SELECT TOP 1 * FROM [{table_name}]")
            cols_raw = [c[0] for c in (cur.description or [])]
        except Exception as ex:
            return {
                "error": f"Impossibile leggere la struttura della tabella {table_name}: {ex}",
                "table": table_name,
                "columns": [],
                "site_col": None,
                "code_col": None,
                "anag_cols": [],
                "qta_col": None,
                "val_col": None,
            }
    finally:
        try:
            cur.close()
        except Exception:
            log_swallowed('delivery_repository:511')

    if len(cols_raw) < 3:
        return {
            "error": f"La tabella {table_name} non contiene abbastanza colonne per Site/Quantità/Valore.",
            "table": table_name,
            "columns": cols_raw,
            "site_col": None,
            "code_col": None,
            "anag_cols": [],
            "qta_col": None,
            "val_col": None,
        }

    # site column
    site_cfg = os.getenv("ACCESS_DELIVERY_SITE_COL")
    site_col = None
    if site_cfg:
        site_cfg_norm = _normalize_name(site_cfg)
        for c in cols_raw:
            if _normalize_name(c) == site_cfg_norm:
                site_col = c
                break
    if not site_col:
        site_col = cols_raw[0]

    # quantity and value columns (default: last two)
    qta_cfg = os.getenv("ACCESS_DELIVERY_QTA_COL")
    val_cfg = os.getenv("ACCESS_DELIVERY_VAL_COL")
    qta_col = None
    val_col = None

    if qta_cfg:
        qta_cfg_norm = _normalize_name(qta_cfg)
        for c in cols_raw:
            if _normalize_name(c) == qta_cfg_norm:
                qta_col = c
                break
    if val_cfg:
        val_cfg_norm = _normalize_name(val_cfg)
        for c in cols_raw:
            if _normalize_name(c) == val_cfg_norm:
                val_col = c
                break

    if not qta_col or not val_col:
        # assume last two, ma ignora colonne tecniche (es. row_uuid ultima)
        cols_eff = [c for c in cols_raw if not _is_tech_col(c)]
        if len(cols_eff) >= 2:
            qta_col = cols_eff[-2]
            val_col = cols_eff[-1]
        else:
            qta_col = cols_raw[-2]
            val_col = cols_raw[-1]

    # codice prodotto
    code_cfg = os.getenv("ACCESS_DELIVERY_CODE_COL")
    code_col = None
    if code_cfg:
        code_cfg_norm = _normalize_name(code_cfg)
        for c in cols_raw:
            if _normalize_name(c) == code_cfg_norm:
                code_col = c
                break
    if not code_col:
        # tenta con nomi più comuni
        for c in cols_raw:
            if _normalize_name(c) in ("codice", "code", "articolo", "sku"):
                code_col = c
                break

    # anagrafica = tutte le colonne tranne site / qta / valore
    anag_cols = [c for c in cols_raw if c not in (site_col, qta_col, val_col) and not _is_tech_col(c)]

    # colonne chiave aggiuntive per la logica DDT:
    # - fattura (data documento)
    # - data (data consegna)
    # - fornitore
    # - descrizione articolo
    doc_date_col = None
    deliv_date_col = None
    supplier_col = None
    desc_col = None

    for c in cols_raw:
        name_norm = _normalize_name(c)
        if not doc_date_col and name_norm in ("fattura", "datafattura"):
            doc_date_col = c
        if not deliv_date_col and name_norm in ("data", "dataconsegna"):
            deliv_date_col = c
        if not supplier_col and name_norm in ("fornitore", "supplier"):
            supplier_col = c
        if not desc_col and name_norm in ("descrizione", "descr", "descrizioneprodotto"):
            desc_col = c

    return {
        "error": None,
        "table": table_name,
        "columns": cols_raw,
        "site_col": site_col,
        "code_col": code_col,
        "anag_cols": anag_cols,
        "qta_col": qta_col,
        "val_col": val_col,
        "doc_date_col": doc_date_col,
        "deliv_date_col": deliv_date_col,
        "supplier_col": supplier_col,
        "desc_col": desc_col,
    }


def save_delivery_document(store_code, header, cols, rows, unit_column=None, code_column=None):
    """Scrive le righe del DDT nella tabella DatiDelivery per lo store indicato.

    Parametri:
      - store_code: codice dello store (Site)
      - header: dict con almeno data_doc, data_rif, numero_ddt, causale, supplier_code (se servono in futuro)
      - cols: elenco delle colonne di anagrafica come arrivate dal listino (price_list.columns)
      - rows: lista di dict, ciascuno:
           {
             "anag": { col_name: value, ... },   # valori anagrafici
             "colli": "...",                    # input utente
             "pezzi": "...",
             "prezzo_ddt": "...",
             "sconto": "..."
           }
      - unit_column: nome colonna anagrafica con la Qta per collo (es. QtaCar)
      - code_column: nome colonna anagrafica con il codice prodotto (es. Codice)

    Logica:
      - calcola colli_tot = Colli + Pezzi / QtaCar
      - calcola valore = colli_tot * Prezzo_DDT * (1 - Sconto%/100)
      - per ogni riga con colli_tot > 0 inserisce in DatiDelivery:
          [Site] + [anagrafica...] + [colli_tot] + [valore]
      - prima dell'insert, se possibile, fa un DELETE per evitare duplicati secondo la chiave:
          Site + Descrizione + Data + Fattura + Fornitore
        (oppure, in mancanza di queste colonne, Site + Codice).
    """
    table_name = get_delivery_table_name()
    conn = get_connection(store_code)
    layout = _detect_delivery_layout(conn, table_name)

    if layout["error"]:
        return {"success": False, "error": layout["error"], "inserted": 0, "skipped": len(rows)}

    site_col = layout["site_col"]
    code_col_db = layout["code_col"]
    anag_cols = layout["anag_cols"]
    qta_col = layout["qta_col"]
    val_col = layout["val_col"]
    doc_date_col_db = layout.get("doc_date_col")
    deliv_date_col_db = layout.get("deliv_date_col")
    supplier_col_db = layout.get("supplier_col")
    desc_col_db = layout.get("desc_col")

    # Se abbiamo un code_column passato e non è stato rilevato da _detect_delivery_layout,
    # proviamo ad usarlo se esiste nella tabella.
    if code_column and (not code_col_db or code_col_db not in layout["columns"]):
        for c in layout["columns"]:
            if _normalize_name(c) == _normalize_name(code_column):
                code_col_db = c
                break

    cursor = conn.cursor()

    # Info tipi colonne per gestire correttamente DATE/DATETIME vs TEXT
    col_info = _get_odbc_columns_info(conn, table_name)
    doc_is_date = _is_date_like_col(col_info.get(doc_date_col_db)) if doc_date_col_db else False
    deliv_is_date = _is_date_like_col(col_info.get(deliv_date_col_db)) if deliv_date_col_db else False

    # Date intestazione: preferiamo oggetti datetime per colonne DATE/DATETIME (locale-safe)
    doc_dt = _to_dt00((header or {}).get("data_doc"))
    deliv_dt = _to_dt00((header or {}).get("data_rif"))

    # Per colonne TEXT manteniamo il formato dd/mm/yyyy per retro-compatibilità
    doc_txt = _to_ddmmyyyy(doc_dt) or _format_header_date_for_text((header or {}).get("data_doc"))
    deliv_txt = _to_ddmmyyyy(deliv_dt) or _format_header_date_for_text((header or {}).get("data_rif"))

    doc_key_val = doc_dt if (doc_is_date and doc_dt is not None) else doc_txt
    deliv_key_val = deliv_dt if (deliv_is_date and deliv_dt is not None) else deliv_txt

    inserted = 0
    skipped = 0
    first_error = None

    # Nome colonna per unità (pezzi per collo)
    unit_col_name = unit_column

    for row in rows:
        anag = row.get("anag") or {}
        colli = _parse_number_eu(row.get("colli"))
        pezzi = _parse_number_eu(row.get("pezzi"))
        prezzo_raw = row.get('prezzo_ddt')
        prezzo_raw_str = str(prezzo_raw).strip() if prezzo_raw is not None else ''
        prezzo = _parse_number_eu(prezzo_raw_str)
        if prezzo_raw_str == '':
            prezzo = _get_price_from_anag(anag)
        sconto_perc = _parse_number_eu(row.get("sconto"))

        # fattore sconto (0-1)
        sconto_factor = 1.0 - (sconto_perc / 100.0)
        if sconto_factor < 0:
            sconto_factor = 0.0
        if sconto_factor > 1:
            sconto_factor = 1.0

        # unità per collo (es. QtaCar)
        unit_factor = 1.0
        if unit_col_name:
            try:
                unit_val = anag.get(unit_col_name)
                unit_factor = _parse_number_eu(unit_val) or 1.0
            except Exception:
                unit_factor = 1.0

        # quantità totale in colli
        qta_tot = colli
        if unit_factor and unit_factor != 0:
            qta_tot += pezzi / unit_factor

        # valore riga
        valore = qta_tot * prezzo * sconto_factor

        if qta_tot <= 0 and abs(valore) < 1e-9:
            skipped += 1
            continue

        # valori di intestazione (date, fornitore)
        site_value = store_code
        doc_date_val = doc_key_val
        deliv_date_val = deliv_key_val
        supplier_val = header.get("supplier_code") or None
        desc_val = None

        # prepara valori anagrafici per la tabella DatiDelivery (match su nome colonna)
        anag_values_for_table = []
        for col_name in anag_cols:
            val = None

            # colonne speciali gestite dall'intestazione del DDT
            if doc_date_col_db and col_name == doc_date_col_db:
                val = doc_date_val
            elif deliv_date_col_db and col_name == deliv_date_col_db:
                val = deliv_date_val
            elif supplier_col_db and col_name == supplier_col_db:
                val = supplier_val
            else:
                # Se la colonna è presente nell'anagrafica del listino, usa quel valore
                if col_name in anag:
                    val = anag[col_name]
                else:
                    # fallback: prova a cercare colonna con nome normalizzato (es. differenze di maiuscole)
                    for k, v in anag.items():
                        if _normalize_name(k) == _normalize_name(col_name):
                            val = v
                            break

            # Normalizzazione speciale per la colonna PREZZO: se il valore arriva come
            # stringa tipo "318.0000" lo convertiamo in numero 318.0, così Access lo
            # interpreta correttamente come 318,00 e non come 3.180.000,00.
            if _normalize_name(col_name) == "prezzo" and val is not None:
                val = _parse_number_eu(val)

            # Normalizzazione speciale per QTACAR/QTAINT: se arrivano come stringhe con virgola
            # (es. '12,00') li convertiamo in float per evitare interpretazioni errate in Access.
            if _normalize_name(col_name) in ("qtacar", "qtaint") and val is not None:
                val = _parse_number_eu(val)

            anag_values_for_table.append(val)

            if desc_col_db and col_name == desc_col_db:
                desc_val = val

        # se possibile, elimina prima la riga esistente per chiave composta
        delete_sql = None
        delete_params = None

        # priorità: Site + Descrizione + Data + Fattura + Fornitore
        if (
            desc_col_db
            and deliv_date_col_db
            and doc_date_col_db
            and supplier_col_db
        ):
            delete_sql = (
                f"DELETE FROM [{table_name}] WHERE "
                f"[{site_col}] = ? AND "
                f"[{desc_col_db}] = ? AND "
                f"[{deliv_date_col_db}] = ? AND "
                f"[{doc_date_col_db}] = ? AND "
                f"[{supplier_col_db}] = ?"
            )
            delete_params = (
                site_value,
                desc_val,
                deliv_date_val,
                doc_date_val,
                supplier_val,
            )
        elif code_col_db:
            # fallback: Site + Codice, se la colonna codice esiste
            code_val = None
            if code_column and code_column in anag:
                code_val = anag.get(code_column)
            else:
                # cerca per nome normalizzato
                for k, v in anag.items():
                    if _normalize_name(k) == _normalize_name(code_col_db):
                        code_val = v
                        break

            if code_val is not None:
                delete_sql = (
                    f"DELETE FROM [{table_name}] WHERE "
                    f"[{site_col}] = ? AND "
                    f"[{code_col_db}] = ?"
                )
                delete_params = (site_value, code_val)

        # esegui DELETE se configurato
        if delete_sql and delete_params is not None:
            try:
                cursor.execute(delete_sql, delete_params)
            except Exception:
                # in caso di errore nel delete proseguiamo comunque con l'INSERT
                log_swallowed('delivery_repository:837')

        # INSERT [Site] + [anagrafica...] + [QtaTot] + [Valore]
        # NB: in SQL Server sono presenti spesso colonne tecniche (es. row_uuid) che NON devono
        # entrare negli anag_cols; se presenti le gestiamo separatamente qui.
        row_uuid_col = None
        for c in (layout.get("columns") or []):
            n = _normalize_name(c).replace(" ", "")
            if n in ("row_uuid", "rowuuid", "uuid"):
                row_uuid_col = c
                break

        insert_cols = [site_col] + anag_cols + [qta_col, val_col] + ([row_uuid_col] if row_uuid_col else [])
        placeholders = ",".join(["?"] * len(insert_cols))
        insert_sql = (
            f"INSERT INTO [{table_name}] ("
            + ",".join(f"[{c}]" for c in insert_cols)
            + ") VALUES ("
            + placeholders
            + ")"
        )

        row_uuid_val = (str(uuid.uuid4()) if row_uuid_col else None)
        values = [site_value] + anag_values_for_table + [qta_tot, valore] + ([row_uuid_val] if row_uuid_col else [])

        try:
            cursor.execute(insert_sql, values)
            inserted += 1
        except Exception as ex:
            # in caso di problemi su una singola riga, la saltiamo
            skipped += 1
            if first_error is None:
                first_error = str(ex)

    conn.commit()
    cursor.close()
    conn.close()

    # Se non abbiamo salvato nulla, meglio segnalare l'errore (prima eccezione catturata)
    if inserted == 0 and skipped > 0 and first_error:
        return {
            "success": False,
            "error": f"Nessuna riga salvata in {table_name}. Prima riga fallita: {first_error}",
            "inserted": inserted,
            "skipped": skipped,
        }

    return {
        "success": True,
        "error": None,
        "inserted": inserted,
        "skipped": skipped,
    }

import math

def _format_number_eu_2(val):
    """Format number with 2 decimals using comma as decimal separator (no thousands)."""
    try:
        if val is None:
            return ""
        f = float(val)
        return f"{f:.2f}".replace(".", ",")
    except Exception:
        return str(val) if val is not None else ""


def get_delivery_document_rows(store_code: str, supplier_name: str, data_consegna: str, data_documento: str) -> Dict[str, Any]:
    """Carica le righe già scritte in DatiDelivery per un dato DDT (store + fornitore + date).
    data_consegna: stringa in formato YYYY-MM-DD (input HTML) oppure DD/MM/YYYY.
    data_documento: stringa in formato YYYY-MM-DD (input HTML) oppure DD/MM/YYYY. In DB è testo (dd/mm/yyyy).
    Ritorna un oggetto simile a get_price_list_for_supplier:
      - columns (anagrafica)
      - rows (dict col->val, con chiavi extra _prefill_colli/_prefill_pezzi/_qta_tot/_valore)
      - price_column, unit_column, code_column, desc_column
    """
    table_name = get_delivery_table_name()
    info: Dict[str, Any] = {
        "error": None,
        "table": table_name,
        "columns": [],
        "rows": [],
        "price_column": None,
        "unit_column": None,
        "code_column": None,
        "desc_column": None,
        "qta_column": None,
        "val_column": None,
    }

    if not supplier_name or not data_consegna or not data_documento:
        info["error"] = "Parametri mancanti (fornitore e date)."
        return info

    try:
        conn = get_connection(store_code)
    except Exception as e:
        info["error"] = f"Errore di connessione al database Access: {e}"
        return info

    try:
        layout = _detect_delivery_layout(conn, table_name)
        if layout.get("error"):
            info["error"] = layout["error"]
            return info

        site_col = layout["site_col"]
        anag_cols = layout["anag_cols"] or []
        qta_col = layout["qta_col"]
        val_col = layout["val_col"]
        doc_col = layout.get("doc_date_col")
        deliv_col = layout.get("deliv_date_col")
        supplier_col = layout.get("supplier_col")
        desc_col = layout.get("desc_col")
        code_col = layout.get("code_col")

        info["columns"] = anag_cols
        info["qta_column"] = qta_col
        info["val_column"] = val_col
        info["desc_column"] = desc_col
        info["code_column"] = code_col

        # detect price column and unit column from anag_cols
        price_col = None
        unit_col = None
        for c in anag_cols:
            cn = _normalize_name(c)
            if not price_col and cn in ("prezzo", "price"):
                price_col = c
            if not unit_col and cn in ("qtacar", "qta car", "qta_car", "qta per collo", "qtapercollo"):
                unit_col = c

        info["price_column"] = price_col
        info["unit_column"] = unit_col

        # format dates for query
        doc_txt = _format_header_date_for_text(data_documento)
        deliv_dt = None
        deliv_txt = _format_header_date_for_text(data_consegna)
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%m-%d-%Y"):
            try:
                deliv_dt = datetime.strptime(str(data_consegna).strip(), fmt)
                break
            except Exception:
                continue

        if not (doc_col and deliv_col and supplier_col):
            info["error"] = "Layout DatiDelivery non riconosciuto: mancano colonne Data/Fattura/Fornitore."
            return info

        cur = conn.cursor()

        base_sql = (
            f"SELECT * FROM [{table_name}] WHERE "
            f"[{site_col}] = ? AND "
            f"[{supplier_col}] = ? AND "
            f"[{deliv_col}] = ? AND "
            f"[{doc_col}] = ?"
        )

        rows_raw = []

        # try 1: delivery date as datetime
        try:
            if deliv_dt is not None:
                cur.execute(base_sql, (store_code, supplier_name, deliv_dt, doc_txt))
                rows_raw = cur.fetchall()
        except Exception:
            rows_raw = []

        # try 2: delivery date as text (dd/mm/yyyy)
        if not rows_raw:
            try:
                cur.execute(base_sql, (store_code, supplier_name, deliv_txt, doc_txt))
                rows_raw = cur.fetchall()
            except Exception as ex:
                info["error"] = f"Errore lettura DatiDelivery: {ex}"
                return info

        cols_all = [c[0] for c in cur.description] if cur.description else []

        # Build row dicts
        for r in rows_raw:
            row_dict = {}
            for idx, col_name in enumerate(cols_all):
                try:
                    row_dict[col_name] = r[idx]
                except Exception:
                    row_dict[col_name] = None

            # Keep only anagrafica keys for display, but we pass through all anag cols
            anag_out = {c: row_dict.get(c) for c in anag_cols}

            qta_tot = row_dict.get(qta_col) if qta_col else 0
            valore = row_dict.get(val_col) if val_col else 0

            unit_factor = 1
            if unit_col:
                try:
                    unit_factor = float(_parse_number_eu(anag_out.get(unit_col))) or 1
                except Exception:
                    unit_factor = 1
            if not unit_factor:
                unit_factor = 1


            def _to_float_mixed(v: Any) -> float:
                if v is None:
                    return 0.0
                try:
                    return float(v)
                except Exception:
                    try:
                        return float(_parse_number_eu(v) or 0)
                    except Exception:
                        return 0.0

            q = _to_float_mixed(qta_tot)

            colli_int = int(math.floor(q)) if q >= 0 else 0
            frac = q - float(colli_int)
            pezzi = int(round(frac * unit_factor)) if unit_factor else 0
            if pezzi < 0:
                pezzi = 0
            if pezzi >= unit_factor:
                # normalizza in caso di arrotondamenti
                colli_int += int(pezzi // unit_factor)
                pezzi = int(pezzi % unit_factor)


            # Calcolo prezzo DDT unitario quando importo un DDT in modifica:
            # prezzo = totale €/qta tot (valore / qta_tot)
            q_num = q
            val_num = _to_float_mixed(valore)
            prezzo_txt = ""
            if q_num:
                try:
                    prezzo_txt = _format_number_eu_2(val_num / q_num)
                except Exception:
                    prezzo_txt = ""

            # enrich dict with prefilled / original values
            anag_out["_prefill_colli"] = str(colli_int)
            anag_out["_prefill_pezzi"] = str(pezzi)
            anag_out["_orig_colli"] = str(colli_int)
            anag_out["_orig_pezzi"] = str(pezzi)
            anag_out["_prefill_prezzo_ddt"] = prezzo_txt
            anag_out["_orig_prezzo_ddt"] = prezzo_txt
            anag_out["_qta_tot"] = _format_number_eu_2(q)
            anag_out["_valore"] = _format_number_eu_2(valore)

            info["rows"].append(anag_out)

        cur.close()
        conn.close()

        if not info["rows"]:
            info["error"] = None  # no rows is not error
        return info

    except Exception as ex:
        try:
            conn.close()
        except Exception:
            log_swallowed('delivery_repository:1100')
        info["error"] = f"Errore lettura DDT: {ex}"
        return info


def delete_delivery_row(store_code: str, supplier_name: str, data_consegna: str, data_documento: str, descrizione: str) -> Dict[str, Any]:
    """Cancella una singola riga in DatiDelivery in base alla chiave composta."""
    table_name = get_delivery_table_name()
    if not (supplier_name and data_consegna and data_documento and descrizione):
        return {"success": False, "error": "Parametri mancanti per la cancellazione."}

    try:
        conn = get_connection(store_code)
    except Exception as e:
        return {"success": False, "error": f"Errore connessione Access: {e}"}

    try:
        layout = _detect_delivery_layout(conn, table_name)
        if layout.get("error"):
            return {"success": False, "error": layout["error"]}

        site_col = layout["site_col"]
        doc_col = layout.get("doc_date_col")
        deliv_col = layout.get("deliv_date_col")
        supplier_col = layout.get("supplier_col")
        desc_col = layout.get("desc_col")

        if not (doc_col and deliv_col and supplier_col and desc_col):
            return {"success": False, "error": "Layout DatiDelivery non riconosciuto per cancellazione."}

        doc_txt = _format_header_date_for_text(data_documento)
        deliv_dt = None
        deliv_txt = _format_header_date_for_text(data_consegna)
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%m-%d-%Y"):
            try:
                deliv_dt = datetime.strptime(str(data_consegna).strip(), fmt)
                break
            except Exception:
                continue

        cur = conn.cursor()
        sql = (
            f"DELETE FROM [{table_name}] WHERE "
            f"[{site_col}] = ? AND "
            f"[{desc_col}] = ? AND "
            f"[{deliv_col}] = ? AND "
            f"[{doc_col}] = ? AND "
            f"[{supplier_col}] = ?"
        )

        deleted = 0
        try:
            if deliv_dt is not None:
                cur.execute(sql, (store_code, descrizione, deliv_dt, doc_txt, supplier_name))
                deleted = cur.rowcount if cur.rowcount is not None else 0
                conn.commit()
        except Exception:
            deleted = 0

        if deleted == 0:
            cur.execute(sql, (store_code, descrizione, deliv_txt, doc_txt, supplier_name))
            deleted = cur.rowcount if cur.rowcount is not None else 0
            conn.commit()

        cur.close()
        conn.close()

        return {"success": True, "deleted": int(deleted or 0), "error": None}

    except Exception as ex:
        try:
            conn.close()
        except Exception:
            log_swallowed('delivery_repository:1173')
        return {"success": False, "error": str(ex)}


def update_delivery_ddt_dates(store_code: str, supplier_name: str, old_data_consegna: str, old_data_documento: str,
                              new_data_consegna: str, new_data_documento: str) -> Dict[str, Any]:
    """Aggiorna le date (Data consegna + Fattura) per tutte le righe del DDT."""
    table_name = get_delivery_table_name()
    if not (supplier_name and old_data_consegna and old_data_documento and new_data_consegna and new_data_documento):
        return {"success": False, "error": "Parametri mancanti per cambio date."}

    try:
        conn = get_connection(store_code)
    except Exception as e:
        return {"success": False, "error": f"Errore connessione Access: {e}"}

    try:
        layout = _detect_delivery_layout(conn, table_name)
        if layout.get("error"):
            return {"success": False, "error": layout["error"]}

        site_col = layout["site_col"]
        doc_col = layout.get("doc_date_col")
        deliv_col = layout.get("deliv_date_col")
        supplier_col = layout.get("supplier_col")

        if not (doc_col and deliv_col and supplier_col):
            return {"success": False, "error": "Layout DatiDelivery non riconosciuto per cambio date."}

        old_doc_dt = _to_dt00(old_data_documento)
        new_doc_dt = _to_dt00(new_data_documento)
        old_deliv_dt = _to_dt00(old_data_consegna)
        new_deliv_dt = _to_dt00(new_data_consegna)

        old_doc_txt = _to_ddmmyyyy(old_doc_dt) or _format_header_date_for_text(old_data_documento)
        new_doc_txt = _to_ddmmyyyy(new_doc_dt) or _format_header_date_for_text(new_data_documento)
        old_deliv_txt = _to_ddmmyyyy(old_deliv_dt) or _format_header_date_for_text(old_data_consegna)
        new_deliv_txt = _to_ddmmyyyy(new_deliv_dt) or _format_header_date_for_text(new_data_consegna)

        old_doc_iso = _to_iso_date(old_doc_dt) or _to_iso_date(old_data_documento)
        new_doc_iso = _to_iso_date(new_doc_dt) or _to_iso_date(new_data_documento)
        old_deliv_iso = _to_iso_date(old_deliv_dt) or _to_iso_date(old_data_consegna)
        new_deliv_iso = _to_iso_date(new_deliv_dt) or _to_iso_date(new_data_consegna)

        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%m-%d-%Y"):
            try:
                old_deliv_dt = datetime.strptime(str(old_data_consegna).strip(), fmt)
                break
            except Exception:
                continue
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%m-%d-%Y"):
            try:
                new_deliv_dt = datetime.strptime(str(new_data_consegna).strip(), fmt)
                break
            except Exception:
                continue

        cur = conn.cursor()
        sql = (
            f"UPDATE [{table_name}] SET "
            f"[{deliv_col}] = ?, "
            f"[{doc_col}] = ? "
            f"WHERE "
            f"[{site_col}] = ? AND "
            f"[{supplier_col}] = ? AND "
            f"[{deliv_col}] = ? AND "
            f"[{doc_col}] = ?"
        )

        updated = 0
        try:
            if old_deliv_dt is not None and new_deliv_dt is not None:
                cur.execute(sql, (new_deliv_dt, new_doc_txt, store_code, supplier_name, old_deliv_dt, old_doc_txt))
                updated = cur.rowcount if cur.rowcount is not None else 0
                conn.commit()
        except Exception:
            updated = 0

        if updated == 0:
            cur.execute(sql, (new_deliv_txt, new_doc_txt, store_code, supplier_name, old_deliv_txt, old_doc_txt))
            updated = cur.rowcount if cur.rowcount is not None else 0
            conn.commit()

        cur.close()
        conn.close()

        return {"success": True, "updated": int(updated or 0), "error": None}

    except Exception as ex:
        try:
            conn.close()
        except Exception:
            log_swallowed('delivery_repository:1265')
        return {"success": False, "error": str(ex)}
# -----------------------------------------------------------------------------
# DELIVERY WEEKLY (Rendiconto -> Gestione delivery)
# -----------------------------------------------------------------------------

from dataclasses import dataclass
from datetime import date as _date, timedelta as _timedelta
from decimal import Decimal
from types import SimpleNamespace

try:
    import pyodbc  # type: ignore
except Exception:  # pragma: no cover
    pyodbc = None  # type: ignore


@dataclass
class DeliveryWeeklyRow:
    store_code: str
    platform: str
    week_start: _date
    payment_online: Decimal
    payment_cash: Decimal
    orders: int
    cancelled_orders: int
    complaints_received: int
    refund_value: Decimal
    complaints_contested: int
    appeals_accepted: int
    refunds_cancelled_value: Decimal
    opening_pct: Decimal | None
    rating_value: Decimal | None
    rating_unit: str


def week_monday(d: _date) -> _date:
    """Normalizza una data al lunedì della stessa settimana."""
    if not isinstance(d, _date):
        # best-effort
        try:
            d = _date.fromisoformat(str(d))
        except Exception:
            d = _date.today()
    return d - _timedelta(days=int(d.weekday()))


def _sql_candidate_drivers(preferred: str | None) -> list[str]:
    out: list[str] = []
    if preferred:
        out.append(preferred)
    out.extend([
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
        "ODBC Driver 13 for SQL Server",
        "ODBC Driver 11 for SQL Server",
        "SQL Server",
    ])
    # de-dup
    seen = set()
    uniq: list[str] = []
    for d in out:
        k = (d or "").strip().lower()
        if not k or k in seen:
            continue
        seen.add(k)
        uniq.append(d)
    return uniq


def _get_sql_conn_app_storehub():
    """Connessione SQL Server su APP_STOREHUB.

    - Se DB_BACKEND=sqlserver usa app_db.get_connection()
    - Altrimenti prova una connessione diretta via pyodbc con env SQLSERVER_*.
    """
    try:
        from app_db import get_backend as _get_backend  # type: ignore
    except Exception:  # pragma: no cover
        _get_backend = lambda: "access"  # type: ignore

    if str(_get_backend() or "").lower() == "sqlserver":
        # In questo caso app_db.get_connection() è già configurata per APP_STOREHUB
        return get_connection(None)

    # fallback: connessione diretta anche se l'app gira in backend Access
    if pyodbc is None:
        raise RuntimeError("pyodbc non disponibile: impossibile connettersi a SQL Server per DELIVERY_WEEKLY.")

    server = os.getenv("SQLSERVER_SERVER") or os.getenv("SQLSERVER_HOST") or r"10.24.1.1\\SQLEXPRESS"
    database = os.getenv("SQLSERVER_DATABASE") or os.getenv("SQLSERVER_DB") or "APP_STOREHUB"
    user = os.getenv("SQLSERVER_USER") or "file"
    password = os.getenv("SQLSERVER_PASSWORD") or ""
    preferred_driver = os.getenv("SQLSERVER_DRIVER")
    encrypt = (os.getenv("SQLSERVER_ENCRYPT") or "no").strip().lower()
    trust_cert = (os.getenv("SQLSERVER_TRUST_CERT") or os.getenv("SQLSERVER_TRUST_SERVER_CERT") or "yes").strip().lower()
    timeout = int(os.getenv("SQLSERVER_TIMEOUT") or "30")

    enc_val = "yes" if encrypt in ("1", "true", "yes", "y") else "no"
    tsc_val = "yes" if trust_cert in ("1", "true", "yes", "y") else "no"

    last_im002 = None
    for driver in _sql_candidate_drivers(preferred_driver):
        conn_str = ";".join([
            f"DRIVER={{{driver}}}",
            f"SERVER={server}",
            f"DATABASE={database}",
            f"UID={user}",
            f"PWD={password}",
            f"Encrypt={enc_val}",
            f"TrustServerCertificate={tsc_val}",
        ]) + ";"
        try:
            return pyodbc.connect(conn_str, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            if getattr(e, "args", None) and len(e.args) > 0 and str(e.args[0]) == "IM002":
                last_im002 = e
                continue
            raise

    raise RuntimeError(
        "ODBC driver per SQL Server non trovato (IM002). "
        "Installa 'ODBC Driver 18 for SQL Server' o 'ODBC Driver 17 for SQL Server', "
        "oppure imposta SQLSERVER_DRIVER nel .env."
    ) from last_im002



def ensure_delivery_weekly_schema() -> None:
    """Assicura la tabella StoreHub per delivery/reclami settimanali."""
    conn = _get_sql_conn_app_storehub()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            IF OBJECT_ID('dbo.DELIVERY_WEEKLY', 'U') IS NULL
            BEGIN
                CREATE TABLE dbo.DELIVERY_WEEKLY (
                    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
                    store_code NVARCHAR(50) NOT NULL,
                    platform NVARCHAR(50) NOT NULL,
                    week_start DATE NOT NULL,
                    payment_online DECIMAL(18,4) NOT NULL DEFAULT 0,
                    payment_cash DECIMAL(18,4) NOT NULL DEFAULT 0,
                    orders INT NOT NULL DEFAULT 0,
                    cancelled_orders INT NULL,
                    complaints_received INT NOT NULL DEFAULT 0,
                    refund_value DECIMAL(18,4) NOT NULL DEFAULT 0,
                    complaints_contested INT NOT NULL DEFAULT 0,
                    appeals_accepted INT NOT NULL DEFAULT 0,
                    refunds_cancelled_value DECIMAL(18,4) NOT NULL DEFAULT 0,
                    opening_pct DECIMAL(6,2) NULL,
                    rating_value DECIMAL(10,4) NULL,
                    rating_unit NVARCHAR(50) NOT NULL DEFAULT 'number',
                    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
                    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
                );
                CREATE UNIQUE INDEX UX_DELIVERY_WEEKLY_store_platform_week
                    ON dbo.DELIVERY_WEEKLY(store_code, platform, week_start);
            END
            IF COL_LENGTH('dbo.DELIVERY_WEEKLY', 'opening_pct') IS NULL
            BEGIN
                ALTER TABLE dbo.DELIVERY_WEEKLY ADD opening_pct DECIMAL(6,2) NULL;
            END
            IF COL_LENGTH('dbo.DELIVERY_WEEKLY', 'cancelled_orders') IS NULL
            BEGIN
                ALTER TABLE dbo.DELIVERY_WEEKLY ADD cancelled_orders INT NULL;
            END
            """
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            log_swallowed('delivery_repository:1440')
        raise
        # best-effort: non bloccare letture in ambienti dove la migration è gestita separatamente
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('delivery_repository:1447')


def _provider_key(value: str) -> str:
    import re

    key = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
    return key[:50] or "provider"


def ensure_delivery_providers_schema(seed_defaults: bool = True) -> None:
    """Tenant-level configuration for delivery providers shown in Rendiconto."""
    conn = _get_sql_conn_app_storehub()
    try:
        cur = conn.cursor()
        cur.execute(
            """
IF OBJECT_ID('dbo.DeliveryProviders', 'U') IS NULL
BEGIN
  CREATE TABLE dbo.DeliveryProviders (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    provider_key NVARCHAR(50) NOT NULL,
    platform NVARCHAR(50) NOT NULL,
    label NVARCHAR(100) NOT NULL,
    logo_filename NVARCHAR(255) NULL,
    rating_unit NVARCHAR(20) NOT NULL DEFAULT 'number',
    opening_mode NVARCHAR(20) NOT NULL DEFAULT 'opening',
    opening_label NVARCHAR(100) NOT NULL DEFAULT '% Apertura',
    is_active BIT NOT NULL DEFAULT 1,
    sort_order INT NOT NULL DEFAULT 0,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_DeliveryProviders_key ON dbo.DeliveryProviders(provider_key);
  CREATE UNIQUE INDEX UX_DeliveryProviders_platform ON dbo.DeliveryProviders(platform);
END
IF COL_LENGTH('dbo.DeliveryProviders', 'opening_mode') IS NULL
  ALTER TABLE dbo.DeliveryProviders ADD opening_mode NVARCHAR(20) NOT NULL DEFAULT 'opening';
IF COL_LENGTH('dbo.DeliveryProviders', 'opening_label') IS NULL
  ALTER TABLE dbo.DeliveryProviders ADD opening_label NVARCHAR(100) NOT NULL DEFAULT '% Apertura';
IF COL_LENGTH('dbo.DeliveryProviders', 'sort_order') IS NULL
  ALTER TABLE dbo.DeliveryProviders ADD sort_order INT NOT NULL DEFAULT 0;
IF COL_LENGTH('dbo.DeliveryProviders', 'is_active') IS NULL
  ALTER TABLE dbo.DeliveryProviders ADD is_active BIT NOT NULL DEFAULT 1;
"""
        )
        if seed_defaults:
            for row in (
                {
                    "provider_key": "deliveroo",
                    "platform": "DELIVEROO",
                    "label": "Deliveroo",
                    "logo_filename": "Deliveroo.png",
                    "rating_unit": "number",
                    "opening_mode": "opening",
                    "opening_label": "% Apertura",
                    "sort_order": 10,
                },
                {
                    "provider_key": "glovo",
                    "platform": "GLOVO",
                    "label": "Glovo",
                    "logo_filename": "Glovo.png",
                    "rating_unit": "percent",
                    "opening_mode": "closure",
                    "opening_label": "% Chiusura",
                    "sort_order": 20,
                },
            ):
                cur.execute(
                    """
IF NOT EXISTS (SELECT 1 FROM dbo.DeliveryProviders WHERE provider_key = ?)
BEGIN
  INSERT INTO dbo.DeliveryProviders
    (provider_key, platform, label, logo_filename, rating_unit, opening_mode, opening_label, is_active, sort_order)
  VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?);
END
""",
                    (
                        row["provider_key"],
                        row["provider_key"],
                        row["platform"],
                        row["label"],
                        row["logo_filename"],
                        row["rating_unit"],
                        row["opening_mode"],
                        row["opening_label"],
                        row["sort_order"],
                    ),
                )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            log_swallowed('delivery_repository:1542')
        raise
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('delivery_repository:1548')


def list_delivery_providers(active_only: bool = False) -> list[dict[str, Any]]:
    ensure_delivery_providers_schema(seed_defaults=False)
    conn = _get_sql_conn_app_storehub()
    try:
        cur = conn.cursor()
        sql = """
SELECT row_uuid, provider_key, platform, label, logo_filename, rating_unit,
       opening_mode, opening_label, is_active, sort_order
FROM dbo.DeliveryProviders
"""
        if active_only:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY sort_order ASC, label ASC"
        cur.execute(sql)
        out: list[dict[str, Any]] = []
        for r in cur.fetchall() or []:
            out.append(
                {
                    "row_uuid": str(r[0]),
                    "provider_key": str(r[1] or "").strip(),
                    "platform": str(r[2] or "").strip().upper(),
                    "label": str(r[3] or "").strip(),
                    "logo_filename": str(r[4] or "").strip(),
                    "rating_unit": str(r[5] or "number").strip().lower() or "number",
                    "opening_mode": str(r[6] or "opening").strip().lower() or "opening",
                    "opening_label": str(r[7] or "% Apertura").strip(),
                    "is_active": bool(r[8]),
                    "sort_order": int(r[9] or 0),
                }
            )
        return out
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('delivery_repository:1586')


def save_delivery_provider(
    *,
    row_uuid: str | None = None,
    provider_key: str = "",
    platform: str = "",
    label: str = "",
    logo_filename: str = "",
    rating_unit: str = "number",
    opening_mode: str = "opening",
    opening_label: str = "% Apertura",
    is_active: bool = True,
    sort_order: int = 0,
) -> str:
    ensure_delivery_providers_schema(seed_defaults=False)
    key = _provider_key(provider_key or platform or label)
    plat = str(platform or key).strip().upper()
    lbl = str(label or plat).strip()
    ru = str(rating_unit or "number").strip().lower()
    if ru not in {"number", "percent"}:
        ru = "number"
    om = str(opening_mode or "opening").strip().lower()
    if om not in {"opening", "closure"}:
        om = "opening"
    ol = str(opening_label or ("% Chiusura" if om == "closure" else "% Apertura")).strip()
    rid = str(row_uuid or "").strip()

    conn = _get_sql_conn_app_storehub()
    try:
        cur = conn.cursor()
        if rid:
            cur.execute(
                """
UPDATE dbo.DeliveryProviders
SET provider_key = ?, platform = ?, label = ?, logo_filename = ?,
    rating_unit = ?, opening_mode = ?, opening_label = ?,
    is_active = ?, sort_order = ?, updated_at = SYSUTCDATETIME()
WHERE row_uuid = ?
""",
                (key, plat, lbl, str(logo_filename or "").strip(), ru, om, ol, 1 if is_active else 0, int(sort_order or 0), rid),
            )
            out_id = rid
        else:
            cur.execute(
                """
INSERT INTO dbo.DeliveryProviders
  (provider_key, platform, label, logo_filename, rating_unit, opening_mode, opening_label, is_active, sort_order)
OUTPUT inserted.row_uuid
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
""",
                (key, plat, lbl, str(logo_filename or "").strip(), ru, om, ol, 1 if is_active else 0, int(sort_order or 0)),
            )
            out_id = str(cur.fetchone()[0])
        conn.commit()
        return out_id
    except Exception:
        try:
            conn.rollback()
        except Exception:
            log_swallowed('delivery_repository:1647')
        raise
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('delivery_repository:1653')


def delete_delivery_provider(row_uuid: str) -> bool:
    ensure_delivery_providers_schema(seed_defaults=False)
    rid = str(row_uuid or "").strip()
    if not rid:
        return False
    conn = _get_sql_conn_app_storehub()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM dbo.DeliveryProviders WHERE row_uuid = ?", (rid,))
        deleted = int(cur.rowcount or 0)
        conn.commit()
        return deleted > 0
    except Exception:
        try:
            conn.rollback()
        except Exception:
            log_swallowed('delivery_repository:1672')
        raise
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('delivery_repository:1678')


def _ensure_delivery_weekly_extra_columns() -> None:
    ensure_delivery_weekly_schema()

def _row_to_delivery_weekly(row) -> DeliveryWeeklyRow:
    return DeliveryWeeklyRow(
        store_code=str(getattr(row, "store_code")),
        platform=str(getattr(row, "platform")),
        week_start=getattr(row, "week_start"),
        payment_online=getattr(row, "payment_online"),
        payment_cash=getattr(row, "payment_cash"),
        orders=int(getattr(row, "orders") or 0),
        cancelled_orders=int(getattr(row, "cancelled_orders", 0) or 0),
        complaints_received=int(getattr(row, "complaints_received") or 0),
        refund_value=getattr(row, "refund_value"),
        complaints_contested=int(getattr(row, "complaints_contested") or 0),
        appeals_accepted=int(getattr(row, "appeals_accepted") or 0),
        refunds_cancelled_value=getattr(row, "refunds_cancelled_value"),
        opening_pct=getattr(row, "opening_pct", None),
        rating_value=getattr(row, "rating_value", None),
        rating_unit=str(getattr(row, "rating_unit")),
    )


def get_weekly(store_code: str, platform: str, week_start: _date) -> DeliveryWeeklyRow | None:
    """Ritorna la riga settimanale per store/platform (se esiste)."""
    _ensure_delivery_weekly_extra_columns()
    ensure_delivery_weekly_schema()
    conn = _get_sql_conn_app_storehub()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT TOP 1 store_code, platform, week_start,
                   payment_online, payment_cash, orders, cancelled_orders,
                   complaints_received, refund_value, complaints_contested,
                   appeals_accepted, refunds_cancelled_value,
                   opening_pct,
                   rating_value, rating_unit
            FROM dbo.DELIVERY_WEEKLY
            WHERE LTRIM(RTRIM(CAST(store_code AS NVARCHAR(50)))) = ?
              AND UPPER(LTRIM(RTRIM(CAST(platform AS NVARCHAR(50))))) = ?
              AND CONVERT(date, week_start) = ?
            ORDER BY updated_at DESC, created_at DESC
            """,
            (str(store_code).strip(), str(platform).strip().upper(), week_monday(week_start)),
        )
        row = cur.fetchone()
        if not row:
            return None
        # pyodbc row: supporta attribute access per nome colonna
        return _row_to_delivery_weekly(row)
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('delivery_repository:1736')


def list_weekly_rows(store_code: str, week_start: _date) -> list[DeliveryWeeklyRow]:
    """Ritorna tutte le righe DELIVERY_WEEKLY per lo store e la settimana (tutte le piattaforme)."""
    _ensure_delivery_weekly_extra_columns()
    ensure_delivery_weekly_schema()
    conn = _get_sql_conn_app_storehub()
    rows: list[DeliveryWeeklyRow] = []
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT store_code, platform, week_start,
                   payment_online, payment_cash, orders, cancelled_orders,
                   complaints_received, refund_value, complaints_contested,
                   appeals_accepted, refunds_cancelled_value,
                   opening_pct,
                   rating_value, rating_unit
            FROM dbo.DELIVERY_WEEKLY
            WHERE LTRIM(RTRIM(CAST(store_code AS NVARCHAR(50)))) = ?
              AND CONVERT(date, week_start) = ?
            ORDER BY platform
            """,
            (str(store_code).strip(), week_monday(week_start)),
        )
        for r in cur.fetchall() or []:
            rows.append(_row_to_delivery_weekly(r))
        return rows
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('delivery_repository:1769')


def export_weekly_rows(
    *,
    store_codes: list[str],
    week_start_from: _date | None = None,
    week_start_to: _date | None = None,
    platform: str | None = None,
) -> list[dict[str, object]]:
    """Estrazione righe DELIVERY_WEEKLY deduplicate per store/provider/settimana.

    Restituisce l'ultima riga disponibile per ciascuna chiave logica
    (store_code, platform, week_start), senza aggregare tra store.
    """
    codes = [str(c or "").strip() for c in (store_codes or []) if str(c or "").strip()]
    if not codes:
        return []

    ensure_delivery_weekly_schema()
    conn = _get_sql_conn_app_storehub()
    try:
        cur = conn.cursor()

        where = [
            "LTRIM(RTRIM(CAST(store_code AS NVARCHAR(50)))) IN ({codes})".format(
                codes=", ".join("?" for _ in codes)
            )
        ]
        params: list[object] = list(codes)

        if week_start_from is not None:
            where.append("CONVERT(date, week_start) >= ?")
            params.append(week_monday(week_start_from))
        if week_start_to is not None:
            where.append("CONVERT(date, week_start) <= ?")
            params.append(week_monday(week_start_to))

        plat = str(platform or "").strip().upper()
        if plat and plat != "ALL":
            where.append("UPPER(LTRIM(RTRIM(CAST(platform AS NVARCHAR(50))))) = ?")
            params.append(plat)

        sql = f"""
        WITH ranked AS (
            SELECT
                LTRIM(RTRIM(CAST(store_code AS NVARCHAR(50)))) AS store_code,
                UPPER(LTRIM(RTRIM(CAST(platform AS NVARCHAR(50))))) AS platform,
                CONVERT(date, week_start) AS week_start,
                payment_online,
                payment_cash,
                orders,
                cancelled_orders,
                complaints_received,
                refund_value,
                complaints_contested,
                appeals_accepted,
                refunds_cancelled_value,
                opening_pct,
                rating_value,
                rating_unit,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        LTRIM(RTRIM(CAST(store_code AS NVARCHAR(50)))),
                        UPPER(LTRIM(RTRIM(CAST(platform AS NVARCHAR(50))))),
                        CONVERT(date, week_start)
                    ORDER BY
                        updated_at DESC,
                        created_at DESC
                ) AS rn
            FROM dbo.DELIVERY_WEEKLY
            WHERE {" AND ".join(where)}
        )
        SELECT
            store_code,
            platform,
            week_start,
            payment_online,
            payment_cash,
            orders,
            cancelled_orders,
            complaints_received,
            refund_value,
            complaints_contested,
            appeals_accepted,
            refunds_cancelled_value,
            opening_pct,
            rating_value,
            rating_unit
        FROM ranked
        WHERE rn = 1
        ORDER BY week_start DESC, store_code ASC, platform ASC
        """
        cur.execute(sql, params)
        rows = cur.fetchall() or []
        out: list[dict[str, object]] = []
        for r in rows:
            pay_online = float(getattr(r, "payment_online", 0) or 0)
            pay_cash = float(getattr(r, "payment_cash", 0) or 0)
            out.append(
                {
                    "store_code": str(getattr(r, "store_code", "") or "").strip(),
                    "platform": str(getattr(r, "platform", "") or "").strip(),
                    "week_start": getattr(r, "week_start", None),
                    "payment_online": pay_online,
                    "payment_cash": pay_cash,
                    "payment_total": pay_online + pay_cash,
                    "orders": int(getattr(r, "orders", 0) or 0),
                    "cancelled_orders": int(getattr(r, "cancelled_orders", 0) or 0),
                    "complaints_received": int(getattr(r, "complaints_received", 0) or 0),
                    "refund_value": float(getattr(r, "refund_value", 0) or 0),
                    "complaints_contested": int(getattr(r, "complaints_contested", 0) or 0),
                    "appeals_accepted": int(getattr(r, "appeals_accepted", 0) or 0),
                    "refunds_cancelled_value": float(getattr(r, "refunds_cancelled_value", 0) or 0),
                    "opening_pct": getattr(r, "opening_pct", None),
                    "rating_value": getattr(r, "rating_value", None),
                    "rating_unit": str(getattr(r, "rating_unit", "") or "").strip(),
                }
            )
        return out
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('delivery_repository:1893')


def get_prev_rating(store_code: str, platform: str, week_start: _date) -> Decimal | None:
    """Rating della settimana precedente (lunedì - 7 giorni)."""
    prev_week = week_monday(week_start) - _timedelta(days=7)
    ensure_delivery_weekly_schema()
    conn = _get_sql_conn_app_storehub()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT rating_value
            FROM dbo.DELIVERY_WEEKLY
            WHERE LTRIM(RTRIM(CAST(store_code AS NVARCHAR(50)))) = ?
              AND UPPER(LTRIM(RTRIM(CAST(platform AS NVARCHAR(50))))) = ?
              AND CONVERT(date, week_start) = ?
            """,
            (str(store_code).strip(), str(platform).strip().upper(), prev_week),
        )
        row = cur.fetchone()
        if not row:
            return None
        try:
            return row[0]
        except Exception:
            return None
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('delivery_repository:1924')


def upsert_weekly(
    store_code: str,
    platform: str,
    week_start: _date,
    *,
    payment_online: Decimal,
    payment_cash: Decimal,
    orders: int,
    cancelled_orders: int,
    complaints_received: int,
    refund_value: Decimal,
    complaints_contested: int,
    appeals_accepted: int,
    refunds_cancelled_value: Decimal,
    opening_pct: Decimal | None,
    rating_value: Decimal | None,
    rating_unit: str,
) -> None:
    """Inserisce/aggiorna i dati settimanali."""
    ws = week_monday(week_start)
    plat = str(platform).strip().upper()
    ru = (rating_unit or "number").strip().lower()

    _ensure_delivery_weekly_extra_columns()
    conn = _get_sql_conn_app_storehub()
    try:
        cur = conn.cursor()
        sql = """
        MERGE dbo.DELIVERY_WEEKLY WITH (HOLDLOCK) AS tgt
        USING (SELECT ? AS store_code, ? AS platform, ? AS week_start) AS src
          ON LTRIM(RTRIM(CAST(tgt.store_code AS NVARCHAR(50)))) = LTRIM(RTRIM(CAST(src.store_code AS NVARCHAR(50))))
         AND UPPER(LTRIM(RTRIM(CAST(tgt.platform AS NVARCHAR(50))))) = UPPER(LTRIM(RTRIM(CAST(src.platform AS NVARCHAR(50)))))
         AND CONVERT(date, tgt.week_start) = CONVERT(date, src.week_start)
        WHEN MATCHED THEN
          UPDATE SET
            payment_online = ?,
            payment_cash = ?,
            orders = ?,
            cancelled_orders = ?,
            complaints_received = ?,
            refund_value = ?,
            complaints_contested = ?,
            appeals_accepted = ?,
            refunds_cancelled_value = ?,
            opening_pct = ?,
            rating_value = ?,
            rating_unit = ?,
            updated_at = SYSUTCDATETIME()
        WHEN NOT MATCHED THEN
          INSERT (
            store_code, platform, week_start,
            payment_online, payment_cash, orders, cancelled_orders,
            complaints_received, refund_value, complaints_contested,
            appeals_accepted, refunds_cancelled_value,
            opening_pct,
            rating_value, rating_unit
          )
          VALUES (
            src.store_code, src.platform, src.week_start,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?,
            ?, ?
          );
        """

        params = (
            str(store_code).strip(),
            plat,
            ws,
            payment_online,
            payment_cash,
            int(orders or 0),
            int(cancelled_orders or 0),
            int(complaints_received or 0),
            refund_value,
            int(complaints_contested or 0),
            int(appeals_accepted or 0),
            refunds_cancelled_value,
            opening_pct,
            rating_value,
            ru,
            payment_online,
            payment_cash,
            int(orders or 0),
            int(cancelled_orders or 0),
            int(complaints_received or 0),
            refund_value,
            int(complaints_contested or 0),
            int(appeals_accepted or 0),
            refunds_cancelled_value,
            opening_pct,
            rating_value,
            ru,
        )

        cur.execute(sql, params)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            log_swallowed('delivery_repository:2030')
        raise
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('delivery_repository:2036')


def list_refunds_agg(store_code: str, range_start: _date, range_end: _date) -> list[dict[str, object]]:
    """Serie settimanale dei rimborsi aggregati (Glovo+Deliveroo) nel periodo."""
    rs = week_monday(range_start)
    re = week_monday(range_end)
    if re < rs:
        rs, re = re, rs

    ensure_delivery_weekly_schema()
    conn = _get_sql_conn_app_storehub()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
              CONVERT(varchar(10), week_start, 23) AS week_start,
              SUM(refund_value) AS refunds_value,
              SUM(refunds_cancelled_value) AS refunds_cancelled,
              SUM(refund_value) - SUM(refunds_cancelled_value) AS refunds_net
            FROM dbo.DELIVERY_WEEKLY
            WHERE store_code = ?
              AND week_start >= ?
              AND week_start <= ?
              AND platform IN ('DELIVEROO', 'GLOVO')
            GROUP BY week_start
            ORDER BY week_start
            """,
            (str(store_code).strip(), rs, re),
        )
        out: list[dict[str, object]] = []
        for r in cur.fetchall() or []:
            out.append(
                {
                    "week_start": str(r.week_start),
                    "refunds_value": r.refunds_value,
                    "refunds_cancelled": r.refunds_cancelled,
                    "refunds_net": r.refunds_net,
                }
            )
        return out
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('delivery_repository:2082')
