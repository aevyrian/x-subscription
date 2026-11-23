import functools
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gallery_dl import exception
from gallery_dl.extractor.twitter import TwitterAPI

from xarchive.engine import Engine
from xarchive.library import index_folder
from xarchive.store import Store
from xarchive.worker import DownloadError


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class EngineReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'data')
        self.store.set_setting('x_cookies', {'auth_token': 'test-only'})
        self.job, _ = self.store.submit('example')
        self.job['task_kind'] = 'legacy'
        self.engine = Engine(self.store)

    def completed_process(self, output):
        return SimpleNamespace(returncode=0, stdout=io.StringIO(output), poll=lambda: 0)

    def test_cookie_snapshot_keeps_its_matching_revision(self):
        self.store.set_setting('x_cookie_revision', 3)
        config = self.engine.config(self.job)
        self.store.set_setting('x_cookie_revision', 4)
        self.store.set_setting('x_cookies', {'auth_token': 'fixture-replacement'})
        self.assertEqual(self.job['cookie_revision'], 3)
        self.assertEqual(config['extractor']['twitter']['cookies'], {'auth_token': 'test-only'})

    def test_rate_limit_yields_even_with_zero_exit_code(self):
        process = self.completed_process('XARCHIVE_POST\n[twitter][error] Rate limit exceeded\n')
        with patch('xarchive.engine.subprocess.Popen', return_value=process):
            with self.assertRaises(DownloadError) as caught:
                self.engine(self.job, lambda: False, lambda n: None)
        self.assertEqual(caught.exception.retry_after, 900)

    def test_recovered_media_429_warning_does_not_fail_successful_task(self):
        process = self.completed_process('[downloader.http][warning] 429 Too Many Requests; trying fallback\nXARCHIVE_POST\nXARCHIVE_SAVED\n')
        with patch('xarchive.engine.subprocess.Popen', return_value=process):
            self.assertEqual(self.engine(self.job, lambda: False, lambda n: None), (0, 0))

    def test_ratelimit_username_in_warning_is_not_an_account_limit(self):
        process = self.completed_process('[twitter][warning] 503: https://x.com/ratelimit/status/429\nXARCHIVE_POST\n')
        with patch('xarchive.engine.subprocess.Popen', return_value=process):
            self.assertEqual(self.engine(self.job, lambda: False, lambda n: None), (0, 0))

    def test_ratelimit_url_on_failed_process_does_not_classify_as_account_limit(self):
        process = self.completed_process('[twitter][error] HTTP 503: https://x.com/ratelimit/status/429\n')
        process.returncode = 1
        with patch('xarchive.engine.subprocess.Popen', return_value=process):
            with self.assertRaises(DownloadError) as caught:
                self.engine(self.job, lambda: False, lambda n: None)
        self.assertIsNone(caught.exception.retry_after)
        self.assertNotIn('限流', str(caught.exception))

    def test_gallery_rate_limit_aborts_instead_of_sleeping(self):
        twitter = self.engine.config(self.job)['extractor']['twitter']
        api = TwitterAPI.__new__(TwitterAPI)
        api.exc = exception
        api.extractor = SimpleNamespace(config=twitter.get, wait=lambda **kwargs: self.fail('Rate limit blocked the queue'))
        with self.assertRaises(exception.AbortExtraction) as caught:
            api._handle_ratelimit(SimpleNamespace(headers={}))
        self.assertNotEqual(caught.exception.code, 0)

    def test_index_and_task_counts_rollback_together(self):
        folder = self.store.downloads / 'example'
        folder.mkdir()
        (folder / '123.json').write_text(json.dumps({'tweet_id': 123, 'author': {'name': 'example'}, 'content': 'fixture'}), encoding='utf-8')
        (folder / '123_1.jpg').write_bytes(b'fixture-image')
        self.store.execute("""CREATE TRIGGER reject_task_counts BEFORE UPDATE OF new_posts ON tasks
                            BEGIN SELECT RAISE(ABORT, 'fixture count failure'); END""")
        with patch('xarchive.engine.subprocess.Popen', return_value=self.completed_process('XARCHIVE_POST\n')):
            with self.assertRaises(sqlite3.IntegrityError):
                self.engine(self.job, lambda: False, lambda n: None)
        self.assertEqual(self.store.query('SELECT COUNT(*) n FROM posts')[0]['n'], 0)
        self.assertEqual(self.store.query('SELECT COUNT(*) n FROM media')[0]['n'], 0)
        self.store.execute('DROP TRIGGER reject_task_counts')
        for _ in range(2):
            with patch('xarchive.engine.subprocess.Popen', return_value=self.completed_process('XARCHIVE_POST\n')):
                self.engine(self.job, lambda: False, lambda n: None)
        row = self.store.query('SELECT new_posts,new_media FROM tasks')[0]
        self.assertEqual(row, {'new_posts': 1, 'new_media': 1})

    def test_periodic_index_skips_idle_directory_and_finds_new_files_during_download(self):
        folder = self.store.downloads / 'example'
        folder.mkdir()
        meta = folder / '123.json'
        image = folder / '123_1.jpg'
        now = 0
        scan_times = []

        def sleep(seconds):
            nonlocal now
            now += seconds
            if now == 12:
                stamp = folder.stat()
                meta.write_text(json.dumps({'tweet_id': 123, 'author': {'name': 'example'}}), encoding='utf-8')
                image.write_bytes(b'first')
                os.utime(folder, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 2_000_000_000))
            if now == 20:
                self.assertEqual(self.store.query('SELECT id FROM posts'), [{'id': '123'}])
                self.assertEqual(self.store.query('SELECT size FROM media'), [{'size': 5}])
            if now == 22:
                # An in-place repair can leave the parent directory timestamp unchanged.
                image.write_bytes(b'repaired')

        def scan(*args, **kwargs):
            scan_times.append(now)
            return index_folder(*args, **kwargs)

        process = self.completed_process('XARCHIVE_POST 123\n')
        process.poll = lambda: None if now < 26 else process.returncode
        clock = SimpleNamespace(monotonic=lambda: now, sleep=sleep)
        with patch('xarchive.engine.subprocess.Popen', return_value=process), \
                patch('xarchive.engine.time', clock), patch('xarchive.engine.index_folder', side_effect=scan):
            self.engine(self.job, lambda: False, lambda n: None)

        self.assertEqual(scan_times, [5, 15, 26])
        self.assertEqual(self.store.query('SELECT size FROM media'), [{'size': 8}])
        self.assertEqual(self.store.query('SELECT new_posts,new_media FROM tasks'), [{'new_posts': 1, 'new_media': 1}])

    def test_subprocess_metadata_write_failure_keeps_previous_json(self):
        source = Path(self.tmp.name) / 'source'
        source.mkdir()
        (source / 'sample.jpg').write_bytes(b'fixture-image')
        server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(source)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        folder = self.store.downloads / 'example'
        folder.mkdir()
        previous = json.dumps({'tweet_id': 123, 'author': {'name': 'example'}, 'content': 'keep previous'})
        metadata = folder / '123.json'
        metadata.write_text(previous, encoding='utf-8')
        self.job['url'] = f'http://127.0.0.1:{server.server_port}/sample.jpg'
        config = self.engine.config(self.job)
        config['extractor']['keywords'] = {'tweet_id': 123, 'author': {'name': 'example'}, 'content': 'replacement'}
        child = '''
import runpy, sys
from gallery_dl.postprocessor.metadata import MetadataPP
original = MetadataPP.open
class PartialWrite:
    def __init__(self, stream): self.stream = stream
    def __enter__(self): return self
    def write(self, content):
        self.stream.write(content[:10])
        self.stream.flush()
        raise OSError('fixture interrupted metadata write')
    def __exit__(self, *args): self.stream.close()
MetadataPP.open = lambda self, path: PartialWrite(original(self, path))
module = sys.argv.pop(1)
runpy.run_module(module, run_name='__main__')
'''
        spawn = subprocess.Popen
        def interrupted_writer(command, **kwargs):
            return spawn([sys.executable, '-B', '-c', child, command[3], *command[4:]], **kwargs)
        with patch.object(self.engine, 'config', return_value=config), patch('xarchive.engine.subprocess.Popen', side_effect=interrupted_writer):
            with self.assertRaises(DownloadError):
                self.engine(self.job, lambda: False, lambda n: None)
        self.assertEqual(metadata.read_text(encoding='utf-8'), previous)
        self.assertFalse(list(folder.glob('*.part')))


if __name__ == '__main__':
    unittest.main()
