# frontend/pages/medicalkg_hotspots.py
# MedicalKG 精神医学热点库入口：复用 AgentPlatform 登录态，不创建新的用户体系。

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import streamlit as st
import streamlit.components.v1 as components

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from frontend.page_utils import is_logged_in, render_auth_sidebar


def _default_medicalkg_base() -> str:
    configured = os.environ.get("MEDICALKG_PUBLIC_BASE", "").strip()
    if configured:
        return configured.rstrip("/")
    try:
        headers = st.context.headers
        host = str(headers.get("host") or "").split(":")[0].strip()
        proto = str(headers.get("x-forwarded-proto") or "http").split(",")[0].strip() or "http"
    except Exception:
        host = ""
        proto = "http"
    if host:
        return f"{proto}://{host}:9335"
    return "http://127.0.0.1:9335"


def _medicalkg_url(base: str) -> str:
    return f"{base.rstrip('/')}/web/psychiatry_trends/"


def _origin(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return "*"
    return f"{parsed.scheme}://{parsed.netloc}"


st.set_page_config(
    page_title="MedicalKG 研究方向热点库",
    layout="wide",
    initial_sidebar_state="expanded",
)

render_auth_sidebar()

st.title("MedicalKG 研究方向热点库")
st.caption("复用 AgentPlatform 当前登录态访问 MedicalKG 9335；用户身份由 Bearer token 在后端校验。")

base = st.text_input(
    "MedicalKG 9335 地址",
    value=_default_medicalkg_base(),
    help="生产部署建议配置 MEDICALKG_PUBLIC_BASE，例如 http://<server-ip>:9335 或平台网关地址。",
)
url = _medicalkg_url(base)
token = str(st.session_state.get("access_token") or st.session_state.get("auth_token") or "").strip()

if not is_logged_in() or not token:
    st.warning("请先在左侧登录。登录后本页会把 AgentPlatform access_token 传给 MedicalKG，用于同步研究方向和热点订阅。")
    st.stop()

st.caption(f"当前嵌入地址：{url}")

component_html = f"""
<style>
  html, body {{
    margin: 0;
    padding: 0;
    overflow: hidden;
    background: #eef3f2;
  }}
  iframe {{
    width: 100%;
    height: 900px;
    border: 0;
    border-radius: 8px;
    background: #eef3f2;
  }}
</style>
<iframe id="medicalkg-hotspots-frame" src="{url}"></iframe>
<script>
  const frame = document.getElementById("medicalkg-hotspots-frame");
  const token = {json.dumps(token)};
  const targetOrigin = {json.dumps(_origin(url))};

  function sendAgentPlatformToken() {{
    if (!token || !frame || !frame.contentWindow) return;
    frame.contentWindow.postMessage({{
      type: "agent_platform_access_token",
      access_token: token
    }}, targetOrigin);
  }}

  frame.addEventListener("load", sendAgentPlatformToken);
  window.addEventListener("message", (event) => {{
    const data = event.data || {{}};
    if (data && data.type === "medicalkg:request_agent_platform_access_token") {{
      sendAgentPlatformToken();
    }}
  }});
  setTimeout(sendAgentPlatformToken, 500);
</script>
"""

components.html(component_html, height=930, scrolling=False)
