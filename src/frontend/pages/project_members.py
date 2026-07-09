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
    api_put,
    can_manage_members,
    format_permission_labels,
    get_project_access_info,
    is_logged_in,
    refresh_user_profile,
)


def _project_label(p: dict) -> str:
    name = p.get("name") or f"项目 {p.get('id')}"
    suffix = " [共享]" if p.get("is_shared") else ""
    return f"{name} (ID {p.get('id')}){suffix}"


def render_project_members_page() -> None:
    st.title("项目成员与权限")
    st.caption("项目负责人或平台管理员可在此添加成员并分配操作权限。")

    if not is_logged_in():
        st.warning("请先登录。")
        return

    refresh_user_profile()
    list_resp = api_get("/project/list")
    if "error" in list_resp:
        st.error(list_resp["error"])
        return

    projects = ((list_resp.get("data") or {}).get("projects")) or []
    non_default = [p for p in projects if not (p or {}).get("is_default")]
    if not non_default:
        st.info("暂无可管理成员的协作项目（个人默认项目不支持成员管理）。请先创建项目。")
        return

    project_options = {_project_label(p): int(p.get("id") or 0) for p in non_default}
    selected_label = st.selectbox("选择项目", list(project_options.keys()))
    project_id = project_options[selected_label]
    perm_keys = list(ALL_PERMISSION_LABELS.keys())

    access_info = get_project_access_info(project_id)
    with st.container(border=True):
        st.markdown("**我的项目权限**")
        st.caption(f"访问类型：`{access_info.get('access', '—')}`")
        st.caption(f"权限：{format_permission_labels(access_info.get('permissions'))}")

    can_edit_members = can_manage_members(project_id)
    if not can_edit_members:
        st.info("您可查看成员列表；添加、编辑或移除成员需要「成员管理」权限。")

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
                st.caption("权限: " + format_permission_labels(perms))

                col_rm, col_edit = st.columns(2)
                if can_edit_members and col_rm.button("移除成员", key=f"rm_{project_id}_{mid}"):
                    resp = api_delete(f"/project/{project_id}/members/{mid}")
                    if "error" in resp:
                        st.error(resp["error"])
                    else:
                        st.success("已移除")
                        st.rerun()

                if can_edit_members:
                    with col_edit.expander("编辑成员", expanded=False):
                        with st.form(f"edit_member_{project_id}_{mid}"):
                            edit_role = st.selectbox(
                                "项目角色",
                                ["member", "project_manager"],
                                index=0 if str(m.get("role")) == "member" else 1,
                                key=f"edit_role_{project_id}_{mid}",
                            )
                            if edit_role == "project_manager":
                                st.caption("项目负责人拥有全部项目权限。")
                                edit_perms: list[str] = []
                            else:
                                edit_perms = st.multiselect(
                                    "操作权限",
                                    perm_keys,
                                    default=[p for p in perms if p in perm_keys] or ["data_download"],
                                    format_func=lambda k: ALL_PERMISSION_LABELS.get(k, k),
                                    key=f"edit_perms_{project_id}_{mid}",
                                )
                            if st.form_submit_button("保存修改", type="primary"):
                                payload = {
                                    "role": edit_role,
                                    "permissions": edit_perms if edit_role == "member" else [],
                                }
                                resp = api_put(f"/project/{project_id}/members/{mid}", payload)
                                if "error" in resp:
                                    st.error(resp["error"])
                                else:
                                    st.success("成员已更新")
                                    refresh_user_profile()
                                    st.rerun()

    st.divider()
    st.subheader("添加成员")
    if not can_edit_members:
        st.caption("无成员管理权限，无法添加或修改成员。")
    else:
        lookup_key = f"member_lookup_{project_id}"
        if lookup_key not in st.session_state:
            st.session_state[lookup_key] = None

        phone_input = st.text_input(
            "协作者手机号",
            placeholder="11 位手机号，例如 13800138000",
            key=f"invite_phone_{project_id}",
        )
        if st.button("查找用户", key=f"lookup_btn_{project_id}"):
            phone = phone_input.strip()
            if not phone:
                st.error("请输入手机号")
            else:
                resp = api_get(f"/users/lookup?phone={phone}")
                if "error" in resp:
                    st.error(resp["error"])
                    st.session_state[lookup_key] = None
                else:
                    st.session_state[lookup_key] = resp.get("data") or {}

        looked_up = st.session_state.get(lookup_key)
        if looked_up:
            st.success(
                f"已找到：{looked_up.get('username') or '—'} "
                f"(ID {looked_up.get('user_id')}) · {looked_up.get('phone') or '—'}"
            )

        with st.form(f"add_member_form_{project_id}"):
            member_role = st.selectbox(
                "项目角色",
                ["member", "project_manager"],
                key=f"add_member_role_{project_id}",
            )
            if member_role == "project_manager":
                st.caption("项目负责人拥有全部项目权限。")
                selected_perms: list[str] = []
            else:
                selected_perms = st.multiselect(
                    "操作权限",
                    perm_keys,
                    default=["data_download"],
                    format_func=lambda k: ALL_PERMISSION_LABELS.get(k, k),
                    key=f"add_member_perms_{project_id}",
                )
            submitted = st.form_submit_button("添加成员", type="primary")
            if submitted:
                phone = phone_input.strip()
                if not phone:
                    st.error("请先输入协作者手机号并完成查找")
                elif not looked_up or str(looked_up.get("phone") or "") != phone:
                    st.error("请先点击「查找用户」确认协作者身份")
                else:
                    resp = api_post(
                        f"/project/{project_id}/members",
                        {
                            "phone": phone,
                            "role": member_role,
                            "permissions": selected_perms if member_role == "member" else [],
                        },
                    )
                    if "error" in resp:
                        st.error(resp["error"])
                    else:
                        st.session_state[lookup_key] = None
                        st.success("成员已添加")
                        refresh_user_profile()
                        st.rerun()


if __name__ == "__main__":
    from frontend.page_utils import render_auth_sidebar

    st.set_page_config(page_title="项目成员", page_icon="👥", layout="wide")
    render_auth_sidebar()
    refresh_user_profile()
    render_project_members_page()
