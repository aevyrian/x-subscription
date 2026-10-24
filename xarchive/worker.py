import logging
import threading
import time

log = logging.getLogger(__name__)


class Interrupted(Exception):
    pass


class DownloadError(Exception):
    """Only safe, deliberately classified messages may leave the engine."""


class Worker:
    def __init__(self, store, runner=None):
        self.store, self.runner = store, runner
        self.stop_event = threading.Event()
        self.thread = None
        self.last_tick = time.monotonic()

    def start(self):
        self.store.recover()
        self.thread = threading.Thread(target=self.loop, name='download-queue', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=20)

    def interrupted(self, identifier):
        rows = self.store.query('SELECT status FROM tasks WHERE id=?', (identifier,))
        return self.stop_event.is_set() or not rows or rows[0]['status'] != 'running'

    def run_one(self):
        task = self.store.claim()
        if not task:
            return False
        try:
            if self.runner is None:
                raise DownloadError('下载引擎尚未接入')
            result = self.runner(task, lambda: self.interrupted(task['id']),
                                 lambda n: self.store.execute('UPDATE tasks SET progress=? WHERE id=?', (n, task['id'])))
            self.store.finish(task, result=result)
        except Interrupted:
            # A stopped service resumes this item; a user cancellation stays cancelled.
            self.store.execute("""UPDATE tasks SET status='queued',attempts=MAX(attempts-1,0),next_run=?
                                  WHERE id=? AND status='running'""", (time.time(), task['id']))
        except DownloadError as exc:
            self.store.finish(task, error=str(exc))
        except Exception:
            # Engine exceptions can contain credentials: expose only the exception category.
            log.error('Task failed unexpectedly; task=%s', task['id'])
            self.store.finish(task, error='内部处理失败，请检查存储目录权限和可用空间')
        return True

    def loop(self):
        while not self.stop_event.is_set():
            try:
                self.last_tick = time.monotonic()
                self.store.schedule()
                if self.run_one():
                    continue
            except Exception:
                log.error('Queue temporarily unavailable; retrying in one second')
            self.stop_event.wait(1)
