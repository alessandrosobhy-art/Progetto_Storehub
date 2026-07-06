from __future__ import annotations

from app_logging import log_swallowed
import csv
import io
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time as dtime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app_db import get_backend, get_connection
from orari_repository import list_turni_week
from staff_repository import list_staff


# ----------------------------
# SQL / DB helpers
# ----------------------------

def _q(name: str) -> str:
    return f"[{str(name).replace(']', ']]')}]"


def _require_sqlserver() -> None:
    if get_backend() != "sqlserver":
        raise RuntimeError(
            "Funzione disponibile con DB_BACKEND=sqlserver (APP_STOREHUB per configurazioni e CMO)."
        )


def _dict_rows(cur) -> List[Dict[str, Any]]:
    cols = [d[0] for d in (cur.description or [])]
    out: List[Dict[str, Any]] = []
    for r in cur.fetchall() or []:
        d: Dict[str, Any] = {}
        for i, c in enumerate(cols):
            v = r[i]
            if hasattr(v, "isoformat") and not isinstance(v, (str, bytes)):
                try:
                    v = v.isoformat()
                except Exception:
                    log_swallowed('admin_labor_cost_repository:41')
            if isinstance(v, float):
                # Clean NaN/inf for JSON
                if math.isnan(v) or math.isinf(v):
                    v = None
            d[str(c)] = v
        out.append(d)
    return out


def _ensure_tables() -> None:
    _require_sqlserver()
    conn = get_connection()
    try:
        cur = conn.cursor()
        # Company profile
        cur.execute(
            """
IF OBJECT_ID('dbo.LABOR_COST_COMPANY_PROFILE','U') IS NULL
BEGIN
    CREATE TABLE dbo.LABOR_COST_COMPANY_PROFILE (
        id INT IDENTITY(1,1) PRIMARY KEY,
        code NVARCHAR(50) NOT NULL,
        name NVARCHAR(120) NOT NULL,
        sector NVARCHAR(80) NULL,
        default_hourly_rate DECIMAL(12,4) NULL,
        annual_hours_full_time DECIMAL(12,2) NOT NULL CONSTRAINT DF_LCP_annual_hours DEFAULT (173*12),
        employer_inps_pct DECIMAL(9,6) NOT NULL CONSTRAINT DF_LCP_inps DEFAULT (0.30),
        inail_pct DECIMAL(9,6) NOT NULL CONSTRAINT DF_LCP_inail DEFAULT (0.02),
        tfr_pct DECIMAL(9,6) NOT NULL CONSTRAINT DF_LCP_tfr DEFAULT (0.0741),
        ferie_permessi_accrual_pct DECIMAL(9,6) NOT NULL CONSTRAINT DF_LCP_fp DEFAULT (0.12),
        tredicesima_pct DECIMAL(9,6) NOT NULL CONSTRAINT DF_LCP_13 DEFAULT (0.0833),
        quattordicesima_pct DECIMAL(9,6) NOT NULL CONSTRAINT DF_LCP_14 DEFAULT (0.0000),
        other_accruals_pct DECIMAL(9,6) NOT NULL CONSTRAINT DF_LCP_other DEFAULT (0.0000),
        is_active BIT NOT NULL CONSTRAINT DF_LCP_active DEFAULT (1),
        notes NVARCHAR(1000) NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_LCP_created DEFAULT (SYSDATETIME()),
        updated_at DATETIME2 NOT NULL CONSTRAINT DF_LCP_updated DEFAULT (SYSDATETIME())
    );
    CREATE UNIQUE INDEX UX_LCP_code ON dbo.LABOR_COST_COMPANY_PROFILE(code);
END
            """
        )
        cur.execute(
            """
IF OBJECT_ID('dbo.LABOR_COST_COMPANY_EXTRA','U') IS NULL
BEGIN
    CREATE TABLE dbo.LABOR_COST_COMPANY_EXTRA (
        id INT IDENTITY(1,1) PRIMARY KEY,
        company_profile_id INT NOT NULL,
        extra_code NVARCHAR(50) NOT NULL,
        label NVARCHAR(120) NOT NULL,
        pct DECIMAL(9,6) NOT NULL,
        is_active BIT NOT NULL CONSTRAINT DF_LCE_active DEFAULT (1),
        sort_order INT NOT NULL CONSTRAINT DF_LCE_sort DEFAULT (0),
        applies_to NVARCHAR(30) NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_LCE_created DEFAULT (SYSDATETIME()),
        updated_at DATETIME2 NOT NULL CONSTRAINT DF_LCE_updated DEFAULT (SYSDATETIME()),
        CONSTRAINT FK_LCE_profile FOREIGN KEY (company_profile_id)
            REFERENCES dbo.LABOR_COST_COMPANY_PROFILE(id)
    );
    CREATE UNIQUE INDEX UX_LCE_profile_code ON dbo.LABOR_COST_COMPANY_EXTRA(company_profile_id, extra_code);
END
            """
        )
        cur.execute(
            """
IF OBJECT_ID('dbo.LABOR_COST_EMPLOYEE_CONFIG','U') IS NULL
BEGIN
    CREATE TABLE dbo.LABOR_COST_EMPLOYEE_CONFIG (
        id INT IDENTITY(1,1) PRIMARY KEY,
        employee_code NVARCHAR(50) NOT NULL,
        employee_name NVARCHAR(255) NULL,
        store_code NVARCHAR(20) NULL,
        company_profile_id INT NULL,
        contract_type NVARCHAR(50) NULL,
        ral DECIMAL(12,2) NULL,
        hourly_rate_override DECIMAL(12,4) NULL,
        inquadramento_override NVARCHAR(80) NULL,
        employer_inps_pct_override DECIMAL(9,6) NULL,
        inail_pct_override DECIMAL(9,6) NULL,
        tfr_pct_override DECIMAL(9,6) NULL,
        hire_date DATE NULL,
        termination_date DATE NULL,
        is_active BIT NOT NULL CONSTRAINT DF_LCEC_active DEFAULT (1),
        source_type NVARCHAR(20) NULL,
        source_note NVARCHAR(500) NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_LCEC_created DEFAULT (SYSDATETIME()),
        updated_at DATETIME2 NOT NULL CONSTRAINT DF_LCEC_updated DEFAULT (SYSDATETIME())
    );
END

IF COL_LENGTH('dbo.LABOR_COST_EMPLOYEE_CONFIG', 'store_code_norm') IS NULL
BEGIN
    ALTER TABLE dbo.LABOR_COST_EMPLOYEE_CONFIG
    ADD store_code_norm AS ISNULL(NULLIF(LTRIM(RTRIM(store_code)), ''), '') PERSISTED;
END

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'dbo.LABOR_COST_EMPLOYEE_CONFIG')
      AND name = N'UX_LCEC_store_empcode'
)
BEGIN
    CREATE UNIQUE INDEX UX_LCEC_store_empcode
        ON dbo.LABOR_COST_EMPLOYEE_CONFIG(store_code_norm, employee_code);
END

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'dbo.LABOR_COST_EMPLOYEE_CONFIG')
      AND name = N'IX_LCEC_profile'
)
BEGIN
    CREATE INDEX IX_LCEC_profile ON dbo.LABOR_COST_EMPLOYEE_CONFIG(company_profile_id);
END

IF COL_LENGTH('dbo.LABOR_COST_EMPLOYEE_CONFIG', 'stage_fixed_hourly_cost') IS NULL
BEGIN
    ALTER TABLE dbo.LABOR_COST_EMPLOYEE_CONFIG ADD stage_fixed_hourly_cost DECIMAL(12,4) NULL;
END

IF COL_LENGTH('dbo.LABOR_COST_EMPLOYEE_CONFIG', 'calc_mode_override') IS NULL
BEGIN
    ALTER TABLE dbo.LABOR_COST_EMPLOYEE_CONFIG ADD calc_mode_override NVARCHAR(30) NULL;
END
            """
        )
        cur.execute(
            """
IF OBJECT_ID('dbo.LABOR_COST_COMPANY_ROLE_RATES','U') IS NULL
BEGIN
    CREATE TABLE dbo.LABOR_COST_COMPANY_ROLE_RATES (
        id INT IDENTITY(1,1) PRIMARY KEY,
        company_profile_id INT NOT NULL,
        staff_role_code NVARCHAR(50) NOT NULL,
        staff_role_label NVARCHAR(120) NOT NULL,
        employer_inps_pct DECIMAL(9,6) NULL,
        inail_pct DECIMAL(9,6) NULL,
        calc_mode NVARCHAR(30) NOT NULL CONSTRAINT DF_LCCR_calc_mode DEFAULT ('STANDARD'),
        fixed_hourly_cost DECIMAL(12,4) NULL,
        is_active BIT NOT NULL CONSTRAINT DF_LCCR_active DEFAULT (1),
        sort_order INT NOT NULL CONSTRAINT DF_LCCR_sort DEFAULT (0),
        notes NVARCHAR(500) NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_LCCR_created DEFAULT (SYSDATETIME()),
        updated_at DATETIME2 NOT NULL CONSTRAINT DF_LCCR_updated DEFAULT (SYSDATETIME()),
        CONSTRAINT FK_LCCR_profile FOREIGN KEY (company_profile_id) REFERENCES dbo.LABOR_COST_COMPANY_PROFILE(id)
    );
    CREATE UNIQUE INDEX UX_LCCR_profile_role ON dbo.LABOR_COST_COMPANY_ROLE_RATES(company_profile_id, staff_role_code);
    CREATE INDEX IX_LCCR_profile ON dbo.LABOR_COST_COMPANY_ROLE_RATES(company_profile_id);
END

IF OBJECT_ID('dbo.LABOR_COST_COMPANY_ROLE_RATE','U') IS NOT NULL
BEGIN
    INSERT INTO dbo.LABOR_COST_COMPANY_ROLE_RATES(
        company_profile_id, staff_role_code, staff_role_label, employer_inps_pct, inail_pct,
        calc_mode, fixed_hourly_cost, is_active, sort_order
    )
    SELECT
        oldr.company_profile_id,
        UPPER(LTRIM(RTRIM(oldr.role_code))),
        COALESCE(NULLIF(LTRIM(RTRIM(oldr.role_label)),''), UPPER(LTRIM(RTRIM(oldr.role_code)))),
        oldr.employer_inps_pct,
        oldr.inail_pct,
        CASE WHEN UPPER(LTRIM(RTRIM(oldr.role_code)))='STAGE' THEN 'FIXED_ALL_IN' ELSE 'STANDARD' END,
        NULL,
        ISNULL(oldr.is_active,1),
        ISNULL(oldr.sort_order,0)
    FROM dbo.LABOR_COST_COMPANY_ROLE_RATE oldr
    WHERE NOT EXISTS (
        SELECT 1
        FROM dbo.LABOR_COST_COMPANY_ROLE_RATES nr
        WHERE nr.company_profile_id = oldr.company_profile_id
          AND nr.staff_role_code = UPPER(LTRIM(RTRIM(oldr.role_code)))
    );
END
            """
        )
        # Seed common extras if no rows at all and a profile exists (handled on create profile too)
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('admin_labor_cost_repository:227')


