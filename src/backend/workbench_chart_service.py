# backend/workbench_chart_service.py — 用户可控出图（图种目录 / 列画像 / 渲染 / NL 解析）

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from configs.config import TEMP_FOLDER

_LOG = logging.getLogger(__name__)

CHART_TYPE_CATALOG: List[Dict[str, Any]] = [
    {"type": "histogram", "label": "直方图", "needs": ["y|x"], "optional": ["params.bins"], "role": "univariate"},
    {"type": "kde", "label": "密度图 KDE", "needs": ["y|x"], "optional": [], "role": "univariate"},
    {"type": "qq", "label": "Q–Q 图", "needs": ["y|x"], "optional": [], "role": "univariate"},
    {"type": "pie", "label": "饼图", "needs": ["x|y"], "optional": [], "role": "categorical"},
    {"type": "missing_heatmap", "label": "缺失热图", "needs": [], "optional": [], "role": "overview"},
    {"type": "violin", "label": "小提琴图", "needs": ["x", "y"], "optional": [], "role": "group_numeric"},
    {"type": "box", "label": "箱线图", "needs": ["x", "y"], "optional": [], "role": "group_numeric"},
    {"type": "bar", "label": "柱状图", "needs": ["x", "y"], "optional": [], "role": "group_numeric"},
    {"type": "strip", "label": "散点抖动图", "needs": ["x", "y"], "optional": [], "role": "group_numeric"},
    {"type": "dot", "label": "点图", "needs": ["x", "y"], "optional": [], "role": "group_numeric"},
    {"type": "ridge", "label": "山脊图", "needs": ["x", "y"], "optional": [], "role": "group_numeric"},
    {"type": "scatter", "label": "散点图", "needs": ["x", "y"], "optional": ["hue"], "role": "bivariate"},
    {"type": "line", "label": "折线图", "needs": ["x", "y"], "optional": ["hue"], "role": "bivariate"},
    {"type": "grouped_bar", "label": "分组柱状图", "needs": ["x", "y", "hue"], "optional": [], "role": "grouped"},
    {"type": "stacked_bar", "label": "堆叠柱状图", "needs": ["x", "y", "hue"], "optional": [], "role": "grouped"},
    {"type": "correlation_heatmap", "label": "相关热图", "needs": ["cols"], "optional": [], "role": "multivariate"},
    {"type": "heatmap", "label": "热图（相关）", "needs": ["cols"], "optional": [], "role": "multivariate"},
    {"type": "pca_scatter", "label": "PCA 散点", "needs": ["cols"], "optional": [], "role": "multivariate"},
    {"type": "forest", "label": "森林图（相关效应）", "needs": ["cols"], "optional": [], "role": "multivariate"},
    {"type": "residual", "label": "残差图", "needs": ["x", "y"], "optional": [], "role": "bivariate"},
    {"type": "volcano", "label": "火山图", "needs": ["x", "y"], "optional": ["params.logfc_col", "params.p_col"], "role": "deg"},
    {"type": "km_curve", "label": "Kaplan–Meier 曲线", "needs": ["x", "y"], "optional": ["hue"], "role": "survival"},
]


def list_chart_types() -> List[Dict[str, Any]]:
    return list(CHART_TYPE_CATALOG)


# 明确不支持的图种别名：命中后不得静默降级为 pie/bar 等
UNSUPPORTED_CHART_REQUESTS: List[Dict[str, str]] = [
    {"name": "玫瑰图", "keys": "玫瑰图,南丁格尔,nightingale,rose chart,polar rose", "reason": "当前工作台未实现极坐标玫瑰图（南丁格尔图）"},
    {"name": "雷达图", "keys": "雷达图,蜘蛛图,radar,spider", "reason": "当前工作台未实现雷达图"},
    {"name": "桑基图", "keys": "桑基,sankey", "reason": "当前工作台未实现桑基图"},
    {"name": "旭日图", "keys": "旭日,sunburst", "reason": "当前工作台未实现旭日图"},
    {"name": "树图", "keys": "树图,treemap", "reason": "当前工作台未实现树图"},
    {"name": "词云", "keys": "词云,wordcloud,word cloud", "reason": "当前工作台未实现词云"},
    {"name": "三维图", "keys": "三维,3d图,3d scatter,surface plot", "reason": "当前工作台未实现三维图"},
]


