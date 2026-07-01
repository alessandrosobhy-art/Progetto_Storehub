from __future__ import annotations

import re
from pathlib import Path
import sys

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finance_store_mapping_repository import import_code_catalog_rows, parse_code_catalog_text, save_code_assignment
load_dotenv(ROOT / ".env")

SOURCE_XLS = Path(r"C:\Users\aless\Desktop\RETAIL CARTE BCC SETTEMBRE.xls")
VALID_FROM = "2026-01-01"


RULES: list[tuple[str, str]] = [
    ("ORIO CENTER", "4020"),
    ("MAXIMO", "4118"),
    ("PORTO ALLEGRO", "4084"),
    ("TORRIBIANCHE", "4040"),
    ("LE CUCINE DI CURNO", "4009"),
    ("LE CUPOLE", "4092"),
    ("PIAZZA LODI", "4151"),
    ("VULCANO BUONO", "4059"),
    ("OFFICINE S", "4081"),
    ("CASAMASSIMA", "4058"),
    ("GRANDE PUGLIA", "4105"),
    ("SERRAVALLE SCRIVIA", "4032"),
    ("PUNTA DI FERRO", "4089"),
    ("PORTOGRUARO", "4163"),
    ("LE DUE VALLI", "4051"),
    ("CONCA D ORO", "4093"),
    ("CENTRONOVA", "4042"),
    ("MOLINETT", "4044"),
    ("MARI E MONTI", "4156"),
    ("STALINGRADO", "4123"),
    ("LE CORTI DI VARESE", "4098"),
    ("GRAN RONDO", "4116"),
    ("METROPOLI", "4043"),
    ("FILZI 19", "4003"),
    ("GAVINANA", "4104"),
    ("LE MAIOLICHE", "4074"),
    ("ROMANINA", "4108"),
    ("DESTRIERO", "4106"),
    ("PRIMAVERA", "4080"),
    ("RESCALDINA", "4088"),
    ("ETNAPOLIS", "4100"),
    ("RONCADELLE", "4073"),
    ("TIBURTINO", "4078"),
    ("CASILINO", "4077"),
    ("TRIESTE", "4162"),
    ("UDINE", "4161"),
    ("FORLI", "4089"),
    ("SALERNO", "4097"),
    ("PORTO SANT ELPIDIO", "4128"),
]


def norm(text: str) -> str:
    s = str(text or "").upper().strip()
    s = s.replace("'", " ")
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def read_lines() -> list[str]:
    df = pd.read_excel(SOURCE_XLS, sheet_name="Foglio1", header=None)
    return [str(x).strip() for x in df.iloc[:, 0].tolist() if str(x).strip() and str(x).strip() != "nan"]


def pick_store_code(label: str) -> str:
    text = norm(label)
    for token, store_code in RULES:
        if token in text:
            return store_code
    return ""


def main() -> None:
    lines = read_lines()
    parsed = parse_code_catalog_text("\n".join(lines))
    rows = parsed.get("rows") or []
    import_code_catalog_rows(rows, seed_source="bcc_xls_seed")

    assigned = 0
    unresolved: list[str] = []
    for row in rows:
        code = str((row or {}).get("code") or "").strip()
        label = str((row or {}).get("label") or "").strip()
        store_code = pick_store_code(label)
        if store_code:
            save_code_assignment(
                code=code,
                store_code=store_code,
                valid_from=VALID_FROM,
                note="Seed automatico da RETAIL CARTE BCC SETTEMBRE.xls",
            )
            assigned += 1
        else:
            unresolved.append(f"{code} - {label}")

    print(f"Catalog imported: {len(rows)}")
    print(f"Assignments seeded: {assigned}")
    print("Unresolved:")
    for item in unresolved:
        print(item)


if __name__ == "__main__":
    main()
