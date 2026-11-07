"""V0.2 projections and subscription-scoped curation state."""
import hashlib
import json
from pathlib import Path
from functools import lru_cache

from .engine import Conflict, encode, now
from .schemas import SCHEMAS, obj, ident, key, string, integer, enum

PERMISSIONS = {
    'processing_queue': 'read',
    'formal_messages': 'read',
    'categories': 'read',
    'events': 'read',
    'event': 'read',
    'category_save': 'manage',
    'event_save': 'manage',
    'event_message_save': 'analyze',
    'queue_decide': 'analyze',
    'queue_fail': 'analyze',
    # Worker-only transition; the service also verifies an active fenced lease.
    'queue_prepare': 'analyze',
}

for method in ('categories', 'events'):
    SCHEMAS[method] = obj({'subscription_id': ident})
SCHEMAS['processing_queue'] = obj({
    'subscription_id': ident,
    'after': {'type': 'string', 'maxLength': 200},
    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100},
    'state': enum('all', 'pending', 'processing', 'retry', 'processed', 'not_included'),
}, required=['subscription_id'])
SCHEMAS['formal_messages'] = obj({
    'subscription_id': ident,
    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100},
}, required=['subscription_id'])
SCHEMAS['event'] = obj({'subscription_id': ident, 'event_id': ident})
common = {'operation_key': key, 'subscription_id': ident, 'id': ident, 'expected_revision': integer}
SCHEMAS['category_save'] = obj({**common, 'name': string(300), 'parent_id': {'anyOf': [ident, {'type': 'null'}]}})
SCHEMAS['event_save'] = obj({**common, 'name': string(300), 'category_id': {'anyOf': [ident, {'type': 'null'}]}, 'status': enum('active', 'paused', 'archived')})
SCHEMAS['event_message_save'] = obj({
    'operation_key': key, 'subscription_id': ident, 'event_id': ident,
    'message_id': ident, 'expected_revision': integer,
    'role': enum('application', 'supplement', 'correction', 'announcement', 'other'),
    'reason': string(10000), 'analysis_id': ident, 'active': {'type': 'boolean'},
})
SCHEMAS['queue_decide'] = obj({
    'operation_key': key, 'subscription_id': ident, 'message_id': ident,
    'message_revision': {'type': 'integer', 'minimum': 1},
    'expected_revision': integer,
    'decision': enum('not_included', 'reopen'),
    'reason': string(5000),
})
SCHEMAS['queue_fail'] = obj({
    'operation_key': key, 'subscription_id': ident, 'run_id': ident,
    'item_id': ident, 'epoch': {'type': 'integer', 'minimum': 1},
    'message_id': ident, 'message_revision': {'type': 'integer', 'minimum': 1},
    'reason': string(5000),
})
SCHEMAS['queue_prepare'] = obj({
    'operation_key': key, 'subscription_id': ident, 'run_id': ident,
    'item_id': ident, 'epoch': {'type': 'integer', 'minimum': 1},
    'message_id': ident, 'message_revision': {'type': 'integer', 'minimum': 1},
})


@lru_cache(maxsize=1)
def policy_version():
    """Fingerprint the bundled Skill/reference set; this is provenance, not proof of model loading."""
    root = Path(__file__).resolve().parents[1] / 'skills' / 'radar'
    digest = hashlib.sha256()
    if not root.exists():
        return 'skill-bundle-unavailable'
    for path in sorted(root.rglob('*.md')):
        digest.update(path.relative_to(root).as_posix().encode('utf-8'))
        digest.update(b'\\0')
        digest.update(path.read_bytes())
        digest.update(b'\\0')
    return 'sha256:' + digest.hexdigest()


