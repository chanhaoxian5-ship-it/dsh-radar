import unittest
import test_phase_a as phase
from radar.worker import Worker

class IncrementalTest(unittest.TestCase):
    setUp = phase.PhaseATest.setUp
    tearDown = phase.PhaseATest.tearDown
    call = phase.PhaseATest.call
    execute = phase.PhaseATest.execute
    plan = phase.PhaseATest.plan

    def start(self, key, trigger='manual'):
        return self.call(self.runner, 'run_start', operation_key=key, subscription_id='s', trigger=trigger)['run_id']

    def test_manual_discovery_skips_existing_but_explicit_recheck_detects_update(self):
        self.execute()
        mid = self.s.db.execute('SELECT id FROM messages').fetchone()[0]
        calls = len(self.adapter.calls)
        self.adapter.version_number = 2
        self.assertEqual(self.worker.step(self.start('discover'))['status'], 'succeeded')
        self.assertEqual(len(self.adapter.calls), calls, 'ordinary manual discovery must not reread article/assets')
        self.assertEqual(self.s.db.execute('SELECT current_revision FROM messages').fetchone()[0], 1)
        rid = self.call(self.runner, 'recheck', operation_key='explicit', subscription_id='s', message_id=mid)['run_id']
        self.assertEqual(Worker(self.s, self.runner, None).step(rid)['status'], 'awaiting_analysis')
        self.assertEqual(self.call(self.runner, 'formal_messages', subscription_id='s')[0]['needs_review'], True)
        self.assertEqual(self.worker.step(rid)['status'], 'succeeded')
        self.assertEqual(self.s.db.execute('SELECT current_revision FROM messages').fetchone()[0], 2)
        count = self.s.db.execute('SELECT COUNT(*) FROM analyses').fetchone()[0]
        rid = self.call(self.runner, 'recheck', operation_key='unchanged', subscription_id='s', message_id=mid)['run_id']
        self.assertEqual(self.worker.step(rid)['status'], 'succeeded')
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM analyses').fetchone()[0], count)

    def test_due_discovery_and_recheck_preserve_update_and_new_notice(self):
        self.execute()
        self.s.call(self.owner, 'review', self.plan())
        self.call(self.owner, 'discovery_plan', operation_key='dp', subscription_id='s', expected_revision=0, interval_seconds=3600, next_at='2020-01-01T00:00:00Z', state='active', authority='user', reason='SYNTHETIC')
        self.adapter.version_number = 2
        self.adapter.discover = lambda fetch=None: ['https://fixture.invalid/notice/1', 'https://fixture.invalid/notice/1', 'https://fixture.invalid/notice/2']
        rid = self.call(self.owner, 'tick', operation_key='tick', at='2026-11-07T00:00:00Z')['queued'][0]
        self.assertEqual(self.worker.step(rid)['status'], 'succeeded')
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 2)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM message_revisions').fetchone()[0], 3)

    def test_existing_pending_evidence_is_analyzed_without_capture(self):
        rid = self.start('collect')
        Worker(self.s, self.runner, None).step(rid)
        self.call(self.runner, 'cancel', operation_key='cancel', run_id=rid)
        calls = len(self.adapter.calls)
        self.assertEqual(self.worker.step(self.start('resume', 'event'))['status'], 'succeeded')
        self.assertEqual(len(self.adapter.calls), calls)
        self.assertEqual(len(self.call(self.runner, 'formal_messages', subscription_id='s')), 1)

    def test_discovery_can_merge_with_queued_recheck_without_being_lost(self):
        self.execute()
        mid = self.s.db.execute('SELECT id FROM messages').fetchone()[0]
        rid = self.call(self.runner, 'recheck', operation_key='rc', subscription_id='s', message_id=mid)['run_id']
        self.adapter.discover = lambda fetch=None: ['https://fixture.invalid/notice/1', 'https://fixture.invalid/notice/2']
        self.assertEqual(self.start('discover-merged'), rid)
        self.assertEqual(self.worker.step(rid)['status'], 'succeeded')
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 2)

    def test_recheck_does_not_silently_merge_into_analysis_retry(self):
        self.worker.analyze = lambda *args: (_ for _ in ()).throw(RuntimeError('SYNTHETIC interruption'))
        rid, result = self.execute()
        self.assertEqual(result['status'], 'queued')
        mid = self.s.db.execute('SELECT id FROM messages').fetchone()[0]
        from radar.engine import Conflict
        with self.assertRaises(Conflict):
            self.call(self.runner, 'recheck', operation_key='while-retrying', subscription_id='s', message_id=mid)
        self.worker.analyze = phase.analyze
        calls = len(self.adapter.calls)
        self.call(self.runner, 'retry_run', operation_key='retry', run_id=rid, expected_epoch=1)
        self.assertEqual(self.worker.step(rid)['status'], 'succeeded')
        self.assertEqual(len(self.adapter.calls), calls)
