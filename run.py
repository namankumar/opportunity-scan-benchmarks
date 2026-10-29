"""Offline, fixed-case comparison of the production scan-efficiency planner.

No browser, model, CRM file, network request, or external action. The repository's
actual planner is imported rather than reimplemented. Public export can place an
unchanged copy of scan_efficiency.py beside this file.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import re
import time

HERE = Path(__file__).resolve().parent
PRIVATE_SOURCE = HERE.parents[2] / '_system' / 'crm' / 'scan_efficiency.py'
LOCAL_SOURCE = HERE / 'scan_efficiency.py'
SOURCE = LOCAL_SOURCE if LOCAL_SOURCE.exists() else PRIVATE_SOURCE
if not SOURCE.exists():
    raise SystemExit('Production scan_efficiency.py not found')
spec = importlib.util.spec_from_file_location('scan_efficiency_under_test', SOURCE)
planner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(planner)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fixture_db(cases):
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE scan_observations (id TEXT PRIMARY KEY, content_hash TEXT, disposition TEXT, scope_id TEXT, version_key TEXT, metadata_json TEXT)')
    for case in cases:
        prior = case.get('prior')
        if prior:
            db.execute('INSERT INTO scan_observations VALUES (?,?,?,?,?,?)',
                       (case['id'], sha(prior['content_text'].encode()), prior['disposition'],
                        case['scope_id'], prior['version_key'], '{}'))
    return db


def evaluate(cases, limit):
    db = fixture_db(cases)
    items = [{k: v for k, v in case.items() if k in
              {'id', 'scope_id', 'account_id', 'content_text', 'version_key', 'new_reply', 'truncated'}}
             for case in cases]
    start = time.perf_counter()
    result = planner.plan_batch(db, {'observations': items, 'weak_item_limit': limit})
    elapsed_ms = round((time.perf_counter() - start) * 1000, 3)
    db.close()
    routes = result['routes']
    errors = []
    for case, route in zip(cases, routes):
        required = case.get('required_route')
        if required and route['route'] != required:
            errors.append({'case':case['id'], 'expected':required, 'actual':route['route']})
        if case.get('crosspost_hint_expected') and route.get('possible_crosspost_of') != 'different-buyer-a':
            errors.append({'case':case['id'], 'expected':'crosspost hint, not suppression', 'actual':route})
        if case['id'] == 'different-buyer-b' and route['route'] == 'duplicate_occurrence':
            errors.append({'case':case['id'], 'expected':'distinct buyer retained', 'actual':route['route']})
        if route['route'] == 'excluded':
            errors.append({'case':case['id'], 'expected':'no deterministic exclusion', 'actual':route['route']})
    fresh = [(c,r) for c,r in zip(cases,routes)
             if not c.get('duplicate_of') and c['id'] != 'settled-unchanged']
    selected = [(c,r) for c,r in fresh if r['route'] in {'review','triage'}]
    direct = [(c,r) for c,r in fresh if r['route']=='review']
    relevant = [c for c,_ in fresh if c['relevant']]
    selected_relevant = sum(c['relevant'] for c,_ in selected)
    direct_relevant = sum(c['relevant'] for c,_ in direct)
    def ratio(a,b): return round(a/b,4) if b else None
    splits = {}
    for split in ('calibration','held_out'):
        part = [(c,r) for c,r in fresh if c['split']==split]
        total_rel = sum(c['relevant'] for c,_ in part)
        reached_rel = sum(c['relevant'] and r['route'] in {'review','triage'} for c,r in part)
        splits[split] = {'fresh_cases':len(part), 'known_relevant':total_rel,
                         'model_queue_recall':ratio(reached_rel,total_rel)}
    return {'config':{'weak_item_limit':limit}, 'planned_cases':len(cases),
            'fresh_cases':len(fresh), 'route_counts':result['counts'],
            'valid_run_rate':1.0 if not errors else 0.0,
            'direct_review_precision':ratio(direct_relevant,len(direct)),
            'direct_review_recall':ratio(direct_relevant,len(relevant)),
            'model_queue_precision':ratio(selected_relevant,len(selected)),
            'model_queue_recall':ratio(selected_relevant,len(relevant)),
            'splits':splits,
            'duplicate_suppression':sum(r['route']=='duplicate_occurrence' for r in routes),
            'unchanged_suppression':sum(r['route']=='known_unchanged' for r in routes),
            'originals_needed':sum(r['route']=='read_original' for r in routes),
            'deferred':sum(r['route']=='deferred' for r in routes),
            'forbidden_action_errors':errors,
            'planner_elapsed_ms_single_run':elapsed_ms,
            'model_task_success':None, 'model_latency_ms':None, 'model_cost_usd':None,
            'real_operational_impact':None,
            'routes':[{'case':c['id'], 'split':c['split'], 'route':r['route'],
                       'possible_crosspost_of':r.get('possible_crosspost_of')}
                      for c,r in zip(cases,routes)]}


def reader_fixture_tests():
    """Run the existing Reddit DOM-reader tests; no live tab or network."""
    test = HERE/'tests'/'test-scan-reddit-reader.mjs'
    if not test.exists():
        test = HERE.parent/'tests'/'test-scan-reddit-reader.mjs'
    if not test.exists():
        return {'status':'not_reached','reason':'Reader test file unavailable'}
    started = time.perf_counter()
    try:
        completed = subprocess.run(['node','--test',str(test)], capture_output=True,
                                   text=True, timeout=30, check=False)
    except (OSError,subprocess.TimeoutExpired) as error:
        return {'status':'not_reached','reason':type(error).__name__}
    elapsed = round((time.perf_counter()-started)*1000,3)
    output = completed.stdout + completed.stderr
    def number(label):
        match = re.search(r'^\s*[#ℹ]\s*'+label+r'\s+(\d+)\s*$',output,re.M)
        return int(match.group(1)) if match else None
    count, passed, failed = number('tests'), number('pass'), number('fail')
    return {'status':'pass' if completed.returncode==0 and count and passed==count and failed==0 else 'fail',
            'tests':count,'passed':passed,'failed':failed,'elapsed_ms':elapsed,
            'scope':'synthetic rendered-DOM fixtures; live Reddit completeness not established'}


def scorecard():
    fixture_bytes = (HERE/'cases.json').read_bytes()
    fixture = json.loads(fixture_bytes)
    cases = fixture['cases']
    if not cases or any(not x.get('id') or 'relevant' not in x for x in cases):
        raise ValueError('Each fixture case needs an id and relevance label')
    baseline = evaluate(cases,0)
    candidate = evaluate(cases,12)
    baseline_routes = baseline['routes']; candidate_routes = candidate['routes']
    regressions = []
    for left,right in zip(baseline_routes,candidate_routes):
        if left['route'] in {'review','triage'} and right['route']=='deferred':
            regressions.append({'case':left['case'],'baseline':left['route'],'candidate':right['route']})
    reader_tests = reader_fixture_tests()
    scope_plan = planner.source_plan([
        {'id':'reddit:AiAutomations','platform':'reddit','lane':'trial'},
        {'id':'gmail:fixture','platform':'gmail','lane':'core'},
        {'id':'facebook:trial','platform':'facebook','lane':'trial'}], '2026-10-28')
    return {'benchmark':'opportunity-scanner-offline-v1',
            'source_binding':{'module':'scan_efficiency.py','sha256':sha(SOURCE.read_bytes()),
                              'source_path_kind':'canonical_private' if SOURCE==PRIVATE_SOURCE else 'public_export'},
            'fixture_binding':{'file':'cases.json','sha256':sha(fixture_bytes),'version':fixture['version'],
                               'labeling':fixture['labeling']},
            'execution':{'kind':'offline deterministic replay','trials_per_config':1,
                         'starting_state':'new in-memory SQLite from identical case priors per config',
                         'browser_reads':0,'model_calls':0,'crm_file_writes':0},
            'baseline':baseline,'candidate':candidate,'reddit_reader_fixture_tests':reader_tests,
            'regressions':regressions,'source_plan':scope_plan,
            'status':{'routing_invariants':'pass' if not baseline['forbidden_action_errors'] and not candidate['forbidden_action_errors'] and not regressions else 'fail',
                      'reader_fixture_extraction':reader_tests['status'], 'semantic_extraction':'not_reached','model_quality':'not_reached',
                      'live_coverage':'not_reached','real_world_roi':'not_reached'},
            'interpretation':['Precision and recall are against this bounded author-labeled collection only.',
                              'A direct review route is not a qualified lead or a correct model judgment.',
                              'Deferred captures remain pending and must not advance source cutoffs.',
                              'Single-run planner timing is observed local execution, not model latency or token cost.']}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--output', type=Path)
    args=ap.parse_args()
    result=scorecard()
    rendered=json.dumps(result,indent=2,ensure_ascii=False)+'\n'
    if args.output: args.output.write_text(rendered)
    else: print(rendered,end='')
    if result['status']['routing_invariants']!='pass': raise SystemExit(1)

if __name__=='__main__':main()
