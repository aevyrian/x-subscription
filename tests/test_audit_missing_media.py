import functools
import json
import sqlite3
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from gallery_dl import config, postprocessor
from tests.test_smart_engine import complete_tweet
from xarchive.engine import Engine
from xarchive.gallery_runner import AtomicMetadataPP, SmartRunner, SmartStop
from xarchive.worker import DownloadError
from xarchive.library import index_folder, list_posts
from xarchive.store import Store


class MissingMediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'data')
        self.store.set_setting('x_cookies', {'auth_token': 'test-only'})
        source = self.root / 'source'
        source.mkdir()
        (source / 'sample.jpg').write_bytes(b'fixture-image')
        self.requests = []
        requests = self.requests
        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                requests.append(self.path)
                return super().do_GET()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(source)))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_port}/sample.jpg'

    def test_smart_restores_missing_file_despite_existing_archive_entry(self):
        task, _ = self.store.submit('example')
        self.store.execute("UPDATE tasks SET status='running',task_kind='history',crawl_mode='media' WHERE id=?", (task['id'],))
        task = self.store.query('SELECT * FROM tasks WHERE id=?', (task['id'],))[0]
        runner = SmartRunner(self.store, task, emit=lambda event: None)
        raw = complete_tweet()
        raw['legacy']['extended_entities'] = {'media': [{'type': 'photo', 'media_url': self.url}]}
        runner.save_tweet(raw, True)
        settings = Engine(self.store).config(task)
        settings['extractor']['retries'] = 0
        with patch.dict(config._config, settings, clear=True), patch.dict(postprocessor._cache, {'metadata': AtomicMetadataPP}):
            runner.download_cached()
            self.assertEqual(len(self.requests), 1)
            path = self.store.downloads / 'example' / '123_1.jpg'
            path.unlink()
            self.store.execute('UPDATE crawl_posts SET completed=0 WHERE task_id=?', (task['id'],))
            SmartRunner(self.store, task, emit=lambda event: None).download_cached()
        self.assertTrue(path.exists(), 'missing media was not restored')
        self.assertEqual(path.read_bytes(), b'fixture-image')
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.store.query('SELECT completed FROM crawl_posts')[0]['completed'], 1)

    def test_legacy_subprocess_restores_missing_file_despite_existing_archive_entry(self):
        task, _ = self.store.submit('example')
        task['task_kind'] = 'legacy'
        task['url'] = self.url
        engine = Engine(self.store)
        settings = engine.config(task)
        settings['extractor']['filename'] = '{tweet_id}_1.{extension}'
        settings['extractor']['keywords'] = {'tweet_id': 123, 'author': {'name': 'example'}}
        with patch.object(engine, 'config', return_value=settings):
            engine(task, lambda: False, lambda n: None)
            path = self.store.downloads / 'example' / '123_1.jpg'
            self.assertTrue(path.exists())
            path.unlink()
            engine(task, lambda: False, lambda n: None)
            (self.root / 'source' / 'sample.jpg').write_bytes(b'longer-fixture-image')
            path.write_bytes(b'')
            engine(task, lambda: False, lambda n: None)
        self.assertTrue(path.exists(), 'missing media was not restored')
        self.assertEqual(path.read_bytes(), b'longer-fixture-image')
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(self.store.query('SELECT size FROM media'), [{'size': len(b'longer-fixture-image')}])

    def test_smart_preserves_existing_indexed_legacy_media_without_downloading(self):
        legacy = self.root / 'old'
        folder = legacy / 'Example'
        folder.mkdir(parents=True)
        (folder / '123.json').write_text(json.dumps({'tweet_id': 123, 'author': {'name': 'Example'}}), encoding='utf-8')
        path = folder / '123_1.jpg'
        path.write_bytes(b'old-media')
        self.store.legacy = legacy.resolve()
        index_folder(self.store, 'Example', legacy=True)
        task, _ = self.store.submit('example')
        self.store.execute("UPDATE tasks SET status='running',task_kind='history',crawl_mode='media' WHERE id=?", (task['id'],))
        task = self.store.query('SELECT * FROM tasks WHERE id=?', (task['id'],))[0]
        runner = SmartRunner(self.store, task, emit=lambda event: None)
        raw = complete_tweet()
        raw['legacy']['extended_entities'] = {'media': [{'type': 'photo', 'media_url': self.url}]}
        runner.save_tweet(raw, True)
        with closing(sqlite3.connect(self.store.config / 'gallery-history.sqlite3')) as db:
            db.execute('CREATE TABLE archive(entry TEXT PRIMARY KEY)')
            db.execute("INSERT INTO archive VALUES ('twitter123_0_1')")
            db.commit()
        settings = Engine(self.store).config(task)
        with patch.dict(config._config, settings, clear=True), patch.dict(postprocessor._cache, {'metadata': AtomicMetadataPP}):
            runner.download_cached()
        self.assertEqual(path.read_bytes(), b'old-media')
        self.assertEqual(self.requests, [])
        self.assertFalse((self.store.downloads / 'example' / '123_1.jpg').exists())
        self.assertEqual(self.store.query('SELECT completed FROM crawl_posts')[0]['completed'], 1)


    def test_smart_legacy_media_also_skips_when_old_archive_key_differs(self):
        legacy = self.root / 'old'
        folder = legacy / 'Example'
        folder.mkdir(parents=True)
        (folder / '123.json').write_text(json.dumps({'tweet_id': 123, 'author': {'name': 'Example'}}), encoding='utf-8')
        path = folder / '123_1.jpg'
        path.write_bytes(b'old-media')
        self.store.legacy = legacy.resolve()
        index_folder(self.store, 'Example', legacy=True)
        task, _ = self.store.submit('example')
        self.store.execute("UPDATE tasks SET status='running',task_kind='history',crawl_mode='media' WHERE id=?", (task['id'],))
        task = self.store.query('SELECT * FROM tasks WHERE id=?', (task['id'],))[0]
        runner = SmartRunner(self.store, task, emit=lambda event: None)
        raw = complete_tweet()
        raw['legacy']['extended_entities'] = {'media': [{'type': 'photo', 'media_url': self.url}]}
        runner.save_tweet(raw, True)
        settings = Engine(self.store).config(task)
        with patch.dict(config._config, settings, clear=True), patch.dict(postprocessor._cache, {'metadata': AtomicMetadataPP}):
            runner.download_cached()
        self.assertEqual(self.requests, [], 'old archive keys may not match current gallery-dl keys')
        self.assertFalse((self.store.downloads / 'example' / '123_1.jpg').exists())
        self.assertEqual(path.read_bytes(), b'old-media')

    def test_smart_repairs_empty_and_missing_legacy_media_in_new_library(self):
        legacy = self.root / 'old'
        folder = legacy / 'example'
        folder.mkdir(parents=True)
        (folder / '123.json').write_text(json.dumps({'tweet_id': 123, 'author': {'name': 'example'}}), encoding='utf-8')
        empty = folder / '123_1.jpg'
        empty.write_bytes(b'old-media')
        missing = folder / '123_2.jpg'
        missing.write_bytes(b'old-media')
        (folder / '123_3.jpg').touch()
        self.store.legacy = legacy.resolve()
        self.assertEqual(index_folder(self.store, 'example', legacy=True), (1, 2))
        empty.write_bytes(b'')
        missing.unlink()
        task, _ = self.store.submit('example')
        self.store.execute("UPDATE tasks SET status='running',task_kind='history',crawl_mode='media' WHERE id=?", (task['id'],))
        task = self.store.query('SELECT * FROM tasks WHERE id=?', (task['id'],))[0]
        runner = SmartRunner(self.store, task, emit=lambda event: None)
        raw = complete_tweet()
        raw['legacy']['extended_entities'] = {'media': [{'type': 'photo', 'media_url': self.url}] * 2}
        runner.save_tweet(raw, True)
        with patch.dict(config._config, Engine(self.store).config(task), clear=True), patch.dict(postprocessor._cache, {'metadata': AtomicMetadataPP}):
            runner.download_cached()
        self.assertEqual(empty.read_bytes(), b'')
        self.assertFalse(missing.exists())
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(index_folder(self.store, 'example'), (0, 2))
        media = list_posts(self.store)['items'][0]['media']
        self.assertEqual({item['path'] for item in media}, {'example/123_1.jpg', 'example/123_2.jpg'})
        self.assertEqual(self.store.query("SELECT size FROM media WHERE path LIKE '@legacy/%'"), [{'size': 0}] * 2)

    def test_single_post_preserves_legacy_media_without_matching_archive_key(self):
        legacy = self.root / 'old'
        folder = legacy / 'Example'
        folder.mkdir(parents=True)
        (folder / '123.json').write_text(json.dumps({'tweet_id': 123, 'author': {'name': 'Example'}}), encoding='utf-8')
        path = folder / '123_1.jpg'
        path.write_bytes(b'old-media')
        self.store.legacy = legacy.resolve()
        index_folder(self.store, 'Example', legacy=True)
        task, _ = self.store.submit('https://x.com/example/status/123')
        task['url'] = self.url
        engine = Engine(self.store)
        settings = engine.config(task)
        settings['extractor']['filename'] = '{tweet_id}_{num}.{extension}'
        settings['extractor']['keywords'] = {'tweet_id': 123, 'num': 1, 'author': {'name': 'example'}}
        with patch.dict('os.environ', {'XARCHIVE_LEGACY_MEDIA': str(legacy)}), patch.object(engine, 'config', return_value=settings):
            engine(task, lambda: False, lambda n: None)
        self.assertEqual(self.requests, [], 'single-post download should preserve indexed old media')
        self.assertFalse((self.store.downloads / 'example' / '123_1.jpg').exists())
        self.assertEqual(path.read_bytes(), b'old-media')


    def test_smart_missing_legacy_mount_fails_without_downloading(self):
        legacy = self.root / 'old'
        folder = legacy / 'Example'
        folder.mkdir(parents=True)
        (folder / '123.json').write_text(json.dumps({'tweet_id': 123, 'author': {'name': 'Example'}}), encoding='utf-8')
        (folder / '123_1.jpg').write_bytes(b'old-media')
        self.store.legacy = legacy.resolve()
        index_folder(self.store, 'Example', legacy=True)
        self.store.legacy = None
        task, _ = self.store.submit('example')
        self.store.execute("UPDATE tasks SET status='running',task_kind='history',crawl_mode='media' WHERE id=?", (task['id'],))
        task = self.store.query('SELECT * FROM tasks WHERE id=?', (task['id'],))[0]
        runner = SmartRunner(self.store, task, emit=lambda event: None)
        raw = complete_tweet()
        raw['legacy']['extended_entities'] = {'media': [{'type': 'photo', 'media_url': self.url}]}
        runner.save_tweet(raw, True)
        settings = Engine(self.store).config(task)
        with patch.dict(config._config, settings, clear=True), patch.dict(postprocessor._cache, {'metadata': AtomicMetadataPP}):
            with self.assertRaises(SmartStop):
                runner.download_cached()
        self.assertEqual(self.requests, [])
        self.assertEqual(self.store.query('SELECT completed FROM crawl_posts')[0]['completed'], 0)

    def test_single_missing_legacy_mount_fails_without_downloading(self):
        legacy = self.root / 'old'
        folder = legacy / 'Example'
        folder.mkdir(parents=True)
        (folder / '123.json').write_text(json.dumps({'tweet_id': 123, 'author': {'name': 'Example'}}), encoding='utf-8')
        (folder / '123_1.jpg').write_bytes(b'old-media')
        self.store.legacy = legacy.resolve()
        index_folder(self.store, 'Example', legacy=True)
        self.store.legacy = None
        task, _ = self.store.submit('https://x.com/example/status/123')
        task['url'] = self.url
        engine = Engine(self.store)
        settings = engine.config(task)
        settings['extractor']['filename'] = '{tweet_id}_{num}.{extension}'
        settings['extractor']['keywords'] = {'tweet_id': 123, 'num': 1, 'author': {'name': 'example'}}
        with patch.dict('os.environ', {'XARCHIVE_LEGACY_MEDIA': ''}), patch.object(engine, 'config', return_value=settings):
            with self.assertRaises(DownloadError):
                engine(task, lambda: False, lambda n: None)
        self.assertEqual(self.requests, [])


if __name__ == '__main__':
    unittest.main()
