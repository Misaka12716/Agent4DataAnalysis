"""SESSION_MEMORY prompt 去重与 persist 复用 digest 的单测。"""

from unittest.mock import patch

from utils.session_memory import (
    _strip_digest_section,
    build_session_memory_markdown,
    format_memory_for_prompt,
    read_session_memory_for_prompt,
)


def test_strip_digest_section_removes_section_4():
    raw = """# 会话记忆

## 1. 会话元数据

meta

## 4. 数据与 Schema 摘要

huge digest here

## 5. 规划状态

plan here
"""
    out = _strip_digest_section(raw)
    assert "数据与 Schema 摘要" not in out
    assert "huge digest here" not in out
    assert "规划状态" in out
    assert "会话元数据" in out


def test_format_memory_for_prompt_strips_section_4():
    excerpt = """## 1. 会话元数据
ok

## 4. 数据与 Schema 摘要
DIGEST_SHOULD_GO

## 5. 规划状态
plan
"""
    block = format_memory_for_prompt(excerpt, "zh")
    assert "DIGEST_SHOULD_GO" not in block
    assert "规划状态" in block
    assert "SESSION_MEMORY" in block


def test_build_session_memory_reuses_workspace_digest_without_reader():
    wc = {
        "file_list": ["data.csv"],
        "workspace_digest": {
            "summary": "1 table",
            "files": {
                "data.csv": {
                    "file_type": "table",
                    "relative_path": "data.csv",
                    "columns": ["a"],
                }
            },
        },
    }
    with patch("utils.session_memory._workspace_digest") as mock_digest:
        with patch("utils.session_memory.list_workspace_files", return_value=[]):
            md = build_session_memory_markdown(
                session_id="sid-test",
                workspace_abs="/tmp/ws",
                lang="zh",
                session_title="t",
                input_data="分析 data.csv",
                plan_data={"需求解析": "解析", "步骤分解": "步骤"},
                planner_summary="摘要",
                requirement_analysis="解析",
                steps_outline="步骤",
                workspace_context=wc,
                code_file_paths=["main.py"],
                coder_results=[],
                correction_attempts=0,
                last_coder_mode="",
                worker_results=None,
                memory_trace=[],
                reporter_done=False,
                report_excerpt="",
                streaming_status="running",
                open_issues="",
                last_event="planner",
                pipeline_note="note",
            )
    mock_digest.assert_not_called()
    assert "data.csv" in md or "1 table" in md or "table" in md


def test_read_session_memory_for_prompt_strips_when_raw_has_section_4():
    raw = """## 3. 工作区清单
list

## 4. 数据与 Schema 摘要
NESTED

## 5. 规划状态
plan
"""
    with patch("utils.session_memory.read_session_memory_raw", return_value=raw):
        out = read_session_memory_for_prompt("any")
    assert "NESTED" not in out
    assert "规划状态" in out
