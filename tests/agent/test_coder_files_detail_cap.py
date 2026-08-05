"""Coder workspace files_detail JSON 截断单测。"""

from coder.workspace_coder import _MAX_FILES_DETAIL_CHARS, _format_workspace_files_info


def test_format_workspace_files_info_truncates_large_detail():
    huge = {"file.csv": {"preview": "x" * (_MAX_FILES_DETAIL_CHARS + 5000)}}
    ctx = {
        "file_list": ["file.csv"],
        "workspace_digest": {"summary": "s", "files": huge},
    }
    text = _format_workspace_files_info(ctx, "zh")
    assert "已截断" in text
    # 整体说明文本不应远超上限太多（含前缀标题）
    assert len(text) < _MAX_FILES_DETAIL_CHARS + 2000
