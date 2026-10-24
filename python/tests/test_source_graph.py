"""Independent multi-cell generated programs and edited-source translation."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from test_compiler_benchmark import compiler_binary  # noqa: F401
from test_compiler_benchmark import tool
from workbook_forge import source_graph as g
from workbook_forge import source_process
from workbook_forge.source_contract import SourceDiagnostic
from workbook_forge.toolkit import WorkbookModel
from workbook_forge.xlsx import export_xlsx, import_xlsx

ROOT = Path(__file__).resolve().parents[2]
CASE = json.loads((ROOT / "fixtures/source-graph.v2.json").read_text())


def fixture(path):
    model = WorkbookModel(sheets=[CASE["sheet"]])
    for address, value in CASE["values"].items():
        model.set_value(CASE["sheet"], address, value)
    for address, formula in CASE["formulas"].items():
        model.set_formula(CASE["sheet"], address, formula)
    model.set_style(CASE["sheet"], "A1", number_format="0.00", bold=True)
    export_xlsx(model, path)


def edit(source, language):
    before = 'mul(reference(e, "$A1"), num(2.0))'
    assert source.count(before) == 1
    source = source.replace(before, 'mul(reference(e, "$A1"), num(5.0))')
    before = '"A2": num(3.0),' if language == "python" else '("A2".to_string(), num(3.0)),'
    assert source.count(before) == 1
    return source.replace(before, before.replace("3.0", "6.0"))


def typed_equal(actual, expected):
    assert {key:g.typed(value) for key,value in actual.items()} == {key:g.typed(value) for key,value in expected.items()}


@pytest.mark.parametrize("origin,target", [("rust", "rust"), ("python", "python"), ("rust", "python"), ("python", "rust")])
def test_four_routes_use_edited_program_without_original(tmp_path, compiler_binary, origin, target):  # noqa: F811
    source = tmp_path / "original.xlsx"
    fixture(source)
    code, manifest = g.compile_workbook(source, origin)
    assert all("formula" not in cell and "cached_value" not in cell for cell in manifest["model"]["sheets"][0]["cells"].values())
    assert "<worksheet" not in json.dumps(manifest)
    edited = edit(code, origin)
    first = tmp_path / ("first.rs" if origin == "rust" else "first.py")
    first.write_text(edited)
    source.unlink()  # Synthetic fixture: later stages have no original OOXML to read.
    expected = CASE["expected_after_edits"]
    actual = g.execute(first, origin, {"A1":7}, tmp_path / "first_executable")
    typed_equal(actual, expected)
    # Raw program invocation works without Forge, XML or manifest parameters.
    command = [str(tmp_path/"first_executable")] if origin == "rust" else [sys.executable, str(first)]
    raw = subprocess.run([*command, "A1=n:7.0"], check=True, capture_output=True, text=True)
    assert "B12\tn\t83" in raw.stdout
    second = tmp_path / ("second.rs" if target == "rust" else "second.py")
    second.write_text(g.translate(first.read_text(), origin, target))
    typed_equal(g.execute(second, target, {"A1":7}, tmp_path/"second_executable"), expected)
    different = g.execute(second, target, {"A1":2}, tmp_path/"changed_executable")
    assert different["B1"] == 10 and different["B2"] == 18 and different["B12"] == 28
    assert different["B3"] == different["B4"] == {"error":"#DIV/0!"}
    output = tmp_path / "output.xlsx"
    typed_equal(g.emit(second, target, manifest, {"A1":7}, output, compiler_binary), expected)
    snapshot = import_xlsx(output).to_dict()
    cells = snapshot["sheets"][0]["cells"]
    assert cells["A2"]["value"] == 6  # Edited code default overrides the original manifest value 3.
    assert cells["B1"]["formula"].removeprefix("=") == "($A1*5.0)"
    assert cells["B2"]["formula"].removeprefix("=") == "(SUM($A$1:A6)+B1)"
    assert cells["B12"]["formula"].removeprefix("=") == "SUM(B$1:B$2)"
    for address in CASE["formulas"]:
        assert g.typed(cells[address]["cached_value"]) == g.typed(expected[address])
    assert cells["A1"]["style"] == manifest["model"]["sheets"][0]["cells"]["A1"]["style"]


@pytest.mark.parametrize("language", ["python", "rust"])
def test_reference_edits_update_dependencies_and_typed_inputs(tmp_path, language):
    source = tmp_path/"source.xlsx"
    fixture(source)
    code, manifest = g.compile_workbook(source, language)
    changed = edit(code, language).replace('reference(e, "$A1")', 'reference(e, "$A2")')
    program = g.import_source(changed, language)
    assert "A1" not in g.validate(program)["B1"]  # Leaf references are not formula edges.
    path=tmp_path/("program.py" if language=="python" else "program.rs")
    path.write_text(changed)
    actual=g.execute(path, language, {"A1":7,"A2":4,"A3":"9","A4":False,"A5":"","A6":None},tmp_path/"program")
    assert actual["B1"]==20 and actual["B2"]==31 and actual["B12"]==51
    assert actual["B5"]==10 and actual["B6"]==2
    # Introduce a forward formula dependency in the source itself.
    changed=changed.replace('reference(e, "$A2")', 'reference(e, "B5")')
    path.write_text(changed)
    assert g.validate(g.import_source(changed,language))["B1"]==["B5"]
    actual=g.execute(path,language,{"A1":7,"A3":"9"},tmp_path/"program2")
    assert actual["B1"]==50 and actual["B2"]==63 and actual["B12"]==113
    assert "$A1" not in g.lower(changed,language,manifest,{"A1":7})["sheets"][0]["cells"]["B1"]["formula"]


@pytest.mark.parametrize("change,code", [("cycle","cycle"),("range","resource_limit"),("scaffold","unsupported_source"),("call","unsupported_source")])
def test_source_refusals(tmp_path, change, code):
    source=tmp_path/"source.xlsx"
    fixture(source)
    text,_=g.compile_workbook(source,"python")
    if change=="cycle":
        text=text.replace('reference(e, "$A1")','reference(e, "B2")')
    elif change=="range":
        text=text.replace('range_ref(e, "$A$1:A6")','range_ref(e, "A1:XFD1048576")')
    elif change=="scaffold":
        text=text.replace("import math","import os")
    else:
        text=text.replace('mul(reference(e, "$A1"), num(2.0))','arbitrary_call()')
    with pytest.raises(SourceDiagnostic) as error:
        g.import_source(text,"python")
    assert error.value.code==code


def test_manifest_cannot_restore_source_formulas_or_lose_roles(tmp_path):
    source=tmp_path/"source.xlsx"
    fixture(source)
    text,manifest=g.compile_workbook(source,"python")
    wrong=copy.deepcopy(manifest)
    wrong["model"]["sheets"][0]["cells"]["B1"]["formula"]="=999"
    with pytest.raises(SourceDiagnostic,match="restored"):
        g.lower(text,"python",wrong,{})
    wrong=copy.deepcopy(manifest)
    wrong["formula_addresses"].remove("B1")
    with pytest.raises(SourceDiagnostic,match="roles"):
        g.lower(text,"python",wrong,{})
    with pytest.raises(SourceDiagnostic,match="overrides"):
        g.lower(text,"python",manifest,{"B1":7})


@pytest.mark.parametrize("language", ["python", "rust"])
@pytest.mark.parametrize("value,arithmetic,total", [
    (None,1,2),("",1,2),(0,1,2),("4",5,2),(4,5,6),(True,2,2),
    ("bad",{"error":"#VALUE!"},2),({"error":"#REF!"},{"error":"#REF!"},{"error":"#REF!"}),
])
def test_blank_numeric_text_boolean_and_error_inputs_are_distinct(tmp_path, language, value, arithmetic, total):
    source=tmp_path/"source.xlsx"
    fixture(source)
    text,_=g.compile_workbook(source,language)
    path=tmp_path/("program.py" if language=="python" else "program.rs")
    path.write_text(text)
    values=g.execute(path,language,{"A3":value},tmp_path/"program")
    assert g.typed(values["A3"])==g.typed(value)
    assert g.typed(values["B5"])==g.typed(arithmetic)
    assert g.typed(values["B6"])==g.typed(total)


def test_cli_graph_profile(tmp_path, compiler_binary):  # noqa: F811
    source=tmp_path/"source.xlsx"
    fixture(source)
    cli=[sys.executable,str(ROOT/"tools/source_compiler.py")]
    bundle=tmp_path/"bundle"
    subprocess.run([*cli,"compile",str(source),str(bundle),"--language","rust","--profile","graph-v2"],check=True)
    first=bundle/"program.rs"
    first.write_text(edit(first.read_text(),"rust"))
    source.unlink()  # Synthetic fixture: later stages have no original OOXML to read.
    second=tmp_path/"program.py"
    subprocess.run([*cli,"translate",str(first),str(second),"--origin","rust","--destination","python"],check=True)
    output=tmp_path/"output.xlsx"
    subprocess.run([*cli,"emit",str(second),str(bundle/"manifest.json"),str(output),"--language","python","--inputs",'{"A1":7}'],check=True)
    assert import_xlsx(output).to_dict()["sheets"][0]["cells"]["B12"]["cached_value"]==83


@pytest.mark.parametrize("language", ["python", "rust"])
def test_rectangular_reversed_ranges_and_error_order(tmp_path, language):
    defaults={"A1":g.scalar(1),"B1":g.scalar(2),"A2":g.scalar("4"),"B2":g.scalar(True),
              "A3":g.scalar({"error":"#REF!"}),"B3":g.scalar({"error":"#N/A"})}
    formulas={"C1":g.from_excel(g.parse_expression("SUM(A1:B2)"),"Calc"),
              "C2":g.from_excel(g.parse_expression("SUM(B2:$A$1)"),"Calc"),
              "C3":g.from_excel(g.parse_expression("SUM(A1:B3)"),"Calc"),
              "C4":g.from_excel(g.parse_expression("SUM(10000000000000000,1,-10000000000000000)"),"Calc")}
    program=g.Program("Calc",defaults,formulas)
    path=tmp_path/("program.py" if language=="python" else "program.rs")
    path.write_text(g.generate(program,language))
    result=g.execute(path,language,{},tmp_path/"program")
    assert result["C1"]==result["C2"]==3
    assert result["C3"]=={"error":"#REF!"}
    assert result["C4"]==0  # Explicit binary64 left fold, not an Excel observation.


@pytest.mark.parametrize("language", ["python", "rust"])
def test_overflow_becomes_typed_num_error(tmp_path, language):
    program=g.Program("Calc",{"A1":g.scalar(1e308)},
                      {"B1":g.Node("mul",args=(g.Node("reference","A1"),g.Node("num",2.0)))})
    path=tmp_path/("program.py" if language=="python" else "program.rs")
    path.write_text(g.generate(program,language))
    assert g.execute(path,language,{},tmp_path/"program")["B1"]=={"error":"#NUM!"}


@pytest.mark.parametrize("language", ["python", "rust"])
def test_payload_budget_refuses_range_materialization_before_export(tmp_path, language):
    ref=g.Node("reference","Z1")
    program=g.Program("Calc",{"Z1":g.scalar("")},
                      {"A1":g.Node("sum_values",args=(g.Node("range_ref","B1:B3"),)),
                       "B1":ref,"B2":ref,"B3":ref})
    path=tmp_path/("program.py" if language=="python" else "program.rs")
    path.write_text(g.generate(program,language))
    with pytest.raises(SourceDiagnostic) as error:
        g.execute(path,language,{"Z1":"x"*400000},tmp_path/"program")
    assert error.value.code=="resource_limit"


def test_node_depth_and_source_budgets():
    node=g.Node("num",1.0)
    for _ in range(32):
        node=g.Node("positive",args=(node,))
    with pytest.raises(SourceDiagnostic,match="32 levels"):
        g.generate(g.Program("Calc",{}, {"A1":node}),"python")
    with pytest.raises(SourceDiagnostic,match="1 MiB"):
        g.import_source("x"*(g.MAX_SOURCE_BYTES+1),"python")
    with pytest.raises(SourceDiagnostic,match="blank literal"):
        g.generate(g.Program("Calc",{}, {"A1":g.Node("blank")}),"python")


@pytest.mark.parametrize("value", [{"error":[]}, {"error":"#NOT_AN_ERROR"}, float("inf"), "\ud800"])
def test_malformed_typed_inputs_have_structured_diagnostics(value):
    with pytest.raises(SourceDiagnostic) as error:
        g.encode(value)
    assert error.value.code=="invalid_input"


@pytest.mark.parametrize("constructor", ["error([])","reference(e, True)","num(True)","num(1e999)","sum_values([arbitrary_call()])"])
def test_malformed_constructors_are_refused(constructor):
    with pytest.raises(SourceDiagnostic):
        g.parse_constructor(constructor)


@pytest.mark.parametrize("language", ["python", "rust"])
@pytest.mark.parametrize("code", sorted(g.LEGACY_ERRORS))
def test_common_error_literals_roundtrip_as_typed_errors(tmp_path, compiler_binary, language, code):  # noqa: F811
    model=WorkbookModel(sheets=["Calc"])
    model.set_formula("Calc","A1","="+code)
    source=tmp_path/"source.xlsx"
    export_xlsx(model,source)
    text,manifest=g.compile_workbook(source,language)
    path=tmp_path/("program.py" if language=="python" else "program.rs")
    path.write_text(text)
    source.unlink()  # Synthetic fixture: later stages have no original OOXML to read.
    output=tmp_path/"output.xlsx"
    assert g.emit(path,language,manifest,{},output,compiler_binary)=={"A1":{"error":code}}
    assert import_xlsx(output).to_dict()["sheets"][0]["cells"]["A1"]["cached_value"]["error"]==code


@pytest.mark.parametrize("code", ["#NULL!", "#SPILL!"])
def test_remaining_error_types_require_profile_three(code):
    node=g.scalar({"error":code})
    with pytest.raises(SourceDiagnostic) as error:
        g.generate(g.Program("Calc",{}, {"A1":node}),"python")
    assert error.value.code=="unsupported_error"


@pytest.mark.parametrize("language", ["python","rust"])
@pytest.mark.parametrize("profile", [1,2])
def test_execution_pins_validated_bytes_before_a_source_path_change(tmp_path,monkeypatch,language,profile):
    if profile==1:
        compiler=tool("source_compiler")
        code=compiler.generate(compiler.expression("a1*2"),language)
    else:
        code=g.generate(g.Program("Calc",{"A1":g.scalar(2)},
                        {"B1":g.Node("mul",args=(g.Node("reference","A1"),g.scalar(2)))}),language)
    path=tmp_path/("mutable.py" if language=="python" else "mutable.rs")
    path.write_text(code)
    real_run=source_process.subprocess.run
    changed=False
    def replacing_run(command,*args,**kwargs):
        nonlocal changed
        if not changed:
            changed=True
            path.write_text("raise Exception('mutable source executed')" if language=="python" else 'compile_error!("mutable source executed");')
            assert str(path) not in command
        return real_run(command,*args,**kwargs)
    monkeypatch.setattr(source_process.subprocess,"run",replacing_run)
    if profile==1:
        assert compiler.execute(path,language,2,tmp_path/"program")==4
    else:
        assert g.execute(path,language,{},tmp_path/"program")["B1"]==4
    assert changed
