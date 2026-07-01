from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import List, Optional

try:
    import pyodbc
except ImportError:  # pragma: no cover
    pyodbc = None

from access_db import get_connection as _access_get_connection


_STOREHUB_DATABASE_OVERRIDE: ContextVar[str] = ContextVar("storehub_database_override", default="")


def _available_sqlserver_drivers() -> List[str]:
    """Return installed ODBC driver names (best-effort)."""
    if pyodbc is None:
        return []
    try:
        return list(pyodbc.drivers())
    except Exception:
        return []


def _driver_version_key(name: str) -> int:
    """Sort helper: extract a numeric version from driver name."""
    import re

    nums = re.findall(r"(\d+)", name)
    if not nums:
        return -1
    try:
        return int(nums[-1])
    except Exception:
        return -1


def _candidate_sqlserver_drivers(preferred: Optional[str]) -> List[str]:
    """Build a de-duplicated list of driver names to try."""
    candidates: List[str] = []
    if preferred:
        candidates.append(preferred)

    installed = _available_sqlserver_drivers()
    # Prefer Microsoft "ODBC Driver XX for SQL Server" drivers (highest XX first)
    installed_sorted = sorted(installed, key=_driver_version_key, reverse=True)
    for d in installed_sorted:
        if "sql server" in d.lower():
            candidates.append(d)

    # Common fallbacks (in case drivers() is empty or oddly named)
    candidates.extend(
        [
            "ODBC Driver 18 for SQL Server",
            "ODBC Driver 17 for SQL Server",
            "ODBC Driver 13 for SQL Server",
            "ODBC Driver 11 for SQL Server",
            "SQL Server",
        ]
    )

    # De-duplicate while preserving order
    seen = set()
    out: List[str] = []
    for d in candidates:
        if not d:
            continue
        key = d.strip()
        if key.lower() in seen:
            continue
        seen.add(key.lower())
        out.append(key)
    return out

def get_backend() -> str:
    v = (os.getenv("DB_BACKEND") or "").strip().lower()
    if v in ("sqlserver", "mssql", "sql"):
        return "sqlserver"
    return "access"


def get_storehub_database_name() -> str:
    override = str(_STOREHUB_DATABASE_OVERRIDE.get() or "").strip()
    if override:
        return override
    try:
        from flask import has_request_context, session

        if has_request_context():
            db_name = str(session.get("tenant_database") or "").strip()
            if db_name:
                return db_name
            master_tenant_key = str(session.get("master_admin_tenant_key") or "").strip()
            if master_tenant_key:
                try:
                    from tenant_config_repository import get_tenant

                    tenant = get_tenant(master_tenant_key) or {}
                    db_name = str(tenant.get("database_name") or "").strip()
                    if db_name:
                        return db_name
                except Exception:
                    pass
    except Exception:
        pass
    return (
        os.getenv("STOREHUB_TENANT_DATABASE")
        or os.getenv("SQLSERVER_DATABASE")
        or os.getenv("SQLSERVER_DB")
        or "APP_STOREHUB"
    )


@contextmanager
def storehub_database_context(database_name: str):
    token = _STOREHUB_DATABASE_OVERRIDE.set(str(database_name or "").strip())
    try:
        yield
    finally:
        _STOREHUB_DATABASE_OVERRIDE.reset(token)


def supports_schema_alter() -> bool:
    # In SQL Server lo schema è gestito lato DB e non vogliamo fare ALTER automatici.
    return get_backend() == "access"

def get_connection(store_code: Optional[str] = None, read_only: bool = False):
    backend = get_backend()
    if backend == "sqlserver":
        return get_connection_sqlserver_database(
            get_storehub_database_name(),
            read_only=read_only,
        )
    # access
    return _access_get_connection(store_code, read_only=read_only)


