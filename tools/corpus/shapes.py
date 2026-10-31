"""More hand-built OOXML shapes for the robustness corpus (standard library only).

``handbuilt.py`` holds the original set; this module adds synthetic shapes
Workbook Forge must read, built with the same helpers so every file is synthetic,
general-purpose and byte-stable across regenerations. ``generate_corpus.py``
writes both sets under the ``handbuilt`` writer.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import handbuilt as hb
from handbuilt import minimal_styles, sheet_xml, simple


def dropdown_formulas():
    """Drop-down cells that already hold formulas. The cells and their <v>
    caches are authored by this builder. A3's cached "No" is a deliberately
    stale cache next to B3 = "Maybe" so the corpus can tell a calculated
    result from a cached one."""
    rows = (
        '<row r="1"><c r="A1" t="inlineStr"><is><t>Choice</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>Source</t></is></c></row>'
        '<row r="2"><c r="A2" t="str"><f>B2</f><v>Yes</v></c>'
        '<c r="B2" t="inlineStr"><is><t>Yes</t></is></c></row>'
        '<row r="3"><c r="A3" t="str"><f>B3</f><v>No</v></c>'
        '<c r="B3" t="inlineStr"><is><t>Maybe</t></is></c></row>'
        '<row r="4"><c r="A4"><f>2*B4</f><v>4</v></c><c r="B4"><v>2</v></c></row>'
    )
    validations = (
        '<dataValidations count="2">'
        '<dataValidation type="list" allowBlank="1" showErrorMessage="1" sqref="A2:A3">'
        '<formula1>"Yes,No"</formula1></dataValidation>'
        '<dataValidation type="whole" operator="between" showErrorMessage="1" sqref="A4">'
        '<formula1>1</formula1><formula2>10</formula2></dataValidation>'
        '</dataValidations>'
    )
    return simple({"Choices": sheet_xml(rows, dimension="A1:B4", after=validations)},
                  styles=minimal_styles())


BUILDERS = {
    "hb_dropdown_formulas": (dropdown_formulas, ["data-validation", "dropdowns", "formula-in-validated-cell"]),
}
STORED: set[str] = set()


def build_all(folder: Path) -> dict[str, list[str]]:
    made = {}
    for name, (builder, categories) in BUILDERS.items():
        compression = zipfile.ZIP_STORED if name in STORED else zipfile.ZIP_DEFLATED
        hb.write_package(folder / f"{name}.xlsx", builder(), compression=compression)
        made[f"{name}.xlsx"] = categories
    return made
