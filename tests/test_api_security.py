import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from xarchive.app import create_app


class ApiSecurityTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app(self.directory.name, run_worker=False)
        self.client = TestClient(self.app)
        self.client.post('/api/v1/auth/setup', json={'password': 'test-admin-only'})

    def tearDown(self):
        self.client.close()
        self.directory.cleanup()

    def test_initialized_site_rejects_setup_without_hashing_again(self):
        with patch('xarchive.app.password_hash', side_effect=AssertionError('duplicate setup performed scrypt')):
            with TestClient(self.app, raise_server_exceptions=False) as visitor:
                for _ in range(12):
                    response = visitor.post('/api/v1/auth/setup', json={'password': 'test-unused-only'})
                    self.assertEqual(response.status_code, 409)
        self.assertTrue(self.client.get('/api/v1/auth').json()['logged_in'])

    def test_proxy_schemes_store_privately_and_can_be_kept_or_cleared(self):
        self.assertFalse(self.client.get('/api/v1/settings').json().get('proxy_configured', True))
        for scheme in ('http', 'https'):
            with self.subTest(scheme=scheme):
                proxy = scheme + '://fixture-user:fixture-pass@127.0.0.1:7890'
                response = self.client.put('/api/v1/settings', json={'x_proxy': proxy})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.app.state.store.setting('x_proxy'), proxy)
                response = self.client.get('/api/v1/settings')
                self.assertTrue(response.json()['proxy_configured'])
                self.assertNotIn(proxy, response.text)
                self.assertNotIn('fixture-user', response.text)
                self.assertNotIn('fixture-pass', response.text)
                for value in (None, '', '   '):
                    self.assertEqual(self.client.put('/api/v1/settings', json={'x_proxy': value}).status_code, 200)
                    self.assertEqual(self.app.state.store.setting('x_proxy'), proxy)
        self.assertEqual(self.client.put('/api/v1/settings', json={'clear_proxy': True}).status_code, 200)
        self.assertFalse(self.app.state.store.setting('x_proxy'))
        self.assertFalse(self.client.get('/api/v1/settings').json()['proxy_configured'])

    def test_invalid_proxy_does_not_echo_credentials_or_partially_save_settings(self):
        self.app.state.store.set_setting('x_proxy', 'http://127.0.0.1:7890')
        invalid = ('ftp://proxy.example:7890', 'socks5://proxy.example:7890',
                   'socks5h://proxy.example:7890', 'http://', 'http://proxy.example:bad',
                   'http://proxy.example:0', 'http://proxy.example:65536',
                   'http://proxy.example/path', 'http://proxy.example?query=1',
                   'http://proxy.example#fragment', 'http://proxy. example:7890',
                   'http://fixture-user:fixture-pass@proxy.example:bad',
                   'http://proxy.example:\n7890')
        for proxy in invalid:
            with self.subTest(proxy=proxy.replace('fixture-pass', '[redacted]')):
                response = self.client.put('/api/v1/settings', json={
                    'x_proxy': proxy, 'cookies': 'auth_token=fixture-cookie-only',
                    'default_schedule': 'daily'})
                self.assertEqual(response.status_code, 400)
                self.assertNotIn('fixture-pass', response.text)
                self.assertNotIn('fixture-cookie-only', response.text)
                self.assertEqual(self.app.state.store.setting('x_proxy'), 'http://127.0.0.1:7890')
                self.assertFalse(self.app.state.store.setting('x_cookies'))
                self.assertEqual(self.app.state.store.setting('default_schedule', 'monthly'), 'monthly')
        response = self.client.put('/api/v1/settings', json={'x_proxy': 'http://fixture-pass@proxy.example', 'extra': True})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('fixture-pass', response.text)

    def test_proxy_configuration_requires_website_login(self):
        token = self.client.post('/api/v1/clients', json={'name': 'fixture-client'}).json()['token']
        self.assertEqual(self.client.put('/api/v1/settings', json={'x_proxy': 'http://127.0.0.1:7890'},
                                         headers={'Authorization': 'Bearer ' + token}).status_code, 403)
        with TestClient(self.app) as visitor:
            self.assertEqual(visitor.put('/api/v1/settings', json={'x_proxy': 'http://127.0.0.1:7890'}).status_code, 401)

    def test_health_detects_unresponsive_worker_and_allows_explicitly_disabled_worker(self):
        self.assertEqual(self.client.get('/health').status_code, 200)
        app = create_app(self.directory.name, run_worker=True, runner=lambda *args: None)
        client = TestClient(app)
        try:
            response = client.get('/health')
            self.assertEqual(response.status_code, 503)
            self.assertNotEqual(response.json()['status'], 'ok')
            self.assertFalse(response.json()['worker'])
            app.state.worker.thread = SimpleNamespace(is_alive=lambda: True)
            app.state.worker.last_tick = time.monotonic()
            response = client.get('/health')
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()['worker'])
            app.state.worker.last_tick = time.monotonic() - 600
            self.assertEqual(client.get('/health').status_code, 503)
        finally:
            client.close()


if __name__ == '__main__':
    unittest.main()
