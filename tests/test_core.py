import tempfile
import time
from datetime import datetime
import unittest
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from xarchive.app import create_app
from xarchive.store import Store, target, next_update, LOCAL_TIME
from xarchive.worker import DownloadError, Interrupted, Worker


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_targets(self):
        self.assertEqual(target('@Example')['url'], 'https://x.com/example/timeline')
        self.assertEqual(target('https://twitter.com/Test/status/123/photo/1?s=20')['post_id'], '123')
        for value in ('https://evil.test/a', 'https://x.com@evil.test/a', 'https://x.com/home', 'https://x.com:999/a', '-oexec', 'https://x.com/a/status/no'):
            with self.assertRaises(ValueError):
                target(value)

    def test_queue_recovery_cancel_retry(self):
        job, _ = self.store.submit('@Example')
        same, duplicate = self.store.submit('https://twitter.com/example/media')
        self.assertTrue(duplicate)
        self.assertEqual(job['id'], same['id'])
        claimed = self.store.claim()
        self.assertEqual(claimed['attempts'], 1)
        Store(self.tmp.name).recover()
        self.assertEqual(self.store.claim()['id'], job['id'])
        self.store.cancel(job['id'])
        self.store.finish(claimed, result=(1, 2))
        self.assertEqual(self.store.query('SELECT status FROM tasks')[0]['status'], 'cancelled')
        self.store.retry(job['id'])
        self.assertEqual(self.store.claim()['attempts'], 1)

    def test_worker_failure_backoff_and_success(self):
        job, _ = self.store.submit('example')
        def fail(*args):
            raise DownloadError('网络连接失败')
        worker = Worker(self.store, fail)
        worker.run_one()
        row = self.store.query('SELECT * FROM tasks')[0]
        self.assertEqual(row['status'], 'retry')
        self.assertGreater(row['next_run'], time.time())
        for expected in ('retry', 'failed'):
            self.store.execute('UPDATE tasks SET next_run=0')
            worker.run_one()
            self.assertEqual(self.store.query('SELECT status FROM tasks')[0]['status'], expected)
        self.store.retry(job['id'])
        worker.runner = lambda *args: (2, 3)
        worker.run_one()
        row = self.store.query('SELECT * FROM tasks')[0]
        self.assertEqual((row['status'], row['new_posts'], row['new_media']), ('succeeded', 2, 3))

    def test_subscription_persists_and_deduplicates(self):
        self.store.subscribe('@Example', 'monthly')
        self.store.execute('UPDATE subscriptions SET next_run=0')
        self.store.schedule()
        self.assertEqual(len(self.store.query('SELECT * FROM tasks')), 1)
        self.assertEqual(Store(self.tmp.name).query('SELECT * FROM subscriptions')[0]['limit_count'], 0)

    def test_calendar_updates_and_one_time(self):
        january = datetime(2028,1,31,16,45,tzinfo=LOCAL_TIME)
        february = datetime.fromtimestamp(next_update('monthly', january.timestamp()), LOCAL_TIME)
        self.assertEqual((february.month, february.day), (2,29))
        self.assertTrue(8 <= february.hour < 18)
        self.assertEqual(datetime.fromtimestamp(next_update('daily',january.timestamp()), LOCAL_TIME).date(), datetime(2028,2,1).date())
        self.assertEqual(datetime.fromtimestamp(next_update('weekly',january.timestamp()), LOCAL_TIME).date(), datetime(2028,2,7).date())
        self.store.subscribe('example','once')
        self.store.execute("UPDATE tasks SET status='succeeded'")
        self.store.schedule()
        self.assertEqual(len(self.store.query('SELECT * FROM tasks')),1)
        sub=self.store.query('SELECT * FROM subscriptions')[0]
        self.assertEqual((sub['enabled'],sub['schedule']), (0,'once'))
        with self.assertRaises(ValueError):
            next_update('hourly')

    def test_upgrade_keeps_login_and_organization(self):
        self.store.set_setting('x_cookies',{'auth_token':'test-only'})
        self.store.execute("INSERT INTO posts(id,user,url,added,favorite,tags) VALUES ('123','example','https://x.com/example/status/123',0,1,'keep')")
        self.store.subscribe('example')
        self.store.execute('ALTER TABLE subscriptions DROP COLUMN schedule')
        self.store.execute('UPDATE subscriptions SET enabled=0,interval_minutes=360,limit_count=100,next_run=0')
        self.store.execute("DELETE FROM settings WHERE key='all_downloads_v2'")
        self.store.execute("DELETE FROM settings WHERE key='quarterly_schedule_v1'")
        self.store.set_setting('timeout_minutes',60)
        upgraded=Store(self.tmp.name)
        sub=upgraded.query('SELECT * FROM subscriptions')[0]
        self.assertEqual((sub['schedule'],sub['limit_count'],sub['enabled']),('quarterly',0,0))
        self.assertGreater(sub['next_run'],time.time())
        self.assertEqual(upgraded.setting('x_cookies'),{'auth_token':'test-only'})
        self.assertEqual(upgraded.query('SELECT favorite,tags FROM posts')[0],{'favorite':1,'tags':'keep'})

    def test_concurrent_intake_has_one_active_task(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.store.submit('example'), range(8)))
        self.assertEqual(sum(not duplicate for _, duplicate in results), 1)
        self.assertEqual(len({job['id'] for job, _ in results}), 1)

    def test_worker_shutdown_resumes_but_user_cancel_does_not(self):
        job, _ = self.store.submit('example')
        def interrupt(*args):
            raise Interrupted()
        Worker(self.store, interrupt).run_one()
        row = self.store.query('SELECT * FROM tasks')[0]
        self.assertEqual((row['status'], row['attempts']), ('queued', 0))
        def cancel(*args):
            self.store.cancel(job['id'])
            raise Interrupted()
        Worker(self.store, cancel).run_one()
        self.assertEqual(self.store.query('SELECT status FROM tasks')[0]['status'], 'cancelled')

    def test_api_auth_and_shared_intake(self):
        with TestClient(create_app(self.tmp.name, run_worker=False)) as client:
            self.assertEqual(client.get('/health').status_code, 200)
            self.assertIn('拾存', client.get('/').text)
            self.assertEqual(client.get('/static/app.js').status_code, 200)
            self.assertEqual(client.get('/static/archive.sqlite3').status_code, 404)
            self.assertIn('@grant        GM_xmlhttpRequest', client.get('/integrations/xarchive.user.js').text)
            self.assertEqual(client.get('/api/v1/tasks').status_code, 401)
            self.assertEqual(client.post('/api/v1/auth/setup', json={'password': 'test-only-password'}).status_code, 200)
            self.assertEqual(client.post('/api/v1/auth/setup', json={'password': 'test-only-password'}).status_code, 409)
            self.assertEqual(client.post('/api/v1/tasks', json={'url': '@example'}, headers={'Origin': 'https://evil.test'}).status_code, 403)
            token = client.post('/api/v1/clients', json={'name': 'test'}).json()['token']
            client.cookies.clear()
            headers = {'Authorization': 'Bearer ' + token}
            response = client.post('/api/v1/tasks', json={'url': '@Example'}, headers={**headers, 'Origin': 'https://x.com'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['task']['user'], 'example')
            self.assertEqual(response.json()['task']['limit_count'], 0)
            for schedule in ('once','daily','weekly','monthly'):
                result=client.post('/api/v1/tasks',json={'url':'@Example','schedule':schedule,'limit':100},headers=headers)
                self.assertEqual(result.status_code,200)
                self.assertEqual(result.json()['task']['limit_count'],0)
            self.assertEqual(self.store.query('SELECT schedule FROM subscriptions')[0]['schedule'],'monthly')
            single=client.post('/api/v1/tasks',json={'url':'https://x.com/other/status/123'},headers=headers)
            self.assertEqual(single.status_code,200)
            self.assertFalse(self.store.query("SELECT 1 FROM subscriptions WHERE user='other'"))
            self.assertEqual(client.get('/api/v1/clients', headers=headers).status_code, 403)
            self.assertEqual(client.post('/api/v1/tasks', json={'url': '@Example', 'limit': True}, headers=headers).status_code, 422)
            self.assertNotIn('test-only-password', client.post('/api/v1/auth/login', json={'password': 'test-only-password', 'bad': True}).text)


if __name__ == '__main__':
    unittest.main()
