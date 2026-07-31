# orchestrator/workbench_chart_captions.py — 每张统计图的文字解读（优先大模型）
"""
准确策略：
- 不看图片像素（当前 LLM 客户端无 vision）。
- 优先使用出图时写入的 chart["facts"]（与图一致的统计量）。
- 若缺失，再按图种从 DataFrame 回算同类事实。
- 大模型只负责把 facts 写成中文，禁止编造数字/变量。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

_LOG = logging.getLogger(__name__)

_TITLE_COL_RE = re.compile(
    r"(?:Distribution|Histogram|Density|Q–Q|Q-Q|Box|Violin|Bar|Strip|Dot|Ridge|"
    r"Counts|Scatter|Pie|Residuals?)\s*:\s*(.+)$",
    re.I,
)


def _ensure_llm_env() -> None:
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


def _cols_from_title(title: str) -> List[str]:
    t = str(title or "").strip()
    m = _TITLE_COL_RE.search(t)
    if not m:
        m2 = re.search(r"(.+?)\s+vs\s+(.+)$", t, re.I)
        if m2:
            return [m2.group(1).strip(), m2.group(2).strip()]
        m3 = re.search(r"(.+?)\s+by\s+(.+)$", t, re.I)
        if m3:
            return [m3.group(1).strip(), m3.group(2).strip()]
        return []
    rest = m.group(1).strip()
    if " by " in rest.lower():
        return [p.strip() for p in re.split(r"\s+by\s+", rest, maxsplit=1, flags=re.I) if p.strip()]
    if " vs " in rest.lower():
        return [p.strip() for p in re.split(r"\s+vs\s+", rest, maxsplit=1, flags=re.I) if p.strip()]
    if "~" in rest:
        return [p.strip() for p in rest.split("~", 1) if p.strip()]
    return [rest]


def _guess_cols(chart: Dict[str, Any], df: Optional[pd.DataFrame]) -> List[str]:
    cols: List[str] = []
    if isinstance(chart.get("columns"), list):
        cols.extend(str(c) for c in chart["columns"] if c)
    for k in ("x", "y", "hue"):
        v = chart.get(k)
        if isinstance(v, str) and v:
            cols.append(v)
    cols.extend(_cols_from_title(str(chart.get("title") or "")))
    title = str(chart.get("title") or "")
    if df is not None:
        for c in sorted((str(x) for x in df.columns), key=len, reverse=True):
            if c and c in title:
                cols.append(c)
    seen = set()
    out = []
    for c in cols:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out[:8]


def _fallback_dist_facts(df: pd.DataFrame, col: str) -> Dict[str, Any]:
    s = pd.to_numeric(df[col], errors="coerce").dropna()
    if s.empty:
        return {"variable": col, "n": 0}
    counts, _ = np.histogram(s.values, bins=min(20, max(5, len(s) // 10 or 5)))
    cv = float(np.std(counts) / (np.mean(counts) + 1e-9))
    skew = float(s.skew()) if len(s) >= 8 else 0.0
    if cv < 0.25:
        shape = "近似均匀（各区间计数接近）"
    elif abs(skew) < 0.35:
        shape = "大致对称"
    elif skew > 0:
        shape = "右偏"
    else:
        shape = "左偏"
    return {
        "variable": col,
        "n": int(len(s)),
        "mean": round(float(s.mean()), 6),
        "median": round(float(s.median()), 6),
        "std": round(float(s.std()), 6),
        "min": round(float(s.min()), 6),
        "max": round(float(s.max()), 6),
        "skewness": round(skew, 4),
        "shape": shape,
        "source": "dataframe_fallback",
    }


def build_chart_evidence(chart: Dict[str, Any], df: Optional[pd.DataFrame]) -> Dict[str, Any]:
    """Return evidence dict the LLM must ground on."""
    ctype = str(chart.get("chart_type") or chart.get("type") or "")
    title = str(chart.get("title") or "")
    cols = _guess_cols(chart, df)
    facts = chart.get("facts") if isinstance(chart.get("facts"), dict) else None

    if not facts and df is not None and cols:
        if ctype in ("histogram", "kde", "qq") or title.lower().startswith(("distribution:", "histogram:", "density:", "q–q:", "q-q:")):
            c0 = cols[0]
            if c0 in df.columns:
                facts = _fallback_dist_facts(df, c0)
        elif ctype == "scatter" and len(cols) >= 2 and cols[0] in df.columns and cols[1] in df.columns:
            aligned = df[[cols[0], cols[1]]].apply(pd.to_numeric, errors="coerce").dropna()
            r = float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1])) if len(aligned) >= 3 else None
            facts = {
                "x": cols[0], "y": cols[1], "n": int(len(aligned)),
                "pearson_r": round(r, 4) if r is not None and np.isfinite(r) else None,
                "source": "dataframe_fallback",
            }
        elif ctype == "volcano":
            # try common DEG columns on the same frame
            for lc, pc in (("logFC", "P.Value"), ("log2FoldChange", "padj"), ("logFC", "adj.P.Val")):
                if lc in df.columns and pc in df.columns:
                    lfc = pd.to_numeric(df[lc], errors="coerce")
                    p = pd.to_numeric(df[pc], errors="coerce")
                    sig = (p < 0.05) & (lfc.abs() > 1)
                    facts = {
                        "logfc_col": lc, "p_col": pc, "n_total": int(len(df)),
                        "threshold": {"abs_logfc": 1.0, "p": 0.05},
                        "n_significant": int(sig.sum()),
                        "n_up": int((sig & (lfc > 0)).sum()),
                        "n_down": int((sig & (lfc < 0)).sum()),
                        "source": "dataframe_fallback",
                    }
                    cols = [lc, pc]
                    break

    return {
        "chart_type": ctype,
        "title": title,
        "columns": cols,
        "facts": facts or {},
        "facts_source": "render" if isinstance(chart.get("facts"), dict) else (
            "fallback" if facts else "none"
        ),
    }


def _facts_to_sentences(ev: Dict[str, Any]) -> str:
    """Deterministic caption from facts (used as rules / LLM fallback)."""
    title = ev.get("title") or ev.get("chart_type") or "chart"
    facts = ev.get("facts") or {}
    ctype = str(ev.get("chart_type") or "")
    if not facts:
        cols = ev.get("columns") or []
        return f"「{title}」：请结合坐标轴阅读。涉及变量：{'、'.join(cols) if cols else '未标注'}。"

    if ctype in ("histogram", "kde") or "shape" in facts:
        var = facts.get("variable") or (ev.get("columns") or ["变量"])[0]
        parts = [f"「{title}」展示变量 {var} 的分布。"]
        if facts.get("n") is not None:
            parts.append(
                f"n={facts.get('n')}，均值={facts.get('mean')}，中位数={facts.get('median')}，"
                f"范围[{facts.get('min')}, {facts.get('max')}]。"
            )
        if facts.get("total_count") is not None:
            parts.append(
                f"共 {facts.get('n_bins')} 个区间、合计计数 {facts.get('total_count')}，"
                f"区间计数约 {facts.get('bin_count_min')}–{facts.get('bin_count_max')}。"
            )
        if facts.get("shape"):
            parts.append(f"形态：{facts['shape']}。")
        return "".join(parts)

    if ctype == "scatter":
        return (
            f"「{title}」为 {facts.get('x')} 对 {facts.get('y')} 的散点图，"
            f"有效点 n={facts.get('n')}，Pearson r={facts.get('pearson_r')}。"
            "关注是否线性、分簇与离群点。"
        )

    if ctype == "volcano":
        return (
            f"「{title}」火山图：阈值 |{facts.get('logfc_col')}|≥"
            f"{(facts.get('threshold') or {}).get('abs_logfc', 1)} 且 "
            f"{facts.get('p_col')}<{(facts.get('threshold') or {}).get('p', 0.05)}。"
            f"显著 {facts.get('n_significant')}（上调 {facts.get('n_up')} / 下调 {facts.get('n_down')}），"
            f"共 {facts.get('n_total')} 个特征。"
        )

    if ctype in ("box", "violin") and facts.get("groups"):
        gtxt = "；".join(
            f"{g.get('group')}: n={g.get('n')}, median={g.get('median')}"
            for g in facts["groups"][:6]
        )
        return f"「{title}」比较 {facts.get('y')} 在 {facts.get('x')} 各组：{gtxt}。"

    if ctype in ("heatmap", "correlation_heatmap") and facts.get("top_pairs_by_abs"):
        tops = facts["top_pairs_by_abs"][:5]
        ttxt = "；".join(f"{p['a']}–{p['b']}={p['value']}" for p in tops)
        return f"「{title}」相关/矩阵热图，绝对值最大的若干对：{ttxt}。"

    if ctype == "bar" and facts.get("top_groups_by_mean"):
        hi = facts.get("highest") or {}
        return (
            f"「{title}」比较各组 {facts.get('y')} 均值（共 {facts.get('n_groups')} 组）；"
            f"最高为 {hi.get('group')}（mean={hi.get('mean')}）。"
        )

    if ctype == "forest" and facts.get("effects"):
        strong = facts.get("strongest_abs") or {}
        return (
            f"「{title}」森林图展示 {facts.get('n_effects')} 个效应量及 95% CI；"
            f"绝对效应最大为 {strong.get('label')}（estimate={strong.get('estimate')}, "
            f"CI [{strong.get('low')}, {strong.get('high')}]）。"
        )

    # generic dump of key facts
    return f"「{title}」关键事实：{json.dumps(facts, ensure_ascii=False)[:280]}"


def _rule_caption(chart: Dict[str, Any], df: Optional[pd.DataFrame]) -> str:
    return _facts_to_sentences(build_chart_evidence(chart, df))


def _mention_token(text: str, token: str) -> bool:
    """True if ``token`` appears as a real mention (not a 1-letter substring hit)."""
    tok = str(token or "").strip()
    if not tok:
        return False
    if len(tok) <= 2:
        # short names like t / p: require framed mention
        patterns = [
            rf"(?<![A-Za-z0-9_]){re.escape(tok)}(?![A-Za-z0-9_])",
            rf"变量\s*{re.escape(tok)}",
            rf"[「\"']{re.escape(tok)}[」\"']",
            rf":\s*{re.escape(tok)}\b",
        ]
        return any(re.search(p, text, flags=re.I) for p in patterns)
    return tok in text or tok.lower() in text.lower()


def _caption_matches_evidence(
    text: str,
    ev: Dict[str, Any],
    *,
    other_titles: Optional[List[str]] = None,
) -> bool:
    if not text or len(text.strip()) < 8:
        return False
    t = text.strip()
    title = str(ev.get("title") or "")
    ctype = str(ev.get("chart_type") or "")
    cols = list(ev.get("columns") or [])
    facts = ev.get("facts") or {}

    # Must acknowledge this chart's title (prevents batch cross-talk)
    if title:
        # allow partial: last segment after ": "
        key = title.split(":")[-1].strip() if ":" in title else title
        if title not in t and key and not _mention_token(t, key) and title.lower() not in t.lower():
            # still ok if primary variable clearly named and chart_type word present
            primary = facts.get("variable") or (cols[0] if cols else "")
            type_ok = (ctype and ctype in t.lower()) or (
                {"histogram": "直方", "forest": "森林", "volcano": "火山", "scatter": "散点"}
                .get(ctype, "") in t
            )
            if not (primary and _mention_token(t, str(primary)) and type_ok):
                return False

    # Reject talking about a different chart title from the same batch
    for ot in other_titles or []:
        if not ot or ot == title:
            continue
        if ot in t and title and title not in t:
            return False

    # Empty-facts refusals are never valid LLM captions for display pairing
    if any(k in t for k in ("未提供相关统计事实", "无法进行解读", "缺少统计事实", "facts 为空")):
        return False

    primary = (
        facts.get("variable")
        or facts.get("y")
        or facts.get("logfc_col")
        or (cols[0] if cols else "")
    )
    if primary and not _mention_token(t, str(primary)):
        title_cols = _cols_from_title(title)
        need = title_cols[0] if title_cols else primary
        if need and not _mention_token(t, str(need)):
            return False

    shape = str(facts.get("shape") or "")
    if "均匀" in shape and any(k in t for k in ("钟形", "正态", "以 0 为中心", "以0为中心")):
        return False

    # Histogram must not be described as forest / volcano etc.
    wrong_type = {
        "histogram": ("森林图", "火山图", "Forest", "Volcano"),
        "kde": ("森林图", "火山图", "Forest"),
        "forest": ("直方", "Histogram", "Distribution:"),
        "volcano": ("森林图", "Forest", "直方"),
    }
    for bad in wrong_type.get(ctype, ()):
        if bad in t and (ctype not in t.lower()):
            # allow if title itself contains the word
            if bad not in title:
                return False
    return True


def _llm_captions_batch(
    charts: List[Dict[str, Any]],
    df: Optional[pd.DataFrame],
    task: str = "",
    profile_text: str = "",
) -> Dict[int, str]:
    _ensure_llm_env()
    try:
        from operator_pipeline import llm_client
    except Exception:
        return {}
    if not llm_client.is_available():
        return {}

    payload = []
    evidence_by_idx: Dict[int, Dict[str, Any]] = {}
    for i, ch in enumerate(charts):
        ev = build_chart_evidence(ch, df)
        evidence_by_idx[i] = ev
        # Skip LLM when no facts — rules caption is safer than hallucinated refusal
        if not ev.get("facts"):
            continue
        payload.append({
            "index": i,
            "chart_type": ev.get("chart_type"),
            "title": ev.get("title"),
            "columns": ev.get("columns"),
            "facts": ev.get("facts"),
            "facts_source": ev.get("facts_source"),
        })

    out_map: Dict[int, str] = {}
    # One chart per call: batch mixing caused Forest captions on Histogram:t
    batch_size = 1
    for start in range(0, len(payload), batch_size):
        batch = payload[start:start + batch_size]
        other_titles = [str(p.get("title") or "") for p in payload if p["index"] != batch[0]["index"]]
        try:
            resp = llm_client.chat_json(
                system=(
                    "你是统计图解读助手。你看不到图片，只能依据本条记录的 facts 写中文解读。"
                    "硬性规则："
                    "1) 只陈述 facts 里的变量、数字与形态；禁止编造；"
                    "2) 第一句必须包含 title 原文；"
                    "3) 若 shape 为均匀，不得写成正态/钟形；"
                    "4) 不要提其它图种标题；2～4 句；不要提算子。"
                    '严格返回 JSON: {"items":[{"index":0,"analysis":"..."}]}，index 与输入一致。'
                ),
                user=(
                    f"任务背景(勿覆盖事实): {(task or '')[:80]}\n"
                    f"本图（仅依据 facts）:\n{json.dumps(batch, ensure_ascii=False)}"
                ),
                max_tokens=400,
                temperature=0.1,
                json_mode=True,
                stage="workbench_chart_caption",
            )
            items = []
            if isinstance(resp, dict):
                items = resp.get("items") or resp.get("charts") or []
            if isinstance(items, list):
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    try:
                        idx = int(it.get("index"))
                    except Exception:
                        continue
                    text = str(it.get("analysis") or it.get("caption") or "").strip()
                    if not text:
                        continue
                    ev = evidence_by_idx.get(idx) or {}
                    if _caption_matches_evidence(text, ev, other_titles=other_titles):
                        out_map[idx] = text
                    else:
                        _LOG.info("reject caption idx=%s title=%s text=%s", idx, ev.get("title"), text[:80])
        except Exception as exc:
            _LOG.debug("chart caption LLM batch failed: %s", exc)
    return out_map


def annotate_charts_with_analysis(
    charts: List[Dict[str, Any]],
    df: Optional[pd.DataFrame] = None,
    task: str = "",
    profile_text: str = "",
    use_llm: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """Attach ``analysis`` (+ ``analysis_source``) to every chart dict."""
    if not charts:
        return charts
    if use_llm is None:
        use_llm = os.getenv("WORKBENCH_CHART_LLM_CAPTION", "1").strip().lower() not in (
            "0", "false", "no",
        )

    # Ensure facts are attached for UI/debug even before LLM
    enriched: List[Dict[str, Any]] = []
    for ch in charts:
        item = dict(ch)
        ev = build_chart_evidence(item, df)
        if not item.get("facts") and ev.get("facts"):
            item["facts"] = ev["facts"]
        if not item.get("columns") and ev.get("columns"):
            item["columns"] = ev["columns"]
        enriched.append(item)

    llm_map: Dict[int, str] = {}
    if use_llm:
        llm_map = _llm_captions_batch(enriched, df, task=task, profile_text=profile_text)

    annotated: List[Dict[str, Any]] = []
    for i, ch in enumerate(enriched):
        item = dict(ch)
        if i in llm_map:
            item["analysis"] = llm_map[i]
            item["analysis_source"] = "llm"
        else:
            item["analysis"] = _rule_caption(item, df)
            item["analysis_source"] = "rules"
        annotated.append(item)
    return annotated
