"""Typed comparison matrix and edited four-route compiler conformance."""
import json
from pathlib import Path
import subprocess

import pytest

from workbook_forge import ErrorValue, evaluate
from workbook_forge import source_graph as g
from workbook_forge.source_contract import SourceDiagnostic
from workbook_forge.toolkit import WorkbookModel
from workbook_forge.xlsx import export_xlsx, import_xlsx
from test_compiler_benchmark import compiler_binary  # noqa: F401
from test_source_graph_v3 import equal

ROOT=Path(__file__).resolve().parents[2]
CASE=json.loads((ROOT/'fixtures/source-graph.v4.json').read_text())
MATRIX=json.loads((ROOT/'fixtures/comparison-cases.v1.json').read_text())


def fixture(path):
    model=WorkbookModel(sheets=[sheet['name'] for sheet in CASE['sheets']])
    for sheet in CASE['sheets']:
        for address,value in sheet.get('values',{}).items():
            model.set_value(sheet['name'],address,value)
        for address,formula in sheet.get('formulas',{}).items():
            model.set_formula(sheet['name'],address,formula)
    model.set_style('Calc','B1',number_format='0.00',bold=True)
    export_xlsx(model,path)
    poison_formula_caches(path)


def poison_formula_caches(path):
    # Reuse the package-cache poison operation without changing expected data.
    import xml.etree.ElementTree as ET
    import zipfile
    with zipfile.ZipFile(path) as archive:
        parts={name:archive.read(name) for name in archive.namelist()}
    ns='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    for name in parts:
        if name.startswith('xl/worksheets/'):
            sheet=ET.fromstring(parts[name])
            for cell in sheet.iter(ns+'c'):
                if cell.find(ns+'f') is not None:
                    cell.attrib.pop('t',None)
                    node=cell.find(ns+'v')
                    if node is None:
                        node=ET.SubElement(cell,ns+'v')
                    node.text='999999'
            parts[name]=ET.tostring(sheet)
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,content in parts.items():
            archive.writestr(name,content)


def edit(source,language):
    expression='mul(reference(e, "\'2026 O\'\'Brien\'!$A1"), num(2.0))'
    condition='greater_equal(reference(e, "\'2026 O\'\'Brien\'!$A1"), num(5.0))'
    default='"\'2026 O\'\'Brien\'!A2": num(3.0),' if language=='python' else '("\'2026 O\'\'Brien\'!A2".to_string(), num(3.0)),'
    assert all(source.count(text)==1 for text in (expression,condition,default))
    return source.replace(expression,expression.replace('2.0','5.0')).replace(condition,condition.replace('5.0','8.0')).replace(default,default.replace('3.0','6.0')).replace("'2026 O''Brien'!$A1","'2026 o''brien'!$A1")


@pytest.mark.parametrize('origin,target',[('rust','rust'),('python','python'),('rust','python'),('python','rust')])
def test_four_routes_compare_and_preserve_edits_without_original(tmp_path,compiler_binary,origin,target):  # noqa: F811
    source=tmp_path/'original.xlsx'
    fixture(source)
    code,manifest=g.compile_workbook(source,origin,4)
    assert all('formula' not in cell and 'cached_value' not in cell for sheet in manifest['model']['sheets'] for cell in sheet['cells'].values())
    first=tmp_path/('first.rs' if origin=='rust' else 'first.py')
    first.write_text(edit(code,origin))
    source.unlink()
    equal(g.execute(first,origin,CASE['overrides'],tmp_path/'first-bin'),CASE['expected_after_edits'])
    second=tmp_path/('second.rs' if target=='rust' else 'second.py')
    second.write_text(g.translate(first.read_text(),origin,target))
    equal(g.execute(second,target,CASE['overrides'],tmp_path/'second-bin'),CASE['expected_after_edits'])
    changed=g.execute(second,target,{"'2026 O''Brien'!A1":7},tmp_path/'changed-bin')
    assert changed["'Calc'!B1"]==0 and changed["'2026 O''Brien'!B1"]==1
    assert changed["'Calc'!B3"]=={'error':'#DIV/0!'}
    assert changed["'Calc'!B4"]==12
    output=tmp_path/'output.xlsx'
    equal(g.emit(second,target,manifest,CASE['overrides'],output,compiler_binary),CASE['expected_after_edits'])
    snapshots=[import_xlsx(output).to_dict(),json.loads(subprocess.run([str(compiler_binary),'decode',str(output)],check=True,capture_output=True,text=True).stdout)]
    for snapshot in snapshots:
        assert [sheet['name'] for sheet in snapshot['sheets']]==[sheet['name'] for sheet in CASE['sheets']]
        for sheet in snapshot['sheets']:
            for address,cell in sheet['cells'].items():
                key=g.sheet_prefix(sheet['name'])+address
                assert g.typed(cell.get('cached_value') if 'formula' in cell else cell.get('value'))==g.typed(CASE['expected_after_edits'][key])
        formula=snapshot['sheets'][0]['cells']['B1']['formula']
        assert '>=8.0' in formula and '*5.0' in formula and '$A1' in formula
        assert snapshot['sheets'][0]['cells']['B1']['style']==manifest['model']['sheets'][0]['cells']['B1']['style']


