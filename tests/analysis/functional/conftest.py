"""功能集成测 conftest：停全局 pymysql mock，重连真库，探活 LLM，准备测试用户。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, Iterator, Tuple

import pytest
from fastapi.testclient import TestClient

_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

# ingest 导入链可能触及 langchain_openai
if "langchain_openai" not in sys.modules:
    from unittest.mock import MagicMock

    sys.modules["langchain_openai"] = MagicMock()

from psych_functional_helpers import (  # noqa: E402
    auth_headers,
    cleanup_psych_user,
    make_psych_and_chunked_app,
    make_psych_app,
)


@pytest.fixture(scope="module", autouse=True)
def enable_real_mysql_and_llm() -> Iterator[None]:
    """停掉根 conftest 的 pymysql patch，重连 mysql_handler，探活 LLM。"""
    import pymysql
    from pymysql.connections import Connection
    from unittest import mock as _mock

    from _pymysql_sentinel import REAL_PYMYSQL_CONNECT

    # 停掉所有 connect patch，并强制写回真实 connect
    for patcher in list(getattr(_mock.patch, "_active_patches", []) or []):
        if getattr(patcher, "attribute", None) == "connect":
            try:
                patcher.stop()
            except RuntimeError:
                pass
    try:
        import conftest as root_cf

        if hasattr(root_cf, "enable_real_pymysql"):
            root_cf.enable_real_pymysql()
    except Exception:
        pass

    pymysql.connect = REAL_PYMYSQL_CONNECT

    from utils.mysql_utils import mysql_handler

    try:
        mysql_handler._connect()
    except Exception as exc:
        pytest.fail(
            f"无法连接 MySQL，请检查仓库根 .env 的 MYSQL_*（见 docs/MySQL.md）: {exc}"
        )

    if not isinstance(mysql_handler.connection, Connection):
        pytest.fail(
            f"pymysql 仍被 mock（conn={type(mysql_handler.connection)}），"
            f"connect={pymysql.connect!r}，无法跑功能集成测"
        )

    rows, err = mysql_handler.query("SELECT 1 AS ok")
    if err or not rows or not isinstance(rows, list) or rows[0].get("ok") != 1:
        pytest.fail(f"MySQL 探活失败: err={err} rows={rows!r}")

    import db.psych_store as psych_store
    from db.psych_schema import ensure_psych_tables

    psych_store._tables_ready = False
    try:
        ensure_psych_tables(mysql_handler)
    except Exception as exc:
        pytest.fail(f"ensure_psych_tables 失败: {exc}")
    psych_store._tables_ready = True

    try:
        from operator_pipeline.llm_client import is_available

        if not is_available():
            pytest.fail(
                "LLM 不可用：请检查 .env 中 OPENAI_API_KEY / OPENAI_API_BASE / DEFAULT_MODEL"
            )
    except Exception as exc:
        pytest.fail(f"LLM 探活失败: {exc}")

    yield

    try:
        import conftest as root_cf

        if hasattr(root_cf, "restore_mock_pymysql"):
            root_cf.restore_mock_pymysql()
    except Exception:
        pass


@pytest.fixture
def psych_users(enable_real_mysql_and_llm) -> Iterator[Tuple[Dict, Dict]]:
    """创建两个真实 users 行，供鉴权与用户隔离；结束后清理 psych 数据与用户。"""
    from db.rbac_store import RbacStore
    from utils.mysql_utils import mysql_handler
    from db.models import TABLE_USERS

    suffix = f"{os.getpid() % 100000:05d}"
    phones = (f"13991{suffix}", f"13992{suffix}")
    users = []
    for i, phone in enumerate(phones):
        # 清理同名残留
        mysql_handler.execute(f"DELETE FROM {TABLE_USERS} WHERE phone=%s", (phone,))
        uid, err = RbacStore.create_user(
            username=f"psych_fn_{suffix}_{i}",
            phone=phone,
            platform_role="user",
            status="active",
        )
        if err or not uid:
            pytest.fail(f"创建测试用户失败: {err}")
        users.append(
            {
                "user_id": int(uid),
                "username": f"psych_fn_{suffix}_{i}",
                "phone": phone,
                "headers": auth_headers(int(uid), f"psych_fn_{suffix}_{i}", phone),
            }
        )

    try:
        yield users[0], users[1]
    finally:
        for u in users:
            cleanup_psych_user(u["user_id"])
            mysql_handler.execute(
                f"DELETE FROM {TABLE_USERS} WHERE id=%s", (u["user_id"],)
            )


@pytest.fixture
def psych_client(enable_real_mysql_and_llm, tmp_path, monkeypatch) -> Iterator[TestClient]:
    """真路由 TestClient；隔离 TEMP/psych 存储到 tmp_path。"""
    psych_root = str(tmp_path / "psych_root")
    os.makedirs(psych_root, exist_ok=True)
    monkeypatch.setenv("TEMP_FOLDER", str(tmp_path) + os.sep)
    monkeypatch.setenv("PSYCH_ROOT", psych_root)
    monkeypatch.setattr("psych.paths.PSYCH_ROOT", psych_root)
    monkeypatch.setattr("configs.config.TEMP_FOLDER", str(tmp_path) + os.sep)

    with TestClient(make_psych_app()) as client:
        yield client


@pytest.fixture
def psych_chunk_client(
    enable_real_mysql_and_llm, tmp_path, monkeypatch
) -> Iterator[TestClient]:
    psych_root = str(tmp_path / "psych_root")
    os.makedirs(psych_root, exist_ok=True)
    monkeypatch.setenv("TEMP_FOLDER", str(tmp_path) + os.sep)
    monkeypatch.setenv("PSYCH_ROOT", psych_root)
    monkeypatch.setattr("psych.paths.PSYCH_ROOT", psych_root)
    monkeypatch.setattr("configs.config.TEMP_FOLDER", str(tmp_path) + os.sep)
    monkeypatch.setattr(
        "backend.chunked_upload_service.TEMP_FOLDER", str(tmp_path) + os.sep
    )

    with TestClient(make_psych_and_chunked_app()) as client:
        yield client
