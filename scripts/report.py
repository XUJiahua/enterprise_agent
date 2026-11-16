#!/usr/bin/env python3
"""Fail closed when artifacts are missing, incomplete, or disagree with expectations."""
import json
from pathlib import Path
import sys
root=Path(sys.argv[1] if len(sys.argv)>1 else 'reports')
rows=[]
for route in ['baseline','zenstack','guarded']:
    data=json.loads((root/route/'results.json').read_text())
    results=data['results'];control=results[0]
    assert control['mutation']=='control' and control['passed'] and len(control['cases'])==16 and all(c['passed'] for c in control['cases'])
    ordinary=[r for r in results if r['mutation'] not in ('control','disable-policy')]
    assert len(ordinary)==5
    for result in results:
        assert all(c.get('kind')!='harness_error' for c in result['cases'])
    blocked=sum(r['passed'] for r in ordinary)
    assert blocked==(0 if route=='baseline' else 5)
    if route=='zenstack':assert any(r['mutation']=='disable-policy' and not r['passed'] for r in results)
    rows.append((route,f"{sum(c['passed'] for c in control['cases'])}/16",f'{blocked}/5'))
startup=json.loads((root/'guarded/startup-probe.json').read_text());assert len(startup)==2 and all(p['passed'] for p in startup)
probe=json.loads((root/'guarded/db-probe.json').read_text());assert probe['ok'] and probe['checks']==6
for route in ['baseline','zenstack','guarded']:
    browser=json.loads((root/'browser'/route/'results.json').read_text())
    assert browser['stats']['expected']==6 and not any(browser['stats'].get(k,0) for k in ['unexpected','flaky','skipped'])
smoke=json.loads((root/'benchmark-smoke.json').read_text());assert smoke['ok'] and smoke['kind']=='synthetic-smoke' and smoke['evaluations']==4
benchmark=json.loads((root/'benchmark/summary.json').read_text());assert benchmark['kind']=='synthetic-smoke' and len(benchmark['rows'])==3
assert all(row['missing']==0 and row['evaluated']==1 and row['first_pass']==0 for row in benchmark['rows'])
runner=json.loads((root/'runner-smoke.json').read_text());assert runner['ok'] and runner['kind']=='synthetic-smoke' and runner['evaluations']==1
access=json.loads((root/'model-access.json').read_text());assert access['model_execution_attempted'] is False
configured=access['openai_api_key_configured_for_run']
assert configured is None or type(configured) is bool
research=json.loads((root/'research/followup.json').read_text());assert research['new_model_generations']==0 and research['frozen_replay_candidates']==9 and research['frozen_replay_cases']==195 and research['posthoc_cases']==45
audit=json.loads((root/'research/pilot-audit.json').read_text());assert audit['manifest_files_verified']==139 and len(audit['trials'])==9
phase2=json.loads((root/'phase2-controls/summary.json').read_text())
assert phase2['kind']=='synthetic-smoke' and phase2['model_calls']==0 and phase2['evaluations']==12
assert phase2['cases_per_evaluation']==46 and phase2['correct_reference_passes']==4
assert phase2['export_same_source_all_cells'] and phase2['evaluator_absent_from_candidate_history']
assert len(phase2['controls'])==12 and sum(c['passed'] for c in phase2['controls'])==4
phase2_driver=json.loads((root/'phase2-driver-control/control.json').read_text())
assert phase2_driver=={'kind':'synthetic-smoke','model_calls':0,'driver_evaluations':1,'passed':True}
atomic_audit=json.loads((root/'atomic-followup/audit.json').read_text())
assert atomic_audit['archive_files_verified']==50 and atomic_audit['new_model_generations']==0 and len(atomic_audit['trials'])==4
atomic_replay=json.loads((root/'atomic-followup/replay/summary.json').read_text())
assert atomic_replay['new_model_generations']==0 and atomic_replay['exact_original_fixture_replay'] is False
assert atomic_replay['candidate_count']==4 and atomic_replay['replayed_cases_passed']==184 and atomic_replay['additional_cases_passed']==28
assert atomic_replay['candidate_authored_go_tests_passed']==4 and len(atomic_replay['capacity_probes'])==4
assert all(p['state_consistent_with_status'] and p['scored_in_original_cohort'] is False for p in atomic_replay['capacity_probes'])
action=json.loads((root/'action-controls/summary.json').read_text())
assert action['kind']=='synthetic-mechanism-control' and action['new_model_generations']==0
assert action['evaluations']==6 and action['cases_per_evaluation']==66 and action['reference_passed'] is True
assert action['negative_controls_detected']==5 and len(action['controls'])==6
assert action['direct_metadata_checks']==12 and len(action['startup_probes'])==7 and all(p['passed'] for p in action['startup_probes'])
cohort=json.loads((root/'phase3-cohort-controls/control.json').read_text())
assert cohort['kind']=='synthetic-v3-cohort-control' and cohort['new_model_generations']==0
assert cohort['human_evaluations']==6 and cohort['reference_passed']==2 and cohort['negative_controls_detected']==4
assert cohort['driver_evaluations']==4 and cohort['kiro_driver_evaluations']==1 and cohort['bundle_replays']==4 and cohort['cases_per_evaluation']==66
assert cohort['standalone_worker_passed'] and cohort['partial_denominator_checked'] and cohort['duplicate_rejected']
verifier=json.loads((root/'verifier.json').read_text());assert verifier['ok'] and len(verifier['results'])==7
for name,kind,cases,isolated,explicit in [('phase4-controls','synthetic-v4-implicit-convention-control',108,8,4),
        ('phase5-controls','synthetic-v5-scattered-convention-control',145,8,4),('phase5b-controls','synthetic-v5-scattered-convention-control',145,7,5)]:
    control=json.loads((root/name/'control.json').read_text())
    assert control['kind']==kind and control['new_model_generations']==0 and control['cases_per_evaluation']==cases
    assert control['human_evaluations']==14==len(control['controls']) and control['worker_transport_evaluations']==2 and control['reference_passed']==2
    assert control['implicit_violations_isolated']==isolated and control['explicit_violations_detected']==explicit
    assert control['crash_control']['outcome']=='candidate-error' and control['partial_denominator_checked'] and control['duplicate_rejected']
