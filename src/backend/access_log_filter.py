"""Uvicorn access-log filters to reduce noisy successful polls."""

from __future__ import annotations

import logging


class SuppressSuccessfulAuthMeFilter(logging.Filter):
    """Hide successful GET /auth/me access lines; keep non-200 for debugging."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 5:
            method = args[1]
            path = str(args[2]).split("?", 1)[0]
            try:
                status = int(args[4])
            except (TypeError, ValueError):
                return True
            if method == "GET" and path == "/auth/me" and status == 200:
                return False
            return True

        try:
            msg = record.getMessage()
        except Exception:
            return True
        # Fallback when record.args shape differs from uvicorn's default.
        if '"GET /auth/me HTTP/' in msg and msg.rstrip().endswith("200"):
            return False
        return True


def install_access_log_filters() -> None:
    logger = logging.getLogger("uvicorn.access")
    if any(isinstance(f, SuppressSuccessfulAuthMeFilter) for f in logger.filters):
        return
    logger.addFilter(SuppressSuccessfulAuthMeFilter())
