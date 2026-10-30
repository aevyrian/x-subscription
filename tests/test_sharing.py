import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from xarchive.app import create_app
from xarchive.sharing import share_origin


class SharingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        with patch.dict('os.environ', {'XARCHIVE_SHARE_ORIGIN': 'https://share.example.test'}):
            self.app = create_app(self.tmp.name, run_worker=False)
        self.client = TestClient(self.app)
        self.client.post('/api/v1/auth/setup', json={'password': 'test-only-password'})
        self.store = self.app.state.store
        for post_id in ('1', '2'):
            self.store.execute('INSERT INTO posts(id,user,url,added,note,tags) VALUES (?,?,?,?,?,?)',
                               (post_id, 'example', f'https://x.com/example/status/{post_id}', 0, 'private note', 'private tag'))
            (self.store.downloads / f'{post_id}.jpg').write_bytes(b'0123456789')
            self.store.execute('INSERT INTO media(id,post_id,path,kind,size) VALUES (?,?,?,?,?)',
                               (int(post_id), post_id, f'{post_id}.jpg', 'image', 10))

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def test_scoped_anonymous_media_and_revocation(self):
        response = self.client.post('/api/v1/posts/1/share')
        self.assertEqual(response.status_code, 200)
        url = response.json()['url']
        path = urlsplit(url).path
        self.assertEqual(self.client.post('/api/v1/posts/1/share').json()['url'], url)
        with TestClient(self.app) as visitor:
            page = visitor.get(url)
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.headers['Cache-Control'], 'private, no-store')
            self.assertEqual(page.headers['Referrer-Policy'], 'no-referrer')
            self.assertIn('noindex', page.headers['X-Robots-Tag'])
            data = visitor.get(url + '/data').json()
            self.assertEqual(set(data), {'id', 'user', 'display_name', 'content', 'posted_at', 'url', 'media'})
            self.assertEqual(data['media'], [{'id': 1, 'kind': 'image', 'url': path + '/media/1'}])
            part = visitor.get(url + '/media/1', headers={'Range': 'bytes=2-4'})
            self.assertEqual((part.status_code, part.content), (206, b'234'))
            self.assertIn('attachment', visitor.get(url + '/media/1?download=true').headers['Content-Disposition'])
            self.assertEqual(visitor.get(url + '/media/2').status_code, 404)
            self.assertEqual(visitor.get('/api/v1/media/1').status_code, 401)
            self.assertEqual(visitor.post('/api/v1/posts/1/share').status_code, 401)
            self.client.delete('/api/v1/posts/1/share')
            for suffix in ('', '/data', '/media/1'):
                self.assertEqual(visitor.get(url + suffix).status_code, 404)
        self.assertNotEqual(self.client.post('/api/v1/posts/1/share').json()['url'], url)

    def test_public_host_blocks_admin_even_with_login(self):
        for path in ('/', '/health', '/api/v1/posts', '/api/v1/settings', '/api/v1/auth',
                     '/api/v1/media/1', '/static/app.js', '/integrations/xarchive.user.js'):
            self.assertEqual(self.client.get(path, headers={'Host': 'share.example.test'}).status_code, 404)
        self.assertEqual(self.client.post('/api/v1/posts/1/share', headers={'Host': 'share.example.test'}).status_code, 404)
        for name in ('share.js', 'share.css', 'viewer.js', 'viewer.css'):
            self.assertEqual(self.client.get('/static/' + name, headers={'Host': 'share.example.test'}).status_code, 200)
        self.assertEqual(self.client.get('/s/invalid').status_code, 404)
        token = self.client.post('/api/v1/clients', json={'name': 'widget'}).json()['token']
        self.client.cookies.clear()
        self.assertEqual(self.client.post('/api/v1/posts/1/share', headers={'Authorization': 'Bearer ' + token}).status_code, 403)

    def test_public_host_with_trailing_dot_blocks_admin(self):
        host = {'Host': 'share.example.test.'}
        self.assertEqual(self.client.get('/api/v1/auth', headers=host).status_code, 404)
        self.assertEqual(self.client.post('/api/v1/posts/1/share', headers=host).status_code, 404)
        self.assertEqual(self.client.get('/static/share.js', headers=host).status_code, 200)
        self.assertEqual(self.client.get('/api/v1/auth').status_code, 200)

    def test_missing_file_and_disabled_share(self):
        url = self.client.post('/api/v1/posts/1/share').json()['url']
        self.store.execute("UPDATE media SET path='../config/archive.sqlite3' WHERE id=1")
        self.assertEqual(self.client.get(url + '/media/1').status_code, 404)
        self.assertEqual(self.client.post('/api/v1/posts/404/share').status_code, 404)
        with patch.dict('os.environ', {'XARCHIVE_SHARE_ORIGIN': ''}):
            with TestClient(create_app(self.tmp.name, run_worker=False)) as disabled:
                disabled.post('/api/v1/auth/login', json={'password': 'test-only-password'})
                self.assertFalse(disabled.get('/api/v1/posts/1/share').json()['enabled'])
                self.assertEqual(disabled.post('/api/v1/posts/1/share').status_code, 503)
        for origin in ('http://example.test', 'https://example.test/path', 'https://user:pass@example.test'):
            with patch.dict('os.environ', {'XARCHIVE_SHARE_ORIGIN': origin}):
                with self.assertRaises(ValueError):
                    share_origin()