def initialize(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS product_categories(
      id TEXT PRIMARY KEY, subscription_id TEXT NOT NULL REFERENCES subscriptions(id),
      name TEXT NOT NULL, parent_id TEXT REFERENCES product_categories(id), revision INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS product_events(
      id TEXT PRIMARY KEY, subscription_id TEXT NOT NULL REFERENCES subscriptions(id),
      name TEXT NOT NULL, category_id TEXT REFERENCES product_categories(id),
      status TEXT NOT NULL, revision INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS event_messages(
      event_id TEXT NOT NULL REFERENCES product_events(id), message_id TEXT NOT NULL REFERENCES messages(id),
      role TEXT NOT NULL, reason TEXT NOT NULL, analysis_id TEXT NOT NULL REFERENCES analyses(id),
      active INTEGER NOT NULL, revision INTEGER NOT NULL, PRIMARY KEY(event_id, message_id)
    );
    CREATE TABLE IF NOT EXISTS product_audit(
      seq INTEGER PRIMARY KEY, principal TEXT NOT NULL, method TEXT NOT NULL,
      payload TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS curation_items(
      subscription_id TEXT NOT NULL REFERENCES subscriptions(id),
      message_id TEXT NOT NULL REFERENCES messages(id),
      message_revision INTEGER NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('pending','processing','retry','processed','not_included')),
      decision TEXT, reason TEXT NOT NULL DEFAULT '', policy_version TEXT NOT NULL DEFAULT 'v1',
      analysis_id TEXT REFERENCES analyses(id), attempts INTEGER NOT NULL DEFAULT 0,
      revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      PRIMARY KEY(subscription_id,message_id,message_revision),
      FOREIGN KEY(message_id,message_revision) REFERENCES message_revisions(message_id,revision)
    );
    CREATE INDEX IF NOT EXISTS curation_queue_scope ON curation_items(subscription_id,state,updated_at);
    CREATE TABLE IF NOT EXISTS curation_history(
      seq INTEGER PRIMARY KEY, principal_id TEXT NOT NULL REFERENCES credentials(id),
      subscription_id TEXT NOT NULL, message_id TEXT NOT NULL, message_revision INTEGER NOT NULL,
      action TEXT NOT NULL, previous_state TEXT, new_state TEXT NOT NULL,
      reason TEXT NOT NULL, policy_version TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TRIGGER IF NOT EXISTS immutable_curation_history_update BEFORE UPDATE ON curation_history BEGIN SELECT RAISE(ABORT,'immutable curation history'); END;
    CREATE TRIGGER IF NOT EXISTS immutable_curation_history_delete BEFORE DELETE ON curation_history BEGIN SELECT RAISE(ABORT,'immutable curation history'); END;
    CREATE TRIGGER IF NOT EXISTS immutable_product_audit_update BEFORE UPDATE ON product_audit BEGIN SELECT RAISE(ABORT,'immutable product audit'); END;
    CREATE TRIGGER IF NOT EXISTS immutable_product_audit_delete BEFORE DELETE ON product_audit BEGIN SELECT RAISE(ABORT,'immutable product audit'); END;
    """)
    # Additive, status-aware backfill: only an analysis pinned to the current
    # source revision counts as processed. Old/unknown records remain pending.
    db.execute("""
      INSERT OR IGNORE INTO curation_items(
        subscription_id,message_id,message_revision,state,decision,reason,policy_version,
        analysis_id,attempts,revision,created_at,updated_at
      )
      SELECT DISTINCT r.subscription_id,m.id,m.current_revision,
        CASE WHEN a.id IS NULL THEN 'pending' WHEN json_extract(a.result,'$.decision')='irrelevant' THEN 'not_included' ELSE 'processed' END,
        CASE WHEN a.id IS NULL THEN NULL ELSE json_extract(a.result,'$.decision') END,
        COALESCE(json_extract(a.result,'$.reason'),''), 'v1', a.id, 0, 1, m.first_seen_at, m.last_checked_at
      FROM messages m
      JOIN run_items i ON i.message_id=m.id
      JOIN runs r ON r.id=i.run_id
      LEFT JOIN matches x ON x.subscription_id=r.subscription_id AND x.message_id=m.id
      LEFT JOIN analyses a ON a.id=x.analysis_id AND a.message_revision=m.current_revision
      WHERE m.current_revision>0
    """)


def _table_exists(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='curation_items'").fetchone() is not None


def _history(db, principal, sub, mid, message_revision, action, before, after, reason, policy_version):
    if not _table_exists(db):
        return
    db.execute(
        'INSERT INTO curation_history(principal_id,subscription_id,message_id,message_revision,action,previous_state,new_state,reason,policy_version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
        (principal, sub, mid, message_revision, action, before, after, reason, policy_version, now()),
    )


def ensure_candidate(db, subscription_id, message_id, message_revision, principal=None):
    """Create one subscription-local curation task inside the source-write transaction."""
    if not _table_exists(db) or not message_revision:
        return
    cursor = db.execute(
        "INSERT OR IGNORE INTO curation_items(subscription_id,message_id,message_revision,state,policy_version,created_at,updated_at) VALUES(?,?,?,'pending',?,?,?)",
        (subscription_id, message_id, message_revision, policy_version(), now(), now()),
    )
    if cursor.rowcount and principal:
        _history(db, principal, subscription_id, message_id, message_revision, 'candidate_created', None, 'pending', '', policy_version())
        db.execute('INSERT INTO changes(kind,entity_id,revision,created_at) VALUES(?,?,?,?)', ('curation_item', message_id, 1, now()))


def invalidate_for_resource_change(db, principal, subscription_id, message_id, message_revision, reason):
    """Reopen a terminal curation item only when a resource basis actually changed or became unknown."""
    if not _table_exists(db):
        return
    row = db.execute(
        'SELECT * FROM curation_items WHERE subscription_id=? AND message_id=? AND message_revision=?',
        (subscription_id, message_id, message_revision),
    ).fetchone()
    if not row or row['state'] not in ('processed', 'not_included'):
        return
    revision = row['revision'] + 1
    db.execute(
        "UPDATE curation_items SET state='pending',reason=?,revision=?,updated_at=? WHERE subscription_id=? AND message_id=? AND message_revision=?",
        (reason[:5000], revision, now(), subscription_id, message_id, message_revision),
    )
    _history(db, principal, subscription_id, message_id, message_revision, 'resource_changed', row['state'], 'pending', reason[:5000], row['policy_version'])
    db.execute('INSERT INTO changes(kind,entity_id,revision,created_at) VALUES(?,?,?,?)', ('curation_item', message_id, revision, now()))


def complete_analysis(db, principal, subscription_id, message_id, message_revision, analysis_id, result):
    """Advance curation state atomically with the immutable analysis write."""
    if not _table_exists(db):
        return
    ensure_candidate(db, subscription_id, message_id, message_revision, principal)
    row = db.execute(
        'SELECT * FROM curation_items WHERE subscription_id=? AND message_id=? AND message_revision=?',
        (subscription_id, message_id, message_revision),
    ).fetchone()
    if row['state'] == 'not_included':
        raise Conflict('candidate was explicitly marked not included; user must reopen it first')
    decision = result['decision']
    new_state = 'not_included' if decision == 'irrelevant' else 'processed'
    reason = result['reason']
    revision = row['revision'] + 1
    db.execute(
        'UPDATE curation_items SET state=?,decision=?,reason=?,analysis_id=?,revision=?,updated_at=? WHERE subscription_id=? AND message_id=? AND message_revision=?',
        (new_state, decision, reason, analysis_id, revision, now(), subscription_id, message_id, message_revision),
    )
    _history(db, principal, subscription_id, message_id, message_revision, 'analysis_completed', row['state'], new_state, reason, row['policy_version'])
    db.execute('INSERT INTO changes(kind,entity_id,revision,created_at) VALUES(?,?,?,?)', ('curation_item', message_id, revision, now()))


def fail_analysis(db, principal, subscription_id, message_id, message_revision, reason):
    if not _table_exists(db) or not message_id or not message_revision:
        return
    row = db.execute(
        'SELECT * FROM curation_items WHERE subscription_id=? AND message_id=? AND message_revision=?',
        (subscription_id, message_id, message_revision),
    ).fetchone()
    if not row or row['state'] != 'processing':
        return
    revision = row['revision'] + 1
    db.execute(
        "UPDATE curation_items SET state='retry',reason=?,revision=?,updated_at=? WHERE subscription_id=? AND message_id=? AND message_revision=?",
        (reason[:5000], revision, now(), subscription_id, message_id, message_revision),
    )
    _history(db, principal, subscription_id, message_id, message_revision, 'analysis_failed', row['state'], 'retry', reason[:5000], row['policy_version'])
    db.execute('INSERT INTO changes(kind,entity_id,revision,created_at) VALUES(?,?,?,?)', ('curation_item', message_id, revision, now()))


def recover_processing(db, principal, run_ids):
    if not _table_exists(db):
        return
    for run_id in run_ids:
        run = db.execute('SELECT subscription_id FROM runs WHERE id=?', (run_id,)).fetchone()
        if not run:
            continue
        rows = db.execute(
            "SELECT * FROM curation_items WHERE subscription_id=? AND state='processing' AND EXISTS (SELECT 1 FROM run_items i JOIN runs r ON r.id=i.run_id WHERE r.subscription_id=curation_items.subscription_id AND i.message_id=curation_items.message_id AND i.message_revision=curation_items.message_revision AND i.run_id=?)",
            (run['subscription_id'], run_id),
        ).fetchall()
        for row in rows:
            revision = row['revision'] + 1
            reason = 'Processing was interrupted; candidate is ready to retry.'
            db.execute("UPDATE curation_items SET state='retry',reason=?,revision=?,updated_at=? WHERE subscription_id=? AND message_id=? AND message_revision=?", (reason, revision, now(), row['subscription_id'], row['message_id'], row['message_revision']))
            _history(db, principal, row['subscription_id'], row['message_id'], row['message_revision'], 'execution_recovered', 'processing', 'retry', reason, row['policy_version'])
            db.execute('INSERT INTO changes(kind,entity_id,revision,created_at) VALUES(?,?,?,?)', ('curation_item', row['message_id'], revision, now()))


def call(s, identity, method, args):
    sub = args['subscription_id']
    s.check(identity, PERMISSIONS[method], sub)
    db = s.db

    def scoped(table, entity_id):
        row = db.execute('SELECT * FROM ' + table + ' WHERE id=? AND subscription_id=?', (entity_id, sub)).fetchone()
        if not row:
            raise ValueError('entity unavailable in subscription')
        return row

    if method == 'processing_queue':
        limit = args.get('limit', 50)
        rows = []
        sql = """
          SELECT q.*,m.source_id,m.external_key,m.current_revision,m.last_checked_at,rev.content
          FROM curation_items q JOIN messages m ON m.id=q.message_id
          JOIN message_revisions rev ON rev.message_id=q.message_id AND rev.revision=q.message_revision
          WHERE q.subscription_id=? AND q.message_revision=m.current_revision
            AND q.message_id>?
        """
        params = [sub, args.get('after', '')]
        if args.get('state') and args['state'] != 'all':
            sql += ' AND q.state=?'
            params.append(args['state'])
        elif args.get('state') != 'all':
            sql += " AND q.state IN ('pending','processing','retry','not_included')"
        sql += ' ORDER BY q.message_id LIMIT ?'
        params.append(limit)
        for r in db.execute(sql, params):
            v = dict(r)
            content = json.loads(v.pop('content'))
            v['title'] = content['title']
            v['published_date'] = content.get('published_date')
            v['published_at_raw'] = content.get('published_at_raw')
            latest = db.execute("""
              SELECT i.id item_id,i.state item_state,i.error,r.id run_id,r.status run_status
              FROM run_items i JOIN runs r ON r.id=i.run_id
              WHERE r.subscription_id=? AND i.message_id=? AND i.message_revision=?
              ORDER BY i.rowid DESC LIMIT 1
            """, (sub, r['message_id'], r['message_revision'])).fetchone()
            v['latest_run'] = dict(latest) if latest else None
            rows.append(v)
        for item in rows:
            item['processing_state'] = item['state']
        return rows

    if method == 'formal_messages':
        rows = []
        for r in db.execute("""
          SELECT m.*,a.id analysis_id,a.result,a.message_revision input_revision,x.decision projection_decision
          FROM matches x JOIN messages m ON m.id=x.message_id
          JOIN analyses a ON a.id=x.analysis_id
          WHERE x.subscription_id=? AND x.decision IN ('relevant','uncertain')
        """, (sub,)):
            v = dict(r)
            content = json.loads(db.execute('SELECT content FROM message_revisions WHERE message_id=? AND revision=?', (r['id'], r['current_revision'])).fetchone()[0])
            v['published_date'] = content.get('published_date')
            v['published_at_raw'] = content.get('published_at_raw')
            v['title'] = content['title']
            updates = [r[0] for r in db.execute("SELECT created_at FROM changes WHERE entity_id=? AND kind IN ('message_revision','resource_version')", (r['id'],))]
            v['update_discovered_at'] = max(updates) if r['current_revision'] > 1 and updates else None
            snapshot = s._call(identity, 'message', {'message_id': r['id']})
            analysis = next(a for a in snapshot['analyses'] if a['id'] == r['analysis_id'])
            v['needs_review'] = analysis['stale']
            v['resource_state_unknown'] = analysis['resource_state_unknown']
            rows.append(v)
        rows = sorted(rows, key=lambda r: (r['published_date'] is not None, r['published_date'] or '', r['id']), reverse=True)
        return rows[:args.get('limit', 50)]

    if method in ('categories', 'events'):
        table = 'product_categories' if method == 'categories' else 'product_events'
        return [dict(r) for r in db.execute('SELECT * FROM ' + table + ' WHERE subscription_id=? ORDER BY name', (sub,))]

    if method == 'event':
        result = dict(scoped('product_events', args['event_id']))
        result['timeline'] = []
        candidates = []
        for row in db.execute('SELECT * FROM event_messages WHERE event_id=? AND active=1', (args['event_id'],)):
            a = db.execute('SELECT * FROM analyses WHERE id=? AND subscription_id=?', (row['analysis_id'], sub)).fetchone()
            if not a:
                continue
            current = db.execute('SELECT current_revision FROM messages WHERE id=?', (row['message_id'],)).fetchone()
            snapshot = s._call(identity, 'message', {'message_id': row['message_id']})
            basis = next(v for v in snapshot['analyses'] if v['id'] == a['id'])
            stale = basis['stale']
            analysis = json.loads(a['result'])
            content = json.loads(db.execute('SELECT content FROM message_revisions WHERE message_id=? AND revision=?', (row['message_id'], a['message_revision'])).fetchone()[0])
            result['timeline'].append({**dict(row), 'published_date': content.get('published_date'), 'published_at_raw': content.get('published_at_raw'), 'title': content['title'], 'requirements': analysis['items'], 'summary': analysis['summary'], 'input_revision': a['message_revision'], 'stale': stale})
            for requirement in analysis['items']:
                candidates.append({
                    'requirement': requirement,
                    'message_id': row['message_id'],
                    'message_revision': a['message_revision'],
                    'analysis_id': a['id'],
                    'title': content['title'],
                    'published_date': content.get('published_date'),
                    'relation_role': row['role'],
                    'relation_reason': row['reason'],
                    'source_decision': analysis['decision'],
                    'source_uncertainties': analysis['uncertainties'],
                    'stale': stale,
                })
        result['timeline'].sort(key=lambda r: (r['published_date'] is None, r['published_date'] or '', r['message_id']))
        # Never silently replace an older requirement with a newer message.
        # Without an explicit, evidenced supersedes relation, conflicting
        # statements are returned side-by-side and marked for human/Agent review.
        import re
        grouped = {}
        for candidate in candidates:
            action = re.sub(r'\s+', ' ', candidate['requirement']['action']).strip().casefold()
            grouped.setdefault(action, []).append(candidate)
        projection = []
        for action, group in grouped.items():
            current = [row for row in group if not row['stale']]
            def substance(requirement):
                value = {key: child for key, child in requirement.items() if key not in ('id', 'evidence')}
                value['materials'] = [
                    {key: child for key, child in material.items() if key != 'evidence'}
                    for material in requirement.get('materials', [])
                ]
                return value
            distinct = {encode(substance(row['requirement'])) for row in current}
            status = 'conflict' if len(distinct) > 1 else 'current_unconfirmed'
            for row in group:
                projection.append({**row, 'status': 'stale' if row['stale'] else status, 'conflict_group': action if status == 'conflict' and not row['stale'] else None})
        result['current_requirements'] = projection
        result['current_requirements_note'] = 'Conflicts remain side-by-side; newer publication time does not supersede prior requirements without explicit evidence.'
        result['source_progress'] = 'unknown'
        result['personal_completion'] = None
        return result

    if method == 'queue_fail':
        s.check(identity, 'execute', sub)
        s.check(identity, 'analyze', sub)
        principal = identity['id']
        def fail():
            run = s.engine._lease(args['run_id'], principal, args['epoch'])
            if run['subscription_id'] != sub:
                raise ValueError('run subscription mismatch')
            item = db.execute('SELECT * FROM run_items WHERE id=? AND run_id=?', (args['item_id'], args['run_id'])).fetchone()
            if not item or item['message_id'] != args['message_id'] or item['message_revision'] != args['message_revision']:
                raise Conflict('queue item no longer matches run evidence')
            fail_analysis(db, principal, sub, args['message_id'], args['message_revision'], args['reason'])
            db.execute("UPDATE run_items SET state='analysis_failed',error=? WHERE id=?", (args['reason'][:500], args['item_id']))
            return {'message_id': args['message_id'], 'state': 'retry'}
        return s.engine._write(principal, args['operation_key'], args, fail)

    if method == 'queue_prepare':
        s.check(identity, 'execute', sub)
        s.check(identity, 'analyze', sub)
        principal = identity['id']
        def prepare():
            run = s.engine._lease(args['run_id'], principal, args['epoch'])
            if run['subscription_id'] != sub:
                raise ValueError('run subscription mismatch')
            item = db.execute('SELECT * FROM run_items WHERE id=? AND run_id=?', (args['item_id'], args['run_id'])).fetchone()
            if not item or item['message_id'] != args['message_id'] or item['message_revision'] != args['message_revision']:
                raise Conflict('queue item no longer matches run evidence')
            current = db.execute('SELECT current_revision FROM messages WHERE id=?', (args['message_id'],)).fetchone()
            if not current or current[0] != args['message_revision']:
                raise Conflict('source revision changed before analysis')
            ensure_candidate(db, sub, args['message_id'], args['message_revision'])
            row = db.execute('SELECT * FROM curation_items WHERE subscription_id=? AND message_id=? AND message_revision=?', (sub, args['message_id'], args['message_revision'])).fetchone()
            if row['state'] in ('processed', 'not_included'):
                db.execute("UPDATE run_items SET state='analyzed',analysis_id=? WHERE id=?", (row['analysis_id'], args['item_id']))
                return {'should_analyze': False, 'state': row['state']}
            version = policy_version()
            revision = row['revision'] + 1
            db.execute("UPDATE curation_items SET state='processing',policy_version=?,attempts=attempts+1,revision=?,updated_at=? WHERE subscription_id=? AND message_id=? AND message_revision=?", (version, revision, now(), sub, args['message_id'], args['message_revision']))
            _history(db, principal, sub, args['message_id'], args['message_revision'], 'analysis_started', row['state'], 'processing', '', version)
            db.execute('INSERT INTO changes(kind,entity_id,revision,created_at) VALUES(?,?,?,?)', ('curation_item', args['message_id'], revision, now()))
            return {'should_analyze': True, 'state': 'processing'}
        return s.engine._write(principal, args['operation_key'], args, prepare)

    if method == 'queue_decide':
        decision = args['decision']
        if decision == 'reopen':
            if identity['authority'] != 'user':
                raise PermissionError('only a user principal can reopen a not-included candidate')
        def write():
            current = db.execute('SELECT current_revision FROM messages WHERE id=?', (args['message_id'],)).fetchone()
            if not current or current[0] != args['message_revision']:
                raise Conflict('candidate source revision changed')
            row = db.execute('SELECT * FROM curation_items WHERE subscription_id=? AND message_id=? AND message_revision=?', (sub, args['message_id'], args['message_revision'])).fetchone()
            if not row or row['revision'] != args['expected_revision']:
                raise Conflict('curation item revision')
            if row['state'] == 'processing':
                raise Conflict('candidate is currently processing')
            if decision == 'not_included':
                if row['state'] not in ('pending', 'retry', 'not_included'):
                    raise Conflict('only a pending candidate can be marked not included')
                new_state = 'not_included'
                stored_decision = 'not_included'
            else:
                if row['state'] != 'not_included':
                    raise Conflict('only a not-included candidate can be reopened')
                new_state = 'pending'
                stored_decision = None
            revision = row['revision'] + 1
            reason = args['reason']
            version = policy_version()
            db.execute('UPDATE curation_items SET state=?,decision=?,reason=?,policy_version=?,revision=?,updated_at=? WHERE subscription_id=? AND message_id=? AND message_revision=?', (new_state, stored_decision, reason, version, revision, now(), sub, args['message_id'], args['message_revision']))
            _history(db, identity['id'], sub, args['message_id'], args['message_revision'], 'queue_decide:' + decision, row['state'], new_state, reason, version)
            s.engine._change('curation_item', args['message_id'], revision)
            return {'message_id': args['message_id'], 'message_revision': args['message_revision'], 'state': new_state, 'revision': revision}
        return s.engine._write(identity['id'], args['operation_key'], args, write)

    if method in ('category_save', 'event_save', 'event_message_save'):
        key_value = args['operation_key']
        def write():
            if method in ('category_save', 'event_save'):
                table = 'product_categories' if method == 'category_save' else 'product_events'
                old = db.execute('SELECT * FROM ' + table + ' WHERE id=?', (args['id'],)).fetchone()
                if old and old['subscription_id'] != sub:
                    raise ValueError('entity unavailable in subscription')
                revision = old['revision'] if old else 0
                if revision != args['expected_revision']:
                    raise Conflict('entity revision')
                if method == 'category_save':
                    parent = args['parent_id']
                    seen = {args['id']}
                    while parent:
                        if parent in seen:
                            raise ValueError('category cycle')
                        seen.add(parent)
                        parent = scoped('product_categories', parent)['parent_id']
                    db.execute('INSERT INTO product_categories VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,parent_id=excluded.parent_id,revision=excluded.revision', (args['id'], sub, args['name'], args['parent_id'], revision + 1))
                else:
                    if args['category_id']:
                        scoped('product_categories', args['category_id'])
                    if old and old['status'] != 'active' and args['status'] == 'active' and identity['authority'] != 'user':
                        raise Conflict('only owner may restore a paused or archived event')
                    db.execute('INSERT INTO product_events VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,category_id=excluded.category_id,status=excluded.status,revision=excluded.revision', (args['id'], sub, args['name'], args['category_id'], args['status'], revision + 1))
                result = {'id': args['id'], 'revision': revision + 1}
            else:
                scoped('product_events', args['event_id'])
                a = db.execute('SELECT * FROM analyses WHERE id=? AND message_id=? AND subscription_id=?', (args['analysis_id'], args['message_id'], sub)).fetchone()
                if not a:
                    raise ValueError('association requires this subscription analysis as evidence')
                old = db.execute('SELECT revision FROM event_messages WHERE event_id=? AND message_id=?', (args['event_id'], args['message_id'])).fetchone()
                revision = old[0] if old else 0
                if revision != args['expected_revision']:
                    raise Conflict('association revision')
                db.execute('INSERT INTO event_messages VALUES(?,?,?,?,?,?,?) ON CONFLICT(event_id,message_id) DO UPDATE SET role=excluded.role,reason=excluded.reason,analysis_id=excluded.analysis_id,active=excluded.active,revision=excluded.revision', (args['event_id'], args['message_id'], args['role'], args['reason'], args['analysis_id'], int(args['active']), revision + 1))
                result = {'event_id': args['event_id'], 'message_id': args['message_id'], 'revision': revision + 1}
            db.execute('INSERT INTO product_audit(principal,method,payload,created_at) VALUES(?,?,?,?)', (identity['id'], method, encode(args), now()))
            s.engine._change(method, result.get('id', args.get('event_id')), result['revision'])
            return result
        return s.engine._write(identity['id'], key_value, args, write)

    raise ValueError('unknown product method')
