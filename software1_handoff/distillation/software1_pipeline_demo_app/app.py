"""Interactive web demo: upload CSV + JSON pipeline → per-step artifacts.

Run from the **repository root** ``h6wf_back``::

    pip install flask
    python -m distillation.software1_pipeline_demo_app

Open http://127.0.0.1:8765

The demo does **not** modify ``software1_solver``; it imports the stock
``Pipeline`` class and writes all step outputs under ``_demo_runs/<run_id>/``.
"""
from __future__ import annotations

import json
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from distillation.software1_pipeline_demo_app.runner import execute_pipeline
from distillation.software1_pipeline_demo_app.run_spec import build_pipeline_from_spec
from distillation.software1_pipeline_demo_app.ui_catalog import build_solver_catalog
from distillation.software1_pipeline_demo_app import llm_client


APP_DIR = Path(__file__).resolve().parent
RUN_ROOT = APP_DIR / "_demo_runs"
RUN_ROOT.mkdir(parents=True, exist_ok=True)

PRESETS: dict[str, str] = {
    "lab_eda_4step": """{
  "steps": [
    { "solver": "missing_summary", "from": "previous" },
    { "solver": "fillna_median", "from": "initial", "mapping": {} },
    { "solver": "outlier_iqr_flag", "from": "step", "step_index": 1, "csv_key": "filled_csv", "mapping": {} },
    { "solver": "pearson_correlation", "from": "step", "step_index": 1, "csv_key": "filled_csv", "mapping": {} }
  ]
}""",
    "profile_metadata_missing": """{
  "steps": [
    { "solver": "metadata_parser", "from": "previous" },
    { "solver": "missing_summary", "from": "initial" },
    { "solver": "describe_full", "from": "initial", "mapping": {} }
  ]
}""",
    "normality_on_raw": """{
  "steps": [
    { "solver": "normality_test", "from": "previous" }
  ]
}""",
}


def create_app() -> Flask:
    app = Flask(__name__, template_folder=str(APP_DIR / "templates"),
                static_folder=str(APP_DIR / "static"))
    app.config["SECRET_KEY"] = uuid.uuid4().hex
    app.config["MAX_CONTENT_LENGTH"] = 52 * 1024 * 1024  # 52 MiB

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            solver_catalog=build_solver_catalog(),
            presets=PRESETS,
            preset_names=sorted(PRESETS.keys()),
            llm_available=llm_client.is_available(),
            llm_model=(llm_client.get_config().model
                        if llm_client.is_available() else None),
        )

    @app.get("/api/presets/<name>")
    def get_preset(name: str):
        body = PRESETS.get(name)
        if body is None:
            abort(404)
        return app.response_class(body, mimetype="application/json")

    @app.post("/run")
    def run_pipeline():
        f = request.files.get("file")
        spec_raw = request.form.get("spec", "").strip()
        if not f or not f.filename:
            flash("请上传一个 CSV 文件", "error")
            return redirect(url_for("index"))
        if not f.filename.lower().endswith(".csv"):
            flash("当前 demo 仅接受 .csv（与 pandas.read_csv 一致）", "error")
            return redirect(url_for("index"))
        try:
            spec = json.loads(spec_raw)
            steps, names = build_pipeline_from_spec(spec)
        except Exception as e:
            flash(f"管线 JSON 解析失败: {type(e).__name__}: {e}", "error")
            return redirect(url_for("index"))

        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8]
        run_dir = RUN_ROOT / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        in_path = run_dir / "input.csv"
        f.save(in_path)

        out_root = run_dir / "pipeline_output"
        use_llm = (request.form.get("use_llm") == "1") and llm_client.is_available()
        try:
            run_result = execute_pipeline(steps, in_path, out_root,
                                           use_llm=use_llm)
        except Exception as e:
            run_result = {
                "ok": False,
                "steps": [],
                "error": f"{type(e).__name__}: {e}",
            }

        files = []
        if in_path.is_file():
            files.append({"path": "input.csv",
                          "size": in_path.stat().st_size})
        if out_root.exists():
            for p in sorted(out_root.rglob("*")):
                if p.is_file():
                    files.append({
                        "path": str(p.relative_to(run_dir)).replace("\\", "/"),
                        "size": p.stat().st_size,
                    })

        manifest = {
            "run_id": run_id,
            "ok": run_result.get("ok", False),
            "error": run_result.get("error"),
            "steps": run_result.get("steps", []),
            "files": files,
            "step_order": names,
            "use_llm": use_llm,
        }
        (run_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return redirect(url_for("result", run_id=run_id))

    @app.get("/result/<run_id>")
    def result(run_id: str):
        run_dir = RUN_ROOT / run_id
        man_path = run_dir / "manifest.json"
        if not man_path.is_file():
            abort(404)
        manifest = json.loads(man_path.read_text(encoding="utf-8"))
        return render_template("result.html", m=manifest, run_id=run_id)

    @app.get("/download/<run_id>/<path:rel_path>")
    def download(run_id: str, rel_path: str):
        run_dir = RUN_ROOT / run_id
        target = (run_dir / rel_path).resolve()
        if not str(target).startswith(str(run_dir.resolve())):
            abort(403)
        if not target.is_file():
            abort(404)
        return send_file(target, as_attachment=True, download_name=target.name)

    @app.get("/download_zip/<run_id>")
    def download_zip(run_id: str):
        run_dir = RUN_ROOT / run_id
        if not run_dir.is_dir():
            abort(404)
        zpath = run_dir / "_bundle.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in run_dir.rglob("*"):
                if p.is_file() and p.name != "_bundle.zip":
                    arc = p.relative_to(run_dir)
                    zf.write(p, arc)
        return send_file(zpath, as_attachment=True, download_name=f"{run_id}_all_outputs.zip")

    return app
