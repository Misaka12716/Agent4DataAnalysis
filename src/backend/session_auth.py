"""会话归属校验（兼容层，实际逻辑在 project_auth）。"""

from backend.project_auth import assert_session_access

# 向后兼容：旧代码仍可从 session_auth 导入
assert_session_owner = assert_session_access

__all__ = ["assert_session_owner", "assert_session_access"]
