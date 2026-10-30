import json
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gallery_dl import config, postprocessor
from gallery_dl.extractor.twitter import TwitterTweetExtractor
from requests import Session
from xarchive.engine import Engine
from xarchive.gallery_runner import AtomicMetadataPP, SmartRunner, SmartStop
from xarchive.store import Store


def tweet(url):
    return {'rest_id': '123', 'legacy': {'id_str': '123', 'lang': 'en',
            'full_text': 'cached body', 'entities': {'urls': []},
            'created_at': 'Mon Oct 20 00:00:00 +0000 2025',
            'extended_entities': {'media': [{'id_str': '111', 'type': 'photo', 'media_url': url}]}},
            'user': {'id_str': '1', 'name': 'Fixture', 'screen_name': 'example',
            'created_at': 'Mon Oct 20 00:00:00 +0000 2025', 'location': '',
            'verified': False, 'protected': False, 'profile_image_url_https': '', 'description': ''}}


class CachedMediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'data')
        task, _ = self.store.submit('example')
        self.store.execute("UPDATE tasks SET status='running',task_kind='history',crawl_mode='media' WHERE id=?", (task['id'],))
        self.task = self.store.query('SELECT * FROM tasks WHERE id=?', (task['id'],))[0]
        self.runner = SmartRunner(self.store, self.task, emit=lambda event: None)

    def server(self):
        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                if self.path == '/expired.jpg':
                    self.send_error(403)
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'new image')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_port}'

    def test_incomplete_media_stops_probe_without_advancing(self):
        for media in ({'type': 'video', 'video_info': {'variants': []}}, {'type': 'photo'}):
            raw = tweet('unused')
            raw['legacy']['extended_entities']['media'] = [media]
            with self.subTest(media=media):
                with self.assertRaises(SmartStop) as caught:
                    self.runner.probe(iter([raw]))
                self.assertEqual(caught.exception.outcome, 'error')
        row = self.store.query('SELECT probe_complete,media_complete FROM tasks')[0]
        self.assertEqual(row, {'probe_complete': 0, 'media_complete': 0})

    def test_probe_allows_duplicate_posts_interspersed_with_new_posts(self):
        def listing():
            for identifier in range(1, 202):
                raw = tweet('https://pbs.twimg.com/fixture.jpg')
                raw['rest_id'] = raw['legacy']['id_str'] = str(identifier)
                yield raw
                yield raw
        self.runner.probe(listing())
        self.assertEqual(self.store.query('SELECT media_posts,probe_complete FROM tasks')[0],
                         {'media_posts': 201, 'probe_complete': 1})

    def test_expired_cached_url_refreshes_only_failed_pending_tweet(self):
        base = self.server()
        self.store.set_setting('x_cookies', {'auth_token': 'test-only'})
        self.runner.save_tweet(tweet(base + '/expired.jpg'), True)
        settings = Engine(self.store).config(self.task)
        settings['extractor']['retries'] = 0
        with patch.dict(config._config, settings, clear=True), \
                patch.dict(postprocessor._cache, {'metadata': AtomicMetadataPP}), \
                patch.object(TwitterTweetExtractor, 'login'), \
                patch.object(TwitterTweetExtractor, 'tweets', return_value=iter([tweet(base + '/new.jpg')])):
            self.runner.download_cached()
        row = self.store.query('SELECT completed,payload FROM crawl_posts')[0]
        self.assertEqual(row['completed'], 1)
        self.assertEqual(json.loads(row['payload'])['legacy']['extended_entities']['media'][0]['media_url'], base + '/new.jpg')
        self.assertEqual((self.store.downloads / 'example' / '123_1.jpg').read_bytes(), b'new image')

    def assert_refresh_preserves_old_media(self, refreshed_ids):
        base = self.server()
        self.store.set_setting('x_cookies', {'auth_token': 'test-only'})
        old = tweet(base + '/first.jpg')
        old_media = old['legacy']['extended_entities']['media']
        old_media[0]['id_str'] = '111'
        old_media.append({'id_str': '222', 'type': 'photo', 'media_url': base + '/expired.jpg'})
        self.runner.save_tweet(old, True)
        refreshed = tweet(base + '/first.jpg')
        refreshed['legacy']['extended_entities']['media'] = [
            {'id_str': identifier, 'type': 'photo', 'media_url': base + '/new.jpg'}
            for identifier in refreshed_ids]
        settings = Engine(self.store).config(self.task)
        settings['extractor']['retries'] = 0
        with patch.dict(config._config, settings, clear=True), \
                patch.dict(postprocessor._cache, {'metadata': AtomicMetadataPP}), \
                patch.object(TwitterTweetExtractor, 'login'), \
                patch.object(TwitterTweetExtractor, 'tweets', return_value=iter([refreshed])):
            with self.assertRaises(SmartStop) as caught:
                self.runner.download_cached()
        self.assertEqual(caught.exception.outcome, 'error')
        row = self.store.query('SELECT completed,payload FROM crawl_posts')[0]
        self.assertEqual(row['completed'], 0)
        self.assertEqual(json.loads(row['payload']), old)

    def test_refreshed_post_missing_media_remains_pending(self):
        self.assert_refresh_preserves_old_media(['111'])

    def test_refreshed_post_with_different_media_remains_pending(self):
        self.assert_refresh_preserves_old_media(['111', '333'])

    def test_refreshed_post_with_reordered_media_remains_pending(self):
        self.assert_refresh_preserves_old_media(['222', '111'])

    def test_refreshed_post_without_media_identity_remains_pending(self):
        self.assert_refresh_preserves_old_media(['111', ''])

    def test_refresh_rate_limit_leaves_cached_post_pending(self):
        base = self.server()
        self.store.set_setting('x_cookies', {'auth_token': 'test-only'})
        self.runner.save_tweet(tweet(base + '/expired.jpg'), True)
        events = []
        self.runner.emit = events.append
        settings = Engine(self.store).config(self.task)
        settings['extractor']['retries'] = 0
        original_request = Session.request
        def request(session, method, url, *args, **kwargs):
            if url.startswith('https://x.com/'):
                return SimpleNamespace(status_code=429, headers={'Retry-After': '120'})
            return original_request(session, method, url, *args, **kwargs)
        def login(extractor):
            extractor.session.get('https://x.com/fixture')
        with patch.dict(config._config, settings, clear=True), \
                patch.dict(postprocessor._cache, {'metadata': AtomicMetadataPP}), \
                patch.object(TwitterTweetExtractor, 'login', login), patch.object(Session, 'request', request):
            with self.assertRaises(SmartStop) as caught:
                self.runner.download_cached()
        self.assertEqual(caught.exception.outcome, 'rate_limit')
        self.assertEqual(events[-1], {'type': 'rate_limit', 'scope': 'shared', 'retry_after': 120})
        self.assertEqual(self.store.query('SELECT completed FROM crawl_posts')[0]['completed'], 0)

    def test_cancelled_task_cannot_replace_cached_payload(self):
        base = self.server()
        self.store.set_setting('x_cookies', {'auth_token': 'test-only'})
        old = tweet(base + '/expired.jpg')
        self.runner.save_tweet(old, True)
        settings = Engine(self.store).config(self.task)
        settings['extractor']['retries'] = 0
        def cancelled(extractor):
            self.store.execute("UPDATE tasks SET status='cancelled',generation=generation+1")
            return iter([tweet(base + '/new.jpg')])
        with patch.dict(config._config, settings, clear=True), \
                patch.dict(postprocessor._cache, {'metadata': AtomicMetadataPP}), \
                patch.object(TwitterTweetExtractor, 'login'), patch.object(TwitterTweetExtractor, 'tweets', cancelled):
            with self.assertRaises(SmartStop) as caught:
                self.runner.download_cached()
        self.assertEqual(caught.exception.outcome, 'interrupted')
        row = self.store.query('SELECT completed,payload FROM crawl_posts')[0]
        self.assertEqual(row['completed'], 0)
        self.assertEqual(json.loads(row['payload']), old)


if __name__ == '__main__':
    unittest.main()
