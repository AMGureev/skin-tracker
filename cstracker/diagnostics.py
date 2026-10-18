"""Small rotating error log for the windowed legacy executable."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import sys
import traceback


def install_error_logging(root, directory):
    logger = logging.getLogger("cstracker.legacy")
    logger.setLevel(logging.ERROR)
    try:
        handler = RotatingFileHandler(Path(directory) / "skin-tracker-errors.log",
                                      maxBytes=512_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
    except OSError:
        return

    def report(exc_type, exception, tb):
        text = "".join(traceback.format_exception(exc_type, exception, tb))
        text = re.sub(r"(?i)(key|token|authorization)([=:]\s*)[^\s&\"']+", r"\1\2[redacted]", text)
        logger.error("%s", text)

    root.report_callback_exception = report
    sys.excepthook = report
