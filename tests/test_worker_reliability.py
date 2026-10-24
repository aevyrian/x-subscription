import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from xarchive.store import Store
from xarchive.worker import DownloadError, Worker


class WorkerReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_finish_retries_preserve_result_without_downloading_again(self):
        self.store.submit('example')
        downloads = []

        def download(*args):
            downloads.append(True)
            return 2, 3

        worker = Worker(self.store, download)
        finish = self.store.finish
        failures = [True, True, True]

        def unavailable_then_finish(*args, **kwargs):
            if failures:
                failures.pop()
                raise sqlite3.OperationalError('fixture database unavailable')
            return finish(*args, **kwargs)

        with patch.object(self.store, 'finish', side_effect=unavailable_then_finish), \
                patch.object(worker.stop_event, 'wait', return_value=False):
            try:
                worker.run_one()
            except sqlite3.OperationalError:
                pass
        row = self.store.query('SELECT * FROM tasks')[0]
        self.assertEqual((row['status'], row['new_posts'], row['new_media']), ('succeeded', 2, 3))
        self.assertEqual(len(downloads), 1)

    def test_cancel_during_finish_retry_stays_cancelled(self):
        task, _ = self.store.submit('example')
        worker = Worker(self.store, lambda *args: (1, 1))
        finish = self.store.finish
        failures = [True]

        def unavailable_then_finish(*args, **kwargs):
            if failures:
                failures.pop()
                raise sqlite3.OperationalError('fixture database unavailable')
            return finish(*args, **kwargs)

        def cancel_during_wait(seconds):
            self.store.cancel(task['id'])
            return False

        with patch.object(self.store, 'finish', side_effect=unavailable_then_finish), \
                patch.object(worker.stop_event, 'wait', side_effect=cancel_during_wait):
            worker.run_one()
        self.assertEqual(self.store.query('SELECT status FROM tasks')[0]['status'], 'cancelled')
        self.store.recover()
        self.assertEqual(self.store.query('SELECT status FROM tasks')[0]['status'], 'cancelled')

    def test_shutdown_during_finish_failure_leaves_task_recoverable(self):
        self.store.submit('example')
        worker = Worker(self.store, lambda *args: (1, 1))
        returned = False
        with patch.object(self.store, 'finish', side_effect=sqlite3.OperationalError('fixture database unavailable')), \
                patch.object(worker.stop_event, 'wait', return_value=True):
            try:
                returned = worker.run_one()
            except sqlite3.OperationalError:
                pass
        self.assertTrue(returned)
        self.assertEqual(self.store.query('SELECT status FROM tasks')[0]['status'], 'running')
        Store(self.tmp.name).recover()
        row = self.store.query('SELECT status,attempts FROM tasks')[0]
        self.assertEqual((row['status'], row['attempts']), ('queued', 0))

    def test_rate_limit_does_not_spend_normal_failure_attempts(self):
        self.store.submit('example')
        self.store.execute('UPDATE tasks SET attempts=1')

        def limited(*args):
            raise DownloadError('X 请求限流', retry_after=900)

        worker = Worker(self.store, limited)
        for _ in range(4):
            self.store.execute('UPDATE tasks SET next_run=0')
            before = time.time()
            worker.run_one()
            row = self.store.query('SELECT * FROM tasks')[0]
            self.assertEqual((row['status'], row['attempts']), ('retry', 1))
            self.assertGreaterEqual(row['next_run'], before + 900)
            self.assertEqual(row['error'], 'X 请求限流')

    def test_health_detects_stale_worker_and_polling_refreshes_it(self):
        task, _ = self.store.submit('example')
        self.store.claim()
        worker = Worker(self.store)
        healthy = getattr(worker, 'healthy', lambda: True)
        self.assertFalse(healthy())
        worker.thread = threading.current_thread()
        worker.last_tick = 0
        with patch('xarchive.worker.time.monotonic', return_value=120):
            self.assertTrue(healthy())
        with patch('xarchive.worker.time.monotonic', return_value=600):
            self.assertFalse(healthy())
            self.assertFalse(worker.interrupted(task['id']))
            self.assertTrue(healthy())


if __name__ == '__main__':
    unittest.main()
