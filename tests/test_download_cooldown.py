from datetime import datetime
import tempfile
import time
import unittest

from xarchive.store import LOCAL_TIME, Store


class DownloadCooldownTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(self.directory.name)

    def test_real_limit_pauses_every_source_and_survives_restart(self):
        self.store.submit('limited')
        self.store.submit('manual')
        job = self.store.claim()
        self.store.finish(job, error='X 请求限流', retry_after=900)
        until = self.store.setting('x_rate_limit_until')
        self.assertIsInstance(until, (float, int))
        self.assertGreater(until, time.time() + 890)
        self.assertIsNone(self.store.claim())
        self.assertIsNone(Store(self.directory.name).claim())
        self.assertEqual(self.store.list_tasks()['cooldown_until'], until)
        self.assertIsNotNone(self.store.claim(timestamp=until + 1))
        self.assertIsNone(self.store.claim(timestamp=until - 1))

    def test_stale_finish_keeps_account_limit_without_overwriting_new_generation(self):
        task, _ = self.store.submit('example')
        old = self.store.claim()
        self.store.cancel(task['id'])
        self.store.retry(task['id'])
        current = self.store.claim()
        self.store.finish(old, error='X 请求限流', retry_after=900)
        self.assertGreater(self.store.setting('x_rate_limit_until', 0), time.time())
        row = self.store.query('SELECT status,generation FROM tasks')[0]
        self.assertEqual((row['status'], row['generation']), ('running', current['generation']))

    def test_cancelled_or_deleted_task_still_preserves_same_account_limit(self):
        for delete in (False, True):
            with self.subTest(delete=delete):
                self.store.set_setting('x_rate_limit_until', 0)
                task, _ = self.store.submit('example')
                claimed = self.store.claim()
                self.store.cancel(task['id'])
                if delete:
                    self.store.delete_task(task['id'])
                self.store.finish(claimed, error='X 请求限流', retry_after=900)
                self.assertGreater(self.store.setting('x_rate_limit_until'), time.time())
                self.assertIsNone(self.store.claim())

    def test_old_cookie_result_cannot_restore_cleared_account_limit(self):
        self.store.submit('example')
        old = self.store.claim()
        self.store.set_setting('x_cookie_revision', 1)
        self.store.finish(old, error='X 请求限流', retry_after=900)
        self.assertFalse(self.store.setting('x_rate_limit_until', 0))
        self.assertLess(self.store.query('SELECT next_run FROM tasks')[0]['next_run'], time.time() + 5)

    def test_cooldown_stops_automatic_dispatch_without_moving_plan(self):
        task, _ = self.store.subscribe('example')
        self.store.cancel(task['id'])
        now = datetime(2026, 10, 25, 10, tzinfo=LOCAL_TIME).timestamp()
        self.store.execute('UPDATE subscriptions SET next_run=?', (now,))
        self.store.set_setting('x_rate_limit_until', now + 900)
        self.assertEqual(self.store.schedule(timestamp=now), 0)
        self.assertEqual(self.store.query('SELECT next_run FROM subscriptions')[0]['next_run'], now)
        self.assertFalse(self.store.query("SELECT 1 FROM tasks WHERE source='scheduled'"))

    def test_complete_update_time_survives_task_deletion_and_single_post(self):
        task, _ = self.store.submit('example')
        complete = self.store.claim()
        self.store.finish(complete, result=(0, 0))
        updated = self.store.query('SELECT last_success_at FROM bloggers')[0]['last_success_at']
        self.assertGreater(updated, 0)
        self.store.delete_task(task['id'])
        self.assertEqual(Store(self.directory.name).query('SELECT last_success_at FROM bloggers')[0]['last_success_at'], updated)
        self.store.submit('https://x.com/example/status/123')
        single = self.store.claim()
        self.store.finish(single, result=(0, 0))
        self.assertEqual(self.store.query('SELECT last_success_at FROM bloggers')[0]['last_success_at'], updated)

    def test_success_history_backfills_once_and_failed_time_is_not_used(self):
        self.store.submit('example')
        complete = self.store.claim()
        self.store.finish(complete, result=(0, 0))
        finished = self.store.query('SELECT finished FROM tasks')[0]['finished']
        self.store.execute('ALTER TABLE bloggers DROP COLUMN last_success_at')
        upgraded = Store(self.directory.name)
        self.assertEqual(upgraded.query('SELECT last_success_at FROM bloggers')[0]['last_success_at'], finished)
        self.store.submit('example')
        failed = self.store.claim()
        self.store.finish(failed, error='Fixture unavailable')
        self.assertEqual(upgraded.query('SELECT last_success_at FROM bloggers')[0]['last_success_at'], finished)

    def test_at_handle_task_filter_is_exact_and_case_insensitive(self):
        self.store.submit('example')
        self.store.submit('example_two')
        self.assertEqual(self.store.list_tasks(q='@ExAmPlE')['total'], 1)
        self.assertEqual(self.store.bulk_tasks('cancel', q='@ExAmPlE')['affected'], 1)
        self.assertEqual(self.store.list_tasks(task_filter='active')['items'][0]['user'], 'example_two')


if __name__ == '__main__':
    unittest.main()
