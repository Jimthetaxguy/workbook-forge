"""Adversarial checks for the bounded compiler contract, not arbitrary-code execution."""
import xml.etree.ElementTree as ET
import zipfile

import pytest

from workbook_forge import source_graph as g
from workbook_forge.source_contract import SourceDiagnostic
from test_source_graph_v3 import fixture

NS='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def mutate(path,part,change):
    with zipfile.ZipFile(path) as archive:
        parts={name:archive.read(name) for name in archive.namelist()}
    tree=ET.fromstring(parts[part])
    change(tree)
    parts[part]=ET.tostring(tree)
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,content in parts.items():
            archive.writestr(name,content)


@pytest.mark.parametrize('part,tag',[
    ('xl/worksheets/sheet2.xml','sheetData'),
    ('xl/worksheets/sheet1.xml','cols'),
    ('xl/workbook.xml','sheets')])
def test_unmodeled_container_metadata_is_refused(part,tag,tmp_path):
    path=tmp_path/'original.xlsx'
    fixture(path)
    def change(tree):
        node=tree.find(NS+tag)
        if node is None:
            node=ET.SubElement(tree,NS+tag)
        node.set('unmodeledMetadata','retain-me')
    mutate(path,part,change)
    with pytest.raises(SourceDiagnostic) as error:
        g.compile_workbook(path,'python',4)
    assert error.value.code=='unsupported_fidelity'


def test_unused_external_relationship_is_refused_without_contact(tmp_path):
    path=tmp_path/'original.xlsx'
    fixture(path)
    def change(tree):
        ET.SubElement(tree,'{http://schemas.openxmlformats.org/package/2006/relationships}Relationship',
            {'Id':'unusedExternal','Type':'http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink','TargetMode':'External','Target':'https://example.invalid/not-contacted'})
    mutate(path,'xl/_rels/workbook.xml.rels',change)
    with pytest.raises(SourceDiagnostic) as error:
        g.compile_workbook(path,'python',4)
    assert error.value.code=='unsupported_fidelity'


@pytest.mark.parametrize('version',[2.0,True,'4'])
def test_program_profile_requires_an_integer_contract(version):
    program=g.Program('Calc',{}, {'A1':g.scalar(1)},(),version)
    with pytest.raises(SourceDiagnostic) as error:
        g.generate(program,'python')
    assert error.value.code=='unsupported_version'


@pytest.mark.parametrize('character',['\u0085','\u2028','\u2029'])
def test_sheet_line_boundaries_cannot_generate_unimportable_source(character):
    name='Sheet'+character+'Name'
    program=g.Program(name,{}, {g.sheet_prefix(name)+'A1':g.scalar(1)},(name,),4)
    with pytest.raises(SourceDiagnostic) as error:
        g.generate(program,'python')
    assert error.value.code=='unsupported_reference'


@pytest.mark.parametrize('language',['python','rust'])
def test_comparison_scaffold_and_unknown_constructors_never_execute(tmp_path,monkeypatch,language):
    program=g.Program('Calc',{}, {"'Calc'!A1":g.Node('equal',args=(g.scalar(1),g.scalar(1)))},('Calc',),4)
    path=tmp_path/('source.py' if language=='python' else 'source.rs')
    path.write_text(g.generate(program,language).replace('equal(num(1.0), num(1.0))','arbitrary_call(num(1.0), num(1.0))'))
    def forbidden(*args,**kwargs):
        pytest.fail('invalid source must not reach process execution')
    monkeypatch.setattr(g,'run_validated_source',forbidden)
    with pytest.raises(SourceDiagnostic):
        g.execute(path,language,{},tmp_path/'unused')
