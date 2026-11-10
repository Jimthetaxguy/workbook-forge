"""Union counts of corpus workbooks carrying toolkit-only content, for the memo."""
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
sys.path.insert(0, str(ROOT / "python"))

from workbook_forge.convert import ConversionError, to_canonical  # noqa: E402
from workbook_forge.workbook import WorkbookError  # noqa: E402
from workbook_forge.xlsx import import_xlsx  # noqa: E402

STYLE = re.compile(r"\.style\.(bold|font_color|fill_color|horizontal)$")
GENERAL = re.compile(r"\.style\.number_format$")
WIDTH = re.compile(r"\.column_widths\.")
BLOCKED = re.compile(r"\.blocked_reason$")
CACHED = re.compile(r"\.cached_value$")
sets = {k: set() for k in ("style", "general", "width", "blocked", "cached")}
for path in sorted((ROOT / "fixtures" / "corpus").glob("*.xlsx")):
    try:
        model = import_xlsx(path)
    except WorkbookError:
        continue
    try:
        _, forward = to_canonical(model.to_dict(), model.envelope)
    except ConversionError:
        continue
    for p in forward.dropped:
        if STYLE.search(p):
            sets["style"].add(path.name)
        elif GENERAL.search(p):
            sets["general"].add(path.name)
        elif WIDTH.search(p):
            sets["width"].add(path.name)
        elif BLOCKED.search(p):
            sets["blocked"].add(path.name)
        elif CACHED.search(p):
            sets["cached"].add(path.name)
for k, v in sets.items():
    print(k, len(v), sorted(v))
print("any non-identity, non-session:", len(set().union(*sets.values())))
print("styles or widths (leave the inventory under Base B):", len(sets["style"] | sets["general"] | sets["width"]))
print("styles beyond General or widths:", len(sets["style"] | sets["width"]))
