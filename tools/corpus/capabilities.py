"""Write docs/capabilities-xlsx.md from a corpus run.

Usage: python tools/corpus/capabilities.py [--report docs/corpus-report.md] [--out docs/capabilities-xlsx.md]

The input is the committed corpus report (``tools/corpus_check.py --report``),
so this needs no engine run and the test can check the document is current.
Each corpus category (the report's Categories column) is placed in one tier:

- supported: modeled and editable; every file in the category passes in the
  engines that read it.
- preserved-only: the content round-trips byte-for-byte but is not modeled or
  editable (the TIERS table below names these).
- refused: at least one engine refuses a file in the category, with the code.

The outcomes and codes come from the run; only the preserved-only list is
maintained by hand, because the round trip cannot tell "kept" from "understood".
python/tests/test_corpus.py keeps the committed document in step with the report.
"""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINES = ("py_toolkit", "py_canonical", "rust")

# Categories whose content is preserved but not modeled or editable. Anything
# not listed (and not refused) is reported as supported.
PRESERVED_ONLY = {
    "charts": "Chart parts are kept byte-for-byte; inspect reports type, anchor and series refs.",
    "chart-image": "Drawing and chart parts are kept; images are never read.",
    "chartsheets": "Listed and kept byte-for-byte; not modeled.",
    "pivot": "Pivot tables and caches are kept and inspected, never refreshed.",
    "pivot-fields": "Pivot field layout is inspected, never refreshed.",
    "page-fields": "Pivot page fields are inspected, never refreshed.",
    "pivot-calculated-field": "The calculated field's formula is kept in the cache; it is not evaluated.",
    "comments": "Comment parts are kept; not editable.",
    "threaded-comments": "Threaded comments and persons parts are kept; not editable.",
    "vml": "Legacy VML drawings are kept as opaque parts.",
    "conditional-formatting": "Rules are kept; not evaluated or editable.",
    "conditional-ext": "x14 conditional-format extensions are kept.",
    "hyperlinks": "Hyperlinks are kept; not editable.",
    "freeze-panes": "Sheet views are kept.",
    "autofilter": "Filters are kept; not applied.",
    "print-settings": "Page setup is kept.",
    "styles": "Imported styles are inspected and kept; style edits are refused.",
    "number-formats": "Number formats are kept and used for display; not editable.",
    "theme": "The theme part is kept.",
    "data-validation": "Rules are inspected and enforced for stop-style entries; the rules are not editable.",
    "form-controls": "ctrlProp parts and the controls list are kept; linked cells are ordinary cells.",
    "slicers": "Slicer and slicer-cache parts are kept; never applied.",
    "timelines": "Timeline parts are kept; never applied.",
    "sparklines": "x14 sparkline groups are kept in the worksheet extLst.",
    "connections": "connections.xml and query tables are kept; never refreshed.",
    "power-query": "The DataMashup item is kept as opaque customXml.",
    "custom-xml": "customXml items are kept as opaque parts.",
    "ole-objects": "Embedded objects are kept as opaque parts; never opened.",
    "outline": "Row and column outline levels are kept.",
    "protection": "Sheet and workbook protection is kept; hashes are never checked.",
    "external-links": "External-link parts are kept; links are never refreshed.",
    "extlst": "Unknown extension lists are kept.",
    "mc-ignorable": "Markup-compatibility content is kept.",
    "metadata": "Cell metadata parts are kept.",
    "bar-chart": "Chart parts are kept byte-for-byte; inspect reports type, anchor and series refs.",
    "line-chart": "Chart parts are kept byte-for-byte; inspect reports type, anchor and series refs.",
    "pie-chart": "Chart parts are kept byte-for-byte; inspect reports type, anchor and series refs.",
    "scatter-chart": "Chart parts are kept byte-for-byte; inspect reports type, anchor and series refs.",
    "chartsheet": "Listed and kept byte-for-byte; not modeled.",
    "images": "Image parts are kept byte-for-byte; never read.",
    "drawings": "Drawing parts are kept byte-for-byte.",
    "query-table": "Query-table parts are kept; never refreshed.",
    "tables": "Table parts are kept; cells inside are ordinary cells, table formula regions are not edited.",
    "totals-row": "The totals row is kept; its formulas are not calculated (table formula region).",
    "calculated-columns": "Calculated-column formulas are kept; not calculated (table formula region).",
    "structured-refs": "Structured references are kept; most are not calculated.",
    "3d-references": "3D references (Jan:Mar!B2) are kept with their cached values; calculation reports `unsupported_formula`.",
    "3d-refs": "3D references (Jan:Mar!B2) are kept with their cached values; calculation reports `unsupported_formula`.",
    "r1c1-looking-names": "The names and formulas are kept; formulas using them are not calculated.",
    "long-formula": "Kept; the toolkit engine does not calculate a long flat chain (expression depth limit), the canonical model does.",
    "formula-length-limit": "Kept; see long-formula.",
    "print-titles": "Print titles are kept.",
    "hidden-rows": "Row visibility is kept.",
    "docprops": "Document properties are kept.",
    "calcchain": "The calculation chain is kept or dropped as Excel allows; never trusted.",
    "dot-segments": "Part names are normalized for reading; parts are kept.",
    "odd-part-names": "Parts are kept under their own names.",
}

