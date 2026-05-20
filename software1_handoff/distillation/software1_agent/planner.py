"""LLM planner: natural-language task → JSON pipeline spec.

The planner is a *single* chat call (not multi-turn) that takes:

  - the user's natural-language task
  - a compact DataFrame profile
  - a markdown rendering of the solver catalog

and returns a strict JSON object of the same shape consumed by
``software1_pipeline_demo_app.run_spec.build_pipeline_from_spec``::

    {
      "rationale": "<one short paragraph>",
      "steps": [
        {"solver": "<id>",
         "from": "previous|initial|step",
         "step_index": <int>,         # only when from=step
         "csv_key": "<key>" or "auto", # only when from=step
         "params": {...},              # solver constructor params (rare)
         "mapping": {role: col|[cols]|{...}}   # any user-fixed roles
        },
        ...
      ]
    }

The mapping field can be empty per step — the runner will fill it
later via the same LLM in :mod:`mapping_engine`.  But if the planner
already knows obvious column names from the profile, it may pre-fill
them; that is treated as a stronger 'manual' override.

中文说明
========
单次 Chat 调用完成「任务理解 → 选算子 → 产出可执行 JSON」。``mapping``
可空，执行时由 ``mapping_engine`` 再补；但若用户在自然语言里写明
路径/分组等，system prompt 要求 **原样抄进 JSON**（mapping fidelity），
否则小模型容易漏导致下游失败。未知算子名会先经 ``_SOLVER_ALIASES``
规范化，无法匹配的记入 ``invalid_solver_ids`` 并中止可执行性。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from distillation.software1_solver.profiler import profile_df, profile_to_text

from distillation.software1_pipeline_demo_app import llm_client
from distillation.software1_pipeline_demo_app.registry import list_solvers

from distillation.software1_agent.catalog import (
    build_catalog_for_planner,
    render_catalog_markdown,
)

# 以下为全英文 system prompt：避免在提示词里混中英导致小模型跑偏；
# 业务规则仍以英文编号列出（mapping fidelity、solver id 必须逐字匹配等）。
PLANNER_SYSTEM = (
    "You are Software 1's analysis planner.  You are given a user's "
    "natural-language task, a compact profile of the user's CSV, and a "
    "catalog of available solvers (operators).  Your job is to chain "
    "the *minimum sufficient* set of solvers to answer the task and "
    "return one JSON object that the runtime can execute directly.\n\n"
    "Hard rules:\n"
    "  1. Output ONE JSON object.  No prose.  No markdown fences.\n"
    "  2. Use solver ids EXACTLY as they appear in the catalog "
    "(verbatim string match — do not translate, paraphrase, abbreviate, "
    "or expand them).  E.g. write `hclust_samples`, not "
    "`hierarchical_clustering`; write `pca_decompose`, not `PCA`.\n"
    "  3. Each step must specify `solver` and `from`.  `from` is one "
    "of: \"previous\" (default — previous step's main csv output), "
    "\"initial\" (the user's original csv), or \"step\" with an "
    "integer `step_index` (0-based) and optional `csv_key`.\n"
    "  4. Prefer chaining quality solvers first when the data is "
    "messy (missing_summary → fillna_median → outlier_iqr_flag → … "
    "real analysis).\n"
    "  5. **CRITICAL — mapping fidelity.**  If the user's task explicitly "
    "provides parameter values for a step (e.g. lines like "
    "`mapping: gene_matrix_csv = /path/to/x.csv`, `group_a = \"WT\"`, "
    "`top_k = 200`, `moderation = true`), you MUST copy ALL of those "
    "key→value pairs verbatim into that step's `mapping` object in the "
    "output JSON.  Do not summarise, do not omit, do not rename keys, "
    "do not change quote style.  Pre-filled file paths must be copied "
    "exactly (the runtime cannot guess them).  If the user did not "
    "provide a value for a role, leave that role out of `mapping` and "
    "the runtime will resolve it.\n"
    "  6. Keep the pipeline short (≤6 steps).  Do not invent steps "
    "that don't move the answer forward.\n"
    "  7. If the task cannot be done with the catalog, output a JSON "
    "object with `\"steps\": []` and `\"rationale\"` explaining why.\n"
    "  8. **CRITICAL — presentation fidelity.**  The user's task "
    "often specifies HOW the *deliverable* should look, separate from "
    "the underlying computation.  Common presentation requirements "
    "include:\n"
    "       - sort / ranking:  \"top N most correlated pairs\", "
    "\"largest hazard ratios first\", \"指出最相关的几对\"\n"
    "       - table shape:     \"long table\", \"one row per "
    "(patient, lab)\", \"每行一次访视\", wide vs long, melt vs pivot\n"
    "       - unit of row:     \"per visit\", \"per patient-visit\", "
    "\"per gene\" vs \"per probe\"\n"
    "       - rendered form:   \"figure\", \"plot\", \"画直方图\", "
    "\"heatmap\", \"volcano plot\"\n"
    "       - column schema:   user names exact output columns or "
    "their order.\n"
    "     Treat these as PART OF THE TASK, not as decoration.  When "
    "planning:\n"
    "       (a) If a single catalog solver directly produces the "
    "requested shape, choose it.\n"
    "       (b) If chaining two solvers (e.g. compute → reshape, "
    "compute → sort/score) yields the requested shape, chain them.\n"
    "       (c) If the catalog has NO solver that can produce the "
    "requested final form (e.g. user asks for a plot but no "
    "plotting solver exists; user asks for long-format but only a "
    "wide-output solver exists; user asks for top-K but no "
    "sort/topk solver exists), you MUST still plan the numerical "
    "computation, AND append one sentence to `rationale` that "
    "EXPLICITLY names the unmet presentation requirement, e.g.:\n"
    "           `limitation: deliverable is wide-format; user "
    "asked for long-format and no melt operator is available in "
    "the catalog`\n"
    "           `limitation: deliverable is numerical bin table; "
    "user asked for a figure and no plotting operator is in the "
    "catalog`\n"
    "           `limitation: pairs are returned unsorted; user "
    "asked for top-K and no sort/topk operator is in the catalog`\n"
    "     Never silently drop a user-stated presentation "
    "requirement.\n"
    "       (d) Do NOT invent solver ids to satisfy a presentation "
    "requirement (rule 2 still applies).  Prefer option (c) "
    "(explicit limitation) over hallucinating a non-existent "
    "solver."
)


# 规划 LLM 常输出的别名 / 口语化名称 → registry 里的正式 solver id。
# 在校验前就地改写 step["solver"]；未收录的别名进入 invalid_solver_ids。
# 新增算子时：若模型爱用简称，在此补一行可降低规划失败率。
_SOLVER_ALIASES: Dict[str, str] = {
    "hierarchical_clustering":   "hclust_samples",
    "hierarchical_cluster":      "hclust_samples",
    "hclust":                    "hclust_samples",
    "pca":                       "pca_decompose",
    "principal_component_analysis": "pca_decompose",
    "limma":                     "limma_deg_two_group",
    "limma_deg":                 "limma_deg_two_group",
    "differential_expression":   "limma_deg_two_group",
    "deg_two_group":             "limma_deg_two_group",
    "probe_to_gene":             "probe_to_gene_collapse",
    "gene_collapse":             "probe_to_gene_collapse",
    # NOTE: probe_deg_collapse_to_gene operates on a DEG TABLE, not on
    # the raw expression matrix.  Aliases below cover the common ways
    # an LLM might refer to GEO2R-style "best probe per gene" collapse.
    "probe_deg_collapse":        "probe_deg_collapse_to_gene",
    "deg_collapse":              "probe_deg_collapse_to_gene",
    "deg_collapse_to_gene":      "probe_deg_collapse_to_gene",
    "best_probe_per_gene":       "probe_deg_collapse_to_gene",
    "best_probe":                "probe_deg_collapse_to_gene",
    "min_adj_p_per_gene":        "probe_deg_collapse_to_gene",
    "geo2r_collapse":            "probe_deg_collapse_to_gene",
    "soft_parser":               "gds_soft_parser",
    "geo_soft_parser":           "gds_soft_parser",
    "pathway_enrichment":        "pathway_enrichment_fisher",
    "fisher_enrichment":         "pathway_enrichment_fisher",
    "hypergeometric_enrichment": "pathway_enrichment_fisher",
    # generic stats common confusions
    "pearson":                   "pearson_correlation",
    "spearman":                  "spearman_correlation",
    "kendall":                   "kendall_correlation",
    "ttest":                     "welch_t_test",
    "t_test":                    "welch_t_test",
    "welch":                     "welch_t_test",
    "anova":                     "oneway_anova",
    "chi2":                      "chi_square_independence",
    "chi_square":                "chi_square_independence",
    "kruskal":                   "kruskal_wallis",
    "mwu":                       "mann_whitney_u_test",
    "mannwhitney":               "mann_whitney_u_test",
    "logreg":                    "logistic_regression",
    "rf":                        "random_forest",
    "hgb":                       "hist_gradient_boosting",
    "histgb":                    "hist_gradient_boosting",
    "xgb":                       "xgboost",
    "lgbm":                      "lightgbm",
    "psm":                       "propensity_score_matching",
    "cox":                       "cox_regression",
}


PLANNER_TEMPLATE = """\
## User task
{task}

