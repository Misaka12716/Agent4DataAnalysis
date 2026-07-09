# frontend/pages/project_workspace.py
# 多项目生命周期管理：详情、raw 上传、资产、目录树、归档/恢复

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import httpx
import streamlit as st

from frontend.page_utils import (
    API_BASE,
    api_base_url,
    api_delete,
    api_get,
    api_post,
    auth_headers,
    auth_headers_upload,
    can_delete,
    can_download,
    can_manage_project,
    can_upload,
    format_permission_labels,
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


def _download_asset(project_id: int, relative_path: str) -> tuple[bytes | None, str | None]:
    rel = (relative_path or "").strip().lstrip("/")
    if not rel:
        return None, "路径无效"
    try:
        resp = httpx.get(
            f"{api_base_url()}/project/{project_id}/download",
            params={"relative_path": rel},
            headers=auth_headers(),
            timeout=120.0,
        )
        if resp.status_code != 200:
            detail = resp.json().get("detail", resp.text[:200]) if resp.headers.get("content-type", "").startswith("application/json") else resp.text[:200]
            return None, str(detail)
        filename = rel.split("/")[-1] or "download.bin"
        return resp.content, filename
    except Exception as e:
        return None, str(e)


def _render_asset_rows(
    assets: list,
    project_id: int,
    allow_download: bool,
    allow_delete: bool,
    is_archived: bool,
    section_key: str,
) -> None:
    for idx, asset in enumerate(assets):
        rel = str(asset.get("relative_path") or "")
        name = str(asset.get("original_filename") or rel.split("/")[-1] or rel)
        cols = st.columns([4, 1, 1])
        cols[0].markdown(f"`{rel}` · {name}")
        prep_key = f"asset_dl_{section_key}_{project_id}_{idx}"
        if allow_download:
            if cols[1].button("下载", key=f"btn_{prep_key}"):
                content, filename_or_err = _download_asset(project_id, rel)
                if content is None:
                    st.session_state[prep_key] = {"error": filename_or_err or "下载失败"}
                else:
                    st.session_state[prep_key] = {
                        "content": content,
                        "filename": filename_or_err or "download.bin",
                    }
            cached = st.session_state.get(prep_key) or {}
            if cached.get("content"):
                cols[1].download_button(
                    "保存文件",
                    data=cached["content"],
                    file_name=cached.get("filename") or "download.bin",
                    key=f"save_{prep_key}",
                )
            elif cached.get("error"):
                cols[1].caption(str(cached["error"])[:40])
        if allow_delete and not is_archived:
            if cols[2].button("删除", key=f"del_{section_key}_{project_id}_{idx}"):
                resp = api_delete(
                    f"/project/{project_id}/assets",
                    json_body={"relative_path": rel},
                )
                if "error" in resp:
                    st.error(resp["error"])
                else:
                    st.success("已删除")
                    st.rerun()


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
    allow_download = can_download(project_id)
    allow_delete_asset = can_delete(project_id)

    detail = api_get(f"/project/{project_id}")
    if "error" in detail:
        st.error(detail["error"])
        return
    info = detail.get("data") or {}
    status = str(info.get("status") or "active")
    is_archived = status == "archived"

    st.subheader("项目工作区")
    shared_tag = " · [共享项目]" if info.get("is_shared") else ""
    st.caption(f"当前项目：**{project_name}** · ID `{project_id}`{shared_tag}")

    with st.container(border=True):
        st.markdown("**我的项目权限**")
        st.caption(f"访问类型：`{info.get('access', '—')}`")
        st.caption(f"权限：{format_permission_labels(info.get('permissions'))}")

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
    st.caption(
        "raw/ 用于预置项目级原始数据，**不会自动进入分析链路**。"
        "分析前请创建会话，将文件复制到会话工作区或直接上传到会话。"
    )
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
                body = resp.json()
                notice = body.get("notice") or ""
                st.success(f"已写入 `{body.get('relative_path', '')}`")
                if notice:
                    st.info(notice)
                st.rerun()
            else:
                st.error(resp.json().get("detail", resp.text[:200]))

    sid = str(st.session_state.get("session_id") or "").strip()
    if sid and not is_archived and allow_upload:
        st.markdown("#### 复制 raw/ 到当前会话")
        st.caption(f"当前会话 `{sid}`：将项目 raw/ 下全部文件复制到会话工作区以供分析。")
        if st.button("复制 raw/ → 会话工作区", key="btn_copy_raw_to_session"):
            r = api_post(
                "/session/copy-from-project-raw",
                {"session_id": sid},
                timeout=120.0,
            )
            if "error" in r:
                st.error(r["error"])
            else:
                copied = ((r.get("data") or {}).get("copied")) or []
                st.success(f"已复制 {len(copied)} 个文件到会话工作区")
                st.rerun()
    elif not sid:
        st.caption("在侧栏选择或创建会话后，可将 raw/ 文件复制到会话工作区。")

    st.markdown("#### 项目资产")
    assets_resp = api_get(f"/project/{project_id}/assets")
    if "error" not in assets_resp:
        assets = ((assets_resp.get("data") or {}).get("assets")) or []
        if assets:
            uploads = [a for a in assets if a.get("asset_type") == "upload"]
            outputs = [a for a in assets if a.get("asset_type") == "analysis_output"]
            if uploads:
                st.markdown("**上传 (upload)**")
                _render_asset_rows(
                    uploads,
                    project_id,
                    allow_download,
                    allow_delete_asset,
                    is_archived,
                    "upload",
                )
            if outputs:
                st.markdown("**分析产出 (analysis_output)**")
                _render_asset_rows(
                    outputs,
                    project_id,
                    allow_download,
                    allow_delete_asset,
                    is_archived,
                    "output",
                )
            if not uploads and not outputs:
                st.info("暂无资产记录。")
        else:
            st.info("暂无资产记录。")
    if not allow_download and not allow_delete_asset:
        st.caption("当前账号无资产下载或删除权限。")

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
