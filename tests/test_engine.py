import functools
import json
import subprocess
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from xarchive.app import create_app
from xarchive.engine import Engine, classify_error, parse_cookies
from xarchive.library import index_folder, list_posts, safe_file
from xarchive.store import Store
from xarchive.worker import Interrupted, Worker


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'data')

    def tearDown(self):
        self.tmp.cleanup()

    def test_cookies_and_redacted_errors(self):
        self.assertEqual(set(parse_cookies('auth_token=test-only; ct0=test-only')), {'auth_token', 'ct0'})
        self.assertEqual(set(parse_cookies('#HttpOnly_.x.com\tTRUE\t/\tTRUE\t0\tauth_token\ttest-only')), {'auth_token'})
        for bad in ('password=anything', 'auth_token=a\r\nOther: a'):
            with self.assertRaises(ValueError):
                parse_cookies(bad)
        self.assertNotIn('sensitive-example', classify_error('401 sensitive-example', 1))

    def fixture(self):
        folder = self.store.downloads / 'example'
        folder.mkdir(exist_ok=True)
        (folder / '123.json').write_text(json.dumps({'tweet_id': 123, 'author': {'name': 'Example', 'nick': '例子'}, 'content': 'hello archive', 'date': '2026-10-22 00:00:00'}), encoding='utf-8')
        (folder / '123_1.jpg').write_bytes(b'fixture-image')
        return folder

    def test_index_search_and_preserved_organization(self):
        self.fixture()
        self.assertEqual(index_folder(self.store, 'example'), (1, 1))
        self.store.execute("UPDATE posts SET favorite=1,tags='旅行,相机',note='keep'")
        self.assertEqual(index_folder(self.store, 'example'), (0, 0))
        result = list_posts(self.store, q='archive', favorite=True, tag='旅行')
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['note'], 'keep')
        self.assertEqual(len(result['items'][0]['media']), 1)
        with self.assertRaises(ValueError):
            safe_file(self.store, '../config/archive.sqlite3')

    def test_real_gallery_dl_download_and_dedup_without_x(self):
        source = Path(self.tmp.name) / 'source'
        source.mkdir()
        image = b'local-download-fixture'
        (source / 'sample.jpg').write_bytes(image)
        server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(source)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            job, _ = self.store.submit('example')
            job['task_kind'] = 'legacy'
            job['url'] = f'http://127.0.0.1:{server.server_port}/sample.jpg'
            self.store.set_setting('x_cookies', {'auth_token': 'test-only'})
            engine = Engine(self.store)
            config = engine.config(job)
            config['extractor']['filename'] = '{tweet_id}_1.{extension}'
            config['extractor']['keywords'] = {'tweet_id':123, 'author':{'name':'example','nick':'Fixture'}, 'content':'Local engine fixture'}
            with patch.object(engine, 'config', return_value=config), patch('xarchive.engine.subprocess.Popen',wraps=subprocess.Popen) as spawned:
                progress=[]
                engine(job, lambda: False, progress.append)
                self.assertEqual(progress[-1],1)
                self.assertEqual(self.store.query('SELECT scanned_posts FROM tasks')[0]['scanned_posts'],1)
                downloaded = self.store.downloads / 'example' / '123_1.jpg'
                self.assertEqual(downloaded.read_bytes(), image)
                self.assertEqual(list_posts(self.store)['total'], 1)
                self.assertEqual(len(list_posts(self.store)['items'][0]['media']), 1)
                modified = downloaded.stat().st_mtime_ns
                engine(job, lambda: False, lambda n: None)
                self.assertEqual(downloaded.stat().st_mtime_ns, modified)
                self.assertNotIn('--post-range',spawned.call_args.args[0])
            self.assertFalse(list(self.store.config.glob('.run-*.json')))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_api_settings_media_and_range(self):
        self.fixture()
        index_folder(self.store, 'example')
        with TestClient(create_app(self.store.root, run_worker=False)) as client:
            client.post('/api/v1/auth/setup', json={'password': 'test-only-password'})
            self.assertEqual(client.put('/api/v1/settings', json={'cookies': 'auth_token=test-only; ct0=test-only'}).status_code, 200)
            settings = client.get('/api/v1/settings').json()
            self.assertTrue(settings['cookie_configured'])
            self.assertNotIn('test-only', json.dumps(settings))
            response = client.get('/api/v1/posts').json()
            url = response['items'][0]['media'][0]['url']
            self.assertEqual(client.get(url, headers={'Range': 'bytes=0-6'}).status_code, 206)
            self.assertEqual(client.patch('/api/v1/posts/123', json={'favorite': True, 'tags': 'a，b,a'}).status_code, 200)
            self.assertEqual(client.get('/api/v1/posts?favorite=true&tag=b').json()['total'], 1)
            client.cookies.clear()
            self.assertEqual(client.get(url).status_code, 401)

    def test_progress_arrives_before_gallery_dl_exits(self):
        source = Path(self.tmp.name) / 'live-source'
        source.mkdir()
        for name in ('first.jpg', 'second.jpg'):
            (source / name).write_bytes(b'local-progress-fixture')
        saved = threading.Event()
        observed = []

        class SlowHandler(QuietHandler):
            def do_GET(self):
                if self.path == '/second.jpg':
                    observed.append(saved.wait(3))
                super().do_GET()

        server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(SlowHandler, directory=str(source)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            job, _ = self.store.submit('example')
            job['task_kind'] = 'legacy'
            job['url'] = f'http://127.0.0.1:{server.server_port}/first.jpg'
            self.store.set_setting('x_cookies', {'auth_token': 'test-only'})
            engine = Engine(self.store)
            config = engine.config(job)
            config['extractor']['filename'] = '{filename}.{extension}'
            config['extractor']['keywords'] = {'tweet_id': 123, 'author': {'name': 'example'}}
            spawn = subprocess.Popen

            def two_files(command, **kwargs):
                return spawn([*command, f'http://127.0.0.1:{server.server_port}/second.jpg'], **kwargs)

            with patch.object(engine, 'config', return_value=config), patch('xarchive.engine.subprocess.Popen', side_effect=two_files):
                engine(job, lambda: False, lambda n: saved.set() if n else None)
            self.assertEqual(observed, [True], 'Saved progress must be visible while the next file is still downloading')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