## Dataset profile
{profile_text}

## Available solvers
{catalog_md}

## Required JSON shape
{example}
"""


_EXAMPLE = {
    "rationale": "1 short sentence about the chosen plan",
    "steps": [
        {"solver": "missing_summary", "from": "previous"},
        {"solver": "fillna_median", "from": "initial"},
        {"solver": "pearson_correlation", "from": "step",
         "step_index": 1, "csv_key": "filled_csv"},
    ],
}


@dataclass
class PlanResult:
    spec: Dict[str, Any]
    rationale: str
    raw: Dict[str, Any]
    valid_solver_ids: List[str] = field(default_factory=list)
    invalid_solver_ids: List[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.spec.get("steps"))


def plan_pipeline(task: str,
                   df: pd.DataFrame,
                   max_tokens: int = 1600,
                   temperature: float = 0.0) -> PlanResult:
    """调用规划 LLM，根据任务与 DataFrame 简介生成可执行的 steps JSON。

    无 .env 或调用失败时返回 ``ok=False``；若 LLM 写了未知 solver id，
    ``PlanResult.error`` 非空且 ``spec.steps`` 仍保留原样便于排查。
    """
    if not llm_client.is_available():
        return PlanResult(
            spec={"steps": []},
            rationale="",
            raw={},
            error="LLM not configured (.env missing API key/base URL)",
        )

    catalog = build_catalog_for_planner()
    catalog_md = render_catalog_markdown(catalog)
    profile = profile_df(df)
    profile_text = profile_to_text(profile, max_lines=120)

    user_msg = PLANNER_TEMPLATE.format(
        task=task.strip(),
        profile_text=profile_text,
        catalog_md=catalog_md,
        example=json.dumps(_EXAMPLE, ensure_ascii=False, indent=2),
    )

    try:
        raw = llm_client.chat_json(PLANNER_SYSTEM, user_msg,
                                    max_tokens=max_tokens,
                                    temperature=temperature)
    except llm_client.LLMError as e:
        return PlanResult(spec={"steps": []}, rationale="", raw={},
                          error=f"planner LLM call failed: {e}")

    if not isinstance(raw, dict) or "steps" not in raw:
        return PlanResult(spec={"steps": []}, rationale="", raw=raw,
                          error="planner output missing 'steps' key")
    if not isinstance(raw["steps"], list):
        return PlanResult(spec={"steps": []}, rationale="",
                          raw=raw,
                          error="planner output 'steps' is not a list")

    valid = {sid for sid, _ in list_solvers()}
    chosen: List[str] = []
    aliased: List[str] = []
    invalid: List[str] = []
    for s in raw["steps"]:
        if not isinstance(s, dict):
            continue
        orig = str(s.get("solver", "")).strip()
        if orig in valid:
            chosen.append(orig)
            continue
        canon = _SOLVER_ALIASES.get(orig.lower())
        if canon and canon in valid:
            s["solver"] = canon  # rewrite in place so runtime sees canonical
            aliased.append(f"{orig} -> {canon}")
            chosen.append(canon)
        else:
            invalid.append(orig)
            chosen.append(orig)

    error = None
    if invalid:
        error = ("planner referenced unknown solvers: "
                 + ", ".join(invalid))

    return PlanResult(
        spec={"steps": raw["steps"]},
        rationale=str(raw.get("rationale", ""))
                  + ((f"\n[planner-aliased: " + "; ".join(aliased) + "]")
                     if aliased else ""),
        raw=raw,
        valid_solver_ids=[s for s in chosen if s in valid],
        invalid_solver_ids=invalid,
        error=error,
    )
