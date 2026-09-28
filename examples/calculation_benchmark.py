"""Compare serial/parallel work in either independently implemented engine.

Run from an installed package. Timings include calculation and report conversion;
they exclude authoring. Resident memory is the process high-water mark, including
the interpreter and any earlier workload. This is not a cross-language speed race.
"""
from __future__ import annotations

import argparse
import json
import resource
import sys
from time import perf_counter_ns

from workbook_forge.toolkit import WorkbookModel


def workload(name: str) -> dict:
    count = 64 if name == "parallel-heavy" else 1000
    cells = {}
    for row in range(1, count + 1):
        if name == "sparse" or row == 1 and name in {"chain", "branching"}:
            cell = {"value": row}
        elif name == "dense":
            cell = {"formula": f"={row}*3+4"}
        elif name == "chain":
            cell = {"formula": f"=A{row - 1}+1"}
        elif name == "branching":
            cell = {"formula": f"=A{row // 2}+1"}
        else:
            cell = {"formula": "=SUM(SORT(SEQUENCE(10000,1,10000,-1)))"}
        cells[f"A{row}"] = cell
    return {
        "sheets": [{"id": "benchmark", "name": "Benchmark", "cells": cells}],
        "outputs": {"last": {"sheet": "Benchmark", "address": f"A{count}"}} if name == "chain" else {},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("python", "rust"), default="python")
    args = parser.parse_args()
    for name in ("sparse", "dense", "chain", "branching", "parallel-heavy"):
        document = workload(name)
        reports, elapsed = [], []
        for workers in (1, 4):
            model = WorkbookModel(document=document, backend=args.backend)
            start = perf_counter_ns()
            reports.append(model.calculate(workers=workers))
            elapsed.append((perf_counter_ns() - start) // 1000)
        serial, parallel = reports
        for field in ("outputs", "values", "diagnostics"):
            assert serial[field] == parallel[field], (name, field)
        assert not serial["diagnostics"]
        expected_work = sum("formula" in cell for cell in document["sheets"][0]["cells"].values())
        assert len(serial["evaluated_cells"]) == expected_work
        peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        print(json.dumps({
            "workload": name, "backend": args.backend, "workers": 4,
            "formula_evaluations": len(serial["evaluated_cells"]),
            "serial_us": elapsed[0], "parallel_us": elapsed[1], "equal": True,
            "peak_process_rss_bytes": peak_rss if sys.platform == "darwin" else peak_rss * 1024,
            "serialized_model_bytes": len(json.dumps(document).encode()),
            "serialized_result_bytes": len(json.dumps(serial).encode()),
        }))


if __name__ == "__main__":
    main()
