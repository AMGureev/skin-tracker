"""Each worker owns its requests session; no networking on the UI thread."""

import threading
import requests


class WorkerHttpClient:
    def __init__(self):
        self._ui_thread = threading.get_ident()
        self._local = threading.local()

    def get(self, url, **kwargs):
        if threading.get_ident() == self._ui_thread:
            raise RuntimeError("Network request attempted on the UI thread")
        if not hasattr(self._local, "session"):
            self._local.session = requests.Session()
        kwargs.setdefault("timeout", (5, 15))
        return self._local.session.get(url, **kwargs)