# Tags that describe who wrote a file or what it holds rather than a feature.
NOT_FEATURES = {
    "budget", "engineering", "gradebook", "inventory", "research-data", "perf", "large",
    "scale", "google-sheets", "libreoffice", "numbers", "excel-markup", "missing-parts",
    "no-dimension", "no-sst", "no-styles", "stale-dimension", "stored-zip", "xml-bom",
    "prefixed-namespace", "absolute-targets", "empty-sheet", "sparse", "xfd1048576",
    "xfd-column", "wide-sheet", "many-sheets", "unicode", "rtl", "whitespace",
}


def parse_report(text: str) -> tuple[dict, dict]:
    """(results, manifest) from the report's Matrix table."""
    lines = text.splitlines()
    start = lines.index("## Matrix")
    header = next(line for line in lines[start:] if line.startswith("| File |"))
    engines = [cell.strip() for cell in header.strip("|").split("|")][3:]
    results, manifest = {}, {}
    for line in lines[lines.index(header) + 2:]:
        if not line.startswith("| `"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        name = cells[0].strip("`")
        manifest[name] = {"writer": cells[1], "categories": [c.strip() for c in cells[2].split(",") if c.strip()]}
        row = {}
        for engine, cell in zip(engines, cells[3:]):
            if cell.startswith("refuse"):
                row[engine] = {"outcome": "refuse", "code": cell.split("`")[1] if "`" in cell else None}
            elif cell.startswith("**"):
                row[engine] = {"outcome": cell.split("**")[1]}
            else:
                row[engine] = {"outcome": cell}
        results[name] = row
    return results, manifest


def _status(record: dict) -> str:
    outcome = record.get("outcome")
    if outcome == "refuse":
        return f"refuse `{record.get('code')}`"
    return outcome or "-"


def build(results: dict, manifest: dict) -> str:
    by_category: dict[str, list[str]] = {}
    for name, info in sorted(manifest.items()):
        if name not in results:
            continue
        for category in info.get("categories", []):
            by_category.setdefault(category, []).append(name)
    tiers = {"supported": [], "preserved-only": [], "refused": []}
    for category, files in sorted(by_category.items()):
        refusals = sorted({
            f"{engine} `{results[f][engine].get('code')}`"
            for f in files for engine in ENGINES
            if results[f].get(engine, {}).get("outcome") == "refuse"
        })
        defects = sorted({
            f"{engine} {results[f][engine].get('outcome')}"
            for f in files for engine in ENGINES
            if results[f].get(engine, {}).get("outcome") in {"crash", "corrupt", "lossy", "untyped"}
        })
        stopped = sum(
            1 for f in files
            if any(results[f].get(e, {}).get("outcome") not in {"pass", "not_run", None} for e in ENGINES)
        )
        row = (category, files, refusals, defects, stopped)
        if category in NOT_FEATURES and not (refusals or defects):
            tiers.setdefault("shapes", []).append(row)
        elif refusals or defects:
            tiers["refused"].append(row)
        elif category in PRESERVED_ONLY:
            tiers["preserved-only"].append(row)
        else:
            tiers["supported"].append(row)
    total = len(results)
    lines = [
        "# XLSX capabilities",
        "",
        "Generated by `python tools/corpus/capabilities.py` from",
        "[the corpus report](corpus-report.md). Do not edit by hand.",
        "",
        f"The corpus holds {total} synthetic workbooks. Each one goes through open, inspect,",
        "calculate, a value edit, an absent-cell insert, export and reimport in three engines:",
        "the Python toolkit (`py_toolkit`), the Python canonical model (`py_canonical`, which",
        "writes a fresh package by design) and Rust (`rust`, also the WASM engine).",
        "",
        "- **Supported**: read, calculated where formulas apply, editable, and round-tripped.",
        "- **Preserved-only**: kept byte-for-byte through an edit and export, but not modeled",
        "  or editable.",
        "- **Refused**: at least one engine stops on at least one file in the category with",
        "  a typed refusal code ([refusal codes](refusal-codes.md)). A category can be refused",
        "  in one engine and read in another, or for one file and not another; the counts and",
        "  engines are listed, and the last section gives every file's outcome.",
        "",
        "A category is a shape or feature a corpus file exercises; one file has several.",
        "This is a robustness statement about Workbook Forge's readers and writers, not an",
        "Excel observation.",
        "",
    ]
    for tier, title in (("supported", "Supported"), ("preserved-only", "Preserved only"), ("refused", "Refused with a code")):
        rows = tiers[tier]
        lines += [f"## {title} ({len(rows)})", ""]
        if tier == "supported":
            lines += ["| Category | Files |", "| --- | ---: |"]
            lines += [f"| {c} | {len(f)} |" for c, f, *_ in rows]
        elif tier == "preserved-only":
            lines += ["| Category | Files | What is kept |", "| --- | ---: | --- |"]
            lines += [f"| {c} | {len(f)} | {PRESERVED_ONLY[c]} |" for c, f, *_ in rows]
        else:
            lines += ["| Category | Files stopped / files | Refusals | Defects |", "| --- | ---: | --- | --- |"]
            lines += [
                f"| {c} | {k} / {len(f)} | {'; '.join(r) or '-'} | {'; '.join(d) or 'none'} |"
                for c, f, r, d, k in rows
            ]
        lines.append("")
    shapes = tiers.get("shapes", [])
    lines += [f"## Writer and content shapes ({len(shapes)})", "",
              "Tags for who wrote a file or what it holds; every file under them passes.", "",
              ", ".join(f"{c} ({len(f)})" for c, f, *_ in shapes), ""]
    lines += ["## Files by category", ""]
    for category, files in sorted(by_category.items()):
        lines.append(f"- **{category}**: " + ", ".join(
            f"`{f}` ({' / '.join(_status(results[f].get(e, {})) for e in ENGINES)})" for f in files
        ))
    lines.append("")
    return "\n".join(lines)


def render(report: Path) -> str:
    results, manifest = parse_report(report.read_text(encoding="utf-8"))
    return build(results, manifest)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default=str(ROOT / "docs/corpus-report.md"))
    parser.add_argument("--out", default=str(ROOT / "docs/capabilities-xlsx.md"))
    args = parser.parse_args()
    Path(args.out).write_text(render(Path(args.report)), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
