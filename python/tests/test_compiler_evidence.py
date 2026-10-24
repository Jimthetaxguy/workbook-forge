"""Evidence coverage is audited separately from implementation agreement."""
import json
from pathlib import Path

from test_compiler_benchmark import tool

ROOT=Path(__file__).resolve().parents[2]


def test_comparison_audit_classifies_every_expectation_without_an_excel_oracle():
    actual=tool('compiler_evidence').build()
    assert actual==json.loads((ROOT/'catalog/compiler-comparison-evidence.v1.json').read_text())
    assert len(actual['expectations'])==486
    assert actual['counts']=={'primary_operator_rule_plus_arithmetic':72,'inferred_from_primary_match_order':168,'local_profile_without_independent_excel_evidence':246}
    assert actual['excel_observed_count']==0
    assert all(row['excel_observed'] is False for row in actual['expectations'])


def test_blank_locale_and_signed_zero_cases_are_not_promoted_by_backend_agreement():
    actual=tool('compiler_evidence').build()
    for row in actual['expectations']:
        if row['pair'].startswith('blank') or row['pair'] in {'two-blanks','unicode-local-ordinal','ascii-case','signed-zero'}:
            assert row['evidence']=='local_profile_without_independent_excel_evidence'


def test_average_primary_conflict_remains_visible_in_the_behavior_contract():
    evidence=json.loads((ROOT/'catalog/average-evidence.v1.json').read_text())
    assert evidence['excel_observed'] is False
    assert {row['direct_bool_numeric_text_excerpt'] for row in evidence['sources']}=={'are counted','are not counted'}
    cases=json.loads((ROOT/'fixtures/average-cases.v1.json').read_text())['cases']
    direct=next(row for row in cases if row['id']=='direct-bools')
    assert direct['evidence']=='conflicted_primary_direct_policy'
