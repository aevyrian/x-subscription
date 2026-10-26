import tempfile
import unittest
from fastapi.testclient import TestClient

from xarchive.app import create_app


class TaskApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app(self.directory.name, run_worker=False)
        self.store = self.app.state.store
        self.client = TestClient(self.app)
        self.client.post('/api/v1/auth/setup', json={'password': 'test-admin-only'})

    def tearDown(self):
        self.client.close()
        self.directory.cleanup()

    def failed(self, user):
        task, _ = self.store.submit(user)
        self.store.execute("UPDATE tasks SET status='failed' WHERE id=?", (task['id'],))
        return task

    def test_filtered_bulk_covers_other_pages_and_preserves_nonmatching_tasks(self):
        for index in range(65):
            self.failed('target' + str(index))
        other = self.failed('another')
        active, _ = self.store.submit('targetactive')
        result = self.client.get('/api/v1/tasks?filter=attention&q=TARGET&limit=5&offset=10').json()
        self.assertEqual(len(result['items']), 5)
        self.assertEqual(result['total'], 65)
        self.assertEqual(result['counts']['attention'], 65)
        self.assertEqual(result['counts']['all'], 66)
        response = self.client.post('/api/v1/tasks/bulk', json={'action': 'delete', 'filter': 'attention', 'q': 'TARGET'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['affected'], 65)
        self.assertEqual(self.store.query('SELECT id FROM tasks ORDER BY id'), sorted([{'id': other['id']}, {'id': active['id']}], key=lambda item: item['id']))

    def test_delete_requires_terminal_state_and_preserves_archive_and_plan(self):
        task, _ = self.store.subscribe('example')
        media = self.store.downloads / 'kept.jpg'
        media.write_bytes(b'test-media')
        self.store.execute("INSERT INTO posts(id,user,url,added) VALUES ('123','example','https://x.com/example/status/123',0)")
        self.store.execute("INSERT INTO media(post_id,path,kind,size) VALUES ('123',?,'image',10)", (str(media),))
        self.assertEqual(self.client.post('/api/v1/tasks/' + task['id'] + '/delete').status_code, 409)
        self.assertEqual(self.client.post('/api/v1/tasks/' + task['id'] + '/cancel').status_code, 200)
        self.assertEqual(self.client.post('/api/v1/tasks/' + task['id'] + '/delete').status_code, 200)
        self.assertFalse(self.store.query('SELECT 1 FROM tasks'))
        self.assertEqual(self.store.query('SELECT last_task FROM subscriptions'), [{'last_task': None}])
        self.assertEqual(self.store.query('SELECT COUNT(*) n FROM posts')[0]['n'], 1)
        self.assertEqual(self.store.query('SELECT COUNT(*) n FROM media')[0]['n'], 1)
        self.assertEqual(media.read_bytes(), b'test-media')

    def test_cancel_and_retry_bulk_skip_ineligible_items(self):
        failed = self.failed('example')
        done = self.failed('finished')
        self.store.execute("UPDATE tasks SET status='succeeded' WHERE id=?", (done['id'],))
        response = self.client.post('/api/v1/tasks/bulk', json={'action': 'cancel', 'filter': 'all'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.json()['affected'], response.json()['skipped']), (1, 1))
        self.assertEqual(self.store.query('SELECT status FROM tasks WHERE id=?', (failed['id'],))[0]['status'], 'cancelled')
        response = self.client.post('/api/v1/tasks/bulk', json={'action': 'retry', 'filter': 'all'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.json()['affected'], response.json()['skipped']), (1, 1))

    def test_new_schedule_options_and_quarterly_default(self):
        self.assertEqual(self.client.get('/api/v1/settings').json()['default_schedule'], 'quarterly')
        self.assertEqual(self.client.post('/api/v1/tasks', json={'url': '@example'}).status_code, 200)
        self.assertEqual(self.store.query('SELECT schedule FROM subscriptions')[0]['schedule'], 'quarterly')
        for schedule in ('three_days', 'weekly', 'monthly', 'quarterly', 'yearly', 'once', 'daily'):
            response = self.client.patch('/api/v1/subscriptions/example', json={'schedule': schedule})
            self.assertEqual(response.status_code, 200, schedule)
        self.assertEqual(self.client.patch('/api/v1/subscriptions/example', json={'schedule': 'hourly'}).status_code, 422)

    def test_bulk_requires_website_login_and_validates_inputs(self):
        token = self.client.post('/api/v1/clients', json={'name': 'test'}).json()['token']
        self.client.cookies.clear()
        body = {'action': 'delete', 'filter': 'all'}
        self.assertEqual(self.client.post('/api/v1/tasks/bulk', json=body).status_code, 401)
        self.assertEqual(self.client.post('/api/v1/tasks/bulk', json=body, headers={'Authorization': 'Bearer ' + token}).status_code, 403)
        self.client.post('/api/v1/auth/login', json={'password': 'test-admin-only'})
        self.assertEqual(self.client.post('/api/v1/tasks/bulk', json={'action': 'delete', 'filter': 'bad'}).status_code, 422)
        self.assertEqual(self.client.post('/api/v1/tasks/bulk', json={**body, 'q': 'x' * 201}).status_code, 422)
        self.assertEqual(self.client.get('/api/v1/tasks?filter=bad').status_code, 422)

    def test_history_confirmation_accepts_only_website_choices_once(self):
        for choice in ('all', 'recent300', 'skip'):
            task, _ = self.store.submit('hist' + choice)
            self.store.execute("UPDATE tasks SET status='awaiting_confirmation' WHERE id=?", (task['id'],))
            path = '/api/v1/tasks/' + task['id'] + '/confirm'
            pending = self.client.get('/api/v1/tasks?filter=confirmation').json()
            self.assertEqual(pending['counts']['confirmation'], 1)
            self.assertEqual(pending['items'][0]['id'], task['id'])
            self.assertEqual(self.client.get('/api/v1/stats').json()['confirmation_tasks'], 1)
            self.assertEqual(self.client.post('/api/v1/tasks/' + task['id'] + '/delete').status_code, 409)
            response = self.client.post(path, json={'choice': choice})
            self.assertEqual(response.status_code, 200, choice)
            row = self.store.query('SELECT * FROM tasks WHERE id=?', (task['id'],))[0]
            self.assertEqual(row['history_choice'], choice)
            self.assertEqual(row['status'], 'cancelled' if choice == 'skip' else 'queued')
            self.assertEqual(self.client.post(path, json={'choice': choice}).status_code, 409)

    def test_history_confirmation_requires_admin_and_validated_choice(self):
        task, _ = self.store.submit('example')
        self.store.execute("UPDATE tasks SET status='awaiting_confirmation' WHERE id=?", (task['id'],))
        path = '/api/v1/tasks/' + task['id'] + '/confirm'
        self.assertEqual(self.client.post(path, json={'choice': 'arbitrary'}).status_code, 422)
        self.assertEqual(self.client.post(path, json={'choice': 'all', 'limit': 500}).status_code, 422)
        token = self.client.post('/api/v1/clients', json={'name': 'test'}).json()['token']
        self.client.cookies.clear()
        self.assertEqual(self.client.post(path, json={'choice': 'all'}).status_code, 401)
        self.assertEqual(self.client.post(path, json={'choice': 'all'}, headers={'Authorization': 'Bearer ' + token}).status_code, 403)

    def test_manual_update_queues_incremental_beside_waiting_history(self):
        history, _ = self.store.submit('example')
        self.store.execute("UPDATE tasks SET status='awaiting_confirmation' WHERE id=?", (history['id'],))
        same = self.client.post('/api/v1/tasks', json={'url': '@example', 'mode': 'download'})
        self.assertEqual(same.status_code, 200)
        self.assertEqual(same.json()['task']['id'], history['id'])
        update = self.client.post('/api/v1/tasks', json={'url': '@example', 'mode': 'update'})
        self.assertEqual(update.status_code, 200)
        self.assertEqual(update.json()['task']['task_kind'], 'incremental')
        self.assertNotEqual(update.json()['task']['id'], history['id'])
        self.assertEqual(self.store.query('SELECT status FROM tasks WHERE id=?', (history['id'],))[0]['status'], 'awaiting_confirmation')
        first = self.client.post('/api/v1/tasks', json={'url': '@firstupdate', 'mode': 'update'})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()['task']['task_kind'], 'history')

    def test_cookie_reset_clears_only_shared_task_cooldown(self):
        import time
        shared, _ = self.store.submit('sharedwait')
        local, _ = self.store.submit('querywait')
        later = time.time() + 900
        for task, scope in ((shared, 'shared'), (local, 'task')):
            self.store.execute("UPDATE tasks SET status='retry',next_run=?,cooldown_until=?,cooldown_scope=?,cooldown_reason='等待额度' WHERE id=?", (later, later, scope, task['id']))
        self.store.set_setting('x_rate_limit_until', later)
        self.assertEqual(self.client.put('/api/v1/settings', json={'clear_cookies': True}).status_code, 200)
        rows = {row['id']: row for row in self.store.query('SELECT * FROM tasks')}
        self.assertEqual(rows[shared['id']]['cooldown_until'], 0)
        self.assertLess(rows[shared['id']]['next_run'], time.time() + 5)
        self.assertEqual(rows[local['id']]['cooldown_until'], later)
        self.assertEqual(rows[local['id']]['next_run'], later)


if __name__ == '__main__':
    unittest.main()
