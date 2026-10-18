"""Bounded background jobs; only the Tk thread delivers their results."""

from dataclasses import dataclass
from collections import OrderedDict
import logging
from queue import Empty, Queue
import sys
import threading
import time


@dataclass
class _Job:
    key: str
    work: object
    success: object
    error: object
    finished: object


class BackgroundTasks:
    def __init__(self, root, workers=3, poll_ms=30, on_busy=None):
        self.root = root
        self._owner = threading.get_ident()
        self._pending = OrderedDict()
        self._results = Queue()
        self._active = {}
        self._debounced = {}
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._closed = False
        self._workers = workers
        self._poll_ms = poll_ms
        self._on_busy = on_busy
        self._timer = root.after(poll_ms, self._drain)
        for index in range(workers):
            threading.Thread(target=self._worker, name=f"tracker-worker-{index}", daemon=True).start()

    def _check_owner(self):
        if threading.get_ident() != self._owner:
            raise RuntimeError("BackgroundTasks must be scheduled from the UI thread")

    def submit(self, key, work, success=None, error=None, finished=None, *, replace=False):
        self._check_owner()
        with self._condition:
            if self._closed or (key in self._active and not replace):
                return False
            job = _Job(key, work, success, error, finished)
            self._active[key] = job
            self._pending[key] = job
            self._condition.notify()
        self._notify_busy()
        return True

    def cancel(self, key):
        self._check_owner()
        with self._lock:
            self._active.pop(key, None)
            self._pending.pop(key, None)
        self._notify_busy()

    def debounce(self, key, delay_ms, callback):
        self._check_owner()
        if self._closed:
            return
        timer = self._debounced.pop(key, None)
        if timer is not None:
            self.root.after_cancel(timer)

        def deliver():
            self._debounced.pop(key, None)
            if not self._closed:
                callback()

        self._debounced[key] = self.root.after(delay_ms, deliver)

    def _worker(self):
        while True:
            with self._condition:
                while not self._pending and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                _, job = self._pending.popitem(last=False)
            try:
                value, error = job.work(), None
            except Exception as exc:
                value, error = None, exc
            self._results.put((job, value, error))

    def _notify_busy(self):
        if self._on_busy and not self._closed:
            self._on_busy(len(self._active))

    def _drain(self):
        if self._closed:
            return
        deadline = time.monotonic() + 0.012
        while time.monotonic() < deadline:
            try:
                job, value, error = self._results.get_nowait()
            except Empty:
                break
            with self._lock:
                if self._active.get(job.key) is not job:
                    continue
                self._active.pop(job.key)
            try:
                if error is None:
                    if job.success:
                        job.success(value)
                elif job.error:
                    job.error(error)
                else:
                    logging.getLogger(__name__).warning("Background job %s failed (%s)", job.key, type(error).__name__)
            except Exception:
                self.root.report_callback_exception(*sys.exc_info())
            finally:
                if job.finished and not self._closed:
                    try:
                        job.finished()
                    except Exception:
                        self.root.report_callback_exception(*sys.exc_info())
                self._notify_busy()
        if not self._closed:
            self._timer = self.root.after(self._poll_ms, self._drain)

    def close(self):
        self._check_owner()
        with self._condition:
            self._closed = True
            self._active.clear()
            self._pending.clear()
            self._condition.notify_all()
        self.root.after_cancel(self._timer)
        for timer in self._debounced.values():
            self.root.after_cancel(timer)
        self._debounced.clear()
