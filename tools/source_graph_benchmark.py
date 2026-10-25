"""Matched executable-source routes for the versioned multi-cell fixture."""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import statistics
import subprocess
import time

from workbook_forge import source_graph as graph
from workbook_forge.toolkit import WorkbookModel
from workbook_forge.xlsx import export_xlsx, import_xlsx

ROOT = Path(__file__).resolve().parents[1]


def build_fixture(path, case):
    sheets = case.get("sheets") or [{"name":case["sheet"],"values":case["values"],"formulas":case["formulas"]}]
    model = WorkbookModel(sheets=[sheet["name"] for sheet in sheets])
    for sheet in sheets:
        for address, value in sheet.get("values",{}).items():
            model.set_value(sheet["name"], address, value)
        for address, formula in sheet.get("formulas",{}).items():
            model.set_formula(sheet["name"], address, formula)
    model.set_style(sheets[0]["name"], "A1", number_format="0.00", bold=True)
    export_xlsx(model, path)


def edit(source, language, version=2):
    if version >= 5:
        average='average_values('+('vec!' if language=='rust' else '')+'[add(reference(e, "C1"), reference(e, "C2")), num(7.5)])'
        assert source.count(average)==1
        source=source.replace(average,average.replace("7.5","11.5"))
    if version >= 4:
        condition = 'greater_equal(reference(e, "\'2026 O\'\'Brien\'!$A1"), num(5.0))'
        assert source.count(condition)==1
        source=source.replace(condition,condition.replace("5.0","8.0"))
    if version >= 3:
        expression = 'mul(reference(e, "\'2026 O\'\'Brien\'!$A1"), num(2.0))'
        default = '"\'2026 O\'\'Brien\'!A2": num(3.0),' if language == "python" else '("\'2026 O\'\'Brien\'!A2".to_string(), num(3.0)),'
        assert source.count(expression) == source.count(default) == 1
        return source.replace(expression,expression.replace("2.0","5.0")).replace(default,default.replace("3.0","6.0")).replace("'2026 O''Brien'!$A1","'2026 o''brien'!$A1")
    expression = 'mul(reference(e, "$A1"), num(2.0))'
    assert source.count(expression) == 1
    source = source.replace(expression, expression.replace("2.0", "5.0"))
    default = '"A2": num(3.0),' if language == "python" else '("A2".to_string(), num(3.0)),'
    assert source.count(default) == 1
    return source.replace(default, default.replace("3.0", "6.0"))


