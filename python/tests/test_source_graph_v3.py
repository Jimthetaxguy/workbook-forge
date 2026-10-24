"""Independent source-v3 expectations, translated edits, and lazy-runtime gates."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

import pytest

from workbook_forge import source_graph as g
from workbook_forge.source_contract import SourceDiagnostic
from workbook_forge.toolkit import WorkbookModel
from workbook_forge.xlsx import export_xlsx, import_xlsx
from test_compiler_benchmark import compiler_binary  # noqa: F401

ROOT=Path(__file__).resolve().parents[2]
CASE=json.loads((ROOT/'fixtures/source-graph.v3.json').read_text())


def fixture(path):
    model=WorkbookModel(sheets=[sheet['name'] for sheet in CASE['sheets']])
    for sheet in CASE['sheets']:
        for address,value in sheet.get('values',{}).items():
            model.set_value(sheet['name'],address,value)
        for address,value in sheet.get('formulas',{}).items():
            model.set_formula(sheet['name'],address,value)
    model.set_style('Calc','B1',number_format='0.00',bold=True)
    export_xlsx(model,path)
    # Poison all formula caches; this newly generated fixture is not an oracle.
    with zipfile.ZipFile(path) as archive:
        parts={name:archive.read(name) for name in archive.namelist()}
    ns='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    for name in parts:
        if not name.startswith('xl/worksheets/'):
            continue
        sheet=ET.fromstring(parts[name])
        for cell in sheet.iter(ns+'c'):
            if cell.find(ns+'f') is not None:
                cell.attrib.pop('t',None)
                value=cell.find(ns+'v')
                if value is None:
                    value=ET.SubElement(cell,ns+'v')
                value.text='999999'
        parts[name]=ET.tostring(sheet)
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,content in parts.items():
            archive.writestr(name,content)


def edit(source,language):
    expression='mul(reference(e, "\'2026 O\'\'Brien\'!$A1"), num(2.0))'
    assert source.count(expression)==1
    source=source.replace(expression,expression.replace('2.0','5.0'))
    default='"\'2026 O\'\'Brien\'!A2": num(3.0),' if language=='python' else '("\'2026 O\'\'Brien\'!A2".to_string(), num(3.0)),'
    assert source.count(default)==1
    return source.replace(default,default.replace('3.0','6.0')).replace("'2026 O''Brien'!$A1","'2026 o''brien'!$A1")


def equal(actual,expected):
    assert {key:g.typed(value) for key,value in actual.items()}=={key:g.typed(value) for key,value in expected.items()}


@pytest.mark.parametrize('origin,target',[('rust','rust'),('python','python'),('rust','python'),('python','rust')])
def test_four_source_routes_preserve_multisheet_edits_without_original(tmp_path,compiler_binary,origin,target):  # noqa: F811
    source=tmp_path/'original.xlsx'
    fixture(source)
    code,manifest=g.compile_workbook(source,origin,version=3)
    assert manifest['source_subset_version']==3
    assert all('formula' not in cell and 'cached_value' not in cell for sheet in manifest['model']['sheets'] for cell in sheet['cells'].values())
    first=tmp_path/('first.rs' if origin=='rust' else 'first.py')
    first.write_text(edit(code,origin))
    source.unlink()
    equal(g.execute(first,origin,CASE['overrides'],tmp_path/'first-bin'),CASE['expected_after_edits'])
    raw=[str(tmp_path/'first-bin')] if origin=='rust' else [sys.executable,'-I',str(first)]
    wire=next(iter(CASE['overrides'])).encode().hex()+'=n:7'
    result=subprocess.run([*raw,wire],capture_output=True,text=True,check=True,cwd=tmp_path)
    assert "'Calc'!B4".encode().hex()+'\tn\t48' in result.stdout
    second=tmp_path/('second.rs' if target=='rust' else 'second.py')
    second.write_text(g.translate(first.read_text(),origin,target))
    program=g.import_source(second.read_text(),target)
    assert g.validate(program)["'Calc'!B1"]==["'2026 O''Brien'!A1"]
    equal(g.execute(second,target,CASE['overrides'],tmp_path/'second-bin'),CASE['expected_after_edits'])
    changed=g.execute(second,target,{"'2026 O''Brien'!A1":2},tmp_path/'changed-bin')
    assert changed["'Calc'!B1"]==10 and changed["'Calc'!B4"]==18 and changed["'2026 O''Brien'!B1"]==11
    output=tmp_path/'output.xlsx'
    equal(g.emit(second,target,manifest,CASE['overrides'],output,compiler_binary),CASE['expected_after_edits'])
    snapshots=[import_xlsx(output).to_dict(),json.loads(subprocess.run([str(compiler_binary),'decode',str(output)],capture_output=True,text=True,check=True).stdout)]
    for snapshot in snapshots:
        assert [sheet['name'] for sheet in snapshot['sheets']]==[sheet['name'] for sheet in CASE['sheets']]
        for sheet in snapshot['sheets']:
            for address,cell in sheet['cells'].items():
                key=g.sheet_prefix(sheet['name'])+address
                assert g.typed(cell.get('cached_value') if 'formula' in cell else cell.get('value'))==g.typed(CASE['expected_after_edits'][key])
        assert '5.0' in snapshot['sheets'][0]['cells']['B1']['formula']
        assert snapshot['sheets'][0]['cells']['B1']['style']==manifest['model']['sheets'][0]['cells']['B1']['style']


@pytest.mark.parametrize('language',['python','rust'])
@pytest.mark.parametrize('code',sorted(g.ERRORS))
def test_nine_error_literals_and_iferror_scalar_contract(tmp_path,language,code):
    program=g.Program('Calc',{}, {"'Calc'!A1":g.scalar({'error':code}),"'Calc'!A2":g.Node('if_error',args=(g.scalar({'error':code}),g.scalar(7)))},('Calc',),3)
    path=tmp_path/('source.py' if language=='python' else 'source.rs')
    path.write_text(g.generate(program,language))
    equal(g.execute(path,language,{},tmp_path/'bin'),{"'Calc'!A1":{'error':code},"'Calc'!A2":7})


def test_python_runtime_lazy_branches_and_host_errors():
    scope={}
    exec(g.template('python',3),scope)
    engine=scope['Engine']({}, {}, ['Calc'])
    calls=[]
    def unused(_):
        calls.append('bad')
        raise RuntimeError('unselected branch executed')
    def selected(_):
        calls.append('good')
        return scope['num'](7)
    assert scope['if_value'](scope['boolean'](True),engine,selected,unused)==('n',7)
    assert scope['if_value'](scope['boolean'](False),engine,unused,selected)==('n',7)
    assert scope['if_error'](scope['num'](8),engine,unused)==('n',8)
    assert scope['if_error'](scope['error']('#N/A'),engine,selected)==('n',7)
    with pytest.raises(RuntimeError):
        scope['if_error'](scope['error']('#N/A'),engine,unused)
    assert calls==['good','good','good','bad']


def test_rust_runtime_lazy_branches_and_panics(tmp_path):
    runtime=g.template('rust',3)
    source=tmp_path/'lazy.rs'
    source.write_text(runtime+'''
fn formula(_: &str, _: &mut Engine)->V {panic!("no formulas")}
fn formula_keys()-> &'static [&'static str] {&[]}
fn is_formula(_: &str)->bool {false}
fn main(){
 let mut e=Engine{sheets:BTreeMap::new(),context:String::new(),defaults:BTreeMap::new(),cache:BTreeMap::new(),active:BTreeSet::new(),cache_bytes:0,materialized_bytes:0};
 assert!(matches!(if_value(boolean(true),&mut e,|_|num(7.0),|_|panic!("eager false")),V::N(7.0)));
 assert!(matches!(if_value(boolean(false),&mut e,|_|panic!("eager true"),|_|num(7.0)),V::N(7.0)));
 assert!(matches!(if_error(num(8.0),&mut e,|_|panic!("eager fallback")),V::N(8.0)));
 assert!(std::panic::catch_unwind(std::panic::AssertUnwindSafe(||if_error(error("#N/A"),&mut e,|_|panic!("host error")))).is_err());
}
''')
    subprocess.run(['rustc','--edition=2024','--crate-name','lazy_profile',str(source),'-o',str(tmp_path/'lazy')],capture_output=True,check=True)
    subprocess.run([str(tmp_path/'lazy')],capture_output=True,check=True)


@pytest.mark.parametrize('reference',["'Missing'!A1","'[external.xlsx]Calc'!A1","'Calc'!A1:'Other'!A1"])
def test_unknown_external_and_three_dimensional_references_refused(reference):
    node=g.Node('range_ref' if ':' in reference else 'reference',reference)
    root=g.Node('sum_values',args=(node,)) if ':' in reference else node
    with pytest.raises(SourceDiagnostic) as error:
        g.validate(g.Program('Calc',{}, {"'Calc'!A1":root},('Calc','Other'),3))
    assert error.value.code=='unsupported_reference'


def test_cross_sheet_cycle_and_manifest_formula_injection_refused(tmp_path):
    root=g.Node('reference',"'Other'!A1")
    program=g.Program('Calc',{}, {"'Calc'!A1":root,"'Other'!A1":g.Node('reference',"'Calc'!A1")},('Calc','Other'),3)
    with pytest.raises(SourceDiagnostic,match='circular'):
        g.generate(program,'python')
    source=tmp_path/'original.xlsx'
    fixture(source)
    code,manifest=g.compile_workbook(source,'python',3)
    bad=copy.deepcopy(manifest)
    bad['model']['sheets'][1]['cells']['A1']['formula']='=999'
    bad['model']['sheets'][1]['cells']['A1']['value']=None
    with pytest.raises(SourceDiagnostic,match='cannot be restored'):
        g.lower(code,'python',bad,{})


@pytest.mark.parametrize('language',['python','rust'])
def test_case_insensitive_quoted_references_and_anchor_retention(tmp_path,language):
    root=g.from_excel(g.parse_expression("'o''brien'!$A1+'O''Brien'!A$2"),'Calc',version=3)
    program=g.Program('Calc',{"'O''Brien'!A1":g.scalar(2),"'O''Brien'!A2":g.scalar(3)}, {"'Calc'!A1":root},('Calc',"O'Brien"),3)
    path=tmp_path/('source.py' if language=='python' else 'source.rs')
    path.write_text(g.generate(program,language))
    assert g.execute(path,language,{},tmp_path/'bin')["'Calc'!A1"]==5
    assert g.formula_text(root)=="('o''brien'!$A1+'O''Brien'!A$2)"


def test_invalid_lambda_edit_is_refused():
    with pytest.raises(SourceDiagnostic):
        g.parse_constructor('if_error(num(1.0), e, lambda x: num(2.0))')


def test_source_package_expansion_and_duplicate_parts_refused_before_import(tmp_path,monkeypatch):
    def forbidden(*args,**kwargs):
        pytest.fail('import must not run before package bounds')
    monkeypatch.setattr(g,'import_xlsx',forbidden)
    path=tmp_path/'oversized.xlsx'
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('expanded.xml',' '*(g.MAX_SOURCE_BYTES+1))
    with pytest.raises(SourceDiagnostic,match='expanded source package'):
        g.compile_workbook(path,'python',3)
    with zipfile.ZipFile(path,'w') as archive:
        archive.writestr('duplicate.xml','one')
        with pytest.warns(UserWarning):
            archive.writestr('duplicate.xml','two')
    with pytest.raises(SourceDiagnostic,match='duplicate OOXML'):
        g.compile_workbook(path,'python',3)


def test_rust_execution_does_not_run_retained_binary_artifact(tmp_path,monkeypatch):
    from workbook_forge import source_process
    program=g.Program('Calc',{}, {"'Calc'!A1":g.scalar(7)},('Calc',),3)
    path=tmp_path/'program.rs'
    path.write_text(g.generate(program,'rust'))
    output=tmp_path/'retained'
    original=source_process.subprocess.run
    def replace_retained(command,**kwargs):
        if command[0]!='rustc':
            assert Path(command[0])!=output
            output.write_bytes(b'invalid replaced artifact')
        return original(command,**kwargs)
    monkeypatch.setattr(source_process.subprocess,'run',replace_retained)
    assert g.execute(path,'rust',{},output)=={"'Calc'!A1":7}
    assert output.read_bytes()==b'invalid replaced artifact'


@pytest.mark.parametrize('kind',['file','symlink','dangling'])
def test_rust_binary_artifact_refuses_existing_targets_non_destructively(tmp_path,kind):
    program=g.Program('Calc',{}, {"'Calc'!A1":g.scalar(7)},('Calc',),3)
    path=tmp_path/'program.rs'
    path.write_text(g.generate(program,'rust'))
    victim=tmp_path/'victim'
    victim.write_bytes(b'preserve')
    artifact=tmp_path/'artifact'
    if kind=='file':
        artifact.write_bytes(b'preserve artifact')
    else:
        artifact.symlink_to(victim if kind=='symlink' else tmp_path/'missing')
    with pytest.raises(SourceDiagnostic) as error:
        g.execute(path,'rust',{},artifact)
    assert error.value.code=='artifact_exists'
    assert victim.read_bytes()==b'preserve'
    assert not (tmp_path/'missing').exists()


def test_graph_v3_cli_consumes_translated_artifact(tmp_path,compiler_binary):  # noqa: F811
    source=tmp_path/'original.xlsx'
    fixture(source)
    cli=[sys.executable,str(ROOT/'tools/source_compiler.py')]
    bundle=tmp_path/'bundle'
    subprocess.run([*cli,'compile',str(source),str(bundle),'--language','rust','--profile','graph-v3'],check=True,capture_output=True)
    first=bundle/'program.rs'
    first.write_text(edit(first.read_text(),'rust'))
    source.unlink()
    second=tmp_path/'program.py'
    subprocess.run([*cli,'translate',str(first),str(second),'--origin','rust','--destination','python'],check=True,capture_output=True)
    output=tmp_path/'output.xlsx'
    subprocess.run([*cli,'emit',str(second),str(bundle/'manifest.json'),str(output),'--language','python','--inputs',json.dumps(CASE['overrides'])],check=True,capture_output=True)
    assert import_xlsx(output).to_dict()['sheets'][0]['cells']['B4']['cached_value']==48


@pytest.mark.parametrize('language',['python','rust'])
def test_scalar_selection_values_coerce_and_blank_iferror_returns_text(tmp_path,language):
    formulas={"'Calc'!B1":'SUM(IFERROR(A1,0))',"'Calc'!B2":'SUM(IF(TRUE,A1,0))',
              "'Calc'!B3":'IFERROR(A2,9)',"'Calc'!B4":'IFERROR(1/0,A2)'}
    program=g.Program('Calc',{"'Calc'!A1":g.scalar('4'),"'Calc'!A2":g.scalar(None)},
        {address:g.from_excel(g.parse_expression(formula),'Calc',version=3) for address,formula in formulas.items()},('Calc',),3)
    path=tmp_path/('source.py' if language=='python' else 'source.rs')
    path.write_text(g.generate(program,language))
    values=g.execute(path,language,{},tmp_path/'bin')
    assert values["'Calc'!B1"]==values["'Calc'!B2"]==4
    assert values["'Calc'!B3"]==values["'Calc'!B4"]==''


@pytest.mark.parametrize('language',['python','rust'])
def test_profile_three_changed_runtime_and_unknown_unselected_function_refused(language):
    program=g.Program('Calc',{}, {"'Calc'!A1":g.scalar(7)},('Calc',),3)
    source=g.generate(program,language)
    with pytest.raises(SourceDiagnostic,match='runtime scaffold'):
        g.import_source(source.replace('Embedded independent','Untrusted modified'),language)
    with pytest.raises(SourceDiagnostic,match='cannot lower'):
        g.from_excel(g.parse_expression('IFERROR(CUBEVALUE(1),7)'),'Calc',version=3)


def test_malformed_profile_three_sheet_identity_is_structured():
    program=g.Program('Calc',{}, {"'Calc'!A1":g.scalar(7)},('Calc',),3)
    source=g.generate(program,'python')
    identity=json.dumps(program.sheets,ensure_ascii=False).encode().hex()
    with pytest.raises(SourceDiagnostic,match='identity list'):
        g.import_source(source.replace('WFG3 SHEET '+identity,'WFG3 SHEET '+b'null'.hex()),'python')


@pytest.mark.parametrize('language',['python','rust'])
def test_constructor_translation_does_not_rewrite_tokens_inside_sheet_names(tmp_path,language):
    name='Résumé true false |e|'
    root=g.from_excel(g.parse_expression("'Résumé true false |e|'!$A$1+1"),'Calc',version=3)
    program=g.Program('Calc',{g.sheet_prefix(name)+'A1':g.scalar(2)}, {"'Calc'!A1":root},('Calc',name),3)
    source=g.generate(program,language)
    assert g.import_source(source,language)==program
    other='rust' if language=='python' else 'python'
    assert g.import_source(g.translate(source,language,other),other)==program
    path=tmp_path/('source.py' if language=='python' else 'source.rs')
    path.write_text(source)
    assert g.execute(path,language,{},tmp_path/'bin')["'Calc'!A1"]==3


def test_malformed_package_reports_structured_diagnostic(tmp_path):
    path=tmp_path/'bad.xlsx'
    path.write_bytes(b'not an OOXML zip')
    with pytest.raises(SourceDiagnostic) as error:
        g.compile_workbook(path,'python',3)
    assert error.value.code=='invalid_package'


def test_repeated_ranges_cannot_bypass_expansion_budget_through_deduplication():
    member=g.Node('range_ref','A1:A100000')
    program=g.Program('Calc',{}, {'B1':g.Node('sum_values',args=(member,member))})
    with pytest.raises(SourceDiagnostic,match='repeated range expansion'):
        g.generate(program,'python')


@pytest.mark.parametrize('language',['python','rust'])
def test_repeated_reference_payloads_are_bounded_before_sum_argument_accumulation(tmp_path,language):
    program=g.Program('Calc',{"'Calc'!A1":g.scalar('x'*100000)},
        {"'Calc'!B1":g.Node('sum_values',args=tuple(g.Node('reference','A1') for _ in range(11)))},('Calc',),3)
    path=tmp_path/('source.py' if language=='python' else 'source.rs')
    path.write_text(g.generate(program,language))
    with pytest.raises(SourceDiagnostic) as error:
        g.execute(path,language,{},tmp_path/'bin')
    assert error.value.code=='resource_limit'
