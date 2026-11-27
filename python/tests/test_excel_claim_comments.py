"""Source text must not use these unreceipted Excel phrases.

Each phrase states behavior as Excel's without a committed receipt. A comment
that needs the rule points at the doc that records it, or says what the code
does.
"""

from pathlib import Path

ROOT = Path(__file__).parents[2]

SOURCE_ROOTS = (
    ROOT / "rust" / "src",
    ROOT / "python" / "workbook_forge",
    ROOT / "typescript" / "src",
)

PHRASES = (
    "as Excel does",
    "as Excel reads it",
    "Excel's reference rules",
    "returns a reference in Excel",
    "required for Excel to recalculate",
    "the way Excel's General format does",
)


def _roots() -> list[Path]:
    roots = [path for path in SOURCE_ROOTS if path.is_dir()]
    web = ROOT / "web"
    if web.is_dir():
        roots.extend(sorted(item / "src" for item in web.iterdir() if (item / "src").is_dir()))
    return roots


def test_unreceipted_excel_phrases_stay_out_of_sources():
    hits: list[str] = []
    for root in _roots():
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for phrase in PHRASES:
                if phrase in text:
                    hits.append(f"{path.relative_to(ROOT)}: {phrase}")
    assert not hits, "\n".join(hits)
