#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Worker export listini (FoodPaper/Operating) in subprocess.

Legge input JSON e scrive avanzamento su progress JSON.
Serve per isolare eventuali crash del driver Access/ODBC dal processo Flask.
"""

from __future__ import annotations

from app_logging import log_swallowed
import argparse
import json
import sys
import time
import traceback
from pathlib import Path


def _json_read_safe(path: Path) -> dict:
    try:
        txt = path.read_text(encoding="utf-8", errors="ignore")
        return json.loads(txt or "{}")
    except Exception:
        return {}


def _json_write_atomic(path: Path, obj: dict) -> None:
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        try:
            path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        except Exception:
            log_swallowed('listini_export_worker:36')


def _append_log(st: dict, msg: str) -> None:
    msg = str(msg or "").strip()
    if not msg:
        return
    logs = st.get("logs")
    if not isinstance(logs, list):
        logs = []
    logs.append(msg)
    if len(logs) > 250:
        logs = logs[-250:]
    st["logs"] = logs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--progress", required=True)
    args = ap.parse_args()

    input_path = Path(args.input)
    prog_path = Path(args.progress)

    # Ensure project root in sys.path
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))

    # Best-effort: disable pyodbc pooling to reduce "Too many client tasks"
    try:
        import pyodbc  # type: ignore
        pyodbc.pooling = False  # type: ignore[attr-defined]
    except Exception:
        log_swallowed('listini_export_worker:71')

    inp = _json_read_safe(input_path)
    if not inp:
        st = _json_read_safe(prog_path)
        st.update({"running": False, "finished": True, "error": "Input non leggibile", "ts": time.time()})
        _json_write_atomic(prog_path, st)
        return 2

    job_id = str(inp.get("job_id") or "")
    listino_type = str(inp.get("type") or "FoodPaper").strip() or "FoodPaper"
    source_store = str(inp.get("source_store") or "9001").strip() or "9001"
    scope = str(inp.get("scope") or "master").strip().lower() or "master"
    columns = inp.get("columns") or []
    rows = inp.get("rows") or []
    store_codes = inp.get("store_codes") or []

    total = len(store_codes)

    st = _json_read_safe(prog_path)
    st.update(
        {
            "id": job_id or st.get("id"),
            "type": listino_type,
            "source_store": source_store,
            "scope": scope,
            "running": True,
            "finished": False,
            "done": 0,
            "total": total,
            "stores_ok": 0,
            "stores_fail": 0,
            "current_store": "",
            "failures": [],
            "error": None,
            "ts": time.time(),
        }
    )
    if "logs" not in st:
        st["logs"] = []
    _json_write_atomic(prog_path, st)

    try:
        from listini_repository import apply_pricelist_to_stores  # type: ignore
    except Exception as e:
        st["running"] = False
        st["finished"] = True
        st["error"] = f"Import worker fallito: {e}"
        st["ts"] = time.time()
        _append_log(st, st["error"])
        _json_write_atomic(prog_path, st)
        return 3

    stores_ok = 0
    stores_fail = 0
    failures = []

    for i, sc in enumerate(store_codes, start=1):
        sc = str(sc)
        st["current_store"] = sc
        st["ts"] = time.time()
        _json_write_atomic(prog_path, st)

        try:
            res = apply_pricelist_to_stores(
                listino_type=listino_type,
                rows=rows,
                columns=columns,
                store_codes=[sc],
                source_store_code=source_store,
            )
            ok = bool(res.get("ok")) and int(res.get("stores_fail") or 0) == 0
            if ok:
                stores_ok += 1
                _append_log(st, f"{i}/{total} · store {sc} · OK")
            else:
                stores_fail += 1
                err = res.get("error") or "Errore aggiornamento store"
                _append_log(st, f"{i}/{total} · store {sc} · ERRORE: {err}")
                failures.append({"store": sc, "error": str(err)})
        except Exception as e:
            stores_fail += 1
            err = str(e)
            _append_log(st, f"{i}/{total} · store {sc} · ERRORE FATALE: {err}")
            _append_log(st, traceback.format_exc())
            failures.append({"store": sc, "error": err})

        st["done"] = i
        st["stores_ok"] = stores_ok
        st["stores_fail"] = stores_fail
        st["failures"] = failures[-50:]
        st["ts"] = time.time()
        _json_write_atomic(prog_path, st)

        # throttle leggero: riduce stress sul driver Access/ODBC
        time.sleep(0.05)

    st["running"] = False
    st["finished"] = True
    st["current_store"] = ""
    st["done"] = total
    st["stores_ok"] = stores_ok
    st["stores_fail"] = stores_fail
    st["failures"] = failures[-50:]
    st["ts"] = time.time()
    _json_write_atomic(prog_path, st)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
