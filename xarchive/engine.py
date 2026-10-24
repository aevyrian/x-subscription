import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

from .library import index_folder
from .worker import DownloadError, Interrupted


def parse_cookies(value):
    if not value.strip():
        return {}
    result = {}
    if '\t' in value:
        for line in value.splitlines():
            if line.startswith('#HttpOnly_'):
                line = line[10:]
            if not line or line.startswith('#'):
                continue
            fields = line.split('\t')
            if len(fields) == 7 and fields[0].lstrip('.').lower() in ('x.com', 'twitter.com'):
                if fields[5] in ('auth_token', 'ct0'):
                    result[fields[5]] = fields[6].strip()
    else:
        value = value.removeprefix('Cookie:').removeprefix('cookie:').strip()
        for item in value.split(';'):
            name, sep, content = item.strip().partition('=')
            if sep and name in ('auth_token', 'ct0'):
                result[name] = content.strip()
    if not result.get('auth_token') or any('\n' in v or '\r' in v for v in result.values()):
        raise ValueError('Cookie 中需要 auth_token；支持 Cookie 请求头或 Netscape 文件内容')
    return result


def classify_error(text, code):
    text = text.lower()
    if re.search(r'\b429\b|rate[ -]?limit', text):
        return 'X 请求限流，稍后自动重试'
    if any(x in text for x in ('401', '403', 'authentication', 'login', 'cookies', 'unauthorized', 'locked')):
        return 'X 登录失效或访问受限，请在设置中更新 Cookie'
    if any(x in text for x in ('404', 'not found', 'not exist', 'unavailable', 'deleted', 'suspended')):
        return '帖子或博主不可用，可能已删除或无访问权限'
    if any(x in text for x in ('timeout', 'connection', 'proxy', 'network', 'resolve', 'ssl')):
        return '网络连接失败，请检查网络或代理后重试'
    if any(x in text for x in ('permission', 'space', 'disk', 'read-only')):
        return '存储不可写或空间不足，请检查 Volume'
    return f'gallery-dl 下载失败（退出码 {code}），可更新 Cookie 或引擎后重试'


class Engine:
    def __init__(self, store):
        self.store = store
        # Remove only our own interrupted per-job snapshots, never user configuration.
        for path in store.config.glob('.run-*.json'):
            path.unlink(missing_ok=True)

    def config(self, task):
        return {
            'cache': {'file': str(self.store.config / 'gallery-cache.sqlite3')},
            'extractor': {
                'base-directory': str(self.store.downloads), 'directory': [task['user']],
                'filename': '{tweet_id}_{num}.{extension}',
                'archive': str(self.store.config / 'gallery-history.sqlite3'),
                'proxy': self.store.setting('x_proxy'),
                'retries': 2, 'timeout': 30,
                'postprocessors': [{'name': 'metadata', 'event': 'post', 'filename': '{tweet_id}.json'}],
                'twitter': {'cookies': self.store.setting('x_cookies', {}), 'text-tweets': True,
                            'retweets': False, 'quoted': False, 'ratelimit': 'abort', 'retries-api': 2,
                            'videos': True, 'articles': False}},
            'output': {'mode': 'null', 'progress': False},
        }

    def __call__(self, task, interrupted, progress):
        if not self.store.setting('x_cookies'):
            raise DownloadError('尚未填写 X Cookie，请打开网站设置')
        settings = self.config(task)
        fd, filename = tempfile.mkstemp(prefix='.run-', suffix='.json', dir=self.store.config)
        process = reader = None
        lines, count, scanned = deque(maxlen=40), [0], [0]
        timed_out = False

        def index_saved():
            with self.store.connect() as db:
                posts, media = index_folder(self.store, task['user'], db=db)
                db.execute('''UPDATE tasks SET new_posts=new_posts+?,new_media=new_media+?,scanned_posts=?
                              WHERE id=? AND generation=?''', (posts, media, scanned[0], task['id'], task['generation']))

        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                os.chmod(filename, 0o600)
                json.dump(settings, stream)
            # Flush progress markers through the pipe while downloads are still running.
            command = [sys.executable, '-u', '-m', 'xarchive.gallery_runner', '--config-ignore', '-c', filename,
                       '--no-input', '--no-colors', '--windows-filenames',
                       '--Print', 'after:XARCHIVE_SAVED ', '--Print', 'post:XARCHIVE_POST ']
            if task['limit_count']:
                command += ['--post-range', '1-' + str(task['limit_count'])]
            command.append(task['url'])
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')

            def read_output():
                for line in iter(lambda: process.stdout.readline(8192), ''):
                    if line.strip() == 'XARCHIVE_SAVED':
                        count[0] += 1
                    elif line.strip() == 'XARCHIVE_POST':
                        scanned[0] += 1
                    else:
                        lines.append(line)

            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()
            minutes = self.store.setting('timeout_minutes', 0)
            deadline = time.monotonic() + minutes * 60 if minutes else None
            last_index = time.monotonic()
            reported = -1
            while process.poll() is None:
                if interrupted():
                    raise Interrupted()
                if deadline and time.monotonic() >= deadline:
                    timed_out = True
                    break
                if (count[0], scanned[0]) != reported:
                    progress(count[0])
                    self.store.execute('UPDATE tasks SET scanned_posts=? WHERE id=? AND generation=?', (scanned[0], task['id'], task['generation']))
                    reported = (count[0], scanned[0])
                if time.monotonic() - last_index >= 5:
                    index_saved()
                    last_index = time.monotonic()
                time.sleep(0.25)
            if timed_out:
                raise DownloadError('任务超过设置的运行时限，稍后重试；已保存内容会保留')
            reader.join(timeout=5)
            progress(count[0])
            if interrupted():
                raise Interrupted()
            output = ''.join(lines)
            if re.search(r'\b429\b|rate[ -]?limit', output, re.IGNORECASE):
                raise DownloadError('X 请求限流，15 分钟后自动重试', retry_after=900)
            if process.returncode:
                raise DownloadError(classify_error(output, process.returncode))
            if not scanned[0]:
                raise DownloadError('没有读取到帖子。博主可能没有内容、访问受限，或 Cookie 已失效')
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if reader:
                reader.join(timeout=5)
            if process and process.stdout:
                process.stdout.close()
            Path(filename).unlink(missing_ok=True)
            index_saved()
        return 0, 0