@pytest.mark.parametrize('language',['python','rust'])
def test_explicit_typed_comparison_matrix_for_literals_and_references(tmp_path,language,compiler_binary):  # noqa: F811
    defaults,formulas,expected={},{},{}
    for row_index,row in enumerate(MATRIX['pairs'],1):
        a,b=g.scalar(row['left']),g.scalar(row['right'])
        defaults[f"'Calc'!A{row_index}"]=a
        defaults[f"'Calc'!B{row_index}"]=b
        variants=[(g.Node('reference',f'A{row_index}'),g.Node('reference',f'B{row_index}'))]
        if a.kind!='blank' and b.kind!='blank':
            variants.append((a,b))
        if b.kind!='blank':
            variants.append((g.Node('reference',f'A{row_index}'),b))
        if a.kind!='blank':
            variants.append((a,g.Node('reference',f'B{row_index}')))
        for variant,(left,right) in enumerate(variants):
            for operator_index,(operator,name) in enumerate(g.COMPARISONS.items()):
                address=f"'Calc'!{g.CellReference(1,3+variant*6+operator_index).render()[:-1]}{row_index}"
                formulas[address]=g.Node(name,args=(left,right))
                order=row['order']
                expected[address]={'error':order} if isinstance(order,str) else {'=':order==0,'<>':order!=0,'<':order<0,'<=':order<=0,'>':order>0,'>=':order>=0}[operator]
    program=g.Program('Calc',defaults,formulas,('Calc',),4)
    path=tmp_path/('source.py' if language=='python' else 'source.rs')
    path.write_text(g.generate(program,language))
    actual=g.execute(path,language,{},tmp_path/'bin')
    for address,value in expected.items():
        assert g.typed(actual[address])==g.typed(value),(address,actual[address],value)
    # The same behavioral specification also checks both existing SDK backends.
    model=WorkbookModel(sheets=['Calc'])
    for address,node in defaults.items():
        model.set_value('Calc',address.rsplit('!',1)[1],g.scalar_value(node))
    for address in formulas:
        model.set_value('Calc',address.rsplit('!',1)[1],None)
    manifest={'source_subset_version':4,'model':model.to_dict(),'formula_addresses':sorted(formulas)}
    lowered=g.lower(path.read_text(),language,manifest,{})
    ir=tmp_path/'matrix.json'
    ir.write_text(json.dumps(lowered))
    reports=[WorkbookModel(document=lowered).calculate(),json.loads(subprocess.run([str(compiler_binary),'calculate',str(ir)],check=True,capture_output=True,text=True).stdout)]
    for report in reports:
        assert not report['diagnostics']
        for address,value in expected.items():
            assert g.typed(report['values']['Calc!'+address.rsplit('!',1)[1]])==g.typed(value)
    for row in MATRIX['pairs']:
        def value(item):
            return ErrorValue(item['error']) if isinstance(item,dict) else item
        for operator in g.COMPARISONS:
            actual=evaluate('=A1'+operator+'B1',{'A1':value(row['left']),'B1':value(row['right'])})
            order=row['order']
            if isinstance(order,str):
                assert isinstance(actual,ErrorValue) and actual.code==order
            else:
                assert type(actual) is bool and actual=={'=':order==0,'<>':order!=0,'<':order<0,'<=':order<=0,'>':order>0,'>=':order>=0}[operator]


@pytest.mark.parametrize('version',[2,3])
def test_prior_profiles_refuse_new_comparison_nodes(version):
    key='A1' if version==2 else "'Calc'!A1"
    with pytest.raises(SourceDiagnostic,match='profile v4'):
        g.generate(g.Program('Calc',{}, {key:g.Node('equal',args=(g.scalar(1),g.scalar(1)))},() if version==2 else ('Calc',),version),'python')


def test_profile_four_cli(tmp_path,compiler_binary):  # noqa: F811
    source=tmp_path/'original.xlsx'
    fixture(source)
    import sys
    cli=[sys.executable,str(ROOT/'tools/source_compiler.py')]
    bundle=tmp_path/'bundle'
    subprocess.run([*cli,'compile',str(source),str(bundle),'--language','rust','--profile','graph-v4'],check=True,capture_output=True)
    first=bundle/'program.rs'
    first.write_text(edit(first.read_text(),'rust'))
    source.unlink()
    second=tmp_path/'second.py'
    subprocess.run([*cli,'translate',str(first),str(second),'--origin','rust','--destination','python'],check=True,capture_output=True)
    output=tmp_path/'output.xlsx'
    subprocess.run([*cli,'emit',str(second),str(bundle/'manifest.json'),str(output),'--language','python','--inputs',json.dumps(CASE['overrides'])],check=True,capture_output=True)
    assert import_xlsx(output).to_dict()['sheets'][0]['cells']['B1']['cached_value']==45
