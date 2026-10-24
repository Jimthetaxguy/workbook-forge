"""AVERAGE's argument origins and counts have an explicit evidence ledger."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from workbook_forge import source_graph as g
from workbook_forge.source_contract import SourceDiagnostic
from workbook_forge.toolkit import WorkbookModel
from workbook_forge.xlsx import export_xlsx, import_xlsx
from test_compiler_benchmark import compiler_binary  # noqa: F401
from test_source_graph_v3 import equal
from test_source_graph_v4 import edit as comparison_edit, poison_formula_caches

ROOT=Path(__file__).resolve().parents[2]
CASE=json.loads((ROOT/'fixtures/source-graph.v5.json').read_text())
AVERAGES=json.loads((ROOT/'fixtures/average-cases.v1.json').read_text())


def fixture(path):
    model=WorkbookModel(sheets=[sheet['name'] for sheet in CASE['sheets']])
    for sheet in CASE['sheets']:
        for address,value in sheet.get('values',{}).items():
            model.set_value(sheet['name'],address,value)
        for address,formula in sheet.get('formulas',{}).items():
            model.set_formula(sheet['name'],address,formula)
    model.set_style('Calc','C1',number_format='0.00',bold=True)
    export_xlsx(model,path)
    poison_formula_caches(path)


def edit(source,language):
    source=comparison_edit(source,language)
    expression='average_values('+('vec!' if language=='rust' else '')+'[add(reference(e, "C1"), reference(e, "C2")), num(7.5)])'
    assert source.count(expression)==1
    return source.replace(expression,expression.replace('7.5','11.5'))


@pytest.mark.parametrize('origin,target',[('rust','rust'),('python','python'),('rust','python'),('python','rust')])
def test_all_four_routes_average_actual_edited_source_without_original(tmp_path,compiler_binary,origin,target):  # noqa: F811
    source=tmp_path/'original.xlsx'
    fixture(source)
    code,manifest=g.compile_workbook(source,origin,5)
    assert all('formula' not in cell and 'cached_value' not in cell for sheet in manifest['model']['sheets'] for cell in sheet['cells'].values())
    first=tmp_path/('first.rs' if origin=='rust' else 'first.py')
    first.write_text(edit(code,origin))
    source.unlink()
    equal(g.execute(first,origin,CASE['overrides'],tmp_path/'first-bin'),CASE['expected_after_edits'])
    second=tmp_path/('second.rs' if target=='rust' else 'second.py')
    second.write_text(g.translate(first.read_text(),origin,target))
    equal(g.execute(second,target,CASE['overrides'],tmp_path/'second-bin'),CASE['expected_after_edits'])
    changed=g.execute(second,target,{"'2026 O''Brien'!A1":7},tmp_path/'changed-bin')
    assert changed["'Calc'!C1"]==6.5
    assert changed["'Calc'!B1"]==0 and changed["'Calc'!C6"]=={'error':'#DIV/0!'}
    output=tmp_path/'output.xlsx'
    equal(g.emit(second,target,manifest,CASE['overrides'],output,compiler_binary),CASE['expected_after_edits'])
    snapshots=[import_xlsx(output).to_dict(),json.loads(subprocess.run([str(compiler_binary),'decode',str(output)],check=True,capture_output=True,text=True).stdout)]
    for snapshot in snapshots:
        for sheet in snapshot['sheets']:
            for address,cell in sheet['cells'].items():
                key=g.sheet_prefix(sheet['name'])+address
                assert g.typed(cell.get('cached_value') if 'formula' in cell else cell.get('value'))==g.typed(CASE['expected_after_edits'][key])
        assert '11.5' in snapshot['sheets'][0]['cells']['C8']['formula']
        assert snapshot['sheets'][0]['cells']['C1']['style']==manifest['model']['sheets'][0]['cells']['C1']['style']


@pytest.mark.parametrize('language',['python','rust'])
def test_average_evidence_cases_run_independently_in_source_and_both_sdks(tmp_path,compiler_binary,language):  # noqa: F811
    defaults,formulas,expected={},{},{}
    sheets=tuple('Case'+str(index) for index in range(len(AVERAGES['cases'])))
    model=WorkbookModel(sheets=list(sheets))
    for name,case in zip(sheets,AVERAGES['cases']):
        for address,value in case['cells'].items():
            defaults[g.sheet_prefix(name)+address]=g.scalar(value)
            model.set_value(name,address,value)
        address=g.sheet_prefix(name)+'Z1'
        formulas[address]=g.from_excel(g.parse_expression(case['formula']),name,version=5)
        expected[address]=case['expected']
        model.set_value(name,'Z1',None)
    program=g.Program(sheets[0],defaults,formulas,sheets,5)
    path=tmp_path/('source.py' if language=='python' else 'source.rs')
    path.write_text(g.generate(program,language))
    values=g.execute(path,language,{},tmp_path/'bin')
    for address,value in expected.items():
        assert g.typed(values[address])==g.typed(value),(address,values[address],value)
    manifest={'source_subset_version':5,'model':model.to_dict(),'formula_addresses':sorted(formulas)}
    lowered=g.lower(path.read_text(),language,manifest,{})
    ir=tmp_path/'model.json'
    ir.write_text(json.dumps(lowered))
    reports=[WorkbookModel(document=lowered).calculate(),json.loads(subprocess.run([str(compiler_binary),'calculate',str(ir)],check=True,capture_output=True,text=True).stdout)]
    for report in reports:
        assert not report['diagnostics']
        for address,value in expected.items():
            sheet,cell=address.rsplit('!',1)
            name=sheet[1:-1].replace("''", "'")
            assert g.typed(report['values'][name+'!'+cell])==g.typed(value)


@pytest.mark.parametrize('language',['python','rust'])
def test_average_counts_255_arguments_and_refuses_larger_calls(tmp_path,language):
    node=g.Node('average_values',args=tuple(g.scalar(1) for _ in range(255)))
    # The existing expression-node cap counts the call itself as well.
    path=tmp_path/('source.py' if language=='python' else 'source.rs')
    path.write_text(g.generate(g.Program('Calc',{}, {"'Calc'!A1":node},('Calc',),5),language))
    assert g.execute(path,language,{},tmp_path/'bin')["'Calc'!A1"]==1
    with pytest.raises(SourceDiagnostic):
        g.generate(g.Program('Calc',{}, {"'Calc'!A1":g.Node('average_values',args=(*node.args,g.scalar(1)))},('Calc',),5),language)
    with pytest.raises(SourceDiagnostic,match='at least one'):
        g.generate(g.Program('Calc',{}, {"'Calc'!A1":g.Node('average_values')},('Calc',),5),language)


@pytest.mark.parametrize('version',[2,3,4])
def test_prior_profiles_do_not_claim_average(version):
    with pytest.raises(SourceDiagnostic,match='profile v5'):
        g.from_excel(g.parse_expression('AVERAGE(1,2)'),'Calc',version=version)


def test_packaged_cli_module_is_an_actual_standalone_entrypoint(tmp_path,compiler_binary):  # noqa: F811
    source=tmp_path/'original.xlsx'
    fixture(source)
    cli=[sys.executable,'-m','workbook_forge.source_cli']
    bundle=tmp_path/'bundle'
    subprocess.run([*cli,'compile',str(source),str(bundle),'--language','rust'],check=True,capture_output=True)
    first=bundle/'program.rs'
    first.write_text(edit(first.read_text(),'rust'))
    source.unlink()
    second=tmp_path/'second.py'
    subprocess.run([*cli,'translate',str(first),str(second),'--origin','rust','--destination','python'],check=True,capture_output=True)
    output=tmp_path/'output.xlsx'
    subprocess.run([*cli,'emit',str(second),str(bundle/'manifest.json'),str(output),'--language','python','--inputs',json.dumps(CASE['overrides'])],check=True,capture_output=True)
    assert import_xlsx(output).to_dict()['sheets'][0]['cells']['C8']['cached_value']==12


@pytest.mark.parametrize('version,flags,expected_option',[
    (5,['--input','9'],'--inputs'),
    (1,['--input','9','--inputs','{}'],'--input;')])
def test_packaged_cli_refuses_mismatched_input_flags_before_execution(tmp_path,monkeypatch,capsys,version,flags,expected_option):
    from workbook_forge import source_cli
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'source_subset_version':version}))
    def forbidden(*args,**kwargs):
        pytest.fail('profile-incompatible inputs must not execute or read source')
    monkeypatch.setattr(g,'emit',forbidden)
    monkeypatch.setattr(source_cli,'read_source',forbidden)
    output=tmp_path/'not-created.xlsx'
    assert source_cli.main(['emit',str(tmp_path/'missing.py'),str(manifest),str(output),'--language','python',*flags])==1
    result=json.loads(capsys.readouterr().out)
    assert result['code']=='invalid_input' and expected_option in result['message']
    assert not output.exists()
