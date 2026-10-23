"""Bounded synthetic workloads and independent arithmetic expectations, version 1."""

from workbook_forge.source_graph import sheet_prefix


def workload(kind, size):
    if kind not in {"chain", "fanout", "ranges", "multisheet"} or size not in {"small", "medium"}:
        raise ValueError("choose a versioned bounded workload")
    n = {"chain": (16, 64), "fanout": (24, 160), "ranges": (8, 64), "multisheet": (16, 96)}[kind][
        size == "medium"
    ]
    data_name, calc_name = (
        ("Data Set", "Calc! O'Brien") if kind == "multisheet" else ("Calc", "Calc")
    )
    values = {"A1": 2}
    formulas = {}
    if kind == "chain":
        formulas = {f"B{i}": f"={'A1' if i == 1 else 'B' + str(i - 1)}+1" for i in range(1, n + 1)}
    elif kind == "fanout":
        formulas = {f"B{i}": f"=$A$1+{i}" for i in range(1, n + 1)}
        formulas["C1"] = f"=SUM(B1:B{n})"
    elif kind == "ranges":
        inputs = 8 if size == "small" else 32
        values = {f"A{i}": i for i in range(1, inputs + 1)}
        formulas = {f"B{i}": f"=SUM(A1:A{inputs})+AVERAGE(A1:A{inputs})" for i in range(1, n + 1)}
    else:
        values = {f"A{i}": i for i in range(1, n + 1)}
        formulas = {
            f"B{i}": f"=IF('Data Set'!$A{i}>0,IFERROR(AVERAGE('Data Set'!A{i}:A{i})+SUM('Data Set'!A{i}:A{i}),9),0)"
            for i in range(1, n + 1)
        }
        formulas.update(C1="=IFERROR(1/0,7)", C2="=IF(FALSE,1/0,4)")
    sheets = [
        {"id": "data", "name": data_name, "cells": {a: {"value": v} for a, v in values.items()}}
    ]
    target = sheets[0] if data_name == calc_name else {"id": "calc", "name": calc_name, "cells": {}}
    target["cells"].update({a: {"formula": v} for a, v in formulas.items()})
    if target is not sheets[0]:
        sheets.append(target)
    document = {"schema_version": 1, "revision": 0, "sheets": sheets, "inputs": {}, "outputs": {}}

    def expected(edited=False):
        numbers = dict(values)
        numbers["A1"] += int(edited)
        result = {sheet_prefix(data_name) + a: float(v) for a, v in numbers.items()}
        if kind == "chain":
            outputs = {f"B{i}": float(numbers["A1"] + i) for i in range(1, n + 1)}
        elif kind == "fanout":
            outputs = {f"B{i}": float(numbers["A1"] + i) for i in range(1, n + 1)}
            outputs["C1"] = float(n * numbers["A1"] + n * (n + 1) // 2)
        elif kind == "ranges":
            total = float(sum(numbers.values()))
            outputs = {f"B{i}": total + total / len(numbers) for i in range(1, n + 1)}
        else:
            outputs = {f"B{i}": float(2 * numbers[f"A{i}"]) for i in range(1, n + 1)}
            outputs.update(C1=7.0, C2=4.0)
        result.update({sheet_prefix(calc_name) + a: v for a, v in outputs.items()})
        return result

    return document, (data_name, "A1", values["A1"] + 1), expected(False), expected(True)
