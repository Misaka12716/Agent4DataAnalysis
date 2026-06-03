from typing import Any


def scalar_or_long_text_block(val: Any) -> str:
    if val is None:
        return "（无）\n\n"
    if isinstance(val, bool):
        return ("是" if val else "否") + "\n\n"
    if isinstance(val, (int, float)):
        return f"{val}\n\n"
    if isinstance(val, str):
        s = val.strip()
        if len(s) > 240 or "\n" in val:
            return f"```\n{val.rstrip()}\n```\n\n"
        return f"{val}\n\n"
    return f"{val}\n\n"


def workspace_digest_to_markdown(obj: Any, depth: int = 0) -> str:
    """
    将 workspace_digest（与 JSON 同构的嵌套 dict/list）转为多级 Markdown，
    供 Planner / Session Memory 阅读。
    """
    level = min(2 + depth, 6)
    bars = "#" * level

    if isinstance(obj, dict):
        chunks: list[str] = []
        for key, val in obj.items():
            chunks.append(f"{bars} {key}\n\n")
            if isinstance(val, (dict, list)):
                chunks.append(workspace_digest_to_markdown(val, depth + 1))
            else:
                chunks.append(scalar_or_long_text_block(val))
        return "".join(chunks)
    if isinstance(obj, list):
        if not obj:
            return "（空）\n\n"
        if all(not isinstance(x, (dict, list)) for x in obj):
            return "".join(f"- {x}\n" for x in obj) + "\n"
        chunks: list[str] = []
        for i, item in enumerate(obj, 1):
            chunks.append(f"{bars} 第 {i} 项\n\n")
            chunks.append(workspace_digest_to_markdown(item, depth + 1))
        return "".join(chunks)
    return scalar_or_long_text_block(obj)
