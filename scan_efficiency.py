"""Conservative scan work planning. No classifications, cutoffs or CRM writes."""
from __future__ import annotations
import collections
import datetime as dt
import hashlib
import json
import re

# These are routing hints, never automatic exclusions. Sol still audits noise.
BUYER = re.compile(r'\b(hir\w*|contract\w*|consult\w*|fractional|looking for|need\w*|help|seeking|client\w*|customer\w*|company|business|team|engineer\w*|workflow\w*|accountability|enablement|transformation|production|integration|budget|spend|ROI|CTO|CAIO|FDE)\b', re.I)
SOCIAL = re.compile(r'\b(party|beer pong|drinks|dinner|picnic|birthday)\b', re.I)
ACK = re.compile(r'(thanks(?: you)?|thank you|congrats|congratulations|\+1|👍|🙏)[!. ]*', re.I)
TERMINAL = {'excluded', 'reviewed', 'published'}


def source_plan(scopes, day=None):
    """Never rotate discovery off. Rotate only weak experimental deep reads."""
    date = dt.date.fromisoformat(day) if day else dt.datetime.now(dt.timezone.utc).date()
    result = []
    for source in scopes:
        scope_id = source['id']
        trial = source.get('lane') == 'trial' and source.get('platform') not in {'reddit', 'gmail', 'slack', 'whatsapp', 'bookface'}
        slot = int(hashlib.sha256(scope_id.encode()).hexdigest()[:8], 16) % 3
        deep_due = not trial or date.toordinal() % 3 == slot
        result.append({'scope_id': scope_id, 'metadata_due': True, 'deep_reads_due': deep_due,
                       'weak_item_limit': 12 if deep_due else 2,
                       'buyer_or_incomplete_bypasses_limit': True})
    return result


def _settled(db, item, text_hash, records=None):
    """Only skip a captured, settled identical version, never a changed body."""
    if db is None:
        return False
    record = None
    if records is not None and item.get('id'):
        record = records.get(item['id'])
    elif item.get('id'):
        record = db.execute('SELECT content_hash,disposition,scope_id,version_key FROM scan_observations WHERE id=?', (item['id'],)).fetchone()
    if records is None and record is None and item.get('scope_id') and item.get('native_id'):
        version = item.get('version_key') or item.get('content_hash') or text_hash
        record = db.execute('SELECT content_hash,disposition,scope_id,version_key FROM scan_observations WHERE scope_id=? AND native_id=? AND version_key=?',
                            (item['scope_id'], item['native_id'], version)).fetchone()
    return bool(record and record['disposition'] in TERMINAL and record['content_hash'] == text_hash
                and (not item.get('scope_id') or record['scope_id'] == item['scope_id'])
                and (not item.get('version_key') or record['version_key'] == item['version_key']))


def plan_batch(db, payload):
    """Return compact routes for saved captures. Deferred IDs stay pending in SQLite."""
    items = payload.get('observations', [])
    if not isinstance(items, list) or len(items) > 250:
        raise ValueError('efficiency planning requires at most250 observations')
    limit = payload.get('weak_item_limit', 12)
    if type(limit) is not int or not 0 <= limit <= 250:
        raise ValueError('weak_item_limit must be0..250')
    ids = [str(x['id']) for x in items if x.get('id')]
    records = {}
    if db is not None and ids:
        placeholders = ','.join('?' for _ in ids)
        records = {x['id']:x for x in db.execute('SELECT id,content_hash,disposition,scope_id,version_key,metadata_json FROM scan_observations WHERE id IN ('+placeholders+')', ids)}
    routes, seen, bodies = [], {}, {}
    weak_counts = collections.Counter()
    for item in items:
        identity = item.get('id') or item.get('native_id')
        if not identity:
            raise ValueError('stable captured id required')
        text = item.get('content_text', item.get('text', ''))
        if not isinstance(text, str):
            raise ValueError('captured text must be a string')
        text_hash = hashlib.sha256(text.encode()).hexdigest()
        metadata = item.get('metadata') or {}
        stored = records.get(item.get('id'))
        stored_metadata = json.loads(stored['metadata_json']) if stored else {}
        scope = item.get('scope_id') or metadata.get('scope_id') or (stored['scope_id'] if stored else 'unspecified')
        # Protect context, changed replies and incomplete captures before noise routing.
        incomplete = (not text.strip() or bool(item.get('truncated') or metadata.get('truncated')
                      or item.get('content_truncated') or metadata.get('content_truncated'))
                      or item.get('content_complete') is False or metadata.get('content_complete') is False
                      or item.get('disposition') == 'needs_content'
                      or bool(stored and (stored['disposition']=='needs_content'
                              or stored_metadata.get('content_truncated')
                              or (stored_metadata.get('content_complete') is False and stored_metadata.get('capture_kind')!='original'))))
        protected = incomplete or bool(item.get('changed') or item.get('new_reply') or metadata.get('new_reply')
                       or metadata.get('observed_change') or metadata.get('reply_count_changed')
                       or item.get('user_requested') or metadata.get('context_required'))
        key = (item.get('account_id') or metadata.get('account_id'), scope, str(identity), item.get('version_key'), text_hash)
        route = {'id': identity, 'scope_id': scope, 'content_hash': text_hash}
        if key in seen and not protected:
            route.update(route='duplicate_occurrence', same_as=seen[key], reason='same captured identity and body; preserve reference')
        elif not protected and _settled(db, item, text_hash, records):
            route.update(route='known_unchanged', reason='identical settled observation; zero model work')
        elif incomplete:
            route.update(route='read_original', reason='complete original required before model classification')
        elif protected:
            route.update(route='review', reason='changed, incomplete or contextual evidence; never noise-filter')
        elif BUYER.search(text):
            route.update(route='review', reason='plausible buyer/hiring/operational pain; unknown budget remains eligible')
        elif ACK.fullmatch(text.strip()) or SOCIAL.search(text):
            route.update(route='noise_audit', reason='one compact Sol audit, not Luna followed by Sol; not excluded')
        elif weak_counts[scope] >= limit:
            route.update(route='deferred', reason='weak-item budget; retain captured ID, do not advance unverified cutoff')
        else:
            route.update(route='triage', reason='new unclassified material; Luna first, retain uncertainty')
            weak_counts[scope] += 1
        # Exact text alone is only a crosspost hint. Different companies can reuse wording.
        if len(text) >= 160 and text_hash in bodies and key not in seen:
            route['possible_crosspost_of'] = bodies[text_hash]
        bodies.setdefault(text_hash, identity)
        seen.setdefault(key, identity)
        routes.append(route)
    counts = dict(collections.Counter(x['route'] for x in routes))
    return {'policy': 'efficiency-v1', 'routes': routes, 'counts': counts,
            'classification_writes': 0, 'cutoff_writes': 0,
            'qualification': 'shortlisted or user-selected only; no email/history/profile expansion during discovery',
            'repair_budget': {'semantic_repairs': 1, 'transport_retries': 1},
            'audit': 'noise_audit requires existing Sol receipt before exclusion; deferred is never covered',
            'model_batches': {task: [x['id'] for x in routes if x['route'] in lanes]
                              for task, lanes in [('scan_review', {'review', 'noise_audit'}), ('scan_triage', {'triage'})]}}


