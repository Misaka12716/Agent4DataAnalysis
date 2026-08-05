import logging

from backend.access_log_filter import (
    SuppressSuccessfulAuthMeFilter,
    install_access_log_filters,
)


def _record(method: str, path: str, status: int) -> logging.LogRecord:
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:1", method, path, "1.1", status),
        exc_info=None,
    )
    return record


def test_suppresses_successful_auth_me():
    filt = SuppressSuccessfulAuthMeFilter()
    assert filt.filter(_record("GET", "/auth/me", 200)) is False
    assert filt.filter(_record("GET", "/auth/me?_=1", 200)) is False


def test_keeps_non_200_auth_me():
    filt = SuppressSuccessfulAuthMeFilter()
    assert filt.filter(_record("GET", "/auth/me", 401)) is True
    assert filt.filter(_record("GET", "/auth/me", 500)) is True


def test_keeps_other_endpoints():
    filt = SuppressSuccessfulAuthMeFilter()
    assert filt.filter(_record("GET", "/health", 200)) is True
    assert filt.filter(_record("GET", "/session/list", 200)) is True


def test_install_is_idempotent():
    logger = logging.getLogger("uvicorn.access")
    before = len(logger.filters)
    install_access_log_filters()
    install_access_log_filters()
    auth_filters = [
        f for f in logger.filters if isinstance(f, SuppressSuccessfulAuthMeFilter)
    ]
    assert len(auth_filters) == 1
    # Clean up so other tests / processes are unaffected.
    for f in auth_filters:
        logger.removeFilter(f)
    assert len(logger.filters) == before
