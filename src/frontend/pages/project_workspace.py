# frontend/pages/project_workspace.py
# 多项目生命周期管理：详情、raw 上传、资产、目录树、归档/恢复

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import httpx
import pandas as pd
import streamlit as st

from frontend.page_utils import (
    API_BASE,
    api_get,
    api_post,
    auth_headers,
    auth_headers_upload,
    can_analyze,
    can_manage_project,
    can_upload,
    is_logged_in,
    refresh_user_profile,
    render_auth_sidebar,
)

try:
    from reader.file_types import guess_upload_mime
except ImportError:

    def guess_upload_mime(name, _=None):
        return "application/octet-stream"


DISEASE_LABELS = {
    "depression": "抑郁",
    "schizophrenia": "精神分裂",
    "anxiety": "焦虑",
    "sleep": "睡眠",
    "child_adolescent": "儿童青少年",
}


def _resolve_current_project(projects: list) -> int:
    pid = int(st.session_state.get("current_project_id", 0) or 0)
    if pid > 0:
        return pid
    for item in projects or []:
        if (item or {}).get("is_default"):
            return int((item or {}).get("id") or 0)
    if projects:
        return int((projects[0] or {}).get("id") or 0)
    return 0


def _project_name(projects: list, project_id: int) -> str:
    for item in projects or []:
        if int((item or {}).get("id") or 0) == project_id:
            return str((item or {}).get("name") or f"项目 {project_id}")
    return f"项目 {project_id}"


def _render_tree_node(node: dict, indent: int = 0) -> None:
    prefix = "　" * indent
    name = node.get("name") or node.get("relative_path") or "."
    if node.get("type") == "directory":
        st.markdown(f"{prefix}📁 **{name or '/'}**")
        for child in node.get("children") or []:
            _render_tree_node(child, indent + 1)
    else:
        size = node.get("size", 0)
        st.markdown(f"{prefix}📄 `{node.get('relative_path', name)}` ({size} B)")


