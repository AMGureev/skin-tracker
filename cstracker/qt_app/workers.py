"""Reusable QThreadPool jobs with typed success and failure signals."""

from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class Worker(QRunnable):
    def __init__(self, function, *args, **kwargs):
        super().__init__()
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        # PySide must keep the Python wrapper alive until queued signal delivery
        # is complete. QThreadPool auto-deletion can otherwise race with it.
        self.setAutoDelete(False)

    @Slot()
    def run(self):
        try:
            value = self.function(*self.args, **self.kwargs)
        except Exception:
            self.signals.error.emit(traceback.format_exc())
        else:
            self.signals.result.emit(value)
        finally:
            self.signals.finished.emit()
