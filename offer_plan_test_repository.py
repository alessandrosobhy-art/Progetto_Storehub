from __future__ import annotations

from app_logging import log_swallowed
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from app_db import get_connection, storehub_database_context
from listini_repository import load_admin_pricelist
from supplier_orders_repository import list_fornitori
from tenant_config_repository import get_tenant


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _num(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value or "").strip().replace("€", "").replace(" ", "")
    if not s:
        return 0.0
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return 0.0


def _parse_date(value: Any) -> date | None:
    s = str(value or "").strip()[:10]
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _fmt_qty(value: float) -> str:
    if abs(value - round(value)) < 0.00001:
        return str(int(round(value)))
    return f"{value:.3f}".rstrip("0").rstrip(".").replace(".", ",")


def _default_db_name() -> str:
    tenant = get_tenant("default") or {}
    return str(tenant.get("database_name") or "APP_STOREHUB").strip() or "APP_STOREHUB"


def ensure_offer_plan_schema() -> None:
    db_name = _default_db_name()
    with storehub_database_context(db_name):
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
IF OBJECT_ID('dbo.StoreHubOfferRecipes','U') IS NULL
BEGIN
  CREATE TABLE dbo.StoreHubOfferRecipes (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    recipe_name NVARCHAR(255) NOT NULL,
    family NVARCHAR(120) NULL,
    production_area NVARCHAR(160) NULL,
    is_active BIT NOT NULL DEFAULT 1,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE UNIQUE INDEX UX_StoreHubOfferRecipes_name ON dbo.StoreHubOfferRecipes(recipe_name);
END
IF OBJECT_ID('dbo.StoreHubOfferRecipeIngredients','U') IS NULL
BEGIN
  CREATE TABLE dbo.StoreHubOfferRecipeIngredients (
    row_uuid UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID() PRIMARY KEY,
    recipe_uuid UNIQUEIDENTIFIER NOT NULL,
    supplier NVARCHAR(255) NULL,
    material NVARCHAR(255) NOT NULL,
    unit NVARCHAR(50) NULL,
    cell NVARCHAR(80) NULL,
    qty_per_portion DECIMAL(18,6) NOT NULL DEFAULT 0,
    thaw_hours INT NOT NULL DEFAULT 0,
    note NVARCHAR(500) NULL,
    sort_order INT NOT NULL DEFAULT 0,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );
  CREATE INDEX IX_StoreHubOfferRecipeIngredients_recipe ON dbo.StoreHubOfferRecipeIngredients(recipe_uuid, sort_order, row_uuid);
END
"""
            )
            conn.commit()
        finally:
            try:
                conn.close()
            except Exception:
                log_swallowed('offer_plan_test_repository:105')


def list_default_suppliers() -> list[str]:
    db_name = _default_db_name()
    with storehub_database_context(db_name):
        rows = list_fornitori()
    suppliers = []
    seen = set()
    for row in rows:
        name = str(row.get("Fornitore") or "").strip()
        key = _norm(name)
        if name and key not in seen:
            seen.add(key)
            suppliers.append(name)
    suppliers.sort(key=str.lower)
    return suppliers


def load_default_dos_materials(limit: int = 1500) -> dict[str, Any]:
    db_name = _default_db_name()
    with storehub_database_context(db_name):
        data = load_admin_pricelist("FoodPaper", max_rows=max(limit, 1))
    rows = []
    by_supplier: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen = set()
    desc_col = data.get("desc_column") or "Descrizione"
    supplier_col = data.get("supplier_column") or "FORNITORE"
    for raw in data.get("rows") or []:
        desc = str(raw.get(desc_col) or raw.get("Descrizione") or raw.get("descrizione") or "").strip()
        if not desc:
            continue
        supplier = str(raw.get(supplier_col) or raw.get("FORNITORE") or "").strip()
        key = (_norm(supplier), _norm(desc))
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "label": f"{desc} ({supplier})" if supplier else desc,
                "descrizione": desc,
                "fornitore": supplier,
                "gruppo": str(raw.get("GRUPPO") or raw.get("Gruppo") or "").strip(),
                "codice": str(raw.get("CODICE") or raw.get("Codice") or "").strip(),
                "unita": str(raw.get("UNITA") or raw.get("Unita") or raw.get("UM") or "").strip(),
                "prezzo": raw.get("PREZZO"),
                "conv": raw.get("CONV"),
            }
        )
        by_supplier[supplier].append(rows[-1])
        if len(rows) >= limit:
            break
    rows.sort(key=lambda r: (str(r.get("descrizione") or "").lower(), str(r.get("fornitore") or "").lower()))
    by_supplier = {
        supplier: sorted(items, key=lambda r: str(r.get("descrizione") or "").lower())
        for supplier, items in sorted(by_supplier.items(), key=lambda kv: str(kv[0]).lower())
    }
    return {
        "ok": bool(data.get("ok")),
        "error": data.get("error"),
        "database": db_name,
        "price_list_name": data.get("price_list_name") or "Listino DOS",
        "rows": rows,
        "by_supplier": by_supplier,
        "count": len(rows),
    }


def list_offer_recipes(include_inactive: bool = True) -> list[dict[str, Any]]:
    ensure_offer_plan_schema()
    db_name = _default_db_name()
    with storehub_database_context(db_name):
        conn = get_connection()
        try:
            cur = conn.cursor()
            sql = """
SELECT row_uuid, recipe_name, family, production_area, is_active, created_at, updated_at
FROM dbo.StoreHubOfferRecipes
"""
            if not include_inactive:
                sql += " WHERE is_active = 1"
            sql += " ORDER BY is_active DESC, recipe_name"
            cur.execute(sql)
            rows = []
            for r in cur.fetchall():
                rows.append(
                    {
                        "row_uuid": str(r[0]),
                        "recipe_name": r[1] or "",
                        "family": r[2] or "",
                        "production_area": r[3] or "",
                        "is_active": bool(r[4]),
                        "created_at": r[5],
                        "updated_at": r[6],
                    }
                )
            return rows
        finally:
            try:
                conn.close()
            except Exception:
                log_swallowed('offer_plan_test_repository:206')


def get_offer_recipe(recipe_uuid: str | None) -> dict[str, Any] | None:
    recipe_uuid = str(recipe_uuid or "").strip()
    if not recipe_uuid:
        return None
    ensure_offer_plan_schema()
    db_name = _default_db_name()
    with storehub_database_context(db_name):
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
SELECT row_uuid, recipe_name, family, production_area, is_active
FROM dbo.StoreHubOfferRecipes
WHERE row_uuid = ?
""",
                (recipe_uuid,),
            )
            r = cur.fetchone()
            if not r:
                return None
            recipe = {
                "row_uuid": str(r[0]),
                "recipe_name": r[1] or "",
                "family": r[2] or "",
                "production_area": r[3] or "",
                "is_active": bool(r[4]),
                "ingredients": [],
            }
            cur.execute(
                """
SELECT row_uuid, supplier, material, unit, cell, qty_per_portion, thaw_hours, note, sort_order
FROM dbo.StoreHubOfferRecipeIngredients
WHERE recipe_uuid = ?
ORDER BY sort_order, row_uuid
""",
                (recipe_uuid,),
            )
            for x in cur.fetchall():
                recipe["ingredients"].append(
                    {
                        "row_uuid": str(x[0]),
                        "supplier": x[1] or "",
                        "material": x[2] or "",
                        "unit": x[3] or "",
                        "cell": x[4] or "",
                        "qty_per_portion": _fmt_qty(float(x[5] or 0)),
                        "thaw_hours": str(int(x[6] or 0)),
                        "note": x[7] or "",
                        "sort_order": int(x[8] or 0),
                    }
                )
            return recipe
        finally:
            try:
                conn.close()
            except Exception:
                log_swallowed('offer_plan_test_repository:266')


