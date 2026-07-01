from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional


def parse_any_date(value: Any, default_order: str = "DMY") -> Optional[date]:
    """Parse a date from many inputs without relying on OS locale.

    Accepted inputs:
    - date / datetime objects
    - strings: YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, MM/DD/YYYY, MM-DD-YYYY

    Disambiguation rules for numeric dates like '01/12/2025':
    - if one part is > 12, it can't be a month => unambiguous
    - otherwise uses default_order ('DMY' by default, suitable for IT)
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    s = str(value).strip()
    if not s:
        return None

    # ISO datetime-like (e.g. 2025-12-21T00:00:00 or 2025-12-21 00:00:00)
    try:
        if "T" in s or ":" in s:
            s2 = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s2)
            return dt.date()
    except Exception:
        pass

    # ISO date (YYYY-MM-DD...)
    try:
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception:
        pass

    # Common separated numeric dates
    m = re.match(r"^\s*(\d{1,4})[\./\-](\d{1,2})[\./\-](\d{1,4})", s)
    if m:
        a, b, c = m.group(1), m.group(2), m.group(3)
        try:
            ia, ib, ic = int(a), int(b), int(c)
        except Exception:
            return None

        # year-first
        if len(a) == 4 and ia > 31:
            try:
                return date(ia, ib, ic)
            except Exception:
                return None

        # year-last
        if len(c) == 4 and ic > 31:
            year = ic
            p1, p2 = ia, ib

            order = (default_order or "DMY").upper()

            # heuristic: if one side > 12 it's not a month
            if p1 > 12 and p2 <= 12:
                order = "DMY"
            elif p2 > 12 and p1 <= 12:
                order = "MDY"

            if order == "MDY":
                month, day = p1, p2
            else:
                day, month = p1, p2

            try:
                return date(year, month, day)
            except Exception:
                return None

    # Fallback explicit formats
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y", "%d.%m.%Y", "%m.%d.%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except Exception:
            continue

    return None


def to_iso(value: Any, default_order: str = "DMY") -> Optional[str]:
    d = parse_any_date(value, default_order=default_order)
    return d.isoformat() if d else None


def to_ddmmyyyy(value: Any, default_order: str = "DMY") -> Optional[str]:
    d = parse_any_date(value, default_order=default_order)
    return d.strftime("%d/%m/%Y") if d else None


def to_datetime00(value: Any, default_order: str = "DMY") -> Optional[datetime]:
    d = parse_any_date(value, default_order=default_order)
    if not d:
        return None
    return datetime(d.year, d.month, d.day)