def get_connection_sqlserver_database(database: str, read_only: bool = False):
    if pyodbc is None:
        raise RuntimeError("pyodbc non disponibile. Installa pyodbc e ODBC Driver per SQL Server.")

    server = os.getenv("SQLSERVER_SERVER") or os.getenv("SQLSERVER_HOST") or r"10.24.1.1\SQLEXPRESS"
    user = os.getenv("SQLSERVER_USER") or "file"
    password = os.getenv("SQLSERVER_PASSWORD") or "Metis2021@"
    preferred_driver = os.getenv("SQLSERVER_DRIVER")
    encrypt = (os.getenv("SQLSERVER_ENCRYPT") or "no").strip().lower()
    trust_cert = (os.getenv("SQLSERVER_TRUST_CERT") or os.getenv("SQLSERVER_TRUST_SERVER_CERT") or "yes").strip().lower()
    timeout = int(os.getenv("SQLSERVER_TIMEOUT") or "30")

    enc_val = "yes" if encrypt in ("1", "true", "yes", "y") else "no"
    tsc_val = "yes" if trust_cert in ("1", "true", "yes", "y") else "no"

    last_im002: Optional[Exception] = None
    for driver in _candidate_sqlserver_drivers(preferred_driver):
        parts = [
            f"DRIVER={{{driver}}}",
            f"SERVER={server}",
            f"DATABASE={database}",
            f"UID={user}",
            f"PWD={password}",
            f"Encrypt={enc_val}",
            f"TrustServerCertificate={tsc_val}",
        ]
        conn_str = ";".join(parts) + ";"
        try:
            return pyodbc.connect(conn_str, timeout=timeout, autocommit=bool(read_only))
        except Exception as e:  # noqa: BLE001
            if getattr(e, "args", None) and len(e.args) > 0 and str(e.args[0]) == "IM002":
                last_im002 = e
                continue
            raise

    available = _available_sqlserver_drivers()
    raise RuntimeError(
        "ODBC driver per SQL Server non trovato (IM002). "
        "Installa 'ODBC Driver 18 for SQL Server' o 'ODBC Driver 17 for SQL Server', "
        "oppure imposta SQLSERVER_DRIVER nel .env con un driver installato. "
        f"Driver disponibili: {available!r}."
    ) from last_im002


def get_connection_ilp(read_only: bool = False):
    """Connessione SQL Server al database ILP (P&L).

    Usa gli stessi parametri di connessione di get_connection(),
    ma forza DATABASE=ILP.
    """
    # Il P&L è disponibile solo su SQL Server.
    if pyodbc is None:
        raise RuntimeError("pyodbc non disponibile. Installa pyodbc e ODBC Driver per SQL Server.")

    database = os.getenv("SQLSERVER_DATABASE_ILP") or os.getenv("SQLSERVER_DB_ILP") or "ILP"
    return get_connection_sqlserver_database(database, read_only=read_only)


def get_connection_database_new(read_only: bool = False):
    """Connessione SQL Server al database 'DATABASE NEW' (Actual P&L da view VW_DATIINVENTARIO).

    Usa gli stessi parametri di connessione di get_connection(),
    ma forza DATABASE=DATABASE NEW (configurabile via env).
    """
    # Disponibile solo su SQL Server.
    if pyodbc is None:
        raise RuntimeError("pyodbc non disponibile. Installa pyodbc e ODBC Driver per SQL Server.")

    database = os.getenv("SQLSERVER_DATABASE_NEW") or os.getenv("SQLSERVER_DB_NEW") or "DATABASE NEW"
    return get_connection_sqlserver_database(database, read_only=read_only)

# --- SQL dialect helpers (Access vs SQL Server) ---

def sql_date(expr: str) -> str:
    # expr può essere un campo (es. [Data]) o un placeholder '?'
    if get_backend() == "sqlserver":
        return f"TRY_CONVERT(date, {expr})"
    return f"DateValue({expr})"

def sql_trim(expr: str) -> str:
    if get_backend() == "sqlserver":
        return f"LTRIM(RTRIM({expr}))"
    return f"Trim({expr})"

def sql_cast_str(expr: str) -> str:
    if get_backend() == "sqlserver":
        return f"CAST({expr} AS NVARCHAR(255))"
    return f"CStr({expr})"

def sql_mid(expr: str, start: int, length: int) -> str:
    # start 1-based in both Access Mid and SQL Server SUBSTRING
    if get_backend() == "sqlserver":
        return f"SUBSTRING({expr}, {start}, {length})"
    return f"Mid({expr}, {start}, {length})"
