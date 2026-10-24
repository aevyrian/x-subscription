import tempfile
import unittest

from xarchive.store import Store
from xarchive.worker import Interrupted, Worker


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)

    def task(self, user='example', status='queued'):
        task, _ = self.store.submit(user)
        self.store.execute('UPDATE tasks SET status=? WHERE id=?', (status, task['id']))
        return task

    def test_filter_counts_and_bulk_cancel_cover_every_page(self):
        self.assertTrue(callable(getattr(self.store, 'list_tasks', None)))
        self.assertTrue(callable(getattr(self.store, 'bulk_tasks', None)))
        for n in range(230):
            self.task('needle' + str(n), ('queued', 'running', 'retry', 'failed', 'succeeded', 'cancelled')[n % 6])
        self.task('unrelated')
        result = self.store.list_tasks('attention', 'needle', limit=2)
        self.assertEqual(result['counts'], {'all': 230, 'active': 116, 'attention': 76, 'done': 38})
        self.assertEqual((result['total'], len(result['items'])), (76, 2))
        self.assertTrue(all('archive_posts' in task for task in result['items']))
        changed = self.store.bulk_tasks('cancel', 'all', 'needle')
        self.assertEqual(changed, {'affected': 154, 'skipped': 76, 'matched': 230})
        self.assertEqual(self.store.query("SELECT status FROM tasks WHERE user='unrelated'")[0]['status'], 'queued')

    def test_delete_skips_active_and_preserves_archive_and_subscription(self):
        self.assertTrue(callable(getattr(self.store, 'delete_task', None)))
        task = self.task(status='failed')
        active = self.task('active')
        self.store.execute("INSERT INTO subscriptions(user,next_run,last_task) VALUES ('example',0,?)", (task['id'],))
        self.store.execute("INSERT INTO posts(id,user,url,added) VALUES ('123','example','fixture',0)")
        self.store.execute("INSERT INTO media(post_id,path,kind,size) VALUES ('123','fixture.jpg','image',10)")
        with self.assertRaises(ValueError):
            self.store.delete_task(active['id'])
        self.assertEqual(self.store.bulk_tasks('delete'), {'affected': 1, 'skipped': 1, 'matched': 2})
        self.assertEqual(self.store.query('SELECT last_task FROM subscriptions'), [{'last_task': None}])
        self.assertEqual(self.store.query('SELECT COUNT(*) n FROM posts')[0]['n'], 1)
        self.assertEqual(self.store.query('SELECT COUNT(*) n FROM media')[0]['n'], 1)
        self.assertEqual(self.store.query('SELECT id FROM tasks'), [{'id': active['id']}])

    def test_bulk_retry_chooses_only_one_record_per_url(self):
        self.assertTrue(callable(getattr(self.store, 'bulk_tasks', None)))
        self.task(status='failed')
        self.task(status='cancelled')
        self.task('active', status='failed')
        self.task('active')
        self.assertEqual(self.store.bulk_tasks('retry'), {'affected': 1, 'skipped': 3, 'matched': 4})
        self.assertEqual(self.store.query("SELECT COUNT(*) n FROM tasks WHERE user='example' AND status='queued'")[0]['n'], 1)

    def test_failed_can_cancel_and_waiting_retry_can_retry_now(self):
        failed = self.task(status='failed')
        self.assertEqual(self.store.cancel(failed['id']), 1)
        waiting = self.task('waiting', status='retry')
        self.store.execute("UPDATE tasks SET source='scheduled',next_run=9999999999 WHERE id=?", (waiting['id'],))
        self.store.retry(waiting['id'])
        result = self.store.query('SELECT status,source,next_run FROM tasks WHERE id=?', (waiting['id'],))[0]
        self.assertEqual((result['status'], result['source']), ('queued', 'website'))
        self.assertLess(result['next_run'], 9999999999)

    def test_old_worker_cannot_finish_or_requeue_retried_generation(self):
        task = self.task()
        old = self.store.claim()
        self.store.cancel(task['id'])
        self.store.retry(task['id'])
        current = self.store.claim()
        self.store.finish(old, result=(3, 4))
        row = self.store.query('SELECT status,new_posts,new_media FROM tasks')[0]
        self.assertEqual(row, {'status': 'running', 'new_posts': 0, 'new_media': 0})
        self.assertTrue(Worker(self.store).interrupted(task['id'], old['generation']))
        self.store.finish(current, result=(1, 2))
        self.assertEqual(self.store.query('SELECT status,new_posts,new_media FROM tasks')[0],
                         {'status': 'succeeded', 'new_posts': 1, 'new_media': 2})

    def test_cancel_delete_while_runner_returns_does_not_restore_record(self):
        self.assertTrue(callable(getattr(self.store, 'delete_task', None)))
        task = self.task()
        def runner(*args):
            self.store.cancel(task['id'])
            self.store.delete_task(task['id'])
            return 1, 1
        Worker(self.store, runner).run_one()
        self.assertEqual(self.store.query('SELECT * FROM tasks'), [])

    def test_manual_intake_promotes_automatic_queued_or_retry(self):
        task, _ = self.store.submit('example', source='scheduled')
        self.store.execute("UPDATE tasks SET status='retry',next_run=9999999999 WHERE id=?", (task['id'],))
        same, duplicate = self.store.submit('example', source='scriptcat:fixture')
        self.assertTrue(duplicate)
        self.assertEqual(same['id'], task['id'])
        self.assertEqual(same['source'], 'scriptcat:fixture')
        self.assertLess(same['next_run'], 9999999999)

    def test_interrupted_old_worker_does_not_requeue_new_generation(self):
        task = self.task()
        def runner(*args):
            self.store.cancel(task['id'])
            self.store.retry(task['id'])
            self.store.claim()
            raise Interrupted()
        Worker(self.store, runner).run_one()
        row = self.store.query('SELECT status,attempts FROM tasks')[0]
        self.assertEqual(row, {'status': 'running', 'attempts': 1})


if __name__ == '__main__':
    unittest.main()