def render_project_lifecycle(api_base: str = API_BASE) -> None:
    if not is_logged_in():
        st.warning("请先登录后再管理项目。")
        return

    list_resp = api_get("/project/list")
    if "error" in list_resp:
        st.error(list_resp["error"])
        return
    projects = ((list_resp.get("data") or {}).get("projects")) or []
    project_id = _resolve_current_project(projects)
    if project_id <= 0:
        st.info("暂无项目，请先在主控制台创建项目。")
        return

    st.session_state["current_project_id"] = project_id
    project_name = _project_name(projects, project_id)

    refresh_user_profile()
    allow_upload = can_upload(project_id)
    allow_manage = can_manage_project(project_id)

    st.subheader("项目工作区")
    st.caption(f"当前项目：**{project_name}** · ID `{project_id}`")

    detail = api_get(f"/project/{project_id}")
    if "error" in detail:
        st.error(detail["error"])
        return
    info = detail.get("data") or {}
    status = str(info.get("status") or "active")
    is_archived = status == "archived"

    c1, c2, c3 = st.columns(3)
    c1.metric("状态", "已归档" if is_archived else "活跃")
    c2.metric("会话数", info.get("session_count", 0))
    subdirs = info.get("subdirs") or {}
    c3.metric("子目录", sum(1 for v in subdirs.values() if v))

    with st.expander("子目录状态", expanded=False):
        st.json(subdirs)

    st.markdown("#### 重命名")
    if not allow_manage:
        st.warning("权限不足：需要项目负责人或平台管理员权限。")
    with st.form("rename_project_form"):
        new_name = st.text_input("新项目名称", value=project_name if not info.get("is_default") else project_name)
        if st.form_submit_button(
            "保存名称",
            disabled=bool(info.get("is_default")) or not allow_manage,
        ):
            r = httpx.put(
                f"{api_base.rstrip('/')}/project/{project_id}",
                json={"name": new_name.strip()},
                headers=auth_headers(),
                timeout=15.0,
            )
            if r.status_code == 200:
                st.success("项目名称已更新")
                st.rerun()
            else:
                st.error(r.json().get("detail", r.text[:200]))

    st.markdown("#### 数据接入（raw/）")
    if is_archived:
        st.warning("项目已归档，无法上传。")
    elif not allow_upload:
        st.warning("权限不足：需要「数据上传」权限。")
    else:
        uploaded = st.file_uploader(
            "上传原始数据到项目 raw/",
            type=["xlsx", "xls", "csv", "tsv", "txt", "md", "json"],
            key="project_raw_upload",
        )
        if uploaded and st.button("写入 raw/", key="btn_project_raw_upload"):
            mime = guess_upload_mime(uploaded.name)
            resp = httpx.post(
                f"{api_base.rstrip('/')}/project/{project_id}/upload",
                files={"file": (uploaded.name, uploaded.getvalue(), mime)},
                headers=auth_headers_upload(),
                timeout=120.0,
            )
            if resp.status_code == 200:
                st.success(f"已写入 `{resp.json().get('relative_path', '')}`")
                st.rerun()
            else:
                st.error(resp.json().get("detail", resp.text[:200]))

    st.markdown("#### 项目资产")
    assets_resp = api_get(f"/project/{project_id}/assets")
    if "error" not in assets_resp:
        assets = ((assets_resp.get("data") or {}).get("assets")) or []
        if assets:
            uploads = [a for a in assets if a.get("asset_type") == "upload"]
            outputs = [a for a in assets if a.get("asset_type") == "analysis_output"]
            if uploads:
                st.markdown("**上传 (upload)**")
                st.dataframe(
                    pd.DataFrame(uploads)[
                        ["relative_path", "original_filename", "file_category", "created_at"]
                    ],
                    use_container_width=True,
                    hide_index=True,
                )
            if outputs:
                st.markdown("**分析产出 (analysis_output)**")
                st.dataframe(
                    pd.DataFrame(outputs)[
                        ["relative_path", "original_filename", "session_id", "created_at"]
                    ],
                    use_container_width=True,
                    hide_index=True,
                )
            if not uploads and not outputs:
                st.info("暂无资产记录。")
        else:
            st.info("暂无资产记录。")

    st.markdown("#### 目录树")
    tree_resp = api_get(f"/project/{project_id}/tree")
    if "error" not in tree_resp:
        trees = ((tree_resp.get("data") or {}).get("trees")) or {}
        for section in ("raw", "outputs", "archive"):
            with st.expander(f"{section}/", expanded=(section == "outputs")):
                node = trees.get(section) or {}
                if node.get("children"):
                    for child in node.get("children") or []:
                        _render_tree_node(child, 0)
                else:
                    st.caption("（空）")

    st.markdown("#### 归档管理")
    if info.get("is_default"):
        st.caption("「个人默认」项目不可归档。")
    elif not allow_manage:
        st.warning("权限不足：需要项目负责人或平台管理员权限。")
    elif is_archived:
        st.info("该项目处于归档状态，写操作已禁用。")
        if info.get("archive_snapshot_path"):
            st.caption(f"最近归档快照：`{info.get('archive_snapshot_path')}`")
        if st.button("恢复项目", key="btn_restore_project"):
            r = httpx.post(
                f"{api_base.rstrip('/')}/project/{project_id}/restore",
                headers=auth_headers(),
                timeout=15.0,
            )
            if r.status_code == 200:
                st.success("项目已恢复")
                st.rerun()
            else:
                st.error(r.json().get("detail", r.text[:200]))
    else:
        confirm = st.checkbox("确认归档：将 raw/ 与 outputs/ 快照至 archive/ 并禁止写操作", key="confirm_archive")
        if st.button("归档项目", key="btn_archive_project", disabled=not confirm or not allow_manage):
            r = httpx.post(
                f"{api_base.rstrip('/')}/project/{project_id}/archive",
                headers=auth_headers(),
                timeout=60.0,
            )
            if r.status_code == 200:
                snap = ((r.json().get("data") or {}).get("archive_snapshot_path")) or ""
                st.success(f"项目已归档{f'，快照：{snap}' if snap else ''}")
                st.rerun()
            else:
                st.error(r.json().get("detail", r.text[:200]))


if __name__ == "__main__":
    st.set_page_config(page_title="项目工作区", page_icon="📁", layout="wide")
    render_auth_sidebar()
    st.title("多项目生命周期管理")
    st.caption("覆盖数据接入、成果沉淀与归档快照；专病模板筛选见「模板管理与分析执行」页。")
    render_project_lifecycle()
