from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finance_store_mapping_repository import delete_assignments_by_code, save_code_assignment


load_dotenv(ROOT / ".env")

SOURCE_XLSX = Path(r"C:\Users\aless\Desktop\new pos i love poke.xlsx")
VALID_FROM = "2026-01-01"


CONFIDENT: dict[str, str] = {
    "563857700001": "4040",
    "563857700002": "4042",
    "563857700005": "4044",
    "563857700007": "4048",
    "563857700008": "4043",
    "563857700009": "4051",
    "563857700010": "4163",
    "563857700011": "4058",
    "563857700012": "4059",
    "563857700013": "4173",
    "563857700014": "4128",
    "563857700015": "4151",
    "563857700016": "4073",
    "563857700017": "4074",
    "563857700018": "4075",
    "563857700019": "4158",
    "563857700020": "4080",
    "563857700021": "4077",
    "563857700022": "4078",
    "563857700023": "4083",
    "563857700024": "4081",
    "563857700025": "4097",
    "563857700026": "4086",
    "563857700028": "4084",
    "563857700029": "4088",
    "563857700030": "4089",
    "563857700031": "4092",
    "563857700032": "4048",
    "563857700033": "4091",
    "563857700034": "4093",
    "563857700035": "4162",
    "563857700036": "4098",
    "563857700038": "4100",
    "563857700039": "4003",
    "563857700040": "4104",
    "563857700041": "4028",
    "563857700042": "4175",
    "563857700043": "4164",
    "563857700044": "4009",
    "563857700045": "4105",
    "563857700047": "4156",
    "563857700048": "4106",
    "563857700049": "4042",
    "563857700053": "4008",
    "563857700056": "4032",
    "563857700057": "4118",
    "563857700058": "4116",
    "563857700059": "4123",
    "563857700060": "4008",
    "563857700081": "4134",
    "563857700082": "4137",
    "563857700083": "4135",
    "563857700084": "4132",
    "563857700085": "4136",
    "563857700086": "4138",
    "563857700087": "4142",
    "563857700088": "4130",
    "563857700089": "4140",
    "563857700090": "4141",
    "563857700092": "4133",
    "563857700093": "4131",
    "563857700094": "4127",
}


UNRESOLVED = {
    "563857700027",
    "563857700037",
    "563857700046",
    "563857700051",
    "563857700052",
    "563857700054",
    "563857700055",
    "563857700061",
    "563857700091",
    "563857700095",
}


def read_codes() -> list[str]:
    raw = pd.read_excel(SOURCE_XLSX, sheet_name="Foglio1", header=None)
    rows = raw.iloc[7:].copy()
    values = []
    for val in rows.iloc[:, 3].tolist():
        s = str(val or "").replace(".0", "").strip()
        if s.startswith("5638577"):
            values.append(s)
    return values


def main() -> None:
    codes = read_codes()
    for code in codes:
        delete_assignments_by_code(code)
        if code in CONFIDENT:
            save_code_assignment(
                code=code,
                store_code=CONFIDENT[code],
                valid_from=VALID_FROM,
                note="Seed da new pos i love poke.xlsx",
            )

    print(f"Total 5638577 codes in file: {len(codes)}")
    print(f"Assigned confidently: {len(CONFIDENT)}")
    print("Unresolved:")
    for code in sorted(UNRESOLVED):
        print(code)


if __name__ == "__main__":
    main()
