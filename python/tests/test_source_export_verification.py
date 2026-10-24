"""A stale or incompatible writer must not silently publish incorrect caches/formulas."""
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET
import zipfile

import pytest

from workbook_forge import source_graph as g
from workbook_forge.source_contract import SourceDiagnostic
from workbook_forge.toolkit import WorkbookModel
from workbook_forge.xlsx import export_xlsx


@pytest.mark.parametrize('mutation',['wrong-cache','rewritten-formula'])
def test_checked_export_verifies_writer_result_before_publication(tmp_path,monkeypatch,mutation):
    model=WorkbookModel(sheets=['Calc'])
    model.set_formula('Calc','A1','=AVERAGE(TRUE,3)')
    original=tmp_path/'original.xlsx'
    export_xlsx(model,original)
    code,manifest=g.compile_workbook(original,'rust',5)
    source=tmp_path/'program.rs'
    source.write_text(code)
    original.unlink()
    real_run=g.subprocess.run
    def stale_writer(command,**kwargs):
        if len(command)>1 and command[1]=='emit-fresh':
            ir=Path(command[2])
            target=Path(command[3])
            export_xlsx(WorkbookModel(document=json.loads(ir.read_text())),target)
            with zipfile.ZipFile(target) as archive:
                parts={name:archive.read(name) for name in archive.namelist()}
            namespace='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
            sheet=ET.fromstring(parts['xl/worksheets/sheet1.xml'])
            if mutation=='wrong-cache':
                sheet.find('.//'+namespace+'v').text='999'
            else:
                sheet.find('.//'+namespace+'f').text='SUM(TRUE,3)'
                sheet.find('.//'+namespace+'v').text='2'
            parts['xl/worksheets/sheet1.xml']=ET.tostring(sheet)
            with zipfile.ZipFile(target,'w') as archive:
                for name,content in parts.items():
                    archive.writestr(name,content)
            return subprocess.CompletedProcess(command,0,stdout=b'',stderr=b'')
        return real_run(command,**kwargs)
    monkeypatch.setattr(g.subprocess,'run',stale_writer)
    output=tmp_path/'should-not-publish.xlsx'
    with pytest.raises(SourceDiagnostic) as error:
        g.emit(source,'rust',manifest,{},output,Path('fake-local-writer'))
    assert error.value.code=='backend_mismatch'
    assert not output.exists()