def _norm_key(s: Any) -> str:
    return "".join(ch for ch in str(s or "").strip().upper() if ch.isalnum())


def _to_float(v: Any, default: float = 0.0) -> float:
    if v is None:
        return float(default)
    if isinstance(v, (int, float)):
        try:
            fv = float(v)
            if math.isnan(fv) or math.isinf(fv):
                return float(default)
            return fv
        except Exception:
            return float(default)
    s = str(v).strip()
    if not s:
        return float(default)
    s = s.replace(" ", "")
    if s.count(",") == 1 and s.count(".") == 0:
        s = s.replace(",", ".")
    elif s.count(",") > 0 and s.count(".") > 0:
        # Italian thousands + decimals ex 1.234,56
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    try:
        return float(s)
    except Exception:
        return float(default)


def _to_int(v: Any, default: int = 0) -> int:
    try:
        return int(round(_to_float(v, float(default))))
    except Exception:
        return int(default)


def _to_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v or "").strip().lower()
    if s in ("1", "true", "t", "yes", "y", "si", "s", "on"):
        return True
    if s in ("0", "false", "f", "no", "n", "off"):
        return False
    return default


def _parse_date(v: Any) -> Optional[date]:
    s = str(v or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            log_swallowed('admin_labor_cost_repository:289')
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        return None


# ----------------------------
# Company profiles / extras
# ----------------------------

DEFAULT_EXTRAS = [
    ("STRAORDINARIA", "Maggiorazione straordinario", 0.15, 10, "worked"),
    ("DOMENICALE", "Maggiorazione domenicale", 0.30, 20, "worked"),
    ("NOTTURNA", "Maggiorazione notturna", 0.25, 30, "worked"),
    ("FESTIVA", "Maggiorazione festiva", 0.30, 40, "worked"),
]

ROLE_PROFILE_DEFAULTS = [
    ("STORE_MANAGER", "Store Manager"),
    ("ASSISTANT", "Assistant"),
    ("BANCONISTA", "Banconista"),
    ("APPRENDISTA", "Apprendista"),
    ("STAGE", "Stage"),
    ("INTERMITTENTE", "Intermittente"),
]

ROLE_PROFILE_LABELS = {code: label for code, label in ROLE_PROFILE_DEFAULTS}


def _normalize_staff_profile_role(value: Any) -> Optional[str]:
    s = str(value or "").strip().lower()
    if not s:
        return None
    k = _norm_key(s)
    if "STORE" in k and "MANAGER" in k:
        return "STORE_MANAGER"
    if k in {"SM"}:
        return "STORE_MANAGER"
    if "ASSISTANT" in k or k in {"ASM"}:
        return "ASSISTANT"
    if "APPRENDIST" in k:
        return "APPRENDISTA"
    if "STAGE" in k or "TIROCIN" in k:
        return "STAGE"
    if "INTERMITT" in k or "INTERINAL" in k or "SOMMINISTRAZ" in k or "AGENZIA" in k:
        return "INTERMITTENTE"
    if "BANCONIST" in k or "ADDETTO" in k or "OPERAT" in k or "CREW" in k:
        return "BANCONISTA"
    return None


def _ensure_company_role_rate_defaults(cur, company_profile_id: int, inps_pct: float, inail_pct: float) -> None:
    pid = int(company_profile_id)
    for pos, (code, label) in enumerate(ROLE_PROFILE_DEFAULTS, start=1):
        calc_mode = "FIXED_ALL_IN" if code in {"STAGE", "INTERMITTENTE"} else "STANDARD"
        cur.execute(
            """
IF NOT EXISTS (SELECT 1 FROM dbo.LABOR_COST_COMPANY_ROLE_RATES WHERE company_profile_id=? AND staff_role_code=?)
INSERT INTO dbo.LABOR_COST_COMPANY_ROLE_RATES(
    company_profile_id, staff_role_code, staff_role_label, employer_inps_pct, inail_pct,
    calc_mode, fixed_hourly_cost, sort_order, is_active
)
VALUES (?,?,?,?,?,?,?,?,1)
            """,
            [
                pid,
                code,
                pid,
                code,
                label,
                (None if code in {"STAGE", "INTERMITTENTE"} else float(inps_pct)),
                (None if code in {"STAGE", "INTERMITTENTE"} else float(inail_pct)),
                calc_mode,
                None,
                pos * 10,
            ],
        )


def list_company_role_rates(company_profile_id: int) -> List[Dict[str, Any]]:
    _ensure_tables()
    pid = int(company_profile_id)
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT employer_inps_pct, inail_pct FROM dbo.LABOR_COST_COMPANY_PROFILE WHERE id=?", [pid])
        row = cur.fetchone()
        if row:
            base_inps = _to_float(row[0], 0.30)
            base_inail = _to_float(row[1], 0.02)
            _ensure_company_role_rate_defaults(cur, pid, base_inps, base_inail)
            conn.commit()
        cur.execute(
            """
SELECT *
FROM dbo.LABOR_COST_COMPANY_ROLE_RATES
WHERE company_profile_id=?
ORDER BY sort_order, staff_role_label
            """,
            [pid],
        )
        rows = _dict_rows(cur)
        out: List[Dict[str, Any]] = []
        seen = set()
        for r in rows:
            code = str(r.get("staff_role_code") or r.get("role_code") or "").strip().upper()
            if code == "INTERINALE":
                code = "INTERMITTENTE"
            if not code or code in seen:
                continue
            seen.add(code)
            out.append({
                "id": _to_int(r.get("id")),
                "company_profile_id": _to_int(r.get("company_profile_id")),
                "role_code": code,
                "role_label": str(r.get("staff_role_label") or r.get("role_label") or ROLE_PROFILE_LABELS.get(code) or code).strip(),
                "employer_inps_pct": _to_float(r.get("employer_inps_pct")) if r.get("employer_inps_pct") is not None else None,
                "inail_pct": _to_float(r.get("inail_pct")) if r.get("inail_pct") is not None else None,
                "sort_order": _to_int(r.get("sort_order")),
                "is_active": bool(r.get("is_active")) if r.get("is_active") is not None else True,
                "calc_mode": str(r.get("calc_mode") or ("FIXED_ALL_IN" if code in {"STAGE", "INTERMITTENTE"} else "STANDARD")).strip().upper(),
                "fixed_hourly_cost": _to_float(r.get("fixed_hourly_cost")) if r.get("fixed_hourly_cost") is not None else None,
            })
        # Ensure all canonical rows are present in payload (UI order)
        idx = {str(x.get('role_code')): x for x in out}
        ordered = []
        for pos, (code, label) in enumerate(ROLE_PROFILE_DEFAULTS, start=1):
            if code in idx:
                rowx = idx[code]
                rowx.setdefault('sort_order', pos*10)
                rowx['role_label'] = rowx.get('role_label') or label
                ordered.append(rowx)
            else:
                ordered.append({
                    'id': 0, 'company_profile_id': pid, 'role_code': code, 'role_label': label,
                    'employer_inps_pct': None, 'inail_pct': None, 'sort_order': pos*10, 'is_active': True,
                    'calc_mode': ('FIXED_ALL_IN' if code in {'STAGE', 'INTERMITTENTE'} else 'STANDARD'), 'fixed_hourly_cost': None,
                })
        return ordered
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('admin_labor_cost_repository:433')


def replace_company_role_rates(company_profile_id: int, role_rates: List[Dict[str, Any]]) -> Dict[str, Any]:
    _ensure_tables()
    pid = int(company_profile_id)
    rows_clean: List[Tuple[str, str, Optional[float], Optional[float], str, Optional[float], int, int]] = []
    base_by_code = {c: l for c, l in ROLE_PROFILE_DEFAULTS}
    for i, r in enumerate(role_rates or []):
        code = str((r or {}).get('role_code') or '').strip().upper()
        if code == 'INTERINALE':
            code = 'INTERMITTENTE'
        if not code:
            continue
        label = str((r or {}).get('role_label') or base_by_code.get(code) or code).strip()
        inps_raw = (r or {}).get('employer_inps_pct')
        inail_raw = (r or {}).get('inail_pct')
        inps = _to_float(inps_raw) if str(inps_raw or '').strip() != '' else None
        inail = _to_float(inail_raw) if str(inail_raw or '').strip() != '' else None
        calc_mode = str((r or {}).get('calc_mode') or ('FIXED_ALL_IN' if code in {'STAGE', 'INTERMITTENTE'} else 'STANDARD')).strip().upper()
        if calc_mode not in {'STANDARD', 'FIXED_ALL_IN'}:
            calc_mode = 'FIXED_ALL_IN' if code in {'STAGE', 'INTERMITTENTE'} else 'STANDARD'
        fixed_raw = (r or {}).get('fixed_hourly_cost')
        fixed_hourly_cost = _to_float(fixed_raw) if str(fixed_raw or '').strip() != '' else None
        sort_order = _to_int((r or {}).get('sort_order'), (i + 1) * 10)
        is_active = 1 if _to_bool((r or {}).get('is_active'), True) else 0
        rows_clean.append((code, label, inps, inail, calc_mode, fixed_hourly_cost, sort_order, is_active))
    if not rows_clean:
        rows_clean = [
            (c, l, None, None, ('FIXED_ALL_IN' if c in {'STAGE', 'INTERMITTENTE'} else 'STANDARD'), None, (i + 1) * 10, 1)
            for i, (c, l) in enumerate(ROLE_PROFILE_DEFAULTS)
        ]
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM dbo.LABOR_COST_COMPANY_ROLE_RATES WHERE company_profile_id=?", [pid])
        for code, label, inps, inail, calc_mode, fixed_hourly_cost, sort_order, is_active in rows_clean:
            cur.execute(
                """
INSERT INTO dbo.LABOR_COST_COMPANY_ROLE_RATES(
    company_profile_id, staff_role_code, staff_role_label, employer_inps_pct, inail_pct,
    calc_mode, fixed_hourly_cost, sort_order, is_active
)
VALUES (?,?,?,?,?,?,?,?,?)
                """,
                [pid, code, label, inps, inail, calc_mode, fixed_hourly_cost, sort_order, is_active],
            )
        conn.commit()
        return {"ok": True, "saved": len(rows_clean)}
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('admin_labor_cost_repository:486')


def _read_company_role_rate_map(company_profile_id: int) -> Dict[str, Dict[str, Any]]:
    rows = list_company_role_rates(company_profile_id)
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        code = str(r.get('role_code') or '').strip().upper()
        if code == 'INTERINALE':
            code = 'INTERMITTENTE'
        if not code:
            continue
        out[code] = r
    return out


def list_company_profiles() -> List[Dict[str, Any]]:
    _ensure_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
SELECT p.*, 
       (SELECT COUNT(*) FROM dbo.LABOR_COST_COMPANY_EXTRA e WHERE e.company_profile_id=p.id) AS extras_count
FROM dbo.LABOR_COST_COMPANY_PROFILE p
ORDER BY p.is_active DESC, p.name ASC
            """
        )
        rows = _dict_rows(cur)
        for r in rows:
            r["id"] = int(r.get("id") or 0)
            for k in (
                "default_hourly_rate",
                "annual_hours_full_time",
                "employer_inps_pct",
                "inail_pct",
                "tfr_pct",
                "ferie_permessi_accrual_pct",
                "tredicesima_pct",
                "quattordicesima_pct",
                "other_accruals_pct",
            ):
                r[k] = _to_float(r.get(k)) if r.get(k) is not None else None
            r["is_active"] = bool(r.get("is_active"))
            r["extras_count"] = _to_int(r.get("extras_count"))
        return rows
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('admin_labor_cost_repository:537')


def get_company_profile(profile_id: int) -> Optional[Dict[str, Any]]:
    _ensure_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM dbo.LABOR_COST_COMPANY_PROFILE WHERE id=?", [int(profile_id)])
        rows = _dict_rows(cur)
        if not rows:
            return None
        p = rows[0]
        p["id"] = int(p.get("id") or 0)
        p["is_active"] = bool(p.get("is_active"))
        for k in (
            "default_hourly_rate",
            "annual_hours_full_time",
            "employer_inps_pct",
            "inail_pct",
            "tfr_pct",
            "ferie_permessi_accrual_pct",
            "tredicesima_pct",
            "quattordicesima_pct",
            "other_accruals_pct",
        ):
            p[k] = _to_float(p.get(k)) if p.get(k) is not None else None
        return p
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('admin_labor_cost_repository:569')


def save_company_profile(payload: Dict[str, Any]) -> Dict[str, Any]:
    _ensure_tables()
    profile_id = _to_int(payload.get("id") or 0)
    code = str(payload.get("code") or "").strip().upper()
    name = str(payload.get("name") or "").strip()
    if not code or not name:
        raise ValueError("Campi obbligatori: code, name")

    vals = {
        "code": code,
        "name": name,
        "sector": (str(payload.get("sector") or "").strip() or None),
        "default_hourly_rate": _to_float(payload.get("default_hourly_rate")) if str(payload.get("default_hourly_rate") or "").strip() else None,
        "annual_hours_full_time": _to_float(payload.get("annual_hours_full_time") or 2076),
        "employer_inps_pct": _to_float(payload.get("employer_inps_pct") or 0.30),
        "inail_pct": _to_float(payload.get("inail_pct") or 0.02),
        "tfr_pct": _to_float(payload.get("tfr_pct") or 0.0741),
        "ferie_permessi_accrual_pct": _to_float(payload.get("ferie_permessi_accrual_pct") or 0.12),
        "tredicesima_pct": _to_float(payload.get("tredicesima_pct") or 0.0833),
        "quattordicesima_pct": _to_float(payload.get("quattordicesima_pct") or 0.0),
        "other_accruals_pct": _to_float(payload.get("other_accruals_pct") or 0.0),
        "is_active": 1 if _to_bool(payload.get("is_active"), True) else 0,
        "notes": (str(payload.get("notes") or "").strip() or None),
    }
    role_hint = _normalize_staff_profile_role(vals.get("inquadramento_override")) or _normalize_staff_profile_role(vals.get("contract_type"))
    if role_hint in {"STAGE", "INTERMITTENTE"}:
        cmo_fixed = _to_float(vals.get("stage_fixed_hourly_cost"))
        if cmo_fixed <= 0:
            raise ValueError(f"CMO fisso obbligatorio per {ROLE_PROFILE_LABELS.get(role_hint, role_hint.title())} (anagrafica costo dipendenti)")

    conn = get_connection()
    try:
        cur = conn.cursor()
        if profile_id > 0:
            cur.execute(
                """
UPDATE dbo.LABOR_COST_COMPANY_PROFILE
SET code=?, name=?, sector=?, default_hourly_rate=?, annual_hours_full_time=?,
    employer_inps_pct=?, inail_pct=?, tfr_pct=?, ferie_permessi_accrual_pct=?,
    tredicesima_pct=?, quattordicesima_pct=?, other_accruals_pct=?,
    is_active=?, notes=?, updated_at=SYSDATETIME()
WHERE id=?
                """,
                [
                    vals["code"], vals["name"], vals["sector"], vals["default_hourly_rate"], vals["annual_hours_full_time"],
                    vals["employer_inps_pct"], vals["inail_pct"], vals["tfr_pct"], vals["ferie_permessi_accrual_pct"],
                    vals["tredicesima_pct"], vals["quattordicesima_pct"], vals["other_accruals_pct"],
                    vals["is_active"], vals["notes"], profile_id,
                ],
            )
            if (cur.rowcount or 0) <= 0:
                raise ValueError("Profilo non trovato")
            pid = profile_id
        else:
            cur.execute(
                """
INSERT INTO dbo.LABOR_COST_COMPANY_PROFILE (
    code,name,sector,default_hourly_rate,annual_hours_full_time,
    employer_inps_pct,inail_pct,tfr_pct,ferie_permessi_accrual_pct,
    tredicesima_pct,quattordicesima_pct,other_accruals_pct,is_active,notes
) OUTPUT INSERTED.id VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    vals["code"], vals["name"], vals["sector"], vals["default_hourly_rate"], vals["annual_hours_full_time"],
                    vals["employer_inps_pct"], vals["inail_pct"], vals["tfr_pct"], vals["ferie_permessi_accrual_pct"],
                    vals["tredicesima_pct"], vals["quattordicesima_pct"], vals["other_accruals_pct"], vals["is_active"], vals["notes"],
                ],
            )
            pid = int(cur.fetchone()[0])
            # Seed extras
            _ensure_company_role_rate_defaults(cur, pid, vals['employer_inps_pct'], vals['inail_pct'])
            for code_x, label, pct, sort_order, applies_to in DEFAULT_EXTRAS:
                cur.execute(
                    """
IF NOT EXISTS (SELECT 1 FROM dbo.LABOR_COST_COMPANY_EXTRA WHERE company_profile_id=? AND extra_code=?)
INSERT INTO dbo.LABOR_COST_COMPANY_EXTRA(company_profile_id,extra_code,label,pct,sort_order,applies_to,is_active)
VALUES (?,?,?,?,?,?,1)
                    """,
                    [pid, code_x, pid, code_x, label, float(pct), int(sort_order), applies_to],
                )
        role_rates_payload = payload.get('role_rates')
        _ensure_company_role_rate_defaults(cur, pid, vals['employer_inps_pct'], vals['inail_pct'])
        if isinstance(role_rates_payload, list):
            cur.execute("DELETE FROM dbo.LABOR_COST_COMPANY_ROLE_RATES WHERE company_profile_id=?", [pid])
            for i, rr in enumerate(role_rates_payload):
                code_rr = str((rr or {}).get('role_code') or '').strip().upper()
                if not code_rr:
                    continue
                label_rr = str((rr or {}).get('role_label') or ROLE_PROFILE_LABELS.get(code_rr) or code_rr).strip()
                inps_rr = _to_float((rr or {}).get('employer_inps_pct')) if str((rr or {}).get('employer_inps_pct') or '').strip() != '' else None
                inail_rr = _to_float((rr or {}).get('inail_pct')) if str((rr or {}).get('inail_pct') or '').strip() != '' else None
                sort_rr = _to_int((rr or {}).get('sort_order'), (i+1)*10)
                active_rr = 1 if _to_bool((rr or {}).get('is_active'), True) else 0
                cur.execute(
                    """
INSERT INTO dbo.LABOR_COST_COMPANY_ROLE_RATES(company_profile_id, staff_role_code, staff_role_label, employer_inps_pct, inail_pct, calc_mode, fixed_hourly_cost, sort_order, is_active)
VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    [
                        pid,
                        code_rr,
                        label_rr,
                        inps_rr,
                        inail_rr,
                        (str((rr or {}).get('calc_mode') or ('FIXED_ALL_IN' if code_rr in {'STAGE', 'INTERMITTENTE'} else 'STANDARD')).strip().upper() or ('FIXED_ALL_IN' if code_rr in {'STAGE', 'INTERMITTENTE'} else 'STANDARD')),
                        (_to_float((rr or {}).get('fixed_hourly_cost')) if str((rr or {}).get('fixed_hourly_cost') or '').strip() != '' else None),
                        sort_rr,
                        active_rr,
                    ],
                )
            _ensure_company_role_rate_defaults(cur, pid, vals['employer_inps_pct'], vals['inail_pct'])
        conn.commit()
        return {"ok": True, "id": pid}
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('admin_labor_cost_repository:689')


def list_company_extras(company_profile_id: int) -> List[Dict[str, Any]]:
    _ensure_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM dbo.LABOR_COST_COMPANY_EXTRA WHERE company_profile_id=? ORDER BY sort_order, label",
            [int(company_profile_id)],
        )
        rows = _dict_rows(cur)
        for r in rows:
            r["id"] = _to_int(r.get("id"))
            r["company_profile_id"] = _to_int(r.get("company_profile_id"))
            r["pct"] = _to_float(r.get("pct"))
            r["sort_order"] = _to_int(r.get("sort_order"))
            r["is_active"] = bool(r.get("is_active"))
        return rows
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('admin_labor_cost_repository:713')


def replace_company_extras(company_profile_id: int, extras: List[Dict[str, Any]]) -> Dict[str, Any]:
    _ensure_tables()
    pid = int(company_profile_id)
    clean: List[Tuple[str, str, float, int, Optional[str], int]] = []
    for i, e in enumerate(extras or []):
        code_x = str((e or {}).get("extra_code") or "").strip().upper()
        label = str((e or {}).get("label") or "").strip()
        if not code_x:
            continue
        if not label:
            label = code_x.title()
        pct = _to_float((e or {}).get("pct"), 0.0)
        sort_order = _to_int((e or {}).get("sort_order"), i * 10)
        applies_to = str((e or {}).get("applies_to") or "").strip() or None
        is_active = 1 if _to_bool((e or {}).get("is_active"), True) else 0
        clean.append((code_x, label, pct, sort_order, applies_to, is_active))

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM dbo.LABOR_COST_COMPANY_EXTRA WHERE company_profile_id=?", [pid])
        for code_x, label, pct, sort_order, applies_to, is_active in clean:
            cur.execute(
                """
INSERT INTO dbo.LABOR_COST_COMPANY_EXTRA(company_profile_id,extra_code,label,pct,sort_order,applies_to,is_active)
VALUES (?,?,?,?,?,?,?)
                """,
                [pid, code_x, label, pct, sort_order, applies_to, is_active],
            )
        conn.commit()
        return {"ok": True, "saved": len(clean)}
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('admin_labor_cost_repository:751')


# ----------------------------
# Employee config
# ----------------------------

EMPLOYEE_IMPORT_MAP = {
    "employee_code": ["CODICE_DIPENDENTE", "CODICE", "MATRICOLA", "ID_DIPENDENTE"],
    "employee_name": ["NOME", "NOME_COGNOME", "DIPENDENTE", "NOMINATIVO"],
    "store_code": ["STORE_CODE", "SITE", "NEGOZIO", "STORE"],
    "contract_type": ["TIPO_CONTRATTO", "CONTRATTO_TIPO", "CONTRATTO"],
    "ral": ["RAL", "RAL_ANNUA"],
    "hourly_rate_override": ["COSTO_ORARIO", "HOURLY_RATE", "PAGA_ORARIA"],
    "inquadramento_override": ["INQUADRAMENTO", "LIVELLO"],
    "company_profile_id": ["COMPANY_PROFILE_ID", "PROFILO_AZIENDA_ID"],
    "employer_inps_pct_override": ["INPS_AZIENDA_PCT", "INPS_PCT", "INPS_OVERRIDE"],
    "inail_pct_override": ["INAIL_PCT", "INAIL_OVERRIDE"],
    "stage_fixed_hourly_cost": ["STAGE_COSTO_ORARIO", "STAGE_HOURLY_COST", "STAGE_CMO_FISSO"],
    "source_note": ["NOTE", "SOURCE_NOTE"],
}


def _pick_csv_column(headers: List[str], aliases: Iterable[str]) -> Optional[str]:
    hmap = {_norm_key(h): h for h in headers}
    for a in aliases:
        if _norm_key(a) in hmap:
            return hmap[_norm_key(a)]
    return None


def list_employee_configs(*, store_code: Optional[str] = None, only_active: bool = False, limit: int = 300) -> List[Dict[str, Any]]:
    _ensure_tables()
    limit = max(1, min(int(limit or 300), 1000))
    store_code = str(store_code or "").strip() or None
    conn = get_connection()
    try:
        cur = conn.cursor()
        where_parts: List[str] = []
        params: List[Any] = []
        if store_code:
            # Include both specific store rows and "shared" rows without store for fallback/default usage.
            where_parts.append("(ISNULL(c.store_code,'') = ? OR ISNULL(c.store_code,'')='')")
            params.append(store_code)
        if only_active:
            where_parts.append("c.is_active = 1")
        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        sql = f"""
SELECT TOP ({limit}) c.*, p.name AS company_profile_name
FROM dbo.LABOR_COST_EMPLOYEE_CONFIG c
LEFT JOIN dbo.LABOR_COST_COMPANY_PROFILE p ON p.id = c.company_profile_id
{where_sql}
ORDER BY c.employee_code ASC
        """
        cur.execute(sql, params)
        rows = _dict_rows(cur)
        for r in rows:
            r["id"] = _to_int(r.get("id"))
            r["company_profile_id"] = _to_int(r.get("company_profile_id")) if r.get("company_profile_id") is not None else None
            r["is_active"] = bool(r.get("is_active"))
            r["employee_code"] = str(r.get("employee_code") or "").strip()
            r["employee_name"] = str(r.get("employee_name") or "").strip()
            r["store_code"] = str(r.get("store_code") or "").strip()
            r["row_origin"] = "config"
            for k in (
                "ral",
                "hourly_rate_override",
                "stage_fixed_hourly_cost",
                "employer_inps_pct_override",
                "inail_pct_override",
                "tfr_pct_override",
            ):
                r[k] = _to_float(r.get(k)) if r.get(k) is not None else None

        # If a store is selected, the UI should start from the Orari anagrafica (STAFF) and then merge any
        # existing cost-config values, so missing employees are visible/editable immediately.
        if store_code:
            try:
                staff_rows = list_staff(store_code=store_code, only_active=False) or []
            except Exception:
                staff_rows = []

            if staff_rows:
                def _norm_code(v: Any) -> str:
                    return str(v or "").strip().upper()

                def _norm_name(v: Any) -> str:
                    return _norm_key(str(v or ""))

                cfg_exact_by_code: Dict[str, Dict[str, Any]] = {}
                cfg_shared_by_code: Dict[str, Dict[str, Any]] = {}
                cfg_by_name_same_store: Dict[str, Dict[str, Any]] = {}

                for r in rows:
                    code_key = _norm_code(r.get("employee_code"))
                    store_r = str(r.get("store_code") or "").strip()
                    if code_key:
                        if store_r == store_code:
                            cfg_exact_by_code[code_key] = r
                        elif not store_r:
                            cfg_shared_by_code.setdefault(code_key, r)
                    if store_r == store_code and r.get("employee_name"):
                        cfg_by_name_same_store.setdefault(_norm_name(r.get("employee_name")), r)

                merged_rows: List[Dict[str, Any]] = []
                used_cfg_ids: set[int] = set()

                for s in staff_rows:
                    staff_name = str((s or {}).get("nome_cognome") or "").strip()
                    staff_code = str((s or {}).get("codice_dipendente") or "").strip()
                    code_key = _norm_code(staff_code)
                    cfg = None
                    if code_key:
                        cfg = cfg_exact_by_code.get(code_key) or cfg_shared_by_code.get(code_key)
                    if not cfg and staff_name:
                        cfg = cfg_by_name_same_store.get(_norm_name(staff_name))

                    base: Dict[str, Any] = dict(cfg or {})
                    if cfg and cfg.get("id"):
                        try:
                            used_cfg_ids.add(int(cfg.get("id")))
                        except Exception:
                            log_swallowed('admin_labor_cost_repository:873')

                    # Fill missing anagrafica fields from Orari (do not overwrite explicit config values)
                    base["employee_code"] = (base.get("employee_code") or "").strip() or staff_code
                    base["employee_name"] = (base.get("employee_name") or "").strip() or staff_name
                    base["store_code"] = (base.get("store_code") or "").strip() or store_code
                    base["row_origin"] = ("orari+config" if cfg else "orari_only")
                    base["staff_nome_cognome"] = staff_name
                    base["staff_codice_dipendente"] = staff_code
                    base["staff_ruolo"] = str((s or {}).get("ruolo") or "").strip()
                    base["staff_ore_contrattuali"] = _to_float((s or {}).get("ore_contrattuali")) if (s or {}).get("ore_contrattuali") is not None else None
                    base["staff_attivo"] = bool((s or {}).get("attivo")) if (s or {}).get("attivo") is not None else True
                    base["is_active"] = bool(base.get("is_active")) if "is_active" in base else True
                    # Ensure keys expected by UI always exist
                    base.setdefault("id", None)
                    base.setdefault("company_profile_id", None)
                    base.setdefault("company_profile_name", None)
                    for k in (
                        "ral",
                        "hourly_rate_override",
                        "stage_fixed_hourly_cost",
                        "employer_inps_pct_override",
                        "inail_pct_override",
                        "tfr_pct_override",
                    ):
                        base.setdefault(k, None)
                    merged_rows.append(base)

                # Keep config-only rows (e.g. imports not yet present in local STAFF)
                for r in rows:
                    rid = _to_int(r.get("id"))
                    if rid and rid in used_cfg_ids:
                        continue
                    code_key = _norm_code(r.get("employee_code"))
                    if code_key and code_key in cfg_exact_by_code and cfg_exact_by_code.get(code_key) is r:
                        if rid in used_cfg_ids:
                            continue
                    if r.get("row_origin") != "config":
                        r["row_origin"] = "config_only"
                    else:
                        r["row_origin"] = "config_only"
                    merged_rows.append(r)

                rows = merged_rows

        def _sort_key(r: Dict[str, Any]):
            return (
                str(r.get("store_code") or ""),
                str(r.get("employee_name") or r.get("staff_nome_cognome") or "").lower(),
                str(r.get("employee_code") or "").upper(),
            )

        rows = sorted(rows, key=_sort_key)
        return rows[:limit]
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('admin_labor_cost_repository:931')
def upsert_employee_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    _ensure_tables()
    employee_code = str(payload.get("employee_code") or "").strip()
    if not employee_code:
        raise ValueError("employee_code obbligatorio")
    store_code = str(payload.get("store_code") or "").strip()
    profile_id_raw = payload.get("company_profile_id")
    profile_id = _to_int(profile_id_raw) if str(profile_id_raw or "").strip() else None
    vals = {
        "employee_code": employee_code,
        "employee_name": str(payload.get("employee_name") or "").strip() or None,
        "store_code": store_code or None,
        "company_profile_id": profile_id,
        "contract_type": str(payload.get("contract_type") or "").strip() or None,
        "ral": _to_float(payload.get("ral")) if str(payload.get("ral") or "").strip() else None,
        "hourly_rate_override": _to_float(payload.get("hourly_rate_override")) if str(payload.get("hourly_rate_override") or "").strip() else None,
        "stage_fixed_hourly_cost": _to_float(payload.get("stage_fixed_hourly_cost")) if str(payload.get("stage_fixed_hourly_cost") or "").strip() else None,
        "inquadramento_override": str(payload.get("inquadramento_override") or "").strip() or None,
        "employer_inps_pct_override": _to_float(payload.get("employer_inps_pct_override")) if str(payload.get("employer_inps_pct_override") or "").strip() else None,
        "inail_pct_override": _to_float(payload.get("inail_pct_override")) if str(payload.get("inail_pct_override") or "").strip() else None,
        "tfr_pct_override": _to_float(payload.get("tfr_pct_override")) if str(payload.get("tfr_pct_override") or "").strip() else None,
        "hire_date": _parse_date(payload.get("hire_date")),
        "termination_date": _parse_date(payload.get("termination_date")),
        "is_active": 1 if _to_bool(payload.get("is_active"), True) else 0,
        "source_type": str(payload.get("source_type") or "manual").strip() or None,
        "source_note": str(payload.get("source_note") or "").strip() or None,
    }
    role_hint = _normalize_staff_profile_role(vals.get("inquadramento_override")) or _normalize_staff_profile_role(vals.get("contract_type"))
    if role_hint in {"STAGE", "INTERMITTENTE"}:
        cmo_fixed = _to_float(vals.get("stage_fixed_hourly_cost"))
        if cmo_fixed <= 0:
            raise ValueError(f"CMO fisso obbligatorio per {ROLE_PROFILE_LABELS.get(role_hint, role_hint.title())} (anagrafica costo dipendenti)")

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
MERGE dbo.LABOR_COST_EMPLOYEE_CONFIG AS tgt
USING (SELECT ? AS employee_code, ? AS store_code) AS src
ON ISNULL(tgt.employee_code,'') = ISNULL(src.employee_code,'')
   AND ISNULL(tgt.store_code,'') = ISNULL(src.store_code,'')
WHEN MATCHED THEN UPDATE SET
    employee_name=?, company_profile_id=?, contract_type=?, ral=?, hourly_rate_override=?, stage_fixed_hourly_cost=?,
    inquadramento_override=?, employer_inps_pct_override=?, inail_pct_override=?, tfr_pct_override=?,
    hire_date=?, termination_date=?, is_active=?, source_type=?, source_note=?, updated_at=SYSDATETIME()
WHEN NOT MATCHED THEN INSERT (
    employee_code, employee_name, store_code, company_profile_id, contract_type, ral, hourly_rate_override, stage_fixed_hourly_cost,
    inquadramento_override, employer_inps_pct_override, inail_pct_override, tfr_pct_override,
    hire_date, termination_date, is_active, source_type, source_note
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
OUTPUT inserted.id;
            """,
            [
                vals["employee_code"], vals["store_code"] or "",
                vals["employee_name"], vals["company_profile_id"], vals["contract_type"], vals["ral"], vals["hourly_rate_override"], vals["stage_fixed_hourly_cost"],
                vals["inquadramento_override"], vals["employer_inps_pct_override"], vals["inail_pct_override"], vals["tfr_pct_override"],
                vals["hire_date"], vals["termination_date"], vals["is_active"], vals["source_type"], vals["source_note"],
                vals["employee_code"], vals["employee_name"], vals["store_code"], vals["company_profile_id"], vals["contract_type"], vals["ral"], vals["hourly_rate_override"], vals["stage_fixed_hourly_cost"],
                vals["inquadramento_override"], vals["employer_inps_pct_override"], vals["inail_pct_override"], vals["tfr_pct_override"],
                vals["hire_date"], vals["termination_date"], vals["is_active"], vals["source_type"], vals["source_note"],
            ],
        )
        row = cur.fetchone()
        conn.commit()
        return {"ok": True, "id": _to_int(row[0] if row else 0)}
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('admin_labor_cost_repository:1002')