def equal(actual, expected):
    assert {key:graph.typed(value) for key,value in actual.items()} == {key:graph.typed(value) for key,value in expected.items()}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(output, binary, repeats, version=2):
    output.mkdir(parents=True, exist_ok=False)
    fixture_bytes = (ROOT/f"fixtures/source-graph.v{version}.json").read_bytes()
    case = json.loads(fixture_bytes)
    overrides = case.get("overrides",{"A1":7})
    sheets = case.get("sheets") or [{"name":case["sheet"],"values":case["values"],"formulas":case["formulas"]}]
    formula_count=sum(len(sheet.get("formulas",{})) for sheet in sheets)
    cell_count=sum(len(sheet.get("values",{}))+len(sheet.get("formulas",{})) for sheet in sheets)
    rows = []
    for origin, target in [("rust","rust"),("python","python"),("rust","python"),("python","rust")]:
        samples, artifacts = [], []
        for sample in range(repeats+1):
            folder = output/f"{origin}-{target}-{sample}"
            folder.mkdir()
            source = folder/"original.xlsx"
            build_fixture(source, case)
            start = time.perf_counter()
            code, manifest = graph.compile_workbook(source, origin,version)
            compiled = time.perf_counter()
            (folder/"manifest.json").write_text(json.dumps(manifest, indent=2))
            first = folder/("first.rs" if origin=="rust" else "first.py")
            first.write_text(edit(code,origin,version))
            input_hash=digest(source)
            source.unlink()  # Remove only this newly generated synthetic fixture.
            equal(graph.execute(first, origin, overrides, folder/"first_executable"),case["expected_after_edits"])
            first_executed = time.perf_counter()
            second = folder/("second.rs" if target=="rust" else "second.py")
            second.write_text(graph.translate(first.read_text(),origin,target))
            translated = time.perf_counter()
            equal(graph.execute(second,target,overrides,folder/"second_executable"),case["expected_after_edits"])
            second_executed = time.perf_counter()
            workbook = folder/"output.xlsx"
            lowered=folder/"lowered-toolkit-ir.json"
            lowered.write_text(json.dumps(graph.lower(second.read_text(),target,manifest,overrides),indent=2,allow_nan=False))
            equal(graph.emit(second,target,manifest,overrides,workbook,binary),case["expected_after_edits"])
            exported = time.perf_counter()
            snapshots = [import_xlsx(workbook).to_dict(), json.loads(subprocess.run(
                [str(binary),"decode",str(workbook)],check=True,capture_output=True,text=True,timeout=60).stdout)]
            for snapshot in snapshots:
                assert [sheet["name"] for sheet in snapshot["sheets"]] == [sheet["name"] for sheet in sheets]
                for imported, expected_sheet in zip(snapshot["sheets"],sheets):
                    cells = imported["cells"]
                    prefix=graph.sheet_prefix(imported["name"]) if version>=3 else ""
                    for address in expected_sheet.get("formulas",{}):
                        assert graph.typed(cells[address]["cached_value"]) == graph.typed(case["expected_after_edits"][prefix+address])
                    for address in expected_sheet.get("values",{}):
                        assert graph.typed(cells[address]["value"]) == graph.typed(case["expected_after_edits"][prefix+address])
                assert snapshot["sheets"][0]["cells"]["A1"]["style"] == manifest["model"]["sheets"][0]["cells"]["A1"]["style"]
            if sample:
                artifacts.append({"input_ooxml_sha256":input_hash,"first_edited_source_sha256":digest(first),
                    "translated_source_sha256":digest(second),"manifest_sha256":digest(folder/"manifest.json"),
                    "lowered_toolkit_ir_sha256":digest(lowered),"output_ooxml_sha256":digest(workbook)})
                samples.append({"total_ms":1000*(exported-start),"ooxml_to_source_ms":1000*(compiled-start),
                    "first_build_execute_ms":1000*(first_executed-compiled),"translation_ms":1000*(translated-first_executed),
                    "second_build_execute_ms":1000*(second_executed-translated),"checked_source_to_ooxml_ms":1000*(exported-second_executed)})
        rows.append({"route":f"ooxml->{origin}_source->{target}_source->ooxml",
            "median_ms":{key:statistics.median(sample[key] for sample in samples) for key in samples[0]},
            "samples":samples,"source_bytes":len(second.read_bytes()),"formula_count":formula_count,
            "cell_count":cell_count,"dependency_edges":sum(map(len,graph.validate(graph.import_source(second.read_text(),target)).values())),
            "original_workbook_available_after_compile":False,"manifest_contains_formulas_or_caches":False,
            "semantic_assertions":"independent fixture expectations, actual source/default edits, typed output and both-backend reimport",
            "artifacts":artifacts,
            "lane":{"reader":"python:xlsx.import_xlsx","source_parser_and_translator":"python:source_graph",
                "first_generated_evaluator":origin,"translated_generated_evaluator":target,
                "checked_emit_evaluator":"python:toolkit.calculate",
                "output_verifier":"python:bounded package reader + formula AST and typed values/errors; publication only after verification", "writer":target+":xlsx.export_xlsx",
                "writer_recalculation":target,"output_contract":"fresh workbook generation; no imported-package baseline",
                "orchestrator":"python","python_required_for":"reader, translator/lowering, validation and checked-emission calculation",
                "standalone_rust_program_requires_python":False,"end_to_end_rust_only":False},
            "excel_observed":False})
    return {"benchmark_version":version,"source_profile":version,"fixture_sha256":hashlib.sha256(fixture_bytes).hexdigest(),
        "comparison_operators":list(graph.COMPARISONS) if version>=4 else [],
        "comparison_evidence":"explicit Excel-targeted type/blank and ordinal collation profile; not Excel-observed" if version>=4 else "not source-compiled",
        "average_direct_policy":"provisional counted policy; conflicting primary Microsoft documentation; no Excel observation" if version>=5 else "not source-compiled",
        "functions_source_compiled":list(graph.FUNCTIONS_BY_VERSION[version]),"runtime_profiles_implemented":115,
        "count_note":"runtime registry membership is separate from source-compilation coverage and Excel observation",
        "timing_scope":"one warmup excluded; same workbook/edits; all Rust builds and process startup included; checked emission executes target program again; final reimports excluded",
        "comparison_note":"feature/cost checkpoint, not a speedup versus the two-cell v1 workload or another library",
        "memory":{"parent_peak_rss_bytes":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "largest_child_peak_rss_bytes":resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
            "scope":"macOS process-lifetime peaks including rustc and compiler-driver validation; not per-route runtime allocation"},
        "routes":rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output",type=Path)
    parser.add_argument("--profile",type=int,choices=[2,3,4,5,6],default=2)
    parser.add_argument("--repeats",type=int,default=3)
    parser.add_argument("--rust-binary",type=Path,default=ROOT/"rust/target/release/examples/compiler_roundtrip")
    args = parser.parse_args()
    if not 1<=args.repeats<=10:
        parser.error("repeats must be 1..10")
    receipt = run(args.output.resolve(),args.rust_binary.resolve(),args.repeats,args.profile)
    (args.output/"receipt.json").write_text(json.dumps(receipt,indent=2)+"\n")
    print(json.dumps(receipt,indent=2))


if __name__=="__main__":
    main()