# v5b: omitting Event is an explicit (fail-loud) failure, not an isolated implicit violation as in v5.
assert next(c for c in json.loads((root/'phase5b-controls/control.json').read_text())['controls'] if c['cell']=='go-action' and c['mode']=='omit-event')['violates'] is None
extension=json.loads((root/'extension-controls.json').read_text())
assert extension['ok'] and extension['model_calls']==0 and extension['controls']==44 and all(r['passed'] for r in extension['results'])
assert (extension['negative_detected'],extension['positive_clean'],extension['uncovered_clean'])==(24,7,13)
interception=json.loads((root/'verifier-extension-rules/summary.json').read_text())
frozen=json.loads((Path(__file__).resolve().parents[1]/'docs/evidence/verifier-extension-rules-2026-11-15/summary.json').read_text())
assert interception==frozen and interception['trials']==280 and interception['new_model_generations']==0 and not interception['frozen_scores_modified']
operations=json.loads((root/'operation-controls.json').read_text())
assert operations['ok'] and operations['model_calls']==0 and operations['controls']==53 and all(r['passed'] for r in operations['results'])
assert (operations['negative_detected'],operations['positive_clean'],operations['uncovered_clean'],operations['unverifiable_detected'])==(34,12,7,4)
replay=json.loads((root/'operation-semantics/summary.json').read_text())
replay_frozen=json.loads((Path(__file__).resolve().parents[1]/'docs/evidence/operation-semantics-2026-11-16/summary.json').read_text())
assert replay==replay_frozen and replay['out_of_sample'] is False and replay['configs']['full']['trials']==228 and replay['new_model_generations']==0
assert operations['catalog']==replay['catalog']
opkernel=(root/'opkernel-db.txt').read_text()
assert '--- PASS: TestCatalogOperationsDB' in opkernel and '--- SKIP' not in opkernel and '\nok ' in opkernel
text='# Controlled POC results\n\n| Path | Healthy acceptance | Faults blocked |\n|---|---:|---:|\n'
for route,acceptance,blocked in rows:text+=f'| {route} | {acceptance} | {blocked} |\n'
text+='\nVerifier adversarial probes: 7/7. Direct SQL probes: 6/6. Startup configuration probes: 2/2. Shared browser acceptance: 18/18. Synthetic evaluator controls: 4 committed-candidate evaluations plus 1 driver end-to-end control (not AI measurements).\n\nThese are hand-authored fault injections, not measured AI defect rates or first-pass productivity. A guarded field over-fetch fails closed with 403; ZenStack nullifies the denied field. Disabling the ZenStack policy client is an expected escaping boundary control.\n'
text+='\nPublic CI model key configured for this run: '+('unknown (local, CI Secret not checked)' if configured is None else str(configured))+'. Model execution is not enabled by PR checks.\n'
text+='\nPR 7 archived replay: 9 candidates / 195 frozen cases; 45 post-hoc cases. Manual interventions and business-contract probes are separate from model first-pass rates.\n'
text+='\nPhase 2 atomic bulk approval: 12 human-authored candidate evaluations, 46 cases each; 4 correct references pass, 8 negative controls are detected. One additional synthetic driver control exercises export, generation protocol, bundle, and evaluation. Zero new model samples. Candidate exports omit evaluator files and prior Git history; this is not a same-host security sandbox.\n'
text+='\nPR 10 audit: 50 archived files and four candidate bundles verified. New public fixtures: 184 replay cases plus 28 additional checks pass; four candidate-authored Go test suites pass. Four request-capacity observations remain separate from first-pass scoring. Original private seed was not used. No new model generations.\n'
text+='\nTyped Action mechanism POC: reference passes 66/66; five negative controls detected, each evaluated against 66 cases. Purchase state, audit rows and completed idempotency response commit in one PostgreSQL transaction. Metadata SQL probes: 12/12; startup checks: 7/7. No new model samples or general workflow-engine claim.\n'
text+='\nImplicit-convention cohort controls v4/v5/v5b (real PostgreSQL 17.6, fake Kiro, no model calls): 14 human candidates and 2 worker-transport evaluations each; references pass; v5b omit-event is an explicit failure.\n'
text+=f"\nExtension verifier (appctl extensions): {extension['controls']} controls on real exports ({extension['negative_detected']} negatives flagged, {extension['positive_clean']} positives clean, {extension['uncovered_clean']} known-uncovered stay clean). Archived interception (post hoc, {interception['trials']} real candidates): any rule TP/FP/FN = {interception['any_rule']['vs_any_frozen_failure']['TP']}/{interception['any_rule']['vs_any_frozen_failure']['FP']}/{interception['any_rule']['vs_any_frozen_failure']['FN']} against frozen failures; frozen scores unchanged.\n"
full=replay['configs']['full'];sem=full['semantic_findings_vs_any_frozen_failure']
text+=f"\nOperation catalog {replay['catalog'][:19]}: catalog kernel passes on PostgreSQL 17.6 for all entries. Catalog-bound verifier (appctl operations): {operations['controls']} controls ({operations['negative_detected']} negatives flagged, of which {operations['unverifiable_detected']} UNVERIFIABLE; {operations['positive_clean']} positives clean; {operations['uncovered_clean']} known-uncovered clean). Retrospective replay on {full['trials']} seen v6 candidates (NOT out-of-sample): TP/FP/FN = {sem['TP']}/{sem['FP']}/{sem['FN']}, silent failures caught {full['silent_caught']}/{full['silent_failures']}.\n"
text+='\nAction evolution cohort v3: same guarded RLS, HTTP adapter, schema and starting candidate; only the Action capability differs. Six human controls and four synthetic generation/transfer evaluations (one through a fake Kiro CLI), 66 checks each. Two correct references pass; four negative controls detected; four bundle replays pass, including two standalone workers. Zero new model generations. Generation kits contain no Oracle or private seed; actual host/network isolation remains an operator deployment requirement.\n'
(root/'comparison.md').write_text(text)
print(text)