def save_offer_recipe(recipe_uuid: str | None, data: dict[str, Any], ingredients: list[dict[str, Any]]) -> str:
    ensure_offer_plan_schema()
    name = str((data or {}).get("recipe_name") or "").strip()
    if not name:
        raise ValueError("Nome ricetta obbligatorio.")
    recipe_uuid = str(recipe_uuid or "").strip()
    family = str((data or {}).get("family") or "").strip() or None
    production_area = str((data or {}).get("production_area") or "").strip() or None
    is_active = 1 if (data or {}).get("is_active") else 0
    cleaned = []
    for idx, raw in enumerate(ingredients, start=1):
        material = str(raw.get("material") or "").strip()
        qty = _num(raw.get("qty_per_portion"))
        if not material or qty <= 0:
            continue
        cleaned.append(
            {
                "supplier": str(raw.get("supplier") or "").strip() or None,
                "material": material,
                "unit": str(raw.get("unit") or "").strip() or None,
                "cell": str(raw.get("cell") or "").strip() or None,
                "qty_per_portion": qty,
                "thaw_hours": int(_num(raw.get("thaw_hours"))),
                "note": str(raw.get("note") or "").strip() or None,
                "sort_order": idx,
            }
        )
    db_name = _default_db_name()
    with storehub_database_context(db_name):
        conn = get_connection()
        try:
            cur = conn.cursor()
            if recipe_uuid:
                cur.execute(
                    """
UPDATE dbo.StoreHubOfferRecipes
SET recipe_name=?, family=?, production_area=?, is_active=?, updated_at=SYSUTCDATETIME()
WHERE row_uuid=?
""",
                    (name, family, production_area, is_active, recipe_uuid),
                )
                new_id = recipe_uuid
            else:
                cur.execute(
                    """
INSERT INTO dbo.StoreHubOfferRecipes (recipe_name, family, production_area, is_active)
OUTPUT inserted.row_uuid
VALUES (?, ?, ?, ?)
""",
                    (name, family, production_area, is_active),
                )
                new_id = str(cur.fetchone()[0])
            cur.execute("DELETE FROM dbo.StoreHubOfferRecipeIngredients WHERE recipe_uuid=?", (new_id,))
            for row in cleaned:
                cur.execute(
                    """
INSERT INTO dbo.StoreHubOfferRecipeIngredients
  (recipe_uuid, supplier, material, unit, cell, qty_per_portion, thaw_hours, note, sort_order)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
""",
                    (
                        new_id,
                        row["supplier"],
                        row["material"],
                        row["unit"],
                        row["cell"],
                        row["qty_per_portion"],
                        row["thaw_hours"],
                        row["note"],
                        row["sort_order"],
                    ),
                )
            conn.commit()
            return new_id
        finally:
            try:
                conn.close()
            except Exception:
                log_swallowed('offer_plan_test_repository:347')


