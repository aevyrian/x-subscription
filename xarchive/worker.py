import logging
import threading
import time

log = logging.getLogger(__name__)


class Interrupted(Exception):
    pass


class BatchComplete(Exception):
    pass


class HistoryConfirmation(Exception):
    pass


class DownloadError(Exception):
    """Only safe, deliberately classified messages may leave the engine."""

    def __init__(self, message, *, retry_after=None, cooldown_scope='shared'):
        super().__init__(message)
        self.retry_after = retry_after
        self.cooldown_scope = cooldown_scope


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

    def healthy(self):
        return bool(self.thread and self.thread.is_alive() and time.monotonic() - self.last_tick < 300)

    def interrupted(self, identifier, generation=None):
        self.last_tick = time.monotonic()
        rows = self.store.query('SELECT status,generation FROM tasks WHERE id=?', (identifier,))
        return (self.stop_event.is_set() or not rows or rows[0]['status'] != 'running'
                or generation is not None and rows[0]['generation'] != generation)

    def run_one(self):
        task = self.store.claim()
        if not task:
            return False
        result = error = retry_after = None
        cooldown_scope, deferred = 'shared', None
        was_interrupted = False
        try:
            if self.runner is None:
                raise DownloadError('下载引擎尚未接入')
            result = self.runner(task, lambda: self.interrupted(task['id'], task['generation']),
                                 lambda n: self.store.execute("UPDATE tasks SET progress=MAX(progress,?) WHERE id=? AND generation=? AND status='running'", (n, task['id'], task['generation'])))
        except Interrupted:
            was_interrupted = True
        except BatchComplete:
            deferred = 'queued'
        except HistoryConfirmation:
            deferred = 'awaiting_confirmation'
        except DownloadError as exc:
            error, retry_after = str(exc), exc.retry_after
            cooldown_scope = exc.cooldown_scope
        except Exception:
            # Engine exceptions can contain credentials: expose only the exception category.
            log.error('Task failed unexpectedly; task=%s', task['id'])
            error = '内部处理失败，请检查存储目录权限和可用空间'
        while True:
            self.last_tick = time.monotonic()
            try:
                if deferred:
                    self.store.execute("""UPDATE tasks SET status=?,attempts=MAX(attempts-1,0),next_run=?
                                          WHERE id=? AND status='running' AND generation=?""",
                                       (deferred, time.time() + 1, task['id'], task['generation']))
                elif was_interrupted:
                    # A stopped service resumes this item; a user cancellation stays cancelled.
                    self.store.execute("""UPDATE tasks SET status='queued',attempts=MAX(attempts-1,0),next_run=?
                                          WHERE id=? AND status='running' AND generation=?""", (time.time(), task['id'], task['generation']))
                else:
                    self.store.finish(task, result=result, error=error, retry_after=retry_after, cooldown_scope=cooldown_scope)
                break
            except Exception:
                log.error('Task state could not be saved; task=%s; retrying', task['id'])
                if self.stop_event.wait(1):
                    break
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