def report(db, payload):
    """Source yield from existing rows; no invented token allocation or new telemetry DB."""
    run = payload['run_id']
    if not db.execute('SELECT 1 FROM scan_runs WHERE id=?', (run,)).fetchone():
        raise ValueError('unknown scan run')
    sources = {}
    for row in db.execute('''SELECT b.scope_id, count(*) captures, count(DISTINCT o.id) unique_items,
        count(DISTINCT CASE WHEN o.disposition IN ('candidate','reviewed','published') THEN o.id END) candidate_or_reviewed,
        count(DISTINCT CASE WHEN o.disposition='excluded' THEN o.id END) excluded,
        count(DISTINCT CASE WHEN o.disposition NOT IN ('excluded','reviewed','published') THEN o.id END) pending
        FROM scan_batches b JOIN scan_batch_items i ON i.batch_id=b.id JOIN scan_observations o ON o.id=i.observation_id
        WHERE b.run_id=? GROUP BY b.scope_id''', (run,)):
        sources[row['scope_id']] = dict(row)
    for row in db.execute('''SELECT o.scope_id,count(DISTINCT l.opportunity_id) linked_opportunities
        FROM scan_opportunity_links l JOIN scan_observations o ON o.id=l.observation_id
        WHERE EXISTS(SELECT 1 FROM scan_batch_items i JOIN scan_batches b ON b.id=i.batch_id WHERE i.observation_id=o.id AND b.run_id=?)
        GROUP BY o.scope_id''', (run,)):
        sources[row['scope_id']]['linked_opportunities'] = row['linked_opportunities']
    membership = {x['id']:x['scope_id'] for x in db.execute('''SELECT DISTINCT o.id,o.scope_id
        FROM scan_observations o JOIN scan_batch_items i ON i.observation_id=o.id
        JOIN scan_batches b ON b.id=i.batch_id WHERE b.run_id=?''', (run,))}
    calls = [dict(x) for x in db.execute('SELECT id,status,usage_json,binding_json FROM scan_model_calls WHERE run_id=?', (run,))]
    for source in sources.values():
        source['model_calls_touching_source'] = 0
        source['reader_elapsed_ms'] = None
    for call in calls:
        involved = {membership[x] for x in json.loads(call['binding_json']).get('observation_ids',[]) if x in membership}
        for scope in involved:
            sources[scope]['model_calls_touching_source'] += 1
    for batch in db.execute('SELECT scope_id,proof_json FROM scan_batches WHERE run_id=?', (run,)):
        elapsed = json.loads(batch['proof_json']).get('reader_elapsed_ms')
        if type(elapsed) in (int,float) and elapsed >= 0:
            source = sources.get(batch['scope_id'])
            if source is not None:
                source['reader_elapsed_ms'] = (source['reader_elapsed_ms'] or 0) + elapsed
    return {'run_id': run, 'sources': list(sources.values()),
            'model_calls': len(calls), 'model_usage_receipts': [{'id': x['id'], 'status': x['status'], 'usage': json.loads(x['usage_json']) if x['usage_json'] else None} for x in calls],
            'notes': ['Candidates are not confirmed budgets or new qualified leads.',
                      'Missing model usage and source elapsed time are unknown; no inferred savings percentage.',
                      'Per-source model-call counts overlap for multi-source batches; do not sum them.',
                      'Reader elapsed values sum only reported batch timings, not full source wall time.',
                      'All discovery scopes remain due; experimental deep-read rotation never hides plausible asks.']}
