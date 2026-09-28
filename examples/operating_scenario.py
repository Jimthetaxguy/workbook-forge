"""Run the same operating model as the Rust example and CLI.

Usage: python examples/operating_scenario.py /new/output/scenario.xlsx --with-unsupported
"""

import argparse
import json
from pathlib import Path

from workbook_forge.toolkit import WorkbookModel, operating_scenario
from workbook_forge.xlsx import export_xlsx, import_xlsx


def main(output: Path, with_unsupported: bool = False) -> None:
    model = operating_scenario()
    model.set_inputs({"unit_price": 25})
    result = model.calculate()
    if result["diagnostics"]:
        raise ValueError(result["diagnostics"])
    export_xlsx(model, output, report=result)
    definition = model.to_dict()
    imported = import_xlsx(output, inputs=definition["inputs"], outputs=definition["outputs"])
    assert imported.calculate()["outputs"] == result["outputs"]
    print(json.dumps(result["outputs"], indent=2))
    if with_unsupported:
        # LET is valid modern Excel syntax beyond Forge's supported function set.
        # It remains outside this tool's output dependencies and is never run here.
        variant_document = model.to_dict()
        variant_document["sheets"][1]["cells"]["J1"] = {"formula": "=LET(x,1,x)"}
        variant_model = WorkbookModel(document=variant_document)
        variant = output.with_stem(output.stem + "-unsupported")
        export_xlsx(variant_model, variant)
        preserved = import_xlsx(variant, inputs=definition["inputs"], outputs=definition["outputs"])
        assert preserved.inspect()["diagnostics"]
        preserved.set_inputs({"unit_price": 26})
        patched = output.with_stem(output.stem + "-preserved")
        export_xlsx(preserved, patched)
        reopened = import_xlsx(patched, inputs=definition["inputs"], outputs=definition["outputs"])
        assert reopened.to_dict()["sheets"][1]["cells"]["J1"]["formula"] == "LET(x,1,x)"
        assert reopened.calculate()["outputs"]["revenue"] == 9620
        blocked = import_xlsx(patched, outputs={"unsupported": {"sheet": "Forecast", "address": "J1"}})
        assert blocked.calculate()["diagnostics"]
        print(json.dumps({"imported_variant": str(variant), "preserved_variant": str(patched), "unsupported_output_diagnostics": blocked.calculate()["diagnostics"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--with-unsupported", action="store_true")
    arguments = parser.parse_args()
    main(arguments.output, arguments.with_unsupported)
