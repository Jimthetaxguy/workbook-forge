"""Audit comparison expectations without using an evaluator as the oracle."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OPERATORS=('=','<>','<','<=','>','>=')
PRIMARY={'numbers','equal-numbers','negative-number-zero'}
INFERRED={'number-bool','false-zero','false-true','text-number','number-text','empty-text-zero','text-bool'}


def build():
    data=(ROOT/'fixtures/comparison-cases.v1.json').read_bytes()
    cases=json.loads(data)
    rows=[]
    for case in cases['pairs']:
        category='primary_operator_rule_plus_arithmetic' if case['id'] in PRIMARY else 'inferred_from_primary_match_order' if case['id'] in INFERRED else 'local_profile_without_independent_excel_evidence'
        forms=[('reference','reference')]
        if case['left'] is not None and case['right'] is not None:
            forms.append(('literal','literal'))
        if case['right'] is not None:
            forms.append(('reference','literal'))
        if case['left'] is not None:
            forms.append(('literal','reference'))
        for left,right in forms:
            for operator in OPERATORS:
                order=case['order']
                expected={'error':order} if isinstance(order,str) else {'=':order==0,'<>':order!=0,'<':order<0,'<=':order<=0,'>':order>0,'>=':order>=0}[operator]
                rows.append({'id':case['id']+':'+left+'-'+right+':'+operator,'pair':case['id'],'left_form':left,'right_form':right,'operator':operator,'expected':expected,'evidence':category,'excel_observed':False})
    assert len(rows)==486 and len({row['id'] for row in rows})==486
    counts=dict(sorted(Counter(row['evidence'] for row in rows).items()))
    assert counts=={'primary_operator_rule_plus_arithmetic':72,'inferred_from_primary_match_order':168,'local_profile_without_independent_excel_evidence':246}
    return {'evidence_version':1,'matrix_sha256':hashlib.sha256(data).hexdigest(),'expectation_count':len(rows),'counts':counts,
        'definitions':{'primary_operator_rule_plus_arithmetic':'Microsoft defines numerical operator meanings; expected values follow independent elementary arithmetic, not an observed Excel run.',
            'inferred_from_primary_match_order':'MATCH documents a compatible sort order. Applying it to operators is inference, not direct operator coercion evidence.',
            'local_profile_without_independent_excel_evidence':'Independently authored expectation with SDK/runtime agreement. Blank adaptation, collation, signed-zero/error edge cases lack independent Excel evidence.'},
        'primary_sources':[{'url':'https://support.microsoft.com/en-us/excel/calculation-operators-and-precedence-in-excel','retrieved':'2026-10-23'},
                           {'url':'https://support.microsoft.com/en-us/excel/functions/match-function','retrieved':'2026-10-23'}],
        'excel_observed_count':0,'agreement_is_not_oracle':True,'expectations':rows}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--output',type=Path,default=ROOT/'catalog/compiler-comparison-evidence.v1.json')
    args=parser.parse_args()
    text=json.dumps(build(),indent=2,ensure_ascii=False)+'\n'
    if args.check:
        if args.output.read_text()!=text:
            raise SystemExit('comparison evidence audit is stale')
    else:
        args.output.write_text(text)


if __name__=='__main__':
    main()
