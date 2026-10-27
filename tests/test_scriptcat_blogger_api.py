import tempfile
import unittest

from fastapi.testclient import TestClient
from xarchive.app import create_app


class ScriptcatBloggerApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app(self.directory.name, run_worker=False)
        self.store = self.app.state.store
        self.admin = TestClient(self.app)
        self.script = TestClient(self.app)
        self.admin.post('/api/v1/auth/setup', json={'password': 'test-admin-only'})
        self.admin.put('/api/v1/connection/password', json={'password': 'test-connection-only'})
        token = self.admin.post('/api/v1/clients', json={'name': 'test'}).json()['token']
        self.password = {'Authorization': 'Bearer test-connection-only'}
        self.legacy = {'Authorization': 'Bearer ' + token}

    def tearDown(self):
        self.script.close()
        self.admin.close()
        self.directory.cleanup()

    def get(self, user, headers=None):
        return self.script.get('/api/v1/scriptcat/bloggers/' + user, headers=headers)

    def test_authentication_unknown_handle_and_no_mutation(self):
        before = {name: self.store.query('SELECT * FROM ' + name) for name in ('bloggers', 'subscriptions', 'tasks')}
        self.assertEqual(self.get('Example').status_code, 401)
        expected = {'user': 'example', 'display_name': None, 'subscription': None,
                    'last_success_at': None, 'active_task': None}
        for headers in (self.password, self.legacy):
            response = self.get('Example', headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), expected)
            self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(self.admin.get('/api/v1/scriptcat/bloggers/Example').json(), expected)
        self.assertEqual(before, {name: self.store.query('SELECT * FROM ' + name) for name in before})
        self.assertEqual(self.script.get('/api/v1/bloggers', headers=self.password).status_code, 403)

    def test_rejects_invalid_and_reserved_handles(self):
        for handle in ('home', 'a-b', 'a' * 16, '%40example'):
            with self.subTest(handle=handle):
                self.assertIn(self.get(handle, self.password).status_code, (400, 422))
        self.assertEqual(self.store.query('SELECT * FROM bloggers'), [])

    def test_subscription_states_and_active_task_allowlist(self):
        task, _ = self.store.subscribe('example', 'monthly')
        self.store.execute("UPDATE bloggers SET display_name='Example Name', note='private note' WHERE user='example'")
        self.store.execute("UPDATE subscriptions SET next_run=123456 WHERE user='example'")
        self.store.execute("UPDATE tasks SET status='awaiting_confirmation', next_run=234567, error='private error' WHERE id=?", (task['id'],))
        before = {name: self.store.query('SELECT * FROM ' + name) for name in ('bloggers', 'subscriptions', 'tasks')}
        result = self.get('EXAMPLE', self.password).json()
        self.assertEqual(result, {'user': 'example', 'display_name': 'Example Name',
            'subscription': {'enabled': True, 'schedule': 'monthly', 'next_run': 123456},
            'last_success_at': None, 'active_task': {'status': 'awaiting_confirmation', 'task_kind': 'history', 'next_run': 234567}})
        self.assertEqual(before, {name: self.store.query('SELECT * FROM ' + name) for name in before})
        for status in ('queued', 'running', 'retry'):
            self.store.execute('UPDATE tasks SET status=? WHERE id=?', (status, task['id']))
            self.assertEqual(self.get('example', self.password).json()['active_task']['status'], status)
        for schedule, enabled in (('monthly', 0), ('once', 0)):
            self.store.execute('UPDATE subscriptions SET enabled=?,schedule=? WHERE user=?', (enabled, schedule, 'example'))
            self.assertEqual(self.get('example', self.password).json()['subscription'],
                             {'enabled': False, 'schedule': schedule, 'next_run': None})
        self.store.execute("UPDATE tasks SET status='failed',finished=999 WHERE id=?", (task['id'],))
        result = self.get('example', self.password).json()
        self.assertIsNone(result['active_task'])
        self.assertIsNone(result['last_success_at'])

    def test_only_persisted_success_sets_last_success(self):
        task, _ = self.store.submit('example')
        self.store.finish(self.store.claim(), result=(0, 0))
        success = self.store.query('SELECT last_success_at FROM bloggers WHERE user=?', ('example',))[0]['last_success_at']
        self.assertGreater(success, 0)
        self.store.delete_task(task['id'])
        failed, _ = self.store.submit('example', source='update')
        self.store.execute("UPDATE tasks SET status='failed',finished=? WHERE id=?", (success + 100, failed['id']))
        self.assertEqual(self.get('example', self.legacy).json()['last_success_at'], success)


if __name__ == '__main__':
    unittest.main()
