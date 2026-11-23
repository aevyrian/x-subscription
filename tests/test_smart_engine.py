import io
import json
import functools
import threading
import tempfile
import unittest
from contextlib import redirect_stdout
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gallery_dl import config, postprocessor
from xarchive.engine import Engine
from xarchive.gallery_runner import AtomicMetadataPP, SmartRunner, SmartStop, legacy_main, retry_delay, tweet_media
from xarchive.store import Store
from xarchive.worker import BatchComplete, DownloadError, HistoryConfirmation, Worker


def tweet(identifier, media=1):
    return {'rest_id': str(identifier), 'legacy': {'id_str': str(identifier),
            'extended_entities': {'media': [{'type': 'photo', 'media_url_https': 'https://pbs.twimg.com/fixture.jpg'}] * media}}}


def complete_tweet(identifier=123):
    return {'rest_id': str(identifier), 'legacy': {'id_str': str(identifier), 'lang': 'en',
            'full_text': 'cached body', 'entities': {'urls': []},
            'created_at': 'Mon Oct 20 00:00:00 +0000 2025'},
            'user': {'id_str': '1', 'name': 'Fixture', 'screen_name': 'example',
            'created_at': 'Mon Oct 20 00:00:00 +0000 2025', 'location': '',
            'verified': False, 'protected': False, 'profile_image_url_https': '', 'description': ''}}


class SmartEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'data')
        self.task, _ = self.store.submit('example')
        self.store.execute("UPDATE tasks SET status='running',task_kind='history',crawl_mode='media' WHERE id=?", (self.task['id'],))
        self.task = self.store.query('SELECT * FROM tasks WHERE id=?', (self.task['id'],))[0]
        self.events = []

    def runner(self):
        return SmartRunner(self.store, self.task, emit=self.events.append)

    def test_rate_delay_uses_both_headers_and_http_date(self):
        self.assertEqual(retry_delay({'Retry-After': '20', 'x-rate-limit-reset': '1040'}, now=1000), 40)
        self.assertEqual(retry_delay({'Retry-After': 'Thu, 01 Jan 1970 00:17:30 GMT'}, now=1000), 50)
        self.assertEqual(retry_delay({'Retry-After': 'invalid'}, now=1000), 900)

    def test_media_is_tweets_not_files_and_excludes_retweets(self):
        self.assertTrue(tweet_media(tweet(1, 4)))
        self.assertFalse(tweet_media(tweet(2, 0)))
        self.assertFalse(tweet_media({'legacy': {'retweeted_status_id_str': '1', 'extended_entities': {'media': [{'type': 'photo'}]}}}))

    def test_media_probe_does_not_scan_unbounded_nonmedia_results(self):
        observed = []
        def unexpected_text_timeline():
            for identifier in range(2000):
                observed.append(identifier)
                yield tweet(identifier + 1, 0)
        with self.assertRaises(SmartStop) as caught:
            self.runner().probe(unexpected_text_timeline())
        self.assertEqual(caught.exception.outcome, 'error')
        self.assertLessEqual(len(observed), 200)

    def test_duplicate_only_listing_is_bounded_and_not_counted_again(self):
        with self.assertRaises(SmartStop) as caught:
            self.runner().probe(tweet(1) for _ in range(2000))
        self.assertEqual(caught.exception.outcome, 'error')
        self.assertEqual(self.store.query('SELECT scanned_posts,media_posts FROM tasks')[0], {'scanned_posts': 1, 'media_posts': 1})

    def test_probe_waits_at_1001_unique_media_tweets(self):
        runner = self.runner()
        source = [tweet(i, 4) for i in range(1001, 0, -1)]
        source.insert(2, tweet(1001, 4))
        with self.assertRaises(SmartStop) as caught:
            runner.probe(iter(source))
        self.assertEqual(caught.exception.outcome, 'confirmation')
        self.assertEqual(self.store.query('SELECT COUNT(*) n FROM crawl_posts')[0]['n'], 1001)
        self.assertEqual(self.store.query('SELECT media_posts FROM tasks')[0]['media_posts'], 1001)
        self.assertFalse(list(self.store.downloads.rglob('*.json')))

    def test_stale_runner_cannot_write_after_cancel_or_generation_change(self):
        runner = self.runner()
        self.store.execute("UPDATE tasks SET status='cancelled',generation=generation+1 WHERE id=?", (self.task['id'],))
        with self.assertRaises(SmartStop):
            runner.save_tweet(tweet(1), True)
        self.assertEqual(self.store.query('SELECT COUNT(*) n FROM crawl_posts')[0]['n'], 0)

    def test_recent300_selects_newest_tweet_ids_without_listing(self):
        runner = self.runner()
        for identifier in range(1, 1002):
            runner.save_tweet(tweet(identifier, 4), True)
        self.store.execute("UPDATE tasks SET history_choice='recent300' WHERE id=?", (self.task['id'],))
        runner.task['history_choice'] = 'recent300'
        batches = []

        def download(_, tweets):
            ids = [tweet['rest_id'] for tweet in tweets]
            batches.append(ids)
            for identifier in ids:
                runner.complete(identifier)

        with patch.object(runner, 'make_extractor'), patch.object(runner, 'download', side_effect=download):
            with self.assertRaises(SmartStop) as caught:
                runner.download_cached()
            self.assertEqual(caught.exception.outcome, 'batch')
            runner.download_cached()

        self.assertEqual(batches, [[str(i) for i in range(1001, 801, -1)],
                                   [str(i) for i in range(801, 701, -1)]])
        self.assertEqual(self.store.query('SELECT progress FROM tasks'), [{'progress': 300}])

    def test_limit_stops_even_before600_and_preserves_scope(self):
        for count in (1, 599, 600, 601):
            for scope in ('shared', 'task'):
                runner = self.runner()
                runner.processed = count
                with self.assertRaises(SmartStop) as caught:
                    runner.limited(SimpleNamespace(headers={'Retry-After': '35'}), scope)
                self.assertEqual(caught.exception.outcome, 'rate_limit')
                self.assertEqual(self.events[-1]['scope'], scope)
                self.assertEqual(self.events[-1]['retry_after'], 35)

    def test_cached_full_tweet_replays_through_real_gallery_without_any_request(self):
        self.store.set_setting('x_cookies', {'auth_token': 'test-only'})
        runner = self.runner()
        runner.update(crawl_mode='full')
        raw = complete_tweet()
        runner.save_tweet(raw, False)
        settings = Engine(self.store).config(self.task)
        with patch.dict(config._config, settings, clear=True), patch.dict(postprocessor._cache, {'metadata': AtomicMetadataPP}), \
                patch('requests.Session.request', side_effect=AssertionError('cached replay requested network')):
            runner.download_cached()
        self.assertEqual(self.store.query('SELECT progress FROM tasks')[0]['progress'], 1)
        self.assertEqual(json.loads((self.store.downloads / 'example' / '123.json').read_text(encoding='utf-8'))['content'], 'cached body')

    def test_recovered_finished_listing_indexes_saved_post_without_reopening_x(self):
        for kind, next_cursor in (('history', None), ('incremental', None), ('incremental', 'older-page')):
            with self.subTest(kind=kind, next_cursor=next_cursor), tempfile.TemporaryDirectory() as root:
                store = Store(root)
                store.set_setting('x_cookies', {'auth_token': 'test-only'})
                task, _ = store.subscribe('example', 'weekly')
                if kind == 'incremental':
                    task, _ = store.submit('example', source='update')
                claimed = store.claim()
                self.assertEqual((claimed['id'], claimed['task_kind']), (task['id'], kind))
                engine = Engine(store)
                first = SmartRunner(store, claimed, emit=lambda event: None)
                first.update(crawl_mode='full')
                post_id = str(int(claimed['since_id'] or 0) + 901)
                settings = engine.config(claimed)
                fresh = first.make_extractor

                def offline_listing(*args):
                    extr = fresh(*args)
                    extr.login = lambda: None
                    extr.metadata = lambda: {}
                    def listing():
                        yield complete_tweet(int(post_id))
                        if next_cursor:
                            yield complete_tweet(int(claimed['since_id']))
                        extr._update_cursor(next_cursor)
                    extr.tweets = listing
                    return extr

                with patch.dict(config._config, settings, clear=True), \
                        patch.dict(postprocessor._cache, {'metadata': AtomicMetadataPP}), \
                        patch.object(first, 'make_extractor', side_effect=offline_listing):
                    self.assertEqual(first.run(), 0)
                self.assertEqual(first.task['probe_complete'], 1)
                self.assertIsNone(first.task['cursor'])
                folder = store.downloads / 'example'
                self.assertTrue((folder / (post_id + '.json')).is_file())
                (folder / (post_id + '_1.jpg')).write_bytes(b'fixture-media')
                self.assertFalse(store.query('SELECT id FROM posts'))

                # Restart between saving the files and having the parent process index them.
                store.recover()
                duplicate, unchanged = store.submit('example', source='update' if kind == 'incremental' else 'website')
                self.assertTrue(unchanged)
                self.assertEqual(duplicate['id'], claimed['id'])

                def run_child(*args, **kwargs):
                    events = []
                    current = store.query('SELECT * FROM tasks WHERE id=?', (claimed['id'],))[0]
                    result = SmartRunner(store, current, emit=events.append).run()
                    stdout = io.StringIO(''.join('XARCHIVE_EVENT ' + json.dumps(event) + '\n' for event in events))
                    return SimpleNamespace(returncode=result, stdout=stdout, poll=lambda: result)

                with patch('xarchive.engine.subprocess.Popen', side_effect=run_child), \
                        patch.object(SmartRunner, 'make_extractor', side_effect=AssertionError('reopened X timeline')) as listing:
                    self.assertTrue(Worker(store, engine).run_one())
                    self.assertEqual(store.query('SELECT status,new_posts,new_media FROM tasks WHERE id=?', (claimed['id'],)),
                                     [{'status': 'succeeded', 'new_posts': 1, 'new_media': 1}])
                    listing.assert_not_called()
                self.assertEqual(store.query('SELECT id FROM posts'), [{'id': post_id}])
                self.assertEqual(store.query('SELECT post_id,size FROM media'),
                                 [{'post_id': post_id, 'size': len(b'fixture-media')}])
                blogger = store.query('SELECT last_success_at,high_water_id FROM bloggers')[0]
                self.assertIsNotNone(blogger['last_success_at'])
                if kind == 'incremental':
                    self.assertEqual(blogger['high_water_id'], post_id)

    def test_full_tweet_media_parse_failure_cannot_mark_text_only_complete(self):
        self.store.set_setting('x_cookies', {'auth_token': 'test-only'})
        runner = self.runner()
        runner.update(crawl_mode='full', cursor='safe-page')
        raw = complete_tweet()
        raw['legacy']['extended_entities'] = {'media': [{'type': 'photo', 'media_url_https': 'https://pbs.twimg.com/malformed.jpg'}]}
        runner.save_tweet(raw, True)
        settings = Engine(self.store).config(self.task)
        with patch.dict(config._config, settings, clear=True), patch.dict(postprocessor._cache, {'metadata': AtomicMetadataPP}), \
                patch('requests.Session.request', side_effect=AssertionError('malformed media requested network')):
            with self.assertRaises(SmartStop) as caught:
                runner.download_cached()
        self.assertEqual(caught.exception.outcome, 'error')
        self.assertEqual(self.store.query('SELECT completed FROM crawl_posts')[0]['completed'], 0)
        self.assertEqual(self.store.query('SELECT cursor FROM tasks')[0]['cursor'], 'safe-page')

    def test_partial_media_parse_failure_keeps_entire_tweet_pending(self):
        self.store.set_setting('x_cookies', {'auth_token': 'test-only'})
        runner = self.runner()
        raw = complete_tweet()
        raw['legacy']['extended_entities'] = {'media': [
            {'type': 'photo', 'media_url': 'http://127.0.0.1:9/first.jpg'},
            {'type': 'photo', 'media_url_https': 'https://pbs.twimg.com/malformed.jpg'}]}
        runner.save_tweet(raw, True)
        settings = Engine(self.store).config(self.task)
        with patch.dict(config._config, settings, clear=True), patch('requests.Session.request', side_effect=AssertionError('partial parse should fail before downloading')):
            with self.assertRaises(SmartStop) as caught:
                runner.download_cached()
        self.assertEqual(caught.exception.outcome, 'error')
        self.assertEqual(self.store.query('SELECT completed FROM crawl_posts')[0]['completed'], 0)

    def test_media_stream_parse_failure_cannot_advance_page_or_finish(self):
        self.store.set_setting('x_cookies', {'auth_token': 'test-only'})
        runner = self.runner()
        runner.update(cursor='safe-page')
        raw = complete_tweet()
        raw['legacy']['extended_entities'] = {'media': [{'type': 'photo', 'media_url_https': 'https://pbs.twimg.com/malformed.jpg'}]}
        settings = Engine(self.store).config(self.task)
        with patch.dict(config._config, settings, clear=True), patch('requests.Session.request', side_effect=AssertionError('bad parse must not request network')):
            extr = runner.make_extractor('media', 'safe-page')
            def listing():
                yield raw
                extr._update_cursor('next-page')
            with self.assertRaises(SmartStop) as caught:
                runner.download(extr, runner.stream(listing(), True))
        self.assertEqual(caught.exception.outcome, 'error')
        self.assertEqual(self.store.query('SELECT cursor FROM tasks')[0]['cursor'], 'safe-page')
        self.assertEqual(self.store.query('SELECT completed FROM crawl_posts')[0]['completed'], 0)

    def test_explicitly_unavailable_media_does_not_block_saving_readable_text(self):
        self.store.set_setting('x_cookies', {'auth_token': 'test-only'})
        runner = self.runner()
        runner.update(crawl_mode='full')
        raw = complete_tweet()
        raw['legacy']['extended_entities'] = {'media': [{'type': 'photo', 'ext_media_availability': {'status': 'Unavailable'}}]}
        self.assertFalse(tweet_media(raw))
        runner.save_tweet(raw, False)
        settings = Engine(self.store).config(self.task)
        with patch.dict(config._config, settings, clear=True), patch.dict(postprocessor._cache, {'metadata': AtomicMetadataPP}), \
                patch('requests.Session.request', side_effect=AssertionError('unavailable media requested network')):
            runner.download_cached()
        self.assertEqual(self.store.query('SELECT completed FROM crawl_posts')[0]['completed'], 1)

    def test_old_pinned_tweet_does_not_end_incremental_page(self):
        runner = self.runner()
        runner.task.update(task_kind='incremental', since_id='500')
        pinned = tweet(1)
        pinned['pinned'] = True
        result = list(runner.stream(iter((pinned, tweet(600)))))
        self.assertEqual([row['rest_id'] for row in result], ['600'])
        self.assertFalse(runner.boundary)

    def test_engine_maps_structured_control_and_allows_zero_incremental_posts(self):
        self.store.set_setting('x_cookies', {'auth_token': 'test-only'})
        for event, expected in (({'type': 'batch'}, BatchComplete), ({'type': 'confirmation'}, HistoryConfirmation),
                                ({'type': 'rate_limit', 'scope': 'task', 'retry_after': 35}, DownloadError),
                                ({'type': 'completed'}, None)):
            process = SimpleNamespace(returncode=0, stdout=io.StringIO('XARCHIVE_EVENT ' + json.dumps(event) + '\n'), poll=lambda: 0)
            with patch('xarchive.engine.subprocess.Popen', return_value=process):
                if expected:
                    with self.assertRaises(expected) as caught:
                        Engine(self.store)(self.task, lambda: False, lambda count: None)
                    if expected is DownloadError:
                        self.assertEqual((caught.exception.cooldown_scope, caught.exception.retry_after), ('task', 35))
                else:
                    self.assertEqual(Engine(self.store)(self.task, lambda: False, lambda count: None), (0, 0))

    def test_profile_threshold_uses_fresh_reported_total_and_unknown_stays_unknown(self):
        for total, expected in ((299, 'full'), (300, 'full'), (301, 'media'), (None, 'media'), (True, 'media')):
            with self.subTest(total=total):
                runner = self.runner()
                runner.task['crawl_mode'] = 'auto'
                fresh = []
                extr = SimpleNamespace(_user={'statuses_count': 280}, tweets=lambda: iter(()))
                def profile(name):
                    fresh.append(name)
                    return {'statuses_count': total}
                extr.api = SimpleNamespace(user_by_screen_name=profile,
                                           _user_id_by_screen_name=lambda name: self.fail('cached total used'))
                extr._assign_user = lambda value: setattr(extr, '_user', value)
                with patch.object(runner, 'make_extractor', return_value=extr), \
                        patch.object(runner, 'download'), patch.object(runner, 'download_cached'):
                    runner.work()
                self.assertEqual(runner.task['crawl_mode'], expected)
                self.assertEqual(runner.task['total_posts'], total if type(total) is int else None)
                self.assertEqual(fresh, ['example'])

    def test_natural_probe_end_at1000_is_exact_without_confirmation(self):
        runner = self.runner()
        with self.store.connect() as db:
            db.executemany('INSERT INTO crawl_posts(task_id,tweet_id,payload,has_media) VALUES (?,?,?,1)',
                           ((self.task['id'], str(identifier), json.dumps(tweet(identifier))) for identifier in range(1, 1001)))
        runner.probe(iter((tweet(1000, 4),)))
        row = self.store.query('SELECT media_posts,media_complete,probe_complete FROM tasks')[0]
        self.assertEqual(row, {'media_posts': 1000, 'media_complete': 1, 'probe_complete': 1})

    def test_real_gallery_failed_page_replays_without_advancing_cursor_or_double_counting(self):
        source = Path(self.tmp.name) / 'source'
        source.mkdir()
        (source / 'sample.jpg').write_bytes(b'local-image')
        failing = [True]
        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                if failing[0]:
                    self.send_error(503)
                else:
                    super().do_GET()
        server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(source)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.store.set_setting('x_cookies', {'auth_token': 'test-only'})
        runner = self.runner()
        runner.update(cursor='old-page')
        settings = Engine(self.store).config(self.task)
        settings['extractor']['retries'] = 0
        raw = complete_tweet()
        raw['legacy']['extended_entities'] = {'media': [{'type': 'photo', 'media_url': f'http://127.0.0.1:{server.server_port}/sample.jpg'}]}
        with patch.dict(config._config, settings, clear=True), patch.dict(postprocessor._cache, {'metadata': AtomicMetadataPP}):
            extr = runner.make_extractor('media', 'old-page')
            def listing():
                yield raw
                extr._update_cursor('next-page')
            with self.assertRaises(SmartStop) as caught:
                runner.download(extr, runner.stream(listing(), True))
            self.assertEqual(caught.exception.outcome, 'error')
            self.assertEqual(self.store.query('SELECT cursor,progress FROM tasks')[0], {'cursor': 'old-page', 'progress': 0})
            failing[0] = False
            resumed = self.runner()
            resumed.download_cached()
            self.assertEqual(self.store.query('SELECT COUNT(*) n,SUM(completed) complete FROM crawl_posts')[0], {'n': 1, 'complete': 1})
            extr = resumed.make_extractor('media', 'old-page')
            def repeated_listing():
                yield raw
                extr._update_cursor('next-page')
            resumed.download(extr, resumed.stream(repeated_listing(), True))
        self.assertEqual(self.store.query('SELECT cursor,progress FROM tasks')[0], {'cursor': 'next-page', 'progress': 1})

    def test_actual_api_low_remaining_keeps_structured_shared_delay(self):
        self.store.set_setting('x_cookies', {'auth_token': 'test-only'})
        runner = self.runner()
        runner.update(crawl_mode='full')
        settings = Engine(self.store).config(self.task)
        with patch.dict(config._config, settings, clear=True):
            extr = runner.make_extractor('full')
            extr.api._user_id_by_screen_name = lambda user: '1'
            extr.api._transaction_id = lambda *args: None
            extr.request = lambda *args, **kwargs: SimpleNamespace(cookies={}, status_code=200,
                headers={'x-rate-limit-remaining': '0', 'x-rate-limit-reset': '1040'})
            with patch.object(runner, 'make_extractor', return_value=extr), patch('xarchive.gallery_runner.time.time', return_value=1000):
                self.assertEqual(runner.run(), 0)
        self.assertEqual(self.events[-1], {'type': 'rate_limit', 'scope': 'shared', 'retry_after': 40})

    def test_legacy_and_single_guard_use_retry_after_without_exposing_response(self):
        from requests import Session
        for host, scope in (('x.com/i/api/graphql/example/UserTweets', 'shared'), ('video.twimg.com/fixture.mp4', 'task')):
            output = io.StringIO()
            response = SimpleNamespace(status_code=429, headers={'Retry-After': '1200'})
            def run_gallery():
                Session().request('GET', 'https://' + host)
                self.fail('429 allowed extraction to continue')
            with patch('requests.Session.request', return_value=response), patch('xarchive.gallery_runner.main', side_effect=run_gallery), redirect_stdout(output):
                self.assertEqual(legacy_main(), 0)
            event = json.loads(output.getvalue().strip().removeprefix('XARCHIVE_EVENT '))
            self.assertEqual(event, {'type': 'rate_limit', 'scope': scope, 'retry_after': 1200})

    def test_worker_engine_structured_limit_persists_task_only_cooldown(self):
        self.store.set_setting('x_cookies', {'auth_token': 'test-only'})
        self.store.execute("UPDATE tasks SET status='queued' WHERE id=?", (self.task['id'],))
        process = SimpleNamespace(returncode=0, stdout=io.StringIO('XARCHIVE_EVENT {"type":"rate_limit","scope":"task","retry_after":1200}\n'), poll=lambda: 0)
        with patch('xarchive.engine.subprocess.Popen', return_value=process):
            self.assertTrue(Worker(self.store, Engine(self.store)).run_one())
        row = self.store.query('SELECT status,cooldown_scope,cooldown_until,attempts FROM tasks')[0]
        self.assertEqual((row['status'], row['cooldown_scope'], row['attempts']), ('retry', 'task', 0))
        self.assertGreater(row['cooldown_until'], 0)
        self.assertFalse(self.store.setting('x_rate_limit_until', 0))


if __name__ == '__main__':
    unittest.main()
