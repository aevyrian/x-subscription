import os
import json
import logging
import math
import sys
import time
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

from gallery_dl import main, postprocessor, config, extractor, job
from gallery_dl.postprocessor.metadata import MetadataPP

from .library import file_has_data, safe_file


class AtomicMetadataPP(MetadataPP):
    @contextmanager
    def open(self, path):
        pending = path + '.part'
        try:
            with super().open(pending) as stream:
                yield stream
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(pending, path)
        finally:
            Path(pending).unlink(missing_ok=True)


def retry_delay(headers, now=None):
    now = time.time() if now is None else now
    values = []
    value = headers.get('Retry-After') or headers.get('retry-after')
    if value:
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            try:
                values.append(parsedate_to_datetime(value).timestamp() - now)
            except (TypeError, ValueError, OverflowError):
                pass
    try:
        values.append(float(headers['x-rate-limit-reset']) - now)
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    values = [value for value in values if math.isfinite(value) and value > 0]
    return max(1, math.ceil(max(values))) if values else 900


def tweet_media(tweet):
    data = tweet.get('legacy', tweet)
    if data.get('retweeted_status_id_str') or data.get('quoted_by_id_str'):
        return False
    found = False
    for media in data.get('extended_entities', {}).get('media', ()):
        if media.get('ext_media_availability', {}).get('status') == 'Unavailable':
            continue
        kind = media.get('type')
        if kind == 'photo':
            available = media.get('media_url_https') or media.get('media_url')
        elif kind in ('video', 'animated_gif'):
            available = any(variant.get('url') for variant in media.get('video_info', {}).get('variants', ()))
        else:
            continue
        if not available:
            raise SmartStop('error')
        found = True
    return found


def media_identity(tweet):
    media = tweet.get('legacy', tweet).get('extended_entities', {}).get('media', ())
    items = [(item.get('type'), str(item.get('id_str') or item.get('id') or item.get('media_key') or ''))
             for item in media if item.get('type') in ('photo', 'video', 'animated_gif')
             and item.get('ext_media_availability', {}).get('status') != 'Unavailable']
    if not items or any(not identifier for _, identifier in items) or len(set(items)) != len(items):
        return None
    return items


class SmartStop(BaseException):
    # gallery-dl catches Exception around HTTP downloads; controls must escape it.
    def __init__(self, outcome):
        self.outcome = outcome


class VerifiedDownloadJob(job.DownloadJob):
    def initialize(self, keywords=None):
        super().initialize(keywords)
        # Let gallery-dl replace empty files through its normal temporary-file flow.
        pathfmt = self.pathfmt
        check_file = pathfmt.check_file
        pathfmt.check_file = lambda: file_has_data(Path(pathfmt.realpath)) and check_file()

    def handle_url(self, url, keywords):
        from .store import Store

        target = Path(self.pathfmt.realdirectory) / self.pathfmt.build_filename(keywords)
        if file_has_data(target):
            return super().handle_url(url, keywords)
        store = getattr(getattr(self, 'owner', None), 'store', None)
        if store is None:
            store = getattr(self, '_archive_store', None)
            if store is None:
                base = self.extractor.config('base-directory')
                store = self._archive_store = Store(Path(base).parent)
        tweet_id, num = str(keywords.get('tweet_id', '')), str(keywords.get('num', ''))
        if tweet_id.isdigit() and num.isdigit():
            for row in store.query("SELECT path,size FROM media WHERE post_id=? AND path LIKE '@legacy/%'", (tweet_id,)):
                if not Path(row['path']).name.startswith(tweet_id + '_' + num + '.'):
                    continue
                if store.legacy is None or not store.legacy.is_dir():
                    raise SmartStop('error')
                try:
                    size = safe_file(store, row['path'], allow_empty=True).stat().st_size
                except (ValueError, FileNotFoundError):
                    size = 0
                if size != row['size']:
                    store.execute('UPDATE media SET size=? WHERE path=?', (size, row['path']))
                if not size:
                    continue
                self.pathfmt.set_filename(keywords)
                self.pathfmt.fix_extension()
                self.handle_skip()
                return
        if self.archive is None:
            return super().handle_url(url, keywords)
        check = self.archive.check
        self.archive.check = lambda kw: bool(check(kw) and file_has_data(target))
        try:
            return super().handle_url(url, keywords)
        finally:
            self.archive.check = check


