import json
import unittest

import test_phase_a as phase
from radar.engine import Conflict
from radar.service import Forbidden


class ProductTest(unittest.TestCase):
    setUp = phase.PhaseATest.setUp
    tearDown = phase.PhaseATest.tearDown
    call = phase.PhaseATest.call
    execute = phase.PhaseATest.execute

    def event(self, event_id):
        return self.call(
            self.owner, 'event_save', operation_key='event-' + event_id,
            subscription_id='s', id=event_id, expected_revision=0,
            name=event_id, category_id=None, status='active',
        )

    def test_shared_notice_associations_and_unlink_preserve_evidence(self):
        self.execute()
        analysis = self.s.db.execute('SELECT * FROM analyses').fetchone()
        self.event('one')
        self.event('two')
        for event_id in ('one', 'two'):
            self.call(
                self.runner, 'event_message_save', operation_key='link-' + event_id,
                subscription_id='s', event_id=event_id, message_id=analysis['message_id'],
                analysis_id=analysis['id'], expected_revision=0, role='application',
                reason='SYNTHETIC analysis evidence', active=True,
            )
        self.call(
            self.runner, 'event_message_save', operation_key='unlink', subscription_id='s',
            event_id='one', message_id=analysis['message_id'], analysis_id=analysis['id'],
            expected_revision=1, role='application', reason='SYNTHETIC unlink', active=False,
        )
        self.assertEqual(self.call(self.runner, 'event', subscription_id='s', event_id='one')['timeline'], [])
        self.assertEqual(len(self.call(self.runner, 'event', subscription_id='s', event_id='two')['timeline']), 1)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM message_revisions').fetchone()[0], 1)
        detail = self.call(self.runner, 'event', subscription_id='s', event_id='two')
        self.assertEqual(detail['source_progress'], 'unknown')
        self.assertIsNone(detail['personal_completion'])
        self.assertEqual(detail['current_requirements'][0]['status'], 'current_unconfirmed')
        foreign = self.s.provision(['read'], ['other'])
        with self.assertRaises(Forbidden):
            self.call(foreign, 'event', subscription_id='s', event_id='two')

    def test_category_cycles_and_event_human_pause(self):
        def category(category_id, parent, revision):
            return self.call(
                self.owner, 'category_save', operation_key=category_id + str(revision),
                subscription_id='s', id=category_id, name=category_id,
                parent_id=parent, expected_revision=revision,
            )

        category('a', None, 0)
        category('b', 'a', 0)
        with self.assertRaises(ValueError):
            category('a', 'b', 1)
        self.event('one')
        self.call(
            self.owner, 'event_save', operation_key='pause', subscription_id='s',
            id='one', expected_revision=1, name='one', category_id=None, status='paused',
        )
        agent = self.s.provision(['read', 'manage'], ['s'])
        with self.assertRaises(Conflict):
            self.call(
                agent, 'event_save', operation_key='restore', subscription_id='s',
                id='one', expected_revision=2, name='one', category_id=None, status='active',
            )

    def test_formal_projection_excludes_unprocessed_and_keeps_stale_summary(self):
        from radar.worker import Worker

        run_id = self.call(self.runner, 'run_start', operation_key='run', subscription_id='s', trigger='manual')['run_id']
        Worker(self.s, self.runner, None).step(run_id)
        self.assertEqual(self.call(self.runner, 'formal_messages', subscription_id='s'), [])
        self.assertEqual(self.call(self.runner, 'processing_queue', subscription_id='s')[0]['processing_state'], 'pending')
        self.worker.step(run_id)
        rows = self.call(self.runner, 'formal_messages', subscription_id='s')
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['needs_review'])
        self.adapter.version_number = 2
        run_id2 = self.call(self.runner, 'recheck', operation_key='run2', subscription_id='s', message_id=rows[0]['id'])['run_id']
        Worker(self.s, self.runner, None).step(run_id2)
        self.assertTrue(self.call(self.runner, 'formal_messages', subscription_id='s')[0]['needs_review'])

    def test_analysis_failure_moves_to_retry_and_reuses_collection(self):
        from radar.worker import Worker

        def fail(*args):
            raise RuntimeError('SYNTHETIC model interruption')

        run_id = self.call(self.runner, 'run_start', operation_key='retry-run', subscription_id='s', trigger='manual')['run_id']
        before = len(self.adapter.calls)
        self.assertEqual(Worker(self.s, self.runner, fail).step(run_id)['status'], 'queued')
        queue = self.call(self.runner, 'processing_queue', subscription_id='s')
        self.assertEqual(queue[0]['state'], 'retry')
        self.assertEqual(queue[0]['attempts'], 1)
        after_failure = len(self.adapter.calls)
        self.assertGreater(after_failure, before)
        self.assertEqual(Worker(self.s, self.runner, phase.analyze).step(run_id)['status'], 'succeeded')
        self.assertEqual(len(self.adapter.calls), after_failure)
        self.assertEqual(self.call(self.runner, 'processing_queue', subscription_id='s', state='processed')[0]['state'], 'processed')
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM curation_history WHERE action='analysis_failed'").fetchone()[0], 1)

    def test_not_included_can_be_reopened_only_by_user(self):
        from radar.worker import Worker

        def irrelevant(snapshot, item, subscription):
            result = phase.analyze(snapshot, item, subscription)
            result['decision'] = 'irrelevant'
            result['reason'] = 'SYNTHETIC out of scope'
            return result

        run_id = self.call(self.runner, 'run_start', operation_key='exclude-run', subscription_id='s', trigger='manual')['run_id']
        self.assertEqual(Worker(self.s, self.runner, irrelevant).step(run_id)['status'], 'succeeded')
        item = self.call(self.runner, 'processing_queue', subscription_id='s')[0]
        self.assertEqual(item['state'], 'not_included')
        self.assertIn('out of scope', item['reason'])
        agent = self.s.provision(['read', 'analyze'], ['s'])
        with self.assertRaises(PermissionError):
            self.call(
                agent, 'queue_decide', operation_key='agent-reopen', subscription_id='s',
                message_id=item['message_id'], message_revision=item['message_revision'],
                expected_revision=item['revision'], decision='reopen', reason='SYNTHETIC retry',
            )
        self.call(
            self.owner, 'queue_decide', operation_key='user-reopen', subscription_id='s',
            message_id=item['message_id'], message_revision=item['message_revision'],
            expected_revision=item['revision'], decision='reopen', reason='SYNTHETIC reconsider',
        )
        run_id2 = self.call(self.runner, 'run_start', operation_key='reopened-run', subscription_id='s', trigger='manual')['run_id']
        self.assertEqual(Worker(self.s, self.runner, phase.analyze).step(run_id2)['status'], 'succeeded')
        self.assertEqual(self.call(self.runner, 'processing_queue', subscription_id='s', state='processed')[0]['state'], 'processed')
        self.assertEqual(len(self.call(self.runner, 'formal_messages', subscription_id='s')), 1)

    def test_processing_lease_recovery_creates_retry_state(self):
        from radar.worker import Worker

        run_id = self.call(self.runner, 'run_start', operation_key='handoff-run', subscription_id='s', trigger='manual')['run_id']
        self.assertEqual(Worker(self.s, self.runner, None).step(run_id)['status'], 'awaiting_analysis')
        run = self.call(self.runner, 'run', run_id=run_id)
        item = run['items'][0]
        lease = self.call(self.runner, 'claim', operation_key='agent-claim', run_id=run_id)
        self.call(
            self.runner, 'queue_prepare', operation_key='agent-prepare', subscription_id='s',
            run_id=run_id, item_id=item['id'], epoch=lease['epoch'],
            message_id=item['message_id'], message_revision=item['message_revision'],
        )
        owner_id = self.s.authenticate(self.owner)['id']
        self.s.engine.startup_recover(owner_id, 'SYNTHETIC-restart')
        queue = self.call(self.runner, 'processing_queue', subscription_id='s')
        self.assertEqual(queue[0]['state'], 'retry')
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM curation_history WHERE action='execution_recovered'").fetchone()[0], 1)

    def test_resource_failure_reopens_processing_without_losing_old_analysis(self):
        self.execute()
        self.adapter.fail_asset = True
        run_id = self.call(self.runner, 'recheck', operation_key='resource-unknown', subscription_id='s', message_id=self.s.db.execute('SELECT id FROM messages').fetchone()[0])['run_id']
        from radar.worker import Worker
        self.assertEqual(Worker(self.s, self.runner, None).step(run_id)['status'], 'awaiting_analysis')
        queue = self.call(self.runner, 'processing_queue', subscription_id='s')
        self.assertEqual(queue[0]['state'], 'pending')
        self.assertIn('unknown', queue[0]['reason'])
        formal = self.call(self.runner, 'formal_messages', subscription_id='s')
        self.assertTrue(formal[0]['resource_state_unknown'])
        self.assertTrue(formal[0]['needs_review'])
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM analyses').fetchone()[0], 1)

    def test_event_keeps_competing_current_requirements_and_flags_stale_basis(self):
        from radar.worker import Worker

        self.execute()
        first = self.s.db.execute("SELECT * FROM analyses WHERE message_revision=1").fetchone()
        self.event('award')
        self.call(
            self.runner, 'event_message_save', operation_key='event-first', subscription_id='s',
            event_id='award', message_id=first['message_id'], analysis_id=first['id'],
            expected_revision=0, role='application', reason='SYNTHETIC first notice', active=True,
        )
        self.adapter.discover = lambda fetch=None: [
            'https://fixture.invalid/notice/1', 'https://fixture.invalid/notice/2',
        ]
        self.adapter.version_number = 2
        original_fetch = self.adapter.fetch
        def fetch(url):
            response = original_fetch(url)
            if url.endswith('/notice/2'):
                content = json.loads(response.body)
                content['blocks'][0]['text'] = '11月20日前由学生提交开题报告，导师签字后交至教学办公室。'
                response.body = json.dumps(content, ensure_ascii=False).encode('utf-8')
            return response
        self.adapter.fetch = fetch
        run_id = self.call(self.runner, 'run_start', operation_key='multi-run', subscription_id='s', trigger='manual')['run_id']
        self.call(self.runner, 'recheck', operation_key='multi-recheck', subscription_id='s', message_id=first['message_id'])
        self.assertEqual(Worker(self.s, self.runner, phase.analyze).step(run_id)['status'], 'succeeded')
        analyses = self.s.db.execute("SELECT * FROM analyses ORDER BY rowid").fetchall()
        self.assertEqual(len(analyses), 3, 'analyses='+repr([dict(a) for a in analyses])+' run='+repr(self.call(self.runner, 'run', run_id=run_id)))
        second = next(a for a in analyses if a['message_id'] != first['message_id'])
        self.call(
            self.runner, 'event_message_save', operation_key='event-second', subscription_id='s',
            event_id='award', message_id=second['message_id'], analysis_id=second['id'],
            expected_revision=0, role='announcement', reason='SYNTHETIC additional notice', active=True,
        )
        updated = max((a for a in analyses if a['message_id'] == first['message_id']), key=lambda a: a['message_revision'])
        self.call(
            self.runner, 'event_message_save', operation_key='event-update-old', subscription_id='s',
            event_id='award', message_id=first['message_id'], analysis_id=updated['id'],
            expected_revision=1, role='correction', reason='SYNTHETIC correction label is not replacement proof', active=True,
        )
        detail = self.call(self.runner, 'event', subscription_id='s', event_id='award')
        self.assertEqual(len(detail['timeline']), 2)
        self.assertTrue(all(not row['stale'] for row in detail['timeline']))
        self.assertEqual({row['status'] for row in detail['current_requirements']}, {'conflict'})
        self.assertEqual(len({row['message_id'] for row in detail['current_requirements']}), 2)
        self.assertIn('without explicit evidence', detail['current_requirements_note'])
