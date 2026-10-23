"""Package patch change detection must preserve scalar types and zero signs."""

import math

import pytest

from workbook_forge.toolkit import WorkbookModel
from workbook_forge.xlsx import export_xlsx, import_xlsx


@pytest.mark.parametrize(
    "before,after", [(0, -0.0), (-0.0, 0), (False, 0), (True, 1), (0, False), (1, True)]
)
def test_imported_scalar_patch_retains_type_and_sign(tmp_path, before, after):
    original = tmp_path / "original.xlsx"
    model = WorkbookModel(
        document={
            "schema_version": 1,
            "sheets": [{"id": "calc", "name": "Calc", "cells": {"A1": {"value": before}}}],
            "outputs": {"value": {"sheet": "Calc", "address": "A1"}},
        }
    )
    export_xlsx(model, original)
    original_bytes = original.read_bytes()
    imported = import_xlsx(original, outputs=model.to_dict()["outputs"])
    imported.set_value("Calc", "A1", after)
    export_xlsx(imported, tmp_path / "output.xlsx")
    restored = import_xlsx(tmp_path / "output.xlsx").to_dict()["sheets"][0]["cells"]["A1"]["value"]
    if isinstance(after, bool):
        assert restored is after
    else:
        assert type(restored) in (int, float) and restored == after
        assert math.copysign(1, restored) == math.copysign(1, after)
    assert original.read_bytes() == original_bytes
