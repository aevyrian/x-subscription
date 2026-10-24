import base64
import json
import tempfile
import unittest

from fastapi.testclient import TestClient
from xarchive.app import create_app


class ConnectionPasswordTest(unittest.TestCase):
    def test_password_validates_and_submits_without_admin_access(self):
        with tempfile.TemporaryDirectory() as root:
            app = create_app(root, run_worker=False)
            with TestClient(app) as admin, TestClient(app) as script:
                self.assertIn('/static/app.js?v=1.0.6', admin.get('/').text)
                self.assertEqual(admin.get('/static/app.js').headers['cache-control'], 'no-cache')
                admin.post('/api/v1/auth/setup', json={'password': 'test-admin-only'})
                password = 'test-connection-only'
                self.assertEqual(admin.put('/api/v1/connection/password', json={'password': password}).status_code, 200)
                headers = {'Authorization': 'Bearer '+password, 'Origin': 'https://x.com'}
                self.assertEqual(script.get('/api/v1/connection/check', headers=headers).json(), {'ok': True, 'method': 'password'})
                task = script.post('/api/v1/tasks', json={'url': 'https://x.com/example/status/123', 'mode': 'download'}, headers=headers)
                self.assertEqual(task.status_code, 200)
                self.assertEqual(task.json()['task']['source'], 'scriptcat:password')
                self.assertEqual(script.get('/api/v1/settings', headers=headers).status_code, 403)
                self.assertEqual(script.put('/api/v1/connection/password', json={'password': password}, headers=headers).status_code, 403)
                wrong = {'Authorization': 'Bearer test-incorrect-only'}
                self.assertEqual(admin.get('/api/v1/connection/check', headers=wrong).status_code, 401)
                self.assertEqual(script.post('/api/v1/tasks', json={'url': '@example'}, headers=wrong).status_code, 401)
                self.assertNotIn(password, json.dumps(app.state.store.query("SELECT value FROM settings WHERE key='connection_password'")))
                settings = admin.get('/api/v1/settings')
                self.assertTrue(settings.json()['connection_password_configured'])
                self.assertNotIn(password, settings.text)
                replacement = 'test-replacement-only'
                admin.put('/api/v1/connection/password', json={'password': replacement})
                self.assertEqual(script.get('/api/v1/connection/check', headers=headers).status_code, 401)
                self.assertEqual(script.get('/api/v1/connection/check', headers={'Authorization': 'Bearer '+replacement}).status_code, 200)
                unicode_password = '测试连接-only-password'
                admin.put('/api/v1/connection/password', json={'password': unicode_password})
                encoded = base64.urlsafe_b64encode(unicode_password.encode()).decode().rstrip('=')
                encoded_headers = {'Authorization': 'Bearer xarchive.'+encoded}
                self.assertEqual(script.get('/api/v1/connection/check', headers=encoded_headers).status_code, 200)
                self.assertEqual(script.post('/api/v1/tasks', json={'url': 'https://x.com/example/status/124', 'mode': 'download'}, headers=encoded_headers).status_code, 200)
                self.assertEqual(script.get('/api/v1/connection/check', headers={'Authorization': 'Bearer xarchive.!!!'}).status_code, 401)

    def test_existing_clients_keep_working_and_wrong_password_is_limited(self):
        with tempfile.TemporaryDirectory() as root:
            with TestClient(create_app(root, run_worker=False)) as client:
                client.post('/api/v1/auth/setup', json={'password': 'test-admin-only'})
                token = client.post('/api/v1/clients', json={'name': 'existing browser'}).json()['token']
                client.put('/api/v1/connection/password', json={'password': 'test-connection-only'})
                self.assertEqual(client.get('/api/v1/connection/check', headers={'Authorization': 'Bearer '+token}).json(), {'ok': True, 'method': 'legacy'})
                for _ in range(10):
                    self.assertEqual(client.get('/api/v1/connection/check', headers={'Authorization': 'Bearer test-incorrect-only'}).status_code, 401)
                self.assertEqual(client.get('/api/v1/connection/check', headers={'Authorization': 'Bearer test-incorrect-only'}).status_code, 429)
                self.assertEqual(client.get('/api/v1/connection/check', headers={'Authorization': 'Bearer '+token}).status_code, 200)


if __name__ == '__main__':
    unittest.main()
