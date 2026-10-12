import pytest

from benchmarks.mechanism_ablations import run, simulation_comparison, temporal_comparison


@pytest.mark.asyncio
async def test_time_scope_challenge_has_positive_and_negative_controls():
    report = await temporal_comparison()
    assert report['methods']['snapshot'] == {'correct': 4, 'false_historical_normal': 0}
    assert report['methods']['no_time_view'] == {'correct': 2, 'false_historical_normal': 2}
    assert all(r['source_unchanged'] and r['error'] is None for r in report['rows'])


def test_recomputation_rejects_arithmetic_and_rule_errors_but_keeps_business_boundary_visible():
    report = simulation_comparison()
    assert report['methods']['program_recompute'] == {'false_acceptance': 1, 'correct': 5}
    assert report['methods']['trust_proposal_without_recompute'] == {'false_acceptance': 5, 'correct': 1}
    semantic = next(r for r in report['rows'] if r['case_id'] == 's6' and r['method'] == 'program_recompute')
    assert semantic['accepted'] and semantic['false_acceptance']
    cents = next(r for r in report['rows'] if r['case_id'] == 's2' and r['method'] == 'program_recompute')
    assert cents['verification']['actual_delta'] == {'1405': '-100.00', '2202': '100.00'}
    assert not cents['verification']['declared_effect_matches']
    assert all(r['source_unchanged'] for r in report['rows'])


@pytest.mark.asyncio
async def test_report_never_represents_offline_challenges_as_a_model_gain():
    report = await run()
    assert report['model_requests'] == 0 and report['all_sources_unchanged']
    assert any('remain unmeasured' in item for item in report['limitations'])
