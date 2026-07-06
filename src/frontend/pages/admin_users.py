# frontend/pages/admin_users.py — 平台管理员：用户管理

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import streamlit as st

from frontend.page_utils import (
    ALL_PERMISSION_LABELS,
    api_get,
    api_post,
    api_put,
    is_logged_in,
    is_platform_admin,
)


def render_admin_users_page() -> None:
    st.title("用户管理")
    st.caption("平台管理员可新增用户、分配角色、启用/禁用账户。")

    if not is_logged_in():
        st.warning("请先登录。")
        return

    if not is_platform_admin():
        st.error("需要平台管理员权限。")
        return

    with st.expander("新增用户", expanded=False):
        with st.form("create_user_form"):
            new_username = st.text_input("用户名", placeholder="例如 zhangsan")
            new_phone = st.text_input("手机号", placeholder="11 位手机号")
            new_role = st.selectbox("平台角色", ["user", "admin"], index=0)
            new_status = st.selectbox("状态", ["active", "blocked"], index=0)
            if st.form_submit_button("创建用户", type="primary"):
                resp = api_post(
                    "/admin/users",
                    {
                        "username": new_username.strip(),
                        "phone": new_phone.strip(),
                        "platform_role": new_role,
                        "status": new_status,
                    },
                )
                if "error" in resp:
                    st.error(resp["error"])
                else:
                    st.success("用户已创建")
                    st.rerun()

    st.subheader("用户列表")
    list_resp = api_get("/admin/users", params={"limit": 100})
    if "error" in list_resp:
        st.error(list_resp["error"])
        return

    users = ((list_resp.get("data") or {}).get("users")) or []
    total = int((list_resp.get("data") or {}).get("total") or len(users))
    st.caption(f"共 {total} 个用户")

    for u in users:
        uid = int(u.get("id") or 0)
        with st.container(border=True):
            c1, c2, c3 = st.columns([2, 2, 1])
            with c1:
                st.markdown(f"**{u.get('username') or '—'}** · ID `{uid}`")
                st.caption(f"手机: {u.get('phone') or '—'}")
            with c2:
                role = str(u.get("platform_role") or "user")
                status = str(u.get("status") or "active")
                st.markdown(f"角色: `{role}` · 状态: `{status}`")
            with c3:
                new_role = st.selectbox(
                    "改角色",
                    ["user", "admin"],
                    index=0 if role != "admin" else 1,
                    key=f"role_{uid}",
                    label_visibility="collapsed",
                )
                new_status = st.selectbox(
                    "改状态",
                    ["active", "blocked"],
                    index=0 if status != "blocked" else 1,
                    key=f"status_{uid}",
                    label_visibility="collapsed",
                )
                if st.button("保存", key=f"save_{uid}"):
                    resp = api_put(
                        f"/admin/users/{uid}",
                        {"platform_role": new_role, "status": new_status},
                    )
                    if "error" in resp:
                        st.error(resp["error"])
                    else:
                        st.success("已更新")
                        st.rerun()

    st.caption("权限码说明：" + " · ".join(f"{k}={v}" for k, v in ALL_PERMISSION_LABELS.items()))


if __name__ == "__main__":
    from frontend.page_utils import refresh_user_profile, render_auth_sidebar

    st.set_page_config(page_title="用户管理", page_icon="👤", layout="wide")
    render_auth_sidebar()
    refresh_user_profile()
    render_admin_users_page()