def detect_unsupported_chart_requests(text: str) -> List[Dict[str, str]]:
    """Return explicit unsupported chart names mentioned in user text."""
    raw = text or ""
    low = raw.lower()
    hit: List[Dict[str, str]] = []
    seen = set()
    for item in UNSUPPORTED_CHART_REQUESTS:
        keys = [k.strip() for k in item["keys"].split(",") if k.strip()]
        if any((k.lower() in low) or (k in raw) for k in keys):
            name = item["name"]
            if name in seen:
                continue
            seen.add(name)
            hit.append({"name": name, "reason": item["reason"], "supported": False})
    return hit


def _supported_type_labels() -> str:
    return "、".join(f"{c['label']}({c['type']})" for c in CHART_TYPE_CATALOG)


def _load_session_df(session_id: str, user_id: int) -> Tuple[Optional[pd.DataFrame], Optional[Path], Optional[str]]:
    from backend.workbench_service import find_session_data_file

    path, err = find_session_data_file(session_id, user_id)
    if err or path is None:
        return None, None, err or "无数据文件"
    try:
        if path.suffix.lower() in (".xlsx", ".xls"):
            df = pd.read_excel(path)
        else:
            df = pd.read_csv(path)
    except Exception as exc:
        return None, path, f"读取数据失败: {exc}"
    return df, path, None


def _role_hint(col: str, series: pd.Series) -> str:
    name = str(col).lower()
    if any(k in name for k in ("time", "day", "month", "follow")):
        return "time_like"
    if any(k in name for k in ("event", "censor", "status", "death", "relapse", "survived")):
        return "event_like"
    if pd.api.types.is_numeric_dtype(series):
        nunq = int(series.nunique(dropna=True))
        if nunq <= 12 and nunq >= 2 and not name.endswith("_id") and name != "id":
            # numeric but low-card — could be coded group
            return "numeric"
        return "numeric"
    nunq = int(series.nunique(dropna=True))
    if 2 <= nunq <= 12:
        return "categorical"
    return "text"


def profile_session_columns(session_id: str, user_id: int) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    df, path, err = _load_session_df(session_id, user_id)
    if err or df is None:
        return None, err
    columns = []
    for c in df.columns:
        s = df[c]
        dtype = str(s.dtype)
        nunq = int(s.nunique(dropna=True))
        miss = float(s.isnull().mean()) if len(s) else 0.0
        role = _role_hint(str(c), s)
        columns.append({
            "name": str(c),
            "dtype": dtype,
            "nunique": nunq,
            "missing_rate": round(miss, 4),
            "role_hint": role,
            "is_numeric": bool(pd.api.types.is_numeric_dtype(s)),
        })
    return {
        "rows": int(len(df)),
        "columns": columns,
        "data_file": str(path) if path else None,
        "numeric_columns": [c["name"] for c in columns if c["is_numeric"]],
        "categorical_columns": [c["name"] for c in columns if c["role_hint"] == "categorical"],
    }, None


def _runs_root() -> Path:
    return Path(TEMP_FOLDER) / "workbench_runs"


