"""Write a public synthetic fixture using the existing workbook/agent contracts."""

import argparse
import json
from pathlib import Path
from workbook_forge.toolkit import WorkbookModel
from workbook_forge.xlsx import export_xlsx


def write_fixture(directory: Path):
    directory.mkdir(parents=True, exist_ok=False)
    cells = {f"A{row}": {"value": 0} for row in range(1, 121)}
    cells.update(
        A1={"value": 2},
        A2={"value": 3},
        A3={"value": "4"},
        A4={"value": False},
        A5={"value": None},
        B1={"formula": "=SUM(A1:A120)", "cached_value": 777},
        B2={"formula": "=IF(A1>0,B1*2,0)", "cached_value": 999},
        B3={"formula": "=1/0"},
    )
    outputs = {
        name: {"sheet": "Calc", "address": address}
        for name, address in [("total", "B1"), ("doubled", "B2"), ("error", "B3")]
    }
    model = WorkbookModel(
        document={
            "schema_version": 1,
            "revision": 0,
            "sheets": [{"id": "calc", "name": "Calc", "cells": cells}],
            "inputs": {},
            "outputs": outputs,
        }
    )
    (directory / "model.json").write_text(json.dumps(model.to_dict()))
    (directory / "bindings.json").write_text(json.dumps({"inputs": {}, "outputs": outputs}))
    export_xlsx(model, directory / "original.xlsx")
    return model


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    write_fixture(parser.parse_args().directory)