def set_offer_recipe_active(recipe_uuid: str, active: bool) -> None:
    recipe_uuid = str(recipe_uuid or "").strip()
    if not recipe_uuid:
        return
    ensure_offer_plan_schema()
    db_name = _default_db_name()
    with storehub_database_context(db_name):
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE dbo.StoreHubOfferRecipes SET is_active=?, updated_at=SYSUTCDATETIME() WHERE row_uuid=?",
                (1 if active else 0, recipe_uuid),
            )
            conn.commit()
        finally:
            try:
                conn.close()
            except Exception:
                log_swallowed('offer_plan_test_repository:369')


def build_offer_plan_preview(test_date: str, offer_rows: list[dict], recipes: list[dict]) -> dict[str, Any]:
    today = _parse_date(test_date) or date.today()
    recipes_by_uuid = {str(r.get("row_uuid") or ""): r for r in recipes}
    offers = []
    for raw in offer_rows:
        recipe_uuid = str(raw.get("recipe_uuid") or "").strip()
        recipe = recipes_by_uuid.get(recipe_uuid) or {}
        dish = str(recipe.get("recipe_name") or raw.get("dish") or "").strip()
        production_date = _parse_date(raw.get("production_date"))
        portions = _num(raw.get("portions"))
        if not dish or not production_date or portions <= 0:
            continue
        offers.append(
            {
                "recipe_uuid": recipe_uuid,
                "production_date": production_date,
                "service": str(raw.get("service") or "").strip() or "-",
                "family": str(recipe.get("family") or raw.get("family") or "").strip() or "-",
                "area": str(recipe.get("production_area") or raw.get("area") or "").strip() or "-",
                "dish": dish,
                "portions": portions,
            }
        )

    thaw_map: dict[tuple, dict] = {}
    shopping_map: dict[tuple, dict] = {}
    missing_recipes = []

    for offer in offers:
        recipe = recipes_by_uuid.get(str(offer.get("recipe_uuid") or "")) or {}
        ingredients = recipe.get("ingredients") or []
        if not ingredients:
            missing_recipes.append(offer)
            continue
        for ingredient in ingredients:
            qty = _num(offer["portions"]) * _num(ingredient.get("qty_per_portion"))
            area = ingredient.get("area") or offer.get("area") or "-"
            if int(ingredient.get("thaw_hours") or 0) > 0:
                thaw_days = max(1, int(ingredient["thaw_hours"] / 24))
                required_date = today + timedelta(days=thaw_days)
                if offer["production_date"] == required_date:
                    key = (
                        required_date.isoformat(),
                        ingredient["cell"],
                        ingredient["material"],
                        ingredient["supplier"],
                        ingredient["unit"],
                    )
                    row = thaw_map.setdefault(
                        key,
                        {
                            "required_date": required_date,
                            "thaw_hours": ingredient["thaw_hours"],
                            "cell": ingredient["cell"],
                            "material": ingredient["material"],
                            "supplier": ingredient["supplier"],
                            "unit": ingredient["unit"],
                            "qty": 0.0,
                            "dishes": set(),
                            "notes": set(),
                        },
                    )
                    row["qty"] += qty
                    row["dishes"].add(offer["dish"])
                    if ingredient["note"]:
                        row["notes"].add(ingredient["note"])
                continue

            if offer["production_date"] == today:
                key = (area, ingredient["cell"], ingredient["material"], ingredient["supplier"], ingredient["unit"])
                row = shopping_map.setdefault(
                    key,
                    {
                        "area": area,
                        "cell": ingredient["cell"],
                        "material": ingredient["material"],
                        "supplier": ingredient["supplier"],
                        "unit": ingredient["unit"],
                        "qty": 0.0,
                        "dishes": set(),
                        "notes": set(),
                    },
                )
                row["qty"] += qty
                row["dishes"].add(offer["dish"])
                if ingredient["note"]:
                    row["notes"].add(ingredient["note"])

    def finalize(row: dict) -> dict:
        out = dict(row)
        out["qty_label"] = _fmt_qty(float(out.get("qty") or 0))
        out["dishes"] = ", ".join(sorted(out.get("dishes") or []))
        out["notes"] = ", ".join(sorted(out.get("notes") or []))
        return out

    thaw_rows = [finalize(r) for r in thaw_map.values()]
    thaw_rows.sort(key=lambda r: (r["required_date"], str(r["cell"]).lower(), str(r["material"]).lower()))
    shopping_rows = [finalize(r) for r in shopping_map.values()]
    shopping_rows.sort(key=lambda r: (str(r["area"]).lower(), str(r["cell"]).lower(), str(r["material"]).lower()))

    return {
        "test_date": today,
        "offers": offers,
        "recipes_count": sum(len(r.get("ingredients") or []) for r in recipes),
        "thaw_rows": thaw_rows,
        "shopping_rows": shopping_rows,
        "missing_recipes": missing_recipes,
    }