class SmartDownloadJob(VerifiedDownloadJob):
    def __init__(self, extr, owner):
        super().__init__(extr)
        self.owner, self.current, self.failed = owner, None, False

    def handle_directory(self, keywords):
        self.finish_current()
        self.current, self.failed = str(keywords['tweet_id']), False
        rows = self.owner.store.query('SELECT payload FROM crawl_posts WHERE task_id=? AND tweet_id=?',
                                      (self.owner.task['id'], self.current))
        if rows:
            raw = json.loads(rows[0]['payload'])
            expected = sum(media.get('type') in ('photo', 'video', 'animated_gif')
                           for media in raw.get('legacy', raw).get('extended_entities', {}).get('media', ())
                           if media.get('ext_media_availability', {}).get('status') != 'Unavailable')
            if keywords.get('count', 0) < expected:
                self.failed = self.owner.failed = True
                raise SmartStop('error')
        try:
            super().handle_directory(keywords)
        except BaseException:
            self.failed = self.owner.failed = True
            raise

    def handle_url(self, url, keywords):
        previous, self.status = self.status, 0
        try:
            super().handle_url(url, keywords)
        except BaseException:
            self.failed = self.owner.failed = True
            raise
        finally:
            if self.status:
                self.failed = self.owner.failed = True
                if self.current is not None:
                    self.owner.failed_media.add(self.current)
            self.status |= previous

    def finish_current(self):
        if self.current is not None:
            if not self.failed:
                self.owner.complete(self.current)
            self.current = None

    def handle_finalize(self):
        try:
            super().handle_finalize()
        finally:
            self.finish_current()


