from __future__ import annotations

import sys

from app_db import storehub_database_context
from performance_repository import ensure_tenant_performance_indexes


def main() -> int:
    db_name = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    if not db_name:
        print("Usage: python scripts/apply_tenant_performance_indexes.py <DATABASE_NAME>")
        return 1
    with storehub_database_context(db_name):
        ensure_tenant_performance_indexes()
    print(f"Performance indexes applied to {db_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