def render_chart_specs(
    session_id: str,
    user_id: int,
    specs: List[Dict[str, Any]],
    run_id: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not specs:
        return None, "请至少提供一个 chart spec"
    df, path, err = _load_session_df(session_id, user_id)
    if err or df is None or path is None:
        return None, err or "无数据"

    if run_id:
        # attach to existing run dir if present
        run_dir = None
        root = _runs_root() / session_id
        candidate = root / run_id
        if candidate.is_dir():
            run_dir = candidate
        else:
            # allow creating under session with given run_id
            run_dir = candidate
            run_dir.mkdir(parents=True, exist_ok=True)
        out_run_id = run_id
    else:
        out_run_id = "custom_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        run_dir = _runs_root() / session_id / out_run_id
        run_dir.mkdir(parents=True, exist_ok=True)

    from viz.chart_renderer import ChartRenderer

    renderer = ChartRenderer(run_dir / "charts")
    charts: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for i, spec in enumerate(specs):
        try:
            c = renderer.render_spec(df, spec)
            if not c:
                errors.append({"index": i, "spec": spec, "error": "渲染结果为空（列类型或样本量可能不满足）"})
                continue
            item = {k: v for k, v in c.items() if k != "base64"}
            if item.get("path") and not item.get("filename"):
                item["filename"] = Path(item["path"]).name
            # carry axis hints for captioning
            for k in ("x", "y", "hue", "cols", "type"):
                if k in spec and k not in item:
                    item[k] = spec.get(k)
            if spec.get("type") and not item.get("chart_type"):
                item["chart_type"] = spec.get("type")
            charts.append(item)
        except Exception as exc:
            errors.append({"index": i, "spec": spec, "error": str(exc)})

    try:
        from orchestrator.workbench_chart_captions import annotate_charts_with_analysis
        charts = annotate_charts_with_analysis(charts, df=df, task="自定义出图", profile_text="")
    except Exception as exc:
        _LOG.debug("custom chart captions failed: %s", exc)

    meta = {
        "mode": "user_specs",
        "session_id": session_id,
        "run_id": out_run_id,
        "charts": charts,
        "errors": errors,
        "count": len(charts),
    }
    (run_dir / "charts.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "chart_specs.json").write_text(json.dumps(specs, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta, None


def _rule_parse_charts(text: str, columns: List[str]) -> List[Dict[str, Any]]:
    """Lightweight NL → specs without LLM."""
    t = (text or "").lower()
    col_map = {c.lower(): c for c in columns}
    specs: List[Dict[str, Any]] = []

    def find_col(*cands: str) -> Optional[str]:
        for c in cands:
            if c.lower() in col_map:
                return col_map[c.lower()]
        for c in columns:
            for cand in cands:
                if cand.lower() in c.lower():
                    return c
        return None

    # patterns: 小提琴/violin, 热图/heatmap, 散点/scatter, 箱线/box, 柱状/bar, 直方图/histogram
    # 若用户只点名了不支持图种，规则解析也不要瞎配 pie
    unsupported = detect_unsupported_chart_requests(text)
    type_aliases = [
        ("violin", ["小提琴", "violin"]),
        ("box", ["箱线", "boxplot", "box"]),
        ("bar", ["柱状", "条形", "bar chart", "bar"]),
        ("scatter", ["散点", "scatter"]),
        ("correlation_heatmap", ["相关热图", "相关矩阵", "correlation", "heatmap", "热图"]),
        ("histogram", ["直方图", "histogram"]),
        ("kde", ["密度", "kde"]),
        ("pie", ["饼图", "饼状图", "pie chart"]),  # 不含玫瑰图
        ("missing_heatmap", ["缺失", "missing"]),
        ("ridge", ["山脊", "ridge"]),
        ("line", ["折线", "line"]),
        ("km_curve", ["生存", "kaplan", "km"]),
        ("volcano", ["火山", "volcano"]),
        ("pca_scatter", ["pca", "主成分"]),
        ("forest", ["森林", "forest"]),
    ]
    wanted_types = []
    for tid, keys in type_aliases:
        if any(k in t or k in text for k in keys):
            wanted_types.append(tid)
    # 英文单独写 pie 且未提玫瑰时也算饼图
    if re.search(r"\bpie\b", t) and "rose" not in t and "玫瑰" not in (text or ""):
        if "pie" not in wanted_types:
            wanted_types.append("pie")
    if not wanted_types:
        return []

    sex = find_col("sex", "gender", "性别")
    age = find_col("age", "年龄")
    fare = find_col("fare", "费用")
    klass = find_col("class", "pclass", "舱位")
    numeric = [c for c in columns if c.lower() in ("age", "fare", "sibsp", "parch", "pclass", "survived")]
    if not numeric:
        numeric = columns[:4]

    for tid in wanted_types:
        if tid in ("violin", "box", "bar", "strip", "dot", "ridge"):
            x = sex or klass or (columns[0] if columns else None)
            y = age or fare or (columns[1] if len(columns) > 1 else None)
            if x and y:
                specs.append({"type": tid, "x": x, "y": y, "title": f"{tid}: {y} by {x}"})
        elif tid in ("scatter", "line", "residual"):
            x = age or (numeric[0] if numeric else None)
            y = fare or (numeric[1] if len(numeric) > 1 else None)
            if x and y:
                specs.append({"type": tid, "x": x, "y": y, "hue": sex, "title": f"{tid}: {x} vs {y}"})
        elif tid in ("correlation_heatmap", "heatmap", "pca_scatter", "forest"):
            specs.append({"type": tid if tid != "heatmap" else "correlation_heatmap", "cols": numeric[:6]})
        elif tid in ("histogram", "kde", "qq"):
            y = age or fare or (numeric[0] if numeric else None)
            if y:
                specs.append({"type": tid, "y": y})
        elif tid == "pie":
            x = klass or sex or (columns[0] if columns else None)
            if x:
                specs.append({"type": "pie", "x": x})
        elif tid == "missing_heatmap":
            specs.append({"type": "missing_heatmap"})
        elif tid == "km_curve":
            # titanic-like: age as time, survived as event
            time_c = find_col("time", "age", "day") or age
            event_c = find_col("event", "survived", "status", "death")
            if time_c and event_c:
                specs.append({"type": "km_curve", "x": time_c, "y": event_c, "hue": sex})
        elif tid == "volcano":
            specs.append({"type": "volcano", "x": "logFC", "y": "P.Value"})
    return specs



def plan_all_chart_specs(
    session_id: str,
    user_id: int,
    preferred_types: Optional[List[str]] = None,
    selected_columns: Optional[List[str]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Build as many feasible chart specs as possible for the session data."""
    profile, err = profile_session_columns(session_id, user_id)
    if err or not profile:
        return None, err
    cols_meta = profile.get("columns") or []
    all_names = [c["name"] for c in cols_meta]
    selected = [c for c in (selected_columns or []) if c in all_names]
    focus = selected or all_names
    num = [c["name"] for c in cols_meta if c.get("is_numeric") and c["name"] in focus]
    cat = [
        c["name"] for c in cols_meta
        if c.get("role_hint") == "categorical" and c["name"] in focus
    ]
    # low-card numeric as group
    for c in cols_meta:
        if c["name"] in focus and c.get("is_numeric") and 2 <= int(c.get("nunique") or 0) <= 8:
            if c["name"] not in cat and c["name"] not in num[:1]:
                cat.append(c["name"])
    valid = {c["type"] for c in CHART_TYPE_CATALOG}
    wanted = [str(t).lower() for t in (preferred_types or []) if str(t).lower() in valid]
    if not wanted:
        wanted = [c["type"] for c in CHART_TYPE_CATALOG]

    g = cat[0] if cat else None
    y0 = num[0] if num else None
    y1 = num[1] if len(num) > 1 else y0
    specs: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []

    def add(spec):
        specs.append(spec)

    for tid in wanted:
        try:
            if tid in ("histogram", "kde", "qq"):
                if not y0:
                    skipped.append({"type": tid, "reason": "需要数值列"}); continue
                add({"type": tid, "y": y0, "title": f"{tid}: {y0}"})
            elif tid == "pie":
                if not g:
                    skipped.append({"type": tid, "reason": "需要分类列"}); continue
                add({"type": "pie", "x": g, "title": f"饼图: {g}"})
            elif tid == "missing_heatmap":
                add({"type": "missing_heatmap", "title": "缺失值热图"})
            elif tid in ("violin", "box", "bar", "strip", "dot", "ridge"):
                if not (g and y0):
                    skipped.append({"type": tid, "reason": "需要分组列+数值列"}); continue
                add({"type": tid, "x": g, "y": y0, "title": f"{tid}: {y0} by {g}"})
            elif tid in ("scatter", "line", "residual"):
                if not (y0 and y1 and y0 != y1):
                    skipped.append({"type": tid, "reason": "需要至少两列数值"}); continue
                add({"type": tid, "x": y0, "y": y1, "hue": g, "title": f"{tid}: {y0} vs {y1}"})
            elif tid in ("grouped_bar", "stacked_bar"):
                if not (g and y0 and len(cat) >= 2):
                    skipped.append({"type": tid, "reason": "需要两个分类列+数值列"}); continue
                add({"type": tid, "x": g, "y": y0, "hue": cat[1], "title": f"{tid}: {y0}"})
            elif tid in ("correlation_heatmap", "heatmap", "pca_scatter", "forest"):
                if len(num) < 2:
                    skipped.append({"type": tid, "reason": "需要≥2数值列"}); continue
                t = "correlation_heatmap" if tid == "heatmap" else tid
                add({"type": t, "cols": num[:8], "title": f"{t}"})
            elif tid == "volcano":
                if not ({"logFC", "P.Value"} <= set(all_names) or {"log2FoldChange", "pvalue"} <= set(all_names)):
                    skipped.append({"type": tid, "reason": "需要差异表达列 logFC/P.Value"}); continue
                add({"type": "volcano", "x": "logFC" if "logFC" in all_names else "log2FoldChange",
                     "y": "P.Value" if "P.Value" in all_names else "pvalue", "title": "火山图"})
            elif tid == "km_curve":
                time_c = next((n for n in focus if any(k in n.lower() for k in ("time", "day", "month", "follow"))), None)
                event_c = next((n for n in focus if any(k in n.lower() for k in ("event", "censor", "status", "death", "relapse", "survived"))), None)
                if not (time_c and event_c):
                    skipped.append({"type": tid, "reason": "需要生存时间+事件列"}); continue
                add({"type": "km_curve", "x": time_c, "y": event_c, "hue": g, "title": f"KM: {time_c}"})
            else:
                skipped.append({"type": tid, "reason": "暂未自动编排"})
        except Exception as exc:
            skipped.append({"type": tid, "reason": str(exc)})

    return {
        "source": "plan_all",
        "charts": specs,
        "count": len(specs),
        "skipped": skipped,
        "columns": all_names,
        "selected_columns": selected,
        "preferred_types": wanted,
    }, None


def parse_chart_request(
    session_id: str,
    user_id: int,
    text: str,
    selected_columns: Optional[List[str]] = None,
    preferred_types: Optional[List[str]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    profile, err = profile_session_columns(session_id, user_id)
    if err or not profile:
        return None, err
    columns = [c["name"] for c in profile.get("columns") or []]
    selected = [c for c in (selected_columns or []) if c in columns]
    preferred = [str(t).strip().lower() for t in (preferred_types or []) if t]
    valid_types = {c["type"] for c in CHART_TYPE_CATALOG}
    preferred = [t for t in preferred if t in valid_types]
    focus_cols = selected or columns
    specs: List[Dict[str, Any]] = []
    source = "rules"
    unsupported = detect_unsupported_chart_requests(text or "")
    llm_unsupported: List[Dict[str, str]] = []

    # Try LLM first
    try:
        from operator_pipeline import llm_client

        # persona-style aliases
        import os
        if not (os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")):
            key = os.getenv("LLM_PROFILE_API_KEY") or ""
            if key:
                os.environ.setdefault("OPENAI_API_KEY", key)
                os.environ.setdefault("ANTHROPIC_API_KEY", key)
        if not (os.getenv("OPENAI_API_BASE") or os.getenv("ANTHROPIC_API_BASE_URL")):
            base = os.getenv("LLM_PROFILE_API_BASE") or ""
            if base:
                os.environ.setdefault("OPENAI_API_BASE", base)
                os.environ.setdefault("ANTHROPIC_API_BASE_URL", base)
        if not (os.getenv("LLM_MODEL") or os.getenv("ANTHROPIC_MODEL")):
            model = os.getenv("LLM_PROFILE_MODEL") or ""
            if model:
                os.environ.setdefault("LLM_MODEL", model)

        if llm_client.is_available():
            catalog = ", ".join(c["type"] for c in CHART_TYPE_CATALOG)
            labels = _supported_type_labels()
            out = llm_client.chat_json(
                system=(
                    "你是数据分析出图助手。把用户出图需求转成 JSON：\n"
                    '{"charts":[{"type":"...","x":null,"y":null,"hue":null,"cols":null,"title":"...","params":{}}],'
                    '"unsupported":[{"name":"用户提到的图种中文名","reason":"不支持的原因"}]}\n'
                    f"支持的 type 只能是: {catalog}（中文名：{labels}）。\n"
                    "硬性规则：\n"
                    "1) 若用户要求玫瑰图/南丁格尔图/雷达图/桑基图/旭日图/树图/词云/三维图等不在目录中的图种，"
                    "必须写入 unsupported，并明确 reason 含「不支持」；禁止用 pie/bar 等顶替。\n"
                    "2) charts 仅包含真正支持的图；若用户只点了不支持图种且未提其它支持图种，charts 必须为 []。\n"
                    "3) 列名必须来自可用列清单；分组图用分类列作 x、数值列作 y；相关/PCA 用数值 cols。\n"
                    "4) 用户未指定图种时，可推断 2～6 张支持的图。"
                ),
                user=(
                    f"全部可用列: {json.dumps(columns, ensure_ascii=False)}\n"
                    f"用户选中的列(优先): {json.dumps(selected or focus_cols, ensure_ascii=False)}\n"
                    f"用户偏好图种(优先): {json.dumps(preferred, ensure_ascii=False)}\n"
                    f"预先检测到的不支持请求: {json.dumps(unsupported, ensure_ascii=False)}\n"
                    f"列画像: {json.dumps(profile.get('columns'), ensure_ascii=False)[:2500]}\n"
                    f"用户需求: {text or '根据选中的列生成合适的统计图'}"
                ),
                max_tokens=900,
                temperature=0.1,
                json_mode=True,
                stage="workbench_chart_parse",
            )
            if isinstance(out, dict):
                raw_un = out.get("unsupported") or out.get("unsupported_charts") or []
                if isinstance(raw_un, list):
                    for u in raw_un:
                        if isinstance(u, dict) and u.get("name"):
                            llm_unsupported.append({
                                "name": str(u.get("name")),
                                "reason": str(u.get("reason") or "当前工作台不支持该图种"),
                                "supported": False,
                            })
                if isinstance(out.get("charts"), list):
                    for ch in out["charts"]:
                        if not isinstance(ch, dict):
                            continue
                        tid = str(ch.get("type") or "").strip().lower()
                        if tid not in valid_types:
                            # unknown type from LLM → unsupported, do not coerce
                            llm_unsupported.append({
                                "name": tid or str(ch.get("title") or "未知图种"),
                                "reason": f"不支持的图种 type={tid!r}，未加入出图队列",
                                "supported": False,
                            })
                            continue
                        for key in ("x", "y", "hue"):
                            if ch.get(key) and ch[key] not in columns:
                                ch[key] = None
                        if isinstance(ch.get("cols"), list):
                            ch["cols"] = [c for c in ch["cols"] if c in columns]
                            if selected:
                                inter = [c for c in ch["cols"] if c in selected]
                                if len(inter) >= 2:
                                    ch["cols"] = inter
                        specs.append(ch)
                    if preferred:
                        preferred_first = [s for s in specs if s.get("type") in preferred]
                        others = [s for s in specs if s.get("type") not in preferred]
                        if preferred_first:
                            specs = preferred_first + others
                    if specs or llm_unsupported or unsupported:
                        source = "llm"
    except Exception as exc:
        _LOG.debug("chart parse LLM failed: %s", exc)

    # 用户点名了不支持图种：禁止把 pie 等当作替代品塞进结果
    if unsupported:
        only_unsupported = False
        # 若需求文本几乎只提不支持图种、没有其它明确支持图种关键词
        support_keys = (
            "直方", "箱线", "小提琴", "散点", "热图", "柱状", "饼图", "火山", "森林",
            "密度", "折线", "山脊", "pca", "缺失", "生存", "km", "histogram", "violin",
            "box", "scatter", "heatmap", "bar", "pie", "volcano",
        )
        raw_l = (text or "").lower()
        if not any(k.lower() in raw_l or k in (text or "") for k in support_keys) and not preferred:
            only_unsupported = True
            specs = []
        elif only_unsupported is False and unsupported and specs:
            # 仍允许其它支持图；但若唯一图是 pie 且文本含玫瑰，则去掉 pie
            rose_hit = any(u["name"] == "玫瑰图" for u in unsupported)
            if rose_hit:
                specs = [s for s in specs if s.get("type") != "pie"]

    if not specs and not unsupported and not llm_unsupported:
        specs = _rule_parse_charts(text or " ".join(preferred), focus_cols if selected else columns)
        if preferred:
            specs = [s for s in specs if s.get("type") in preferred] or specs
        source = "rules"

    # merge unsupported lists
    merged_un: List[Dict[str, str]] = []
    seen_names = set()
    for u in unsupported + llm_unsupported:
        n = u.get("name") or ""
        if n in seen_names:
            continue
        seen_names.add(n)
        merged_un.append(u)

    msg_parts = []
    if merged_un:
        msg_parts.append(
            "不支持：" + "；".join(f"{u['name']}（{u['reason']}）" for u in merged_un)
        )
        msg_parts.append("支持的图种见页面图种列表（直方图/小提琴/火山/热图等）。")
    if specs:
        msg_parts.append(f"已规划 {len(specs)} 张支持的统计图。")
    elif merged_un:
        msg_parts.append("未生成任何替代图，避免把不支持的图种画成饼图等近似图。")

    return {
        "source": source,
        "charts": specs,
        "count": len(specs),
        "unsupported": merged_un,
        "message": "".join(msg_parts),
        "columns": columns,
        "selected_columns": selected,
        "preferred_types": preferred,
        "text": text,
        "supported_types": [{"type": c["type"], "label": c["label"]} for c in CHART_TYPE_CATALOG],
    }, None