class SmartRunner:
    def __init__(self, store, task, emit=None, batch_size=200):
        self.store, self.task = store, dict(task)
        self.emit = emit or (lambda event: print('XARCHIVE_EVENT ' + json.dumps(event), flush=True))
        self.batch_size, self.processed = batch_size, 0
        self.active_job, self.failed, self.boundary = None, False, False
        self.failed_media = set()

    def guard(self, db):
        row = db.execute("SELECT 1 FROM tasks WHERE id=? AND generation=? AND status='running'",
                         (self.task['id'], self.task['generation'])).fetchone()
        if row is None:
            raise SmartStop('interrupted')

    def update(self, **values):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.guard(db)
            fields = ','.join(key + '=?' for key in values)
            db.execute('UPDATE tasks SET ' + fields + ' WHERE id=? AND generation=? AND status=\'running\'',
                       (*values.values(), self.task['id'], self.task['generation']))
        self.task.update(values)

    def counts(self, db):
        return db.execute('''SELECT COUNT(*) scanned,COALESCE(SUM(has_media),0) media,
                             COALESCE(SUM(completed),0) completed,
                             COALESCE(SUM(has_media*completed),0) media_complete
                             FROM crawl_posts WHERE task_id=?''', (self.task['id'],)).fetchone()

    def save_tweet(self, tweet, has_media):
        identifier = str(tweet.get('rest_id') or tweet.get('legacy', tweet).get('id_str', ''))
        if not identifier.isdigit() or len(identifier) > 25:
            raise SmartStop('error')
        payload = json.dumps(tweet, separators=(',', ':'), ensure_ascii=False)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.guard(db)
            inserted = db.execute('''INSERT OR IGNORE INTO crawl_posts(task_id,tweet_id,payload,has_media)
                                     VALUES (?,?,?,?)''',
                                  (self.task['id'], identifier, payload, int(has_media))).rowcount
            counts = self.counts(db)
            db.execute('''UPDATE tasks SET scanned_posts=?,media_posts=?
                          WHERE id=? AND generation=? AND status='running' ''',
                       (counts['scanned'], counts['media'], self.task['id'], self.task['generation']))
            completed = db.execute('SELECT completed FROM crawl_posts WHERE task_id=? AND tweet_id=?',
                                   (self.task['id'], identifier)).fetchone()['completed']
        self.task['media_posts'] = counts['media']
        self.emit({'type': 'progress', 'completed': counts['completed'], 'scanned': counts['scanned']})
        return bool(inserted), bool(completed)

    def complete(self, identifier):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.guard(db)
            changed = db.execute('''UPDATE crawl_posts SET completed=1
                                    WHERE task_id=? AND tweet_id=? AND completed=0''',
                                 (self.task['id'], identifier)).rowcount
            counts = self.counts(db)
            db.execute('''UPDATE tasks SET progress=?,scanned_posts=?
                          WHERE id=? AND generation=? AND status='running' ''',
                       (counts['completed'], counts['scanned'],
                        self.task['id'], self.task['generation']))
        self.processed += changed
        self.emit({'type': 'progress', 'completed': counts['completed'], 'scanned': counts['scanned']})

    def limited(self, response, scope='shared'):
        self.emit({'type': 'rate_limit', 'scope': scope, 'retry_after': retry_delay(response.headers)})
        raise SmartStop('rate_limit')

    def ensure_media_saved(self):
        if self.store.query('SELECT 1 FROM crawl_posts WHERE task_id=? AND has_media=1 AND completed=0 LIMIT 1',
                            (self.task['id'],)):
            raise SmartStop('error')

    def make_extractor(self, mode, cursor=None, post_id=None):
        suffix = '/status/' + post_id if post_id else '/media' if mode == 'media' else '/tweets'
        extr = extractor.find('https://x.com/' + self.task['user'] + suffix)
        original_config = extr.config
        def options(key, default=None):
            if key == 'cursor':
                return cursor or True
            if key == 'text-tweets':
                return mode != 'media'
            return original_config(key, default)
        extr.config = options
        extr.initialize()
        request = extr.session.request
        def checked_request(method, url, *args, **kwargs):
            response = request(method, url, *args, **kwargs)
            if response.status_code == 429:
                parsed = urlsplit(url)
                scope = 'task' if parsed.hostname in ('pbs.twimg.com', 'video.twimg.com') else 'shared'
                self.limited(response, scope)
            return response
        extr.session.request = checked_request
        extr.api._handle_ratelimit = lambda response: self.limited(response)
        update_cursor = extr._update_cursor
        def checkpoint(value):
            result = update_cursor(value)
            if self.active_job is not None:
                self.active_job.finish_current()
                self.ensure_media_saved()
            if self.failed:
                raise SmartStop('error')
            if self.boundary or value is None:
                self.update(cursor=None, probe_complete=1, media_complete=1)
            else:
                self.update(cursor=value)
            if self.boundary:
                raise SmartStop('completed')
            if value is not None and self.processed >= self.batch_size:
                raise SmartStop('batch')
            return result
        extr._update_cursor = checkpoint
        return extr

    def probe(self, tweets):
        skipped = 0
        for tweet in tweets:
            has_media = tweet_media(tweet)
            inserted, _ = self.save_tweet(tweet, has_media)
            if not inserted or not has_media:
                skipped += 1
                # Stop a broken media listing instead of scanning it indefinitely.
                if skipped >= self.batch_size:
                    raise SmartStop('error')
            else:
                skipped = 0
            if not has_media:
                continue
            self.processed += inserted
            if self.task['media_posts'] > 1000 and not self.task.get('history_choice'):
                raise SmartStop('confirmation')
        self.update(probe_complete=1, media_complete=1, cursor=None)

    def cached_posts(self):
        recent = self.task.get('history_choice') == 'recent300'
        limit = ' LIMIT 300' if recent else ''
        media_only = ' AND has_media=1' if self.task['crawl_mode'] == 'media' else ''
        return self.store.query('''SELECT tweet_id,payload FROM (SELECT tweet_id,payload,completed FROM crawl_posts WHERE task_id=?
                                  ''' + media_only + ''' ORDER BY length(tweet_id) DESC,tweet_id DESC''' + limit + ''')
                                  WHERE completed=0 ORDER BY length(tweet_id) DESC,tweet_id DESC LIMIT ?''',
                                (self.task['id'], self.batch_size))

    def download(self, extr, tweets):
        extr.tweets = lambda: tweets
        self.active_job = SmartDownloadJob(extr, self)
        try:
            status = self.active_job.run()
        finally:
            self.active_job = None
        if status or self.failed:
            raise SmartStop('error')

    def download_cached(self):
        selected = self.cached_posts()
        if selected:
            # Cached raw tweets contain everything needed; replay performs no X listing request.
            extr = self.make_extractor(self.task['crawl_mode'])
            extr.login = lambda: None
            extr.metadata = lambda: {}
            try:
                self.download(extr, (json.loads(row['payload']) for row in selected))
            except SmartStop as stop:
                if stop.outcome != 'error' or not self.failed_media:
                    raise
                original = {row['tweet_id']: json.loads(row['payload']) for row in selected}
                for identifier in self.failed_media:
                    with self.store.connect() as db:
                        self.guard(db)
                    fresh = self.make_extractor(self.task['crawl_mode'], post_id=identifier)
                    fresh.login()
                    tweet = next((item for item in fresh.tweets()
                                  if str(item.get('rest_id') or item.get('legacy', item).get('id_str', '')) == identifier), None)
                    expected = media_identity(original[identifier])
                    if tweet is None or not tweet_media(tweet) or not expected or media_identity(tweet) != expected:
                        raise SmartStop('error')
                    with self.store.connect() as db:
                        db.execute('BEGIN IMMEDIATE')
                        self.guard(db)
                        db.execute('UPDATE crawl_posts SET payload=? WHERE task_id=? AND tweet_id=? AND completed=0',
                                   (json.dumps(tweet, separators=(',', ':'), ensure_ascii=False), self.task['id'], identifier))
                selected_ids = {row['tweet_id'] for row in selected}
                selected = [row for row in self.cached_posts() if row['tweet_id'] in selected_ids]
                self.failed = False
                self.failed_media.clear()
                extr = self.make_extractor(self.task['crawl_mode'])
                extr.login = lambda: None
                extr.metadata = lambda: {}
                self.download(extr, (json.loads(row['payload']) for row in selected))
            pending = {row['tweet_id'] for row in self.cached_posts()}
            if any(row['tweet_id'] in pending for row in selected):
                raise SmartStop('error')
            if pending:
                raise SmartStop('batch')

    def stream(self, tweets, media_only=False):
        since = self.task.get('since_id') if self.task['task_kind'] == 'incremental' else None
        for tweet in tweets:
            data = tweet.get('legacy', tweet)
            if data.get('retweeted_status_id_str') or data.get('quoted_by_id_str'):
                continue
            identifier = str(tweet.get('rest_id') or data.get('id_str', ''))
            if since and identifier.isdigit() and int(identifier) <= int(since):
                if not tweet.get('pinned'):
                    self.boundary = True
                continue
            has_media = tweet_media(tweet)
            if media_only and not has_media:
                continue
            _, completed = self.save_tweet(tweet, has_media)
            if not completed:
                yield tweet

    def work(self):
        kind, mode = self.task['task_kind'], self.task['crawl_mode']
        if kind == 'history' and mode == 'auto':
            extr = self.make_extractor('full')
            extr._assign_user(extr.api.user_by_screen_name(self.task['user']))
            total = extr._user.get('statuses_count')
            total = total if type(total) is int and total >= 0 else None
            mode = 'full' if total is not None and total <= 300 else 'media'
            self.update(crawl_mode=mode, total_posts=total)
        if kind == 'history' and mode == 'media':
            choice = self.task.get('history_choice')
            if choice == 'skip':
                return
            if choice != 'recent300' and not self.task['probe_complete'] and choice != 'all':
                self.processed = 0
                extr = self.make_extractor('media', self.task.get('cursor'))
                self.probe(extr.tweets())
            self.processed = 0
            self.download_cached()
            if choice != 'all' or self.task['probe_complete']:
                return
        else:
            self.download_cached()
            if self.task['probe_complete']:
                # A recovered task may only need the parent process to index files already saved.
                return
        self.processed = 0
        mode = 'media' if kind == 'history' and mode == 'media' else 'full'
        extr = self.make_extractor(mode, self.task.get('cursor'))
        tweets = extr.tweets()
        self.download(extr, self.stream(tweets, mode == 'media'))
        self.ensure_media_saved()
        self.update(probe_complete=1, media_complete=1, cursor=None)

    def run(self):
        try:
            self.work()
        except SmartStop as stop:
            if stop.outcome != 'rate_limit':
                self.emit({'type': stop.outcome})
            return 1 if stop.outcome == 'error' else 0
        except Exception:
            self.emit({'type': 'error'})
            return 1
        self.emit({'type': 'completed'})
        return 0


