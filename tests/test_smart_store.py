import tempfile
import time
import unittest
from unittest.mock import patch

from xarchive.store import Store
from xarchive.worker import BatchComplete, HistoryConfirmation, Worker


class SmartStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)

    def awaiting(self, user='example'):
        task, _ = self.store.submit(user)
        self.store.execute("UPDATE tasks SET status='awaiting_confirmation',crawl_mode='media',media_posts=1001 WHERE id=?", (task['id'],))
        return task

    def test_history_and_incremental_are_independent_and_incremental_has_priority(self):
        history, _ = self.store.subscribe('example')
        incremental, duplicate = self.store.submit('example', source='scheduled')
        self.assertFalse(duplicate)
        self.assertEqual(incremental['task_kind'], 'incremental')
        self.assertIsNotNone(incremental['since_id'])
        self.store.execute("UPDATE tasks SET source='website'")
        self.assertEqual(self.store.claim()['id'], incremental['id'])
        self.assertEqual(self.store.claim()['id'], history['id'])

    def test_failed_partial_incremental_does_not_advance_next_boundary(self):
        self.store.execute("INSERT INTO bloggers(user,created,history_status) VALUES ('existing',0,'legacy')")
        self.store.execute("INSERT INTO posts(id,user,url,added) VALUES ('100','existing','fixture',0)")
        first, _ = self.store.submit('existing')
        self.assertEqual(first['since_id'], '100')
        self.store.execute("INSERT INTO posts(id,user,url,added) VALUES ('300','existing','partial',0)")
        self.store.execute("UPDATE tasks SET status='failed' WHERE id=?", (first['id'],))
        second, _ = self.store.submit('existing')
        self.assertEqual(second['since_id'], '100')

    def test_single_post_does_not_skip_first_blogger_history(self):
        single, _ = self.store.submit('https://x.com/example/status/123')
        self.store.finish(self.store.claim())
        history, _ = self.store.subscribe('example')
        self.assertEqual(history['task_kind'], 'history')
        self.assertNotEqual(single['id'], history['id'])

    def test_first_legacy_subscription_runs_history_once(self):
        self.store.execute("INSERT INTO bloggers(user,created,history_status) VALUES ('existing',0,'legacy')")
        self.store.execute("INSERT INTO posts(id,user,url,added) VALUES ('100','existing','fixture',0)")
        before = time.time()
        history, duplicate = self.store.subscribe('existing')
        self.assertFalse(duplicate)
        self.assertEqual((history['task_kind'], history['status'], history['limit_count']), ('history', 'queued', 0))
        self.assertIsNone(history['since_id'])
        self.assertEqual(history['history_choice'], '')
        self.assertGreaterEqual(history['next_run'], before)
        self.assertLessEqual(history['next_run'], time.time())
        same, duplicate = self.store.subscribe('existing')
        self.assertTrue(duplicate)
        self.assertEqual(same['id'], history['id'])
        claimed = self.store.claim()
        self.assertEqual(claimed['id'], history['id'])
        self.store.finish(claimed)
        incremental, duplicate = self.store.subscribe('existing')
        self.assertFalse(duplicate)
        self.assertEqual((incremental['task_kind'], incremental['since_id']), ('incremental', '100'))
        self.assertEqual(self.store.query("SELECT COUNT(*) n FROM tasks WHERE task_kind='history'")[0]['n'], 1)
        self.assertEqual(self.store.query('SELECT id FROM posts'), [{'id': '100'}])

    def test_existing_legacy_subscription_stays_incremental(self):
        self.store.execute("INSERT INTO bloggers(user,created,history_status) VALUES ('existing',0,'legacy')")
        self.store.execute("INSERT INTO subscriptions(user,next_run) VALUES ('existing',9999999999)")
        task, _ = self.store.subscribe('existing')
        self.assertEqual(task['task_kind'], 'incremental')
        self.assertEqual(self.store.query('SELECT history_status FROM bloggers')[0]['history_status'], 'legacy')

    def test_first_subscription_keeps_explicit_history_decisions(self):
        for state in ('complete', 'recent300', 'skipped'):
            with self.subTest(state=state):
                self.store.execute('INSERT INTO bloggers(user,created,history_status) VALUES (?,0,?)', (state, state))
                task, _ = self.store.subscribe(state)
                self.assertEqual(task['task_kind'], 'incremental')
                self.assertEqual(self.store.query('SELECT history_status FROM bloggers WHERE user=?', (state,))[0]['history_status'], state)

    def test_confirmation_cannot_be_bypassed_by_intake_retry_or_restart(self):
        task = self.awaiting()
        same, duplicate = self.store.submit('example')
        self.assertTrue(duplicate)
        self.assertEqual(same['id'], task['id'])
        same, duplicate = self.store.subscribe('example')
        self.assertTrue(duplicate)
        self.assertEqual((same['id'], same['status']), (task['id'], 'awaiting_confirmation'))
        with self.assertRaises(ValueError):
            self.store.retry(task['id'])
        self.assertEqual(self.store.bulk_tasks('retry')['affected'], 0)
        reopened = Store(self.tmp.name)
        reopened.recover()
        self.assertIsNone(reopened.claim())
        self.assertEqual(reopened.list_tasks('confirmation')['total'], 1)
        self.assertEqual(reopened.list_tasks('attention')['total'], 1)

    def test_cancelled_confirmation_retry_restores_original_choice_and_cache(self):
        task = self.awaiting()
        self.store.execute("INSERT INTO crawl_posts(task_id,tweet_id,payload,has_media) VALUES (?, '123','{}',1)", (task['id'],))
        self.store.cancel(task['id'])
        self.store.retry(task['id'])
        row = self.store.query('SELECT status,media_posts FROM tasks WHERE id=?', (task['id'],))[0]
        self.assertEqual(row, {'status': 'awaiting_confirmation', 'media_posts': 1001})
        self.assertEqual(len(self.store.query('SELECT * FROM crawl_posts WHERE task_id=?', (task['id'],))), 1)
        self.assertEqual(self.store.confirm_history(task['id'], 'all')['status'], 'queued')

    def test_cancelled_confirmation_bulk_retry_and_conflict(self):
        first = self.awaiting('first')
        blocked = self.awaiting('blocked')
        self.store.cancel(first['id'])
        self.store.cancel(blocked['id'])
        self.store.submit('blocked')
        result = self.store.bulk_tasks('retry')
        self.assertEqual(result, {'affected': 1, 'skipped': 2, 'matched': 3})
        self.assertEqual(self.store.query('SELECT status FROM tasks WHERE id=?', (first['id'],))[0]['status'], 'awaiting_confirmation')
        with self.assertRaises(ValueError):
            self.store.retry(blocked['id'])

    def test_media_history_does_not_advance_incremental_baseline(self):
        now = 1700000000
        baseline = str((now * 1000 - 1288834974657) << 22)
        with patch('xarchive.store.time.time', return_value=now):
            history, _ = self.store.subscribe('example')
            claimed = self.store.claim()
            newest = str(int(baseline) + 200)
            self.store.execute("UPDATE tasks SET crawl_mode='media' WHERE id=?", (history['id'],))
            self.store.execute("INSERT INTO crawl_posts(task_id,tweet_id,payload,has_media,completed) VALUES (?, ?, '{}', 1, 1)", (history['id'], newest))
            self.store.finish(claimed)
            incremental, _ = self.store.submit('example', source='update')
        self.assertEqual(incremental['since_id'], baseline)
        self.assertLess(int(incremental['since_id']), int(baseline) + 100)

    def test_old_running_history_cannot_advance_inflight_incremental_watermark(self):
        now = 1700000000
        baseline = str((now * 1000 - 1288834974657) << 22)
        with patch('xarchive.store.time.time', return_value=now):
            history, _ = self.store.subscribe('example')
            claimed = self.store.claim()
            incremental, _ = self.store.submit('example', source='update')
            self.store.execute("INSERT INTO crawl_posts(task_id,tweet_id,payload,has_media,completed) VALUES (?, ?, '{}', 1, 1)", (history['id'], str(int(baseline) + 200)))
            self.store.finish(claimed)
            self.store.cancel(incremental['id'])
            replacement, _ = self.store.submit('example', source='update')
        self.assertEqual(replacement['since_id'], baseline)

    def test_old_history_without_watermark_uses_creation_time_for_incremental(self):
        now = 1700000000
        baseline = str((now * 1000 - 1288834974657) << 22)
        with patch('xarchive.store.time.time', return_value=now):
            history, _ = self.store.subscribe('example')
            self.store.execute('UPDATE bloggers SET high_water_id=NULL')
            self.store.execute("INSERT INTO posts(id,user,url,added) VALUES (?, 'example', 'fixture', 0)", (str(int(baseline) + 200),))
            incremental, _ = self.store.submit('example', source='update')
        self.assertEqual(incremental['since_id'], baseline)

    def test_successful_incremental_advances_its_own_watermark(self):
        history, _ = self.store.subscribe('example')
        self.store.claim()
        incremental, _ = self.store.submit('example', source='update')
        claimed = self.store.claim()
        newer = str(int(incremental['since_id']) + 200)
        self.store.execute("INSERT INTO crawl_posts(task_id,tweet_id,payload,has_media,completed) VALUES (?, ?, '{}', 0, 1)", (incremental['id'], newer))
        self.store.finish(claimed)
        self.assertEqual(self.store.query('SELECT high_water_id FROM bloggers')[0]['high_water_id'], newer)

    def test_all_and_recent300_choices_are_persisted_without_resetting_progress(self):
        for choice in ('all', 'recent300'):
            with self.subTest(choice=choice):
                task = self.awaiting(choice)
                self.store.execute("UPDATE tasks SET cursor='page2',scanned_posts=1001,progress=2 WHERE id=?", (task['id'],))
                row = self.store.confirm_history(task['id'], choice)
                self.assertEqual((row['status'], row['history_choice']), ('queued', choice))
                self.assertEqual((row['cursor'], row['scanned_posts'], row['progress']), ('page2', 1001, 2))
                with self.assertRaises(ValueError):
                    self.store.confirm_history(task['id'], choice)

    def test_skip_preserves_archive_and_subscription(self):
        task, _ = self.store.subscribe('example')
        self.store.execute("UPDATE tasks SET status='awaiting_confirmation',media_posts=1001")
        self.store.execute("INSERT INTO posts(id,user,url,added) VALUES ('123','example','fixture',0)")
        self.assertEqual(self.store.confirm_history(task['id'], 'skip')['status'], 'cancelled')
        self.assertEqual(self.store.query('SELECT enabled FROM subscriptions')[0]['enabled'], 1)
        self.assertEqual(self.store.query('SELECT COUNT(*) n FROM posts')[0]['n'], 1)
        self.assertEqual(self.store.submit('example')[0]['task_kind'], 'incremental')
        with self.assertRaises(ValueError):
            self.store.retry(task['id'])
        self.assertEqual(self.store.bulk_tasks('retry')['affected'], 0)

    def test_task_limit_yields_other_tasks_above_and_below600(self):
        for scanned in (599, 600, 601):
            with self.subTest(scanned=scanned):
                limited, _ = self.store.submit('limited' + str(scanned))
                other, _ = self.store.submit('other' + str(scanned))
                task = self.store.claim()
                self.store.execute("UPDATE tasks SET scanned_posts=?,cursor='page3' WHERE id=?", (scanned, task['id']))
                self.store.finish(task, error='媒体端点限流', retry_after=120, cooldown_scope='task')
                self.assertEqual(self.store.claim()['id'], other['id'])
                self.assertFalse(self.store.setting('x_rate_limit_until', 0))
                self.store.retry(limited['id'])
                self.assertIsNone(self.store.claim())
                row = self.store.query('SELECT * FROM tasks WHERE id=?', (limited['id'],))[0]
                self.assertEqual((row['cursor'], row['scanned_posts']), ('page3', scanned))
                self.assertGreater(row['cooldown_until'], time.time() + 110)
                self.store.cancel(limited['id'])

    def test_shared_limit_stops_history_and_incremental_after_restart(self):
        self.store.subscribe('example')
        self.store.submit('other')
        task = self.store.claim()
        self.store.finish(task, error='X API 限流', retry_after=1200, cooldown_scope='shared')
        self.store.submit('example', source='scheduled')
        self.assertIsNone(self.store.claim())
        reopened = Store(self.tmp.name)
        reopened.recover()
        self.assertIsNone(reopened.claim())
        self.assertGreater(reopened.list_tasks()['cooldown_until'], time.time() + 1190)

    def test_worker_batch_and_confirmation_are_not_failure_or_success(self):
        task, _ = self.store.submit('example')
        def batch(*args):
            raise BatchComplete()
        Worker(self.store, batch).run_one()
        row = self.store.query('SELECT * FROM tasks')[0]
        self.assertEqual((row['status'], row['attempts']), ('queued', 0))
        self.store.execute('UPDATE tasks SET next_run=0')
        def confirm(*args):
            raise HistoryConfirmation()
        Worker(self.store, confirm).run_one()
        row = self.store.query('SELECT * FROM tasks')[0]
        self.assertEqual(row['status'], 'awaiting_confirmation')
        self.assertIsNone(self.store.query('SELECT last_success_at FROM bloggers')[0]['last_success_at'])

    def test_recover_keeps_cursor_and_distinct_tweet_counts(self):
        task, _ = self.store.submit('example')
        claimed = self.store.claim()
        self.store.execute("UPDATE tasks SET cursor='page4',scanned_posts=601,progress=600 WHERE id=?", (task['id'],))
        self.store.execute("INSERT INTO crawl_posts(task_id,tweet_id,payload,has_media,completed) VALUES (?, '123','{}',1,1)", (task['id'],))
        reopened = Store(self.tmp.name)
        reopened.recover()
        row = reopened.claim()
        self.assertEqual((row['cursor'], row['scanned_posts'], row['progress']), ('page4', 601, 600))
        self.assertNotEqual(row['generation'], claimed['generation'])
        self.assertEqual(len(reopened.query('SELECT * FROM crawl_posts')), 1)

    def test_legacy_schema_upgrade_preserves_active_tasks_and_data(self):
        # The previous release schema is reconstructed without loading a second app.
        with self.store.connect() as db:
            db.execute('DROP TABLE crawl_posts')
            db.execute('DROP INDEX tasks_active')
            for column in ('task_kind', 'crawl_mode', 'history_choice', 'total_posts', 'media_posts',
                           'media_complete', 'probe_complete', 'cursor', 'since_id', 'cooldown_scope',
                           'cooldown_reason', 'cooldown_until'):
                db.execute('ALTER TABLE tasks DROP COLUMN ' + column)
            db.execute("INSERT INTO bloggers(user,created) VALUES ('legacy',0)")
            db.execute("INSERT INTO tasks(id,user,url,limit_count,source,next_run,created,status,progress) VALUES ('old','legacy','https://x.com/legacy/timeline',0,'website',1,1,'retry',42)")
            db.execute("INSERT INTO posts(id,user,url,added,note,favorite) VALUES ('123','legacy','fixture',0,'keep',1)")
            db.execute("INSERT INTO media(post_id,path,kind,size) VALUES ('123','keep.jpg','image',10)")
            db.execute("INSERT INTO subscriptions(user,next_run,enabled) VALUES ('legacy',12345,0)")
        upgraded = Store(self.tmp.name)
        row = upgraded.query('SELECT * FROM tasks')[0]
        self.assertEqual((row['id'], row['status'], row['progress'], row['task_kind']), ('old','retry',42,'legacy'))
        self.assertEqual(upgraded.query('SELECT note,favorite FROM posts'), [{'note':'keep','favorite':1}])
        self.assertEqual(upgraded.query('SELECT path FROM media'), [{'path':'keep.jpg'}])
        self.assertEqual(upgraded.query('SELECT next_run,enabled FROM subscriptions'), [{'next_run':12345,'enabled':0}])
        self.assertEqual(upgraded.query('PRAGMA quick_check')[0]['quick_check'], 'ok')
        self.assertEqual(upgraded.query('PRAGMA foreign_key_check'), [])


if __name__ == '__main__':
    unittest.main()
