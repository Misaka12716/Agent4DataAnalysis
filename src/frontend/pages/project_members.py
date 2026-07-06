# frontend/pages/project_members.py — 项目成员与权限管理

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import streamlit as st

from frontend.page_utils import (
    ALL_PERMISSION_LABELS,
    api_delete,
    api_get,
    api_post,
    can_manage_members,
    is_logged_in,
)


def render_project_members_page() -> None:
    st.title("项目成员与权限")
    st.caption("项目负责人或平台管理员可在此添加成员并分配操作权限。")

    if not is_logged_in():
        st.warning("请先登录。")
        return

    list_resp = api_get("/project/list")
    if "error" in list_resp:
        st.error(list_resp["error"])
        return

    projects = ((list_resp.get("data") or {}).get("projects")) or []
    non_default = [p for p in projects if not (p or {}).get("is_default")]
    if not non_default:
        st.info("暂无可管理成员的协作项目（个人默认项目不支持成员管理）。请先创建项目。")
        return

    project_options = {
        f"{p.get('name')} (ID {p.get('id')})": int(p.get("id") or 0) for p in non_default
    }
    selected_label = st.selectbox("选择项目", list(project_options.keys()))
    project_id = project_options[selected_label]

    can_edit_members = can_manage_members(project_id)
    if not can_edit_members:
        st.warning("权限不足：需要「成员管理」权限。以下内容为只读展示。")

    members_resp = api_get(f"/project/{project_id}/members")
    if "error" in members_resp:
        st.warning(f"无法加载成员列表: {members_resp['error']}")
    else:
        members = ((members_resp.get("data") or {}).get("members")) or []
        st.subheader(f"当前成员 ({len(members)})")
        for m in members:
            mid = int(m.get("user_id") or 0)
            with st.container(border=True):
                st.markdown(
                    f"**{m.get('username') or '—'}** (用户 ID `{mid}`) · "
                    f"角色 `{m.get('role')}` · 手机 {m.get('phone') or '—'}"
                )
                perms = m.get("permissions") or []
                labels = [ALL_PERMISSION_LABELS.get(p, p) for p in perms]
                st.caption("权限: " + (", ".join(labels) if labels else "（无）"))
                if can_edit_members and st.button("移除成员", key=f"rm_{project_id}_{mid}"):
                    resp = api_delete(f"/project/{project_id}/members/{mid}")
                    if "error" in resp:
                        st.error(resp["error"])
                    else:
                        st.success("已移除")
                        st.rerun()

    st.divider()
    st.subheader("添加成员")
    if not can_edit_members:
        st.caption("无成员管理权限，无法添加或修改成员。")
    else:
        with st.form("add_member_form"):
            target_user_id = st.number_input("用户 ID", min_value=1, step=1, value=1)
            member_role = st.selectbox("项目角色", ["member", "project_manager"])
            perm_keys = list(ALL_PERMISSION_LABELS.keys())
            if member_role == "project_manager":
                st.caption("项目负责人拥有全部项目权限。")
                selected_perms = perm_keys
            else:
                selected_perms = st.multiselect(
                    "操作权限",
                    perm_keys,
                    default=["data_download"],
                    format_func=lambda k: ALL_PERMISSION_LABELS.get(k, k),
                )
            if st.form_submit_button("添加成员", type="primary"):
                resp = api_post(
                    f"/project/{project_id}/members",
                    {
                        "user_id": int(target_user_id),
                        "role": member_role,
                        "permissions": selected_perms if member_role == "member" else [],
                    },
                )
                if "error" in resp:
                    st.error(resp["error"])
                else:
                    st.success("成员已添加")
                    st.rerun()


if __name__ == "__main__":
    import streamlit as st

    from frontend.page_utils import refresh_user_profile, render_auth_sidebar

    st.set_page_config(page_title="项目成员", page_icon="👥", layout="wide")
    render_auth_sidebar()
    refresh_user_profile()
    render_project_members_page()
