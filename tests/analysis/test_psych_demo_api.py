"""2.1.4 Demo 页 /psych-app 与静态资源。"""

from __future__ import annotations

from pathlib import Path

from psych_test_helpers import make_psych_app


def test_psych_app_page_ok(psych_client):
    """联调 Demo 无需鉴权。"""
    r = psych_client.get("/psych-app")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "2.1.4" in r.text or "psych" in r.text.lower() or "<!DOCTYPE" in r.text or "<html" in r.text.lower()


def test_psych_static_app_js_ok(psych_client):
    r = psych_client.get("/static/psych/app.js")
    assert r.status_code == 200
    assert "psych" in r.text.lower() or "fetch" in r.text.lower() or len(r.content) > 0


def test_psych_static_styles_ok(psych_client):
    r = psych_client.get("/static/psych/styles.css")
    assert r.status_code == 200


def test_psych_demo_ui_files_exist_on_disk():
    ui = Path(__file__).resolve().parents[2] / "src" / "frontend" / "web" / "psych"
    assert (ui / "index.html").is_file()
    assert (ui / "app.js").is_file()
    paths = {getattr(r, "path", None) for r in make_psych_app().routes}
    assert "/psych-app" in paths
