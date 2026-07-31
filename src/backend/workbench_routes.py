# backend/workbench_routes.py — 2.2.10 数据分析辅助工作台 API

from __future__ import annotations

from fastapi import Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from backend.jwt_auth import CurrentUser, get_current_user


def _ok(data, status: int = 200) -> JSONResponse:
    return JSONResponse(content={"status": "success", "data": data}, status_code=status)


def _require_workbench_session(session_id: str) -> None:
    """Validate a workspace-local ID without touching AgentPlatform session ownership."""
    from backend.workbench_service import require_workbench_session

    _, err = require_workbench_session(session_id)
    if err:
        raise HTTPException(status_code=400, detail=err)


def register_workbench_routes(app) -> None:
    @app.post("/workbench/session/create")
    async def workbench_session_create(current_user: CurrentUser = Depends(get_current_user)):
        """Create a standalone workbench workspace, not an AgentPlatform session."""
        from backend.workbench_service import create_workbench_session

        return _ok(create_workbench_session(), 201)

    @app.post("/workbench/session/upload")
    async def workbench_session_upload(
        file: UploadFile = File(...),
        session_id: str = Form(...),
        current_user: CurrentUser = Depends(get_current_user),
    ):
        """Upload data into the standalone workbench workspace."""
        from backend.workbench_service import upload_workbench_file

        content = await file.read()
        if len(content) > 200 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="上传文件不能超过 200 MB")
        resp, err = upload_workbench_file(session_id, file.filename or "", content)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp, 201)

    @app.post("/workbench/run")
    async def workbench_run(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.workbench_service import start_run

        session_id = str(body.get("session_id") or "").strip()
        task = str(body.get("task") or body.get("message") or "").strip()
        _require_workbench_session(session_id)
        auto_charts = body.get("auto_charts")
        if auto_charts is None:
            auto_charts = False
        chart_specs = body.get("chart_specs") if isinstance(body.get("chart_specs"), list) else None
        resp, err = start_run(
            session_id,
            0,
            task,
            body.get("project_name"),
            auto_charts=bool(auto_charts),
            chart_specs=chart_specs,
        )
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp, 201)

    @app.get("/workbench/chart-types")
    async def workbench_chart_types(current_user: CurrentUser = Depends(get_current_user)):
        from backend.workbench_chart_service import list_chart_types

        types = list_chart_types()
        return _ok({"count": len(types), "chart_types": types})

    @app.get("/workbench/columns")
    async def workbench_columns(
        session_id: str,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.workbench_chart_service import profile_session_columns

        _require_workbench_session(session_id)
        resp, err = profile_session_columns(session_id, 0)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    @app.post("/workbench/charts/render")
    async def workbench_charts_render(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.workbench_chart_service import render_chart_specs

        session_id = str(body.get("session_id") or "").strip()
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id 不能为空")
        _require_workbench_session(session_id)
        specs = body.get("charts") or body.get("chart_specs") or []
        if not isinstance(specs, list):
            raise HTTPException(status_code=400, detail="charts 必须是数组")
        run_id = str(body.get("run_id") or "").strip() or None
        resp, err = render_chart_specs(session_id, 0, specs, run_id=run_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp, 201)

    @app.post("/workbench/charts/plan-all")
    async def workbench_charts_plan_all(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        """按数据可行性编排尽可能多的图种规格（用于「全部出图」）。"""
        from backend.workbench_chart_service import plan_all_chart_specs

        session_id = str(body.get("session_id") or "").strip()
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id 不能为空")
        _require_workbench_session(session_id)
        preferred = body.get("preferred_types") or body.get("chart_types") or []
        selected = body.get("selected_columns") or body.get("columns") or []
        if not isinstance(preferred, list):
            preferred = []
        if not isinstance(selected, list):
            selected = []
        resp, err = plan_all_chart_specs(
            session_id,
            0,
            preferred_types=[str(x) for x in preferred] or None,
            selected_columns=[str(x) for x in selected] or None,
        )
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    @app.post("/workbench/charts/parse")
    async def workbench_charts_parse(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.workbench_chart_service import parse_chart_request

        session_id = str(body.get("session_id") or "").strip()
        text = str(body.get("text") or body.get("message") or body.get("query") or "").strip()
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id 不能为空")
        selected = body.get("selected_columns") or body.get("columns") or []
        preferred = body.get("preferred_types") or body.get("chart_types") or []
        if not isinstance(selected, list):
            selected = []
        if not isinstance(preferred, list):
            preferred = []
        if not text and not selected and not preferred:
            raise HTTPException(status_code=400, detail="请提供出图描述，或先点选列/图种")
        _require_workbench_session(session_id)
        resp, err = parse_chart_request(
            session_id,
            0,
            text,
            selected_columns=[str(x) for x in selected],
            preferred_types=[str(x) for x in preferred],
        )
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    @app.get("/workbench/progress")
    async def workbench_progress(
        run_id: str,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.workbench_service import get_progress

        resp, err = get_progress(run_id)
        if err:
            raise HTTPException(status_code=404, detail=err)
        return _ok(resp)

    @app.get("/workbench/runs/{run_id}")
    async def workbench_run_detail(
        run_id: str,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.workbench_service import get_run_detail

        resp, err = get_run_detail(run_id)
        if err:
            raise HTTPException(status_code=404, detail=err)
        return _ok(resp)

    @app.get("/workbench/runs")
    async def workbench_list_runs(
        session_id: str,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.workbench_service import list_runs

        _require_workbench_session(session_id)
        runs, err = list_runs(session_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok({"runs": runs})

    @app.post("/workbench/cancel")
    async def workbench_cancel(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.workbench_service import cancel_run

        run_id = str(body.get("run_id") or "").strip()
        resp, err = cancel_run(run_id)
        if err:
            raise HTTPException(status_code=404, detail=err)
        return _ok(resp)

    @app.post("/workbench/modify")
    async def workbench_modify(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.workbench_service import modify_run

        run_id = str(body.get("run_id") or "").strip()
        new_task = str(body.get("task") or body.get("message") or body.get("modification") or "").strip()
        if not new_task:
            raise HTTPException(status_code=400, detail="请提供新的分析任务")
        resp, err = modify_run(run_id, new_task)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp, 201)

    @app.get("/workbench/stream")
    async def workbench_stream(
        run_id: str,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.workbench_service import stream_run_events

        async def _gen():
            for chunk in stream_run_events(run_id):
                yield chunk

        return StreamingResponse(_gen(), media_type="text/event-stream")

    @app.get("/workbench/solvers")
    async def workbench_list_solvers(current_user: CurrentUser = Depends(get_current_user)):
        """分析能力目录：供页面说明；用户用中文点名步骤与顺序，由大模型编排。"""
        try:
            from operator_pipeline.ui_catalog import build_solver_catalog

            # 常用中文短名（用户写任务用这个，不必写英文 id）
            zh_short = {
                "missing_summary": "缺失检查",
                "describe_full": "描述统计",
                "distribution_histogram": "分布直方图",
                "outlier_iqr_flag": "异常值识别",
                "normality_test": "正态性检验",
                "data_imputation": "缺失填补",
                "encode_categorical": "类别编码",
                "pearson_correlation": "Pearson 相关",
                "spearman_correlation": "Spearman 相关",
                "groupby_stat": "分组统计",
                "welch_t_test": "t 检验",
                "mann_whitney_u_test": "非参数检验（Mann-Whitney）",
                "kruskal_wallis": "Kruskal-Wallis 检验",
                "oneway_anova": "单因素方差分析",
                "chi_square_independence": "卡方检验",
                "multiple_correction": "多重校正",
                "linear_regression": "线性回归",
                "limma_deg_two_group": "差异表达（limma）",
                "pca_decompose": "主成分分析 PCA",
            }
            catalog = build_solver_catalog()
            solvers = []
            for c in catalog:
                sid = c.get("id") or ""
                desc = (c.get("desc") or "").strip()
                # 短标题：优先中文表；否则用描述首句（截断），避免整段英文挤在标题里
                short = zh_short.get(sid)
                if not short:
                    head = desc.split("。")[0].split(".")[0].strip() if desc else sid
                    short = head if 0 < len(head) <= 40 else (sid or head[:40])
                solvers.append({
                    "id": sid,
                    "zh_name": short,
                    "label": short,
                    "desc": desc,
                    "params": c.get("params") or [],
                })
            how_to = (
                "在「分析任务」里用自然语言写清要用的分析及顺序即可，例如："
                "「先做缺失检查和描述统计，再做正态性检验，然后做相关和 t 检验，最后多重校正」。"
                "不必写算子编号；大模型会按你写的顺序匹配并编排。下方列表供查阅与点选中文名。"
            )
            return _ok({
                "count": len(solvers),
                "solvers": solvers,
                "how_to": how_to,
                "nl_planning_supported": True,
            })
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/workbench/suggest")
    async def workbench_suggest(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        """Preview data profile + analysis suggestions without full run."""
        import pandas as pd
        from orchestrator.workbench_orchestrator import _generate_suggestions, _load_csv
        from backend.workbench_service import find_session_data_file
        from operator_library.profiler import profile_df, profile_to_text

        session_id = str(body.get("session_id") or "").strip()
        _require_workbench_session(session_id)
        csv_path, err = find_session_data_file(session_id, 0)
        if err:
            raise HTTPException(status_code=400, detail=err)
        df = _load_csv(csv_path)
        prof = profile_df(df)
        task = str(body.get("task") or "分析这份数据")
        suggestions = _generate_suggestions(df, task)
        return _ok({
            "rows": len(df),
            "columns": list(df.columns),
            "profile_text": profile_to_text(prof)[:2000],
            "suggestions": suggestions,
        })

    @app.get("/workbench/runs/{run_id}/artifacts")
    async def workbench_artifacts(
        run_id: str,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.workbench_service import get_run_artifacts

        resp, err = get_run_artifacts(run_id)
        if err:
            raise HTTPException(status_code=404, detail=err)
        return _ok(resp)

    @app.get("/workbench/runs/{run_id}/chart/{filename}")
    async def workbench_chart(
        run_id: str,
        filename: str,
        current_user: CurrentUser = Depends(get_current_user),
        record: int = 0,
        download: int = 0,
    ):
        from fastapi.responses import FileResponse
        from backend.workbench_service import resolve_chart_path, record_export, get_run_detail

        path, err = resolve_chart_path(run_id, filename)
        if err or not path:
            raise HTTPException(status_code=404, detail=err or "not found")
        if record or download:
            try:
                detail, _ = get_run_detail(run_id)
                sid = (detail or {}).get("session_id") or ""
                if sid:
                    record_export(
                        sid, 0, run_id,
                        kind="chart",
                        artifact_path=str(path),
                        note=f"download chart {filename}",
                    )
            except Exception:
                pass
        if download:
            return FileResponse(
                path,
                media_type="image/png",
                filename=filename,
                content_disposition_type="attachment",
            )
        return FileResponse(path, media_type="image/png")

    @app.get("/workbench/runs/{run_id}/charts.zip")
    async def workbench_charts_zip(run_id: str, current_user: CurrentUser = Depends(get_current_user)):
        """打包下载该次分析的全部图表 PNG。"""
        import io
        import zipfile
        from fastapi.responses import StreamingResponse
        from backend.workbench_service import _find_run_dir

        rd = _find_run_dir(run_id)
        if not rd:
            raise HTTPException(status_code=404, detail=f"run_id 不存在: {run_id}")
        charts_dir = rd / "charts"
        if not charts_dir.is_dir():
            raise HTTPException(status_code=404, detail="尚无图表可导出")
        pngs = sorted(charts_dir.glob("*.png"))
        if not pngs:
            raise HTTPException(status_code=404, detail="尚无 PNG 图表")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in pngs:
                zf.write(p, arcname=p.name)
            sm = rd / "summary.md"
            if sm.is_file():
                zf.write(sm, arcname="summary.md")
            ev = rd / "evaluation.json"
            if ev.is_file():
                zf.write(ev, arcname="evaluation.json")
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{run_id}_charts.zip"'},
        )

    @app.post("/workbench/resume")
    async def workbench_resume(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        """从某一步断点续跑（保留之前算子产物）。"""
        from backend.workbench_service import resume_from_step

        parent = str(body.get("run_id") or body.get("parent_run_id") or "").strip()
        from_step = int(body.get("from_step") if body.get("from_step") is not None else -1)
        if not parent:
            raise HTTPException(status_code=400, detail="请提供 run_id")
        if from_step < 0:
            raise HTTPException(status_code=400, detail="请提供 from_step（从 0 起）")
        task = str(body.get("task") or "").strip() or None
        resp, err = resume_from_step(parent, from_step, 0, task)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp, 201)

    @app.post("/workbench/export")
    async def workbench_export(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        """登记导出 / 打包摘要图表到导出台账。"""
        from backend.workbench_service import export_run_bundle, record_export

        session_id = str(body.get("session_id") or "").strip()
        run_id = str(body.get("run_id") or "").strip()
        kind = str(body.get("kind") or "bundle").strip()
        _require_workbench_session(session_id)
        if kind == "bundle":
            if not run_id or not session_id:
                raise HTTPException(status_code=400, detail="bundle 导出需要 session_id 与 run_id")
            resp, err = export_run_bundle(run_id, session_id, 0)
        else:
            resp, err = record_export(
                session_id, 0, run_id or None,
                kind=kind,
                artifact_path=str(body.get("artifact_path") or ""),
                note=str(body.get("note") or ""),
            )
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp, 201)

    @app.get("/workbench/exports")
    async def workbench_list_exports(
        session_id: str,
        run_id: str = "",
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.workbench_service import list_exports

        _require_workbench_session(session_id)
        items, err = list_exports(session_id, run_id.strip() or None)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok({"exports": items, "count": len(items)})

    @app.get("/workbench/kg/status")
    async def workbench_kg_status(current_user: CurrentUser = Depends(get_current_user)):
        """知识图谱接入探活（不阻塞分析）。"""
        from backend.workbench_kg_client import probe_kg_status, kg_api_base, kg_eval_enabled

        probe = probe_kg_status()
        available = bool((probe or {}).get("available"))
        if kg_eval_enabled() and available:
            integration = "medicalkg_discovery+llm"
        elif available:
            integration = "medicalkg_probe"
        else:
            integration = "offline"
        return _ok({
            "api_base": kg_api_base(),
            "eval_enabled": kg_eval_enabled(),
            "probe": probe,
            "integration": integration,
        })

    @app.post("/workbench/chat")
    async def workbench_chat(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        """轻量对话入口：基于最近 run 摘要回答，或给出下一步引导。"""
        from backend.workbench_service import get_run_detail, list_runs

        session_id = str(body.get("session_id") or "").strip()
        message = str(body.get("message") or body.get("text") or "").strip()
        run_id = str(body.get("run_id") or "").strip()
        if session_id:
            _require_workbench_session(session_id)
        detail = None
        if run_id:
            detail, _ = get_run_detail(run_id)
        elif session_id:
            items, _ = list_runs(session_id)
            if items:
                detail, _ = get_run_detail(str(items[0].get("run_id")))
        summary = ""
        if isinstance(detail, dict):
            summary = str(detail.get("summary") or "")[:1200]
        if summary:
            reply = f"结合最近分析结果：\n\n{summary}\n\n你的问题：{message or '（无）'}"
        else:
            reply = (
                "还没有可引用的分析结果。请先上传数据并点击「开始分析」，"
                "或在自定义出图区点选列/图种后使用「大模型出图」。"
                + (f"\n你的问题：{message}" if message else "")
            )
        return _ok({
            "reply": reply,
            "run_id": (detail or {}).get("run_id") if isinstance(detail, dict) else run_id or None,
            "source": "workbench_chat",
        })
