"""Classify the analyzer diagnostics the corpus gains in to_canonical by cause."""
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(sys.argv[1])
sys.path.insert(0, str(ROOT / "python"))

from workbook_forge.convert import ConversionError, to_canonical  # noqa: E402
from workbook_forge.workbook import WorkbookError  # noqa: E402
from workbook_forge.xlsx import import_xlsx  # noqa: E402

classes = Counter()
per_class_workbooks = {}
total = 0
for path in sorted((ROOT / "fixtures" / "corpus").glob("*.xlsx")):
    try:
        model = import_xlsx(path)
    except WorkbookError:
        continue
    try:
        workbook, _ = to_canonical(model.to_dict(), model.envelope)
    except ConversionError:
        continue
    has_tables = bool(model.envelope.tables)
    for d in workbook.diagnostics:
        total += 1
        m = d.message
        if "unsupported name" in m:
            c = "defined name absent from the toolkit document (name table is a side input)"
        elif "could not be parsed" in m and has_tables:
            c = "structured reference; table regions live in the envelope, not the document"
        elif "could not be parsed" in m:
            c = "parse_error, other"
        elif "3D reference" in m:
            c = "3D reference (engine limit)"
        elif "in-cell array function" in m:
            c = "in-cell array function (engine limit)"
        elif "unsupported function" in m:
            c = "unsupported function (engine limit)"
        else:
            c = f"other: {d.code}"
        classes[c] += 1
        per_class_workbooks.setdefault(c, set()).add(path.name)
print("total", total)
for c, n in classes.most_common():
    print(n, "in", len(per_class_workbooks[c]), "workbooks:", c, sorted(per_class_workbooks[c]) if n < 6 else "")