def smart_main(filename):
    from .store import Store
    logging.disable(logging.CRITICAL)
    with open(filename, encoding='utf-8') as stream:
        settings = json.load(stream)
    context = settings.pop('_xarchive')
    config.clear()
    config.load((filename,), strict=True)
    store = Store(context['root'])
    rows = store.query('SELECT * FROM tasks WHERE id=? AND generation=? AND status=\'running\'',
                       (context['task_id'], context['generation']))
    if not rows:
        print('XARCHIVE_EVENT {"type":"interrupted"}', flush=True)
        return 0
    return SmartRunner(store, rows[0]).run()


def legacy_main():
    from gallery_dl.extractor.twitter import TwitterAPI
    from requests import Session
    request, handle_limit, download_job = Session.request, TwitterAPI._handle_ratelimit, job.DownloadJob
    def limited(response, scope='shared'):
        print('XARCHIVE_EVENT ' + json.dumps({'type': 'rate_limit', 'scope': scope,
                                            'retry_after': retry_delay(response.headers)}), flush=True)
        raise SmartStop('rate_limit')
    def checked_request(session, method, url, *args, **kwargs):
        response = request(session, method, url, *args, **kwargs)
        if response.status_code == 429:
            host = urlsplit(url).hostname
            limited(response, 'task' if host in ('pbs.twimg.com', 'video.twimg.com') else 'shared')
        return response
    Session.request = checked_request
    TwitterAPI._handle_ratelimit = lambda api, response: limited(response)
    job.DownloadJob = VerifiedDownloadJob
    try:
        return main()
    except SmartStop as stop:
        return 1 if stop.outcome == 'error' else 0
    finally:
        Session.request, TwitterAPI._handle_ratelimit = request, handle_limit
        job.DownloadJob = download_job


if __name__ == '__main__':
    # Keep gallery-dl's metadata format; replace only its file publishing step.
    postprocessor._cache['metadata'] = AtomicMetadataPP
    raise SystemExit(smart_main(sys.argv[2]) if len(sys.argv) == 3 and sys.argv[1] == '--smart' else legacy_main())