def import_employee_configs_csv(content_bytes: bytes, *, default_store_code: str | None = None, default_company_profile_id: int | None = None) -> Dict[str, Any]:
    _ensure_tables()
    raw = content_bytes.decode("utf-8-sig", errors="ignore")
    sample = raw[:5000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;")
        delim = dialect.delimiter
    except Exception:
        delim = ";" if sample.count(";") >= sample.count(",") else ","

    rdr = csv.DictReader(io.StringIO(raw), delimiter=delim)
    headers = rdr.fieldnames or []
    if not headers:
        raise ValueError("CSV vuoto o intestazioni non rilevate")

    col_map: Dict[str, Optional[str]] = {}
    for target, aliases in EMPLOYEE_IMPORT_MAP.items():
        col_map[target] = _pick_csv_column(headers, aliases)

    if not col_map.get("employee_code"):
        raise ValueError("Colonna codice dipendente non trovata. Attese es. CODICE_DIPENDENTE / CODICE / MATRICOLA")

    rows = list(rdr)
    saved = 0
    skipped = 0
    errors: List[str] = []
    for i, row in enumerate(rows, start=2):
        try:
            payload: Dict[str, Any] = {}
            for target, src_col in col_map.items():
                if src_col and src_col in row:
                    payload[target] = row.get(src_col)
            if default_store_code and not str(payload.get("store_code") or "").strip():
                payload["store_code"] = default_store_code
            if default_company_profile_id and not str(payload.get("company_profile_id") or "").strip():
                payload["company_profile_id"] = default_company_profile_id
            payload["source_type"] = "csv"
            code = str(payload.get("employee_code") or "").strip()
            if not code:
                skipped += 1
                continue
            upsert_employee_config(payload)
            saved += 1
        except Exception as e:
            skipped += 1
            if len(errors) < 20:
                errors.append(f"Riga {i}: {e}")
    return {
        "ok": True,
        "delimiter": delim,
        "headers": headers,
        "mapped_columns": col_map,
        "rows_total": len(rows),
        "saved": saved,
        "skipped": skipped,
        "errors": errors,
    }


# ----------------------------
# CMO / auxiliary lookups
# ----------------------------


def list_cmo_rates() -> List[Dict[str, Any]]:
    _require_sqlserver()
    conn = get_connection()
    try:
        cur = conn.cursor()
        # Best effort column names (CONTRATTO / VALORE as indicated by user)
        cur.execute("SELECT TOP 500 * FROM dbo.CMO ORDER BY 1")
        rows = _dict_rows(cur)
        out: List[Dict[str, Any]] = []
        for r in rows:
            keys = {str(k).upper(): k for k in r.keys()}
            contr_key = keys.get("CONTRATTO") or keys.get("CONTRACT") or next((k for k in r.keys() if _norm_key(k) == "CONTRATTO"), None)
            val_key = keys.get("VALORE") or keys.get("VALUE") or next((k for k in r.keys() if _norm_key(k) == "VALORE"), None)
            if not contr_key or not val_key:
                continue
            out.append({
                "contratto": str(r.get(contr_key) or "").strip(),
                "valore": _to_float(r.get(val_key)),
            })
        # remove empty + duplicate by normalized contract, keep first
        dedup: Dict[str, Dict[str, Any]] = {}
        for r in out:
            k = _norm_key(r.get("contratto"))
            if not k or k in dedup:
                continue
            dedup[k] = r
        return sorted(dedup.values(), key=lambda x: str(x.get("contratto") or "").lower())
    finally:
        try:
            conn.close()
        except Exception:
            log_swallowed('admin_labor_cost_repository:1100')


# ----------------------------
# Projection engine (test page)
# ----------------------------

@dataclass
class ShiftMetrics:
    worked_hours: float = 0.0
    extra_hours: float = 0.0
    ferie_hours: float = 0.0
    permessi_hours: float = 0.0
    rol_hours: float = 0.0
    prestito_hours: float = 0.0
    sunday_hours: float = 0.0
    night_hours: float = 0.0
    festive_hours: float = 0.0
    days_present: int = 0


def _hhmm_to_minutes(s: str) -> Optional[int]:
    s = str(s or "").strip()
    if not s:
        return None
    try:
        hh, mm = s.split(":", 1)
        hh_i = int(hh)
        mm_i = int(mm)
        if hh_i < 0 or hh_i > 23 or mm_i < 0 or mm_i > 59:
            return None
        return hh_i * 60 + mm_i
    except Exception:
        return None


def _interval_minutes(start_s: str, end_s: str) -> int:
    sm = _hhmm_to_minutes(start_s)
    em = _hhmm_to_minutes(end_s)
    if sm is None or em is None:
        return 0
    if em < sm:
        # overnight
        return (24 * 60 - sm) + em
    return em - sm


def _interval_night_minutes(start_s: str, end_s: str, night_start: int = 22 * 60, night_end: int = 6 * 60) -> int:
    sm = _hhmm_to_minutes(start_s)
    em = _hhmm_to_minutes(end_s)
    if sm is None or em is None:
        return 0
    # split into timeline 0..2880 for easy overlap if overnight
    if em <= sm:
        em += 24 * 60
    segments = [(sm, em)]
    # night windows in both days: [22:00,30:00) and [0,6:00)
    windows = [
        (night_start, 24 * 60),
        (0, night_end),
        (24 * 60 + night_start, 48 * 60),
        (24 * 60, 24 * 60 + night_end),
    ]
    tot = 0
    for a, b in segments:
        for x, y in windows:
            left = max(a, x)
            right = min(b, y)
            if right > left:
                tot += (right - left)
    return tot


def _italy_fixed_holidays() -> set[Tuple[int, int]]:
    return {
        (1, 1), (1, 6), (4, 25), (5, 1), (6, 2), (8, 15),
        (11, 1), (12, 8), (12, 25), (12, 26),
    }


def _is_fixed_holiday(d: date) -> bool:
    return (d.month, d.day) in _italy_fixed_holidays()


def _read_profile_extras(pid: int) -> Dict[str, float]:
    extras = list_company_extras(pid)
    out: Dict[str, float] = {}
    for e in extras:
        if not e.get("is_active", True):
            continue
        out[str(e.get("extra_code") or "").strip().upper()] = _to_float(e.get("pct"), 0.0)
    return out


def _read_employee_configs_map(store_code: str) -> Dict[str, Dict[str, Any]]:
    rows = list_employee_configs(store_code=store_code, only_active=False, limit=5000)
    exact: Dict[str, Dict[str, Any]] = {}
    generic: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        code = str(r.get("employee_code") or "").strip()
        if not code:
            continue
        sk = str(r.get("store_code") or "").strip()
        if sk:
            exact[f"{sk}::{code}"] = r
        else:
            generic[code] = r
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in generic.items():
        out[k] = v
    for k, v in exact.items():
        _, code = k.split("::", 1)
        out[code] = v  # store-specific overrides generic
    return out


def _build_cmo_map() -> Dict[str, float]:
    rows = list_cmo_rates()
    return {_norm_key(r.get("contratto")): _to_float(r.get("valore")) for r in rows if str(r.get("contratto") or "").strip()}


def _calc_row_metrics(row: Dict[str, Any]) -> Tuple[float, float]:
    mins = (
        _interval_minutes(row.get("inizio_1") or "", row.get("fine_1") or "")
        + _interval_minutes(row.get("inizio_2") or "", row.get("fine_2") or "")
    )
    night_mins = (
        _interval_night_minutes(row.get("inizio_1") or "", row.get("fine_1") or "")
        + _interval_night_minutes(row.get("inizio_2") or "", row.get("fine_2") or "")
    )
    return (mins / 60.0, night_mins / 60.0)


def _extract_shift_metrics(rows: List[Dict[str, Any]]) -> ShiftMetrics:
    m = ShiftMetrics()
    seen_days: set[str] = set()
    for r in rows or []:
        d_s = str((r or {}).get("data") or "").strip()
        try:
            d = date.fromisoformat(d_s)
        except Exception:
            d = None
        c1 = str((r or {}).get("causale") or "").strip().upper()
        c2 = str((r or {}).get("causale2") or "").strip().upper()
        sp1 = str((r or {}).get("s_prestito") or "").strip()
        sp2 = str((r or {}).get("s_prestito2") or "").strip()
        row_hours, row_night_hours = _calc_row_metrics(r)

        caus_combo = f" {c1} | {c2} "
        is_extra = ("EXTRA" in c1) or ("EXTRA" in c2)
        is_ferie = ("FERIE" in c1) or ("FERIE" in c2)
        is_perm = ("PERMESS" in c1) or ("PERMESS" in c2) or ("ALLATT" in c1) or ("ALLATT" in c2)
        is_rol = (c1 == "ROL") or (c2 == "ROL") or (" ROL " in caus_combo)
        is_prest = ("PRESTITO" in c1) or ("PRESTITO" in c2) or bool(sp1) or bool(sp2)
        is_non_cost_accrual_use = is_ferie or is_perm or is_rol

        if row_hours > 0:
            if not is_non_cost_accrual_use:
                m.worked_hours += row_hours
                if is_extra:
                    m.extra_hours += row_hours
                if is_prest:
                    m.prestito_hours += row_hours
                if d is not None:
                    if d.weekday() == 6:  # Sunday
                        m.sunday_hours += row_hours
                    if _is_fixed_holiday(d):
                        m.festive_hours += row_hours
                m.night_hours += max(0.0, row_night_hours)
            if d_s:
                seen_days.add(d_s)
        else:
            if d_s and (c1 or c2):
                seen_days.add(d_s)

        if is_ferie:
            m.ferie_hours += row_hours
        if is_perm:
            m.permessi_hours += row_hours
        if is_rol:
            m.rol_hours += row_hours

    m.days_present = len(seen_days)
    m.extra_hours = min(m.extra_hours, m.worked_hours)
    m.prestito_hours = min(m.prestito_hours, m.worked_hours)
    m.sunday_hours = min(m.sunday_hours, m.worked_hours)
    m.festive_hours = min(m.festive_hours, m.worked_hours)
    m.night_hours = min(m.night_hours, m.worked_hours)
    return m


def projection_test(*, store_code: str, week_start: date | str, company_profile_id: Optional[int] = None, revenues_actual: Optional[float] = None) -> Dict[str, Any]:
    _ensure_tables()
    store_code = str(store_code or "").strip()
    if not store_code:
        raise ValueError("store_code obbligatorio")
    if isinstance(week_start, str):
        d = _parse_date(week_start)
        if d is None:
            raise ValueError("week_start non valido")
        week0 = d
    else:
        week0 = week_start
    week0 = week0 - timedelta(days=week0.weekday())
    week1 = week0 + timedelta(days=6)

    profile = get_company_profile(int(company_profile_id)) if company_profile_id else None
    if profile is None:
        profiles = [p for p in list_company_profiles() if p.get("is_active")]
        if not profiles:
            raise RuntimeError("Nessun profilo azienda configurato")
        profile = profiles[0]
    pid = int(profile["id"])
    extras_pct = _read_profile_extras(pid)
    role_rate_map = _read_company_role_rate_map(pid)

    cmo_map = _build_cmo_map()  # solo riferimento setup/debug, non usato nel calcolo test
    staff = list_staff(store_code=store_code, only_active=True)
    shifts = list_turni_week(store_code=store_code, start_day=week0, end_day=week1, nominativi=None)
    emp_cfg_map = _read_employee_configs_map(store_code)

    # Group shifts by nominativo
    by_name: Dict[str, List[Dict[str, Any]]] = {}
    for r in shifts or []:
        nom = str((r or {}).get("nominativo") or "").strip()
        if not nom:
            continue
        by_name.setdefault(nom, []).append(r)

    # Staff map by name/code
    staff_by_name = {str(s.get("nome_cognome") or "").strip(): s for s in (staff or [])}

    lines: List[Dict[str, Any]] = []
    warnings: List[str] = []
    unmatched_shift_names: List[str] = []

    def pctv(code: str, default: float = 0.0) -> float:
        return _to_float(extras_pct.get(code.upper()), default)

    for nom, rows in sorted(by_name.items(), key=lambda kv: kv[0].lower()):
        srow = staff_by_name.get(nom)
        codice = str((srow or {}).get("codice_dipendente") or "").strip()
        ore_contr = _to_float((srow or {}).get("ore_contrattuali"))
        ruolo = str((srow or {}).get("ruolo") or "").strip()

        if not srow:
            unmatched_shift_names.append(nom)

        emp_cfg = emp_cfg_map.get(codice) if codice else None
        if not emp_cfg and codice:
            warnings.append(f"{nom} ({codice}): configurazione costo dipendente non trovata")

        metrics = _extract_shift_metrics(rows)
        if metrics.worked_hours <= 0 and metrics.ferie_hours <= 0 and metrics.permessi_hours <= 0 and metrics.rol_hours <= 0:
            continue

        # Determine inquadramento (priority: employee override, row inquadramento prevalente, ruolo)
        inq_override = str((emp_cfg or {}).get("inquadramento_override") or "").strip()
        inq_counts: Dict[str, int] = {}
        for r in rows:
            inq = str((r or {}).get("inquadramento") or "").strip()
            if inq:
                inq_counts[inq] = inq_counts.get(inq, 0) + 1
        inq_schedule = sorted(inq_counts.items(), key=lambda x: (-x[1], x[0].lower()))[0][0] if inq_counts else ""
        inquadramento = inq_override or inq_schedule or ruolo

        cmo_hourly = cmo_map.get(_norm_key(inquadramento)) if inquadramento else None
        ral = _to_float((emp_cfg or {}).get("ral")) if emp_cfg else 0.0
        hourly_override = _to_float((emp_cfg or {}).get("hourly_rate_override")) if emp_cfg and (emp_cfg.get("hourly_rate_override") is not None) else 0.0
        stage_fixed_hourly = _to_float((emp_cfg or {}).get("stage_fixed_hourly_cost")) if emp_cfg and (emp_cfg.get("stage_fixed_hourly_cost") is not None) else 0.0
        annual_hours = _to_float(profile.get("annual_hours_full_time") or 2076.0, 2076.0)
        derived_from_ral = (ral / annual_hours) if (ral > 0 and annual_hours > 0) else None
        role_bucket = _normalize_staff_profile_role(ruolo) or _normalize_staff_profile_role(inq_schedule) or _normalize_staff_profile_role((emp_cfg or {}).get('contract_type'))
        is_stage = role_bucket == "STAGE"
        is_interinale = role_bucket in {"INTERMITTENTE", "INTERINALE"}
        is_fixed_cmo_role = is_stage or is_interinale

        base_hourly = None
        base_source = None
        if is_fixed_cmo_role and stage_fixed_hourly > 0:
            base_hourly = stage_fixed_hourly
            base_source = "stage_fisso_dipendente" if is_stage else "intermittente_fisso_dipendente"
        elif not is_fixed_cmo_role and hourly_override > 0:
            base_hourly = hourly_override
            base_source = "override_dipendente"
        elif not is_fixed_cmo_role and derived_from_ral and derived_from_ral > 0:
            base_hourly = float(derived_from_ral)
            base_source = "ral/ore_annue"
        elif not is_fixed_cmo_role:
            default_hr = profile.get("default_hourly_rate")
            if default_hr is not None and _to_float(default_hr) > 0:
                base_hourly = _to_float(default_hr)
                base_source = "profilo_default"

        if is_fixed_cmo_role and stage_fixed_hourly <= 0:
            role_label = ROLE_PROFILE_LABELS.get(role_bucket, role_bucket or 'dipendente')
            raise ValueError(f"{nom}{' ('+codice+')' if codice else ''}: CMO fisso obbligatorio per {role_label} in anagrafica costo dipendenti")

        if not base_hourly:
            base_hourly = 0.0
            base_source = "missing"
            if is_fixed_cmo_role:
                warnings.append(f"{nom}{' ('+codice+')' if codice else ''}: costo orario non determinato (CMO fisso obbligatorio mancante)")
            else:
                warnings.append(f"{nom}{' ('+codice+')' if codice else ''}: costo orario non determinato (override/RAL/profilo mancanti)")

        # Employer percentages / accruals (employee override > quota ruolo profilo azienda > default profilo)
        rr = role_rate_map.get(role_bucket or "") if role_bucket else None
        inps_pct = (
            _to_float((emp_cfg or {}).get("employer_inps_pct_override"))
            if emp_cfg and emp_cfg.get("employer_inps_pct_override") is not None
            else (_to_float(rr.get('employer_inps_pct')) if rr and rr.get('employer_inps_pct') is not None else _to_float(profile.get("employer_inps_pct"), 0.30))
        )
        inail_pct = (
            _to_float((emp_cfg or {}).get("inail_pct_override"))
            if emp_cfg and emp_cfg.get("inail_pct_override") is not None
            else (_to_float(rr.get('inail_pct')) if rr and rr.get('inail_pct') is not None else _to_float(profile.get("inail_pct"), 0.02))
        )
        tfr_pct = _to_float((emp_cfg or {}).get("tfr_pct_override")) if emp_cfg and emp_cfg.get("tfr_pct_override") is not None else _to_float(profile.get("tfr_pct"), 0.0741)
        ferie_perm_acc_pct = _to_float(profile.get("ferie_permessi_accrual_pct"), 0.12)
        mens13_pct = _to_float(profile.get("tredicesima_pct"), 0.0833)
        mens14_pct = _to_float(profile.get("quattordicesima_pct"), 0.0)
        other_acc_pct = _to_float(profile.get("other_accruals_pct"), 0.0)

        # Direct compensation estimate from hours from schedules (ferie/permessi covered through accruals)
        worked_regular_hours = max(0.0, metrics.worked_hours)
        if is_stage:
            # Stage: usa le ore pianificate negli orari (simulazione test, costo fisso all-in)
            cost_hours_used = worked_regular_hours
            cost_hours_policy = "planned_hours"
        elif is_interinale:
            # Intermittente: usa le ore effettive lavorate registrate negli orari (costo fisso all-in)
            cost_hours_used = worked_regular_hours
            cost_hours_policy = "worked_hours"
        else:
            cost_hours_used = worked_regular_hours
            cost_hours_policy = "worked_hours"

        direct_base = cost_hours_used * base_hourly
        if is_fixed_cmo_role:
            prem_straord = 0.0
            prem_domen = 0.0
            prem_notte = 0.0
            prem_fest = 0.0
            direct_comp = direct_base
        else:
            prem_straord = metrics.extra_hours * base_hourly * pctv("STRAORDINARIA", 0.15)
            prem_domen = metrics.sunday_hours * base_hourly * pctv("DOMENICALE", 0.30)
            prem_notte = metrics.night_hours * base_hourly * pctv("NOTTURNA", 0.25)
            # Avoid double-counting Sunday and fixed holiday on same day? Keep both configurable and transparent.
            prem_fest = metrics.festive_hours * base_hourly * pctv("FESTIVA", 0.30)
            direct_comp = direct_base + prem_straord + prem_domen + prem_notte + prem_fest

        if is_fixed_cmo_role:
            # Stage/Intermittente: costo orario fisso all-in (nessun accantonamento/onere aziendale in questa simulazione test)
            acc_tfr = 0.0
            acc_ferie_perm = 0.0
            acc_13 = 0.0
            acc_14 = 0.0
            acc_other = 0.0
            oneri_inps = 0.0
            oneri_inail = 0.0
        else:
            acc_tfr = direct_comp * tfr_pct
            acc_ferie_perm = direct_comp * ferie_perm_acc_pct
            acc_13 = direct_comp * mens13_pct
            acc_14 = direct_comp * mens14_pct
            acc_other = direct_comp * other_acc_pct
            oneri_inps = direct_comp * inps_pct
            oneri_inail = direct_comp * inail_pct

        total_gross = direct_comp + acc_tfr + acc_ferie_perm + acc_13 + acc_14 + acc_other + oneri_inps + oneri_inail

        full_cost_per_worked_hour = (total_gross / worked_regular_hours) if worked_regular_hours > 0 else 0.0
        prestito_storno = metrics.prestito_hours * full_cost_per_worked_hour
        total_net = total_gross - prestito_storno

        line = {
            "employee_name": nom,
            "employee_code": codice,
            "ruolo": ruolo,
            "inquadramento": inquadramento,
            "contract_type": (ROLE_PROFILE_LABELS.get(role_bucket) if role_bucket else ((emp_cfg or {}).get("contract_type") if emp_cfg else None)),
            "role_bucket": role_bucket,
            "ore_contrattuali": ore_contr,
            "hours_worked": round(metrics.worked_hours, 2),
            "cost_hours_used": round(cost_hours_used, 2),
            "cost_hours_policy": cost_hours_policy,
            "hours_extra": round(metrics.extra_hours, 2),
            "hours_ferie": round(metrics.ferie_hours, 2),
            "hours_permessi": round(metrics.permessi_hours, 2),
            "hours_rol": round(metrics.rol_hours, 2),
            "hours_prestito": round(metrics.prestito_hours, 2),
            "hours_sunday": round(metrics.sunday_hours, 2),
            "hours_night": round(metrics.night_hours, 2),
            "hourly_rate": round(base_hourly, 4),
            "hourly_rate_source": base_source,
            "cmo_hourly": round(cmo_hourly, 4) if cmo_hourly is not None else None,
            "stage_fixed_hourly_cost": round(stage_fixed_hourly, 4) if stage_fixed_hourly else None,
            "is_stage": bool(is_stage),
            "is_interinale": bool(is_interinale),
            "ral": round(ral, 2) if ral else None,
            "direct_base": round(direct_base, 2),
            "prem_straordinaria": round(prem_straord, 2),
            "prem_domenicale": round(prem_domen, 2),
            "prem_notturna": round(prem_notte, 2),
            "prem_festiva": round(prem_fest, 2),
            "direct_comp": round(direct_comp, 2),
            "oneri_inps": round(oneri_inps, 2),
            "oneri_inail": round(oneri_inail, 2),
            "acc_tfr": round(acc_tfr, 2),
            "acc_ferie_permessi": round(acc_ferie_perm, 2),
            "acc_13": round(acc_13, 2),
            "acc_14": round(acc_14, 2),
            "acc_other": round(acc_other, 2),
            "total_gross": round(total_gross, 2),
            "prestito_storno": round(prestito_storno, 2),
            "total_net": round(total_net, 2),
        }
        lines.append(line)

    if unmatched_shift_names:
        warnings.append(
            f"{len(unmatched_shift_names)} nominativi in orari non presenti in anagrafica STAFF (match per nome): "
            + ", ".join(unmatched_shift_names[:8])
            + ("…" if len(unmatched_shift_names) > 8 else "")
        )

    summary = {
        "hours_worked": round(sum(_to_float(x.get("hours_worked")) for x in lines), 2),
        "hours_extra": round(sum(_to_float(x.get("hours_extra")) for x in lines), 2),
        "hours_ferie": round(sum(_to_float(x.get("hours_ferie")) for x in lines), 2),
        "hours_permessi": round(sum(_to_float(x.get("hours_permessi")) for x in lines), 2),
        "hours_rol": round(sum(_to_float(x.get("hours_rol")) for x in lines), 2),
        "hours_prestito": round(sum(_to_float(x.get("hours_prestito")) for x in lines), 2),
        "direct_comp": round(sum(_to_float(x.get("direct_comp")) for x in lines), 2),
        "oneri_inps": round(sum(_to_float(x.get("oneri_inps")) for x in lines), 2),
        "oneri_inail": round(sum(_to_float(x.get("oneri_inail")) for x in lines), 2),
        "acc_tfr": round(sum(_to_float(x.get("acc_tfr")) for x in lines), 2),
        "acc_ferie_permessi": round(sum(_to_float(x.get("acc_ferie_permessi")) for x in lines), 2),
        "acc_13": round(sum(_to_float(x.get("acc_13")) for x in lines), 2),
        "acc_14": round(sum(_to_float(x.get("acc_14")) for x in lines), 2),
        "acc_other": round(sum(_to_float(x.get("acc_other")) for x in lines), 2),
        "total_gross": round(sum(_to_float(x.get("total_gross")) for x in lines), 2),
        "prestito_storno": round(sum(_to_float(x.get("prestito_storno")) for x in lines), 2),
        "total_net": round(sum(_to_float(x.get("total_net")) for x in lines), 2),
        "employees_count": len(lines),
    }

    rev = _to_float(revenues_actual) if revenues_actual is not None and str(revenues_actual).strip() != "" else None
    if rev and rev > 0:
        summary["revenues_actual"] = round(rev, 2)
        summary["labor_cost_pct_net"] = round((summary["total_net"] / rev) * 100.0, 2)
        summary["labor_cost_pct_gross"] = round((summary["total_gross"] / rev) * 100.0, 2)
    else:
        summary["revenues_actual"] = None
        summary["labor_cost_pct_net"] = None
        summary["labor_cost_pct_gross"] = None

    # Breakdown for charts/cards in UI
    cost_stack = [
        {"label": "Retribuzione diretta", "value": summary["direct_comp"]},
        {"label": "INPS", "value": summary["oneri_inps"]},
        {"label": "INAIL", "value": summary["oneri_inail"]},
        {"label": "TFR", "value": summary["acc_tfr"]},
        {"label": "Ferie/Permessi acc.", "value": summary["acc_ferie_permessi"]},
        {"label": "13a", "value": summary["acc_13"]},
        {"label": "14a", "value": summary["acc_14"]},
        {"label": "Altri acc.", "value": summary["acc_other"]},
        {"label": "Storno prestiti", "value": -summary["prestito_storno"]},
    ]

    return {
        "ok": True,
        "meta": {
            "store_code": store_code,
            "week_start": week0.isoformat(),
            "week_end": week1.isoformat(),
            "company_profile_id": pid,
            "company_profile_name": profile.get("name"),
            "calculation_scope": {
                "hours_source": "orari.STAFF_P / STAFF_TURNI",
                "staff_source": "STAFF (codice_dipendente per match config)",
                "cmo_source": "CMO (CONTRATTO -> INQUADRAMENTO, VALORE -> costo orario)",
                "prestito_rule": "ore con causale PRESTITO o campi S_Prestito valorizzati vengono calcolate e poi stornate pro-quota",
                "notes": "Ferie/Permessi/Allattamento senza orari non generano costo diretto, sono coperti dagli accantonamenti; festività nazionali mobili non incluse in automatico",
            },
        },
        "profile": profile,
        "extras_pct": extras_pct,
        "summary": summary,
        "cost_stack": cost_stack,
        "lines": lines,
        "warnings": warnings,
    }


# ----------------------------
# UI helper payload
# ----------------------------


def get_setup_overview() -> Dict[str, Any]:
    _ensure_tables()
    profiles = list_company_profiles()
    cmo = list_cmo_rates()
    return {
        "ok": True,
        "profiles": profiles,
        "default_extras": [
            {"extra_code": c, "label": l, "pct": p, "sort_order": s, "applies_to": a}
            for c, l, p, s, a in DEFAULT_EXTRAS
        ],
        "cmo_contracts": cmo,
        "hints": {
            "employee_import_expected_fields": list(EMPLOYEE_IMPORT_MAP.keys()),
            "match_key": "employee_code (codice dipendente)",
            "important_orari_causes": ["EXTRA", "FERIE", "PERMESSI", "ALLATTAMENTO", "ROL", "PRESTITO"],
            "test_hourly_priority": ["stage_fisso_dipendente", "override_dipendente", "ral/ore_annue", "profilo_default"],
        },
    }
