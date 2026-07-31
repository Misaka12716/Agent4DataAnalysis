"""Compact, LLM-friendly summary of the solver catalog.

Used to teach the planner *which* operators exist and *what shape*
each one expects.  Kept short on purpose so that the planner prompt
stays well under the model's context budget.

中文说明
========
把 ``registry.list_solvers`` 里的算子压成「能力分桶 + 每算子的角色签名」，
再渲染成 markdown 塞进规划 prompt。故意短：tokens 预算有限；详细 I/O 仍以
各 solver 的 ``SolverContract`` 为准。
"""
from __future__ import annotations

from typing import Any, Dict, List

from operator_pipeline.registry import (
    list_solvers,
    make_solver,
)


# ---------------------------------------------------------------------------
# V8 §G — Freeloader operators (hidden from the planner's catalog).
#
# Empirically the planner regularly schedules these operators but the Coder
# *never* reads their CSV products — instead the Coder rewrites the same
# thing inline (df.isna().mean() / sklearn directly).  We measured this on
# the V8 retrieval study (3 benchmarks × 30 items each, 9 cells total):
#
#   missing_summary       17 planned /  1 consumed   (usage 0.06)
#   random_forest          8 planned /  0 consumed   (usage 0.00)
#   logistic_regression    3 planned /  0 consumed   (usage 0.00)
#
# Hiding them from the planner-catalog (a) frees ~150 tokens of catalog
# budget, (b) deletes the "EDA-3-piece set" reflex (the planner used to
# always front-load missing_summary even when unused), (c) the Coder still
# writes the equivalent pandas/sklearn inline as before.  The solvers
# themselves are NOT deleted from the registry — they're still callable
# programmatically and via the pipeline UI; they're just invisible to the
# Stage-3 LLM operator selector.  Keep this list short and only add an op
# after the V8 study clearly flags it as a freeloader.
# ---------------------------------------------------------------------------
_HIDDEN_FROM_PLANNER: set = {
    "missing_summary",
    "random_forest",
    "logistic_regression",
}


# ---------------------------------------------------------------------------
# Capability buckets — guides the planner about *when* to reach for what.
# These are short, human-curated tags; they do not constrain the catalog.
# ---------------------------------------------------------------------------
_BUCKETS: List[Dict[str, Any]] = [
    {
        "tag": "data_governance",
        # V8 §G: missing_summary was hidden as a freeloader (planner used
        # to schedule it 17× across 30 items × 3 conditions but the Coder
        # consumed it only 1× — it rewrites df.isna().mean() inline).  See
        # _HIDDEN_FROM_PLANNER and docs/V8_OPERATOR_CLEANUP_AND_PIPELINE.md.
        "label": "数据治理 / 清洗 / 质量评估",
        "members": ["metadata_parser", "data_imputation",
                     "outlier_iqr_flag", "consistency_check",
                     "encode_categorical"],
    },
    {
        "tag": "descriptive",
        "label": "描述性统计 / 分布",
        "members": ["describe_full", "distribution_histogram",
                     "normality_test", "column_stat", "groupby_stat"],
    },
    {
        "tag": "radar_contract_tasks",
        "label": "RADAR 诊断任务：带类型合同的任务级算子",
        "members": ["radar_typed_task"],
    },
    # ===== V8 BUCKETS (W19..W28) — placed early so they stay visible =====
    {
        "tag": "evidence_synthesis",
        "label": "证据合成 / 多治疗比较 / 元分析 (W19, 比传统两两元分析强)",
        "members": ["network_meta_analysis"],
    },
    {
        "tag": "psychometric",
        "label": "心理测量学：项目反应理论 / 有序回归 / 症状网络 (W20/W24/W26, 用于 PANSS/PHQ Likert 数据)",
        "members": ["irt_calibration", "ordinal_regression",
                     "symptom_network_analysis"],
    },
    {
        "tag": "longitudinal",
        "label": "纵向轨迹 / 增长曲线 / 联合模型 (W21/W27, 多次随访/重复测量首选)",
        "members": ["latent_growth_curve", "joint_longitudinal_survival",
                     "panss_trajectory_responder", "time_series_features"],
    },
    {
        "tag": "causal_inference",
        "label": "因果推断 / 真实世界证据 (RWE) / ATE 估计 (W25/W29, 非随机化对照首选; "
                  "工具变量场景用 instrumental_variable_2sls)",
        "members": ["g_formula_tmle", "propensity_score_matching",
                     "risk_difference_ci", "causal_pair_test",
                     "instrumental_variable_2sls"],
    },
    {
        "tag": "causal_discovery",
        "label": "因果图发现 (W30/W31, 从观测数据中学习 DAG 结构): "
                  "PC 给 CPDAG 骨架, DirectLiNGAM 给完全定向 DAG (要求线性+非高斯噪声)",
        "members": ["causal_discovery_pc", "causal_discovery_lingam"],
    },
    {
        "tag": "gxe_pgx_v8",
        "label": "GxE / PRS × 环境交互 (W22, 基因-环境相互作用研究首选)",
        "members": ["prs_x_env_interaction"],
    },
    {
        "tag": "bayesian_subgroup",
        "label": "贝叶斯分层 / 小亚组收缩估计 (W23, 异质性亚组分析首选)",
        "members": ["bayesian_hierarchical_glm"],
    },
    {
        "tag": "fairness_audit",
        "label": "公平性审计 / 子群差异 / 4/5 法则 (W28, 凡涉及模型预测+保护属性必跑)",
        "members": ["disparate_impact_audit"],
    },
    # ===== /V8 BUCKETS =====
    {
        "tag": "association",
        "label": "相关性 / 关联性 / 多重比较",
        "members": ["pearson_correlation", "spearman_correlation",
                     "kendall_correlation", "association_rules",
                     "multiple_correction"],
    },
    {
        "tag": "hypothesis",
        "label": "差异性检验 / 组间比较 / 单比例置信区间",
        "members": ["welch_t_test", "mann_whitney_u_test",
                     "chi_square_independence", "oneway_anova",
                     "kruskal_wallis", "proportion_ci"],
    },
    {
        "tag": "prediction_classical",
        # V8 §G: logistic_regression + random_forest were hidden as
        # freeloaders (3/0 and 8/0 consumed across the V8 retrieval study
        # — the Coder always preferred to write sklearn inline rather than
        # consume the operator's predictions csv).  Tree boosters /
        # SVM / KNN stay visible because their CV bootstrap CI is
        # genuinely hard for the Coder to reproduce inline.
        "label": "经典分类 / 回归建模（含 CV）",
        "members": ["linear_regression",
                     "hist_gradient_boosting", "xgboost", "lightgbm",
                     "svm_rbf", "knn_k_selection"],
    },
    {
        "tag": "survival",
        "label": "生存分析 / 风险预警 (Cox 协变量调整 + KM 曲线/log-rank/中位生存)",
        "members": ["cox_regression", "survival_kaplan_meier",
                     "propensity_score_matching"],
    },
    {
        "tag": "time_series",
        "label": "时间序列预测 / 滞后特征 (W33 ARIMA 自动定阶预测, time_series_lag 为下游特征)",
        "members": ["ts_arima_forecast", "time_series_lag",
                     "time_series_features", "causal_pair_test"],
    },
    {
        "tag": "clinical_specific",
        "label": "临床专用：参考区间、PANSS、纵向特征、文本、滞后",
        "members": ["reference_range_flag", "panss_factor_score",
                     "panss_trajectory_responder",
                     "time_series_features", "time_series_lag",
                     "text_features"],
    },
    {
        "tag": "transcriptomics_v82",
        "label": "转录组学 V8.2/8.3 (B1-B8, 表格输入)："
                  "differential_expression_limma 做 per-gene univariate DE "
                  "(二元/连续 trait 均支持); "
                  "lasso_cv_select 做多变量 L1 联合基因选择 "
                  "(GenoTEX paper LassoCV 风格, unconditional 题首选, "
                  "默认 drop Age/Gender, 含 α 边界自适应+univariate fallback); "
                  "lmm_select = lasso_cv_select 的 batch-aware 版 (frequentist "
                  "两步近似 sparse_lmm.LMM, batch_strategy explicit/pca1_quantile/none); "
                  "residualization_regress = lasso_cv_select 的 condition-aware 版 "
                  "(paper ResidualizationRegressor 等价, conditional GTA 题专用); "
                  "batch_effect_detect 用 PCA 特征值 gap 决定要不要切换到 lmm_select; "
                  "pathway_enrichment_ora 拿 DE hit 名单做超几何富集; "
                  "gsea_preranked 用完整 ranked list 做 GSEA; "
                  "gene_set_score 用 ssGSEA 给每个样本算 pathway 分数 "
                  "(凡是 GenoMAS / GenoTEX / BioAgent Bench 类的 RNA-seq 表格题首选这一组)",
        "members": ["differential_expression_limma",
                     "lasso_cv_select",
                     "lmm_select",
                     "residualization_regress",
                     "batch_effect_detect",
                     "pathway_enrichment_ora",
                     "gsea_preranked",
                     "gene_set_score"],
    },
    {
        "tag": "bioinformatics",
        "label": "生信专用：SOFT / 探针聚合 / DEG(limma+edgeR+DESeq2) / PCA / 聚类 / 通路富集 / 中介分析 / PGx交互 / 孟德尔随机化 / ComBat批次校正",
        "members": ["gds_soft_parser", "probe_to_gene_collapse",
                     "probe_deg_collapse_to_gene",
                     "limma_deg_two_group", "pca_decompose",
                     "hclust_samples", "pathway_enrichment_fisher",
                     "mediation_analysis", "pgx_interaction",
                     "mendelian_randomization",
                     "edger_de", "deseq2_de",
                     "combat_batch_correction"],
    },

    {
        "tag": "cheminformatics",
        "label": "化学信息学：分子指纹 / 描述符 / 子结构过滤 / 相似度 / QSAR性质预测 (SMILES-based, rdkit)",
        "members": ["morgan_fingerprint", "molecular_descriptors",
                     "substructure_filter", "tanimoto_similarity",
                     "molecular_property_predict"],
    },
    {
        "tag": "biosignal",
        "label": "生物信号处理：ECG/HRV / EDA / EOG / 事件相关分析 (neurokit2)",
        "members": ["ecg_hrv_analysis", "eda_analysis",
                     "eog_analysis", "event_related_analysis"],
    },
    {
        "tag": "single_cell",
        "label": "单细胞 RNA-seq：QC归一化 / 高变基因 / 降维 / 聚类 / marker基因 (scanpy+anndata)",
        "members": ["gene_filter_normalize", "highly_variable_genes",
                     "sc_dim_reduction", "sc_clustering", "sc_marker_genes"],
    },
    {
        "tag": "feature_engineering",
        "label": "特征工程：特征选择(SFS/RF/MI) / 缩放归一化(z-score/minmax/robust)",
        "members": ["feature_selection", "normalize_scale"],
    },
]


def _solver_signature(solver_id: str) -> Dict[str, Any]:
    """Return roles + static_params + output_files for one solver."""
    try:
        s = make_solver(solver_id)
    except Exception as e:
        return {"id": solver_id, "error": f"{type(e).__name__}: {e}"}
    c = s.contract
    roles = {}
    for k, spec in c.roles.items():
        roles[k] = {
            "role": spec.role.value,
            "optional": spec.optional,
            "desc": spec.description,
        }
    return {
        "id": solver_id,
        "name": c.name,
        "capability": c.capability,
        "description": c.description,
        "roles": roles,
        "outputs": dict(c.output_files),
        "output_kind": dict(getattr(c, "output_kind", {}) or {}),
        "default_params": dict(c.static_params),
    }


# ---------------------------------------------------------------------------
# Output-kind legend + hard rule (V8 改法-2)
# ---------------------------------------------------------------------------
# Catalog 里每个 output key 后会加一个 [t] / [s] 标签：
#   [t] = data table — 每行 = 一个观测/实体；可作为下游数据算子的输入
#   [s] = stats/summary/coefficients/model — 不是数据表，不能作为下游
#         数据算子的输入，只能让 coder 检视、对比、或抽取最终答案
# 没有显式标注的 output key 渲染时不带标签（仅过渡期使用）。
_OUTPUT_KIND_LEGEND_EN = (
    "Output kind legend (the `[t]` / `[s]` tag after each output key):\n"
    "- `[t]` = **data table** — rows are observations/entities; safe as "
    "`input_source` for any downstream DATA operator (data_imputation, "
    "outlier_iqr_flag, encode_categorical, regression, …).\n"
    "- `[s]` = **stats / summary / coefficients / model artifact** — "
    "NOT a data table.  It is a result table whose rows are statistics "
    "(columns, coefficients, bins, pairs, group estimates, …).  Feeding "
    "a `[s]` output as `input_source` to a DATA operator is almost "
    "always WRONG (e.g. feeding `linear_regression.coef_csv` to another "
    "regression is a category error).  Only inspect / compare / pass "
    "the `[s]` output to a `__coder__` step that consumes statistics."
)
_OUTPUT_KIND_LEGEND_ZH = (
    "**输出类型标签 (output key 后面的 `[t]` / `[s]`)**:\n"
    "- `[t]` = **数据表** — 每行 = 一个观测/实体; 可以作为任何下游"
    "**数据算子**的 `input_source` (data_imputation / outlier_iqr_flag "
    "/ encode_categorical / 回归 等)。\n"
    "- `[s]` = **统计/摘要/系数/模型工件** — **不是**数据表, 而是"
    "结果表 (每行 = 一个统计量/系数/分箱/分组估计 …)。把 `[s]` 输出"
    "作为下游**数据算子**的 `input_source` 几乎一定是错的 (例如把 "
    "`linear_regression.coef_csv` 喂给另一个回归, 这是类型错误)。"
    "`[s]` 输出只用来 **观察 / 对比 / 交给 `__coder__` 抽取最终答案**。"
)


def _output_with_kind(outputs: Dict[str, str],
                       kinds: Dict[str, str]) -> str:
    """Render output keys like `coef_csv[s], fit_json[s]`."""
    parts: List[str] = []
    for k in outputs.keys():
        tag = kinds.get(k)
        if tag in ("t", "s"):
            parts.append(f"{k}[{tag}]")
        else:
            parts.append(k)
    return ", ".join(parts)


def build_catalog_for_planner() -> List[Dict[str, Any]]:
    """List of compact solver descriptors for the planner LLM.

    Solvers in :data:`_HIDDEN_FROM_PLANNER` (V8 §G freeloaders identified by
    the retrieval study) are skipped here — they are still registered and
    still callable programmatically, but the planner LLM never sees them
    and therefore stops scheduling them as no-op steps.
    """
    out: List[Dict[str, Any]] = []
    for sid, _desc in list_solvers():
        if sid in _HIDDEN_FROM_PLANNER:
            continue
        sig = _solver_signature(sid)
        out.append(sig)
    return out


# ---------------------------------------------------------------------------
# Capability gaps — explicit list of things our operator catalog CANNOT do.
# Surfaced to the planner so it stops hallucinating non-existent operators
# (and instead routes those steps to `__coder__`).  Curated by hand; keep
# focused on the gaps we actually see in benchmarks.
# ---------------------------------------------------------------------------
_CAPABILITY_GAPS_EN: List[str] = [
    "**Format / unit normalization** (e.g. \"22 lbs\" vs \"22 pounds\" "
    "vs \"weight = 22\", \"2024/01/01\" vs \"Jan 1 2024\", \"100,000\" "
    "vs \"100k\") — there is NO operator that does this.  When the task "
    "mentions 'inconsistent formatting' or arbitrary unit/format "
    "variants, route the cleaning step to `__coder__` with an explicit "
    "instruction to parse the variants.",
    "**Free-form string dedup / fuzzy matching** (e.g. \"NYC\" vs "
    "\"New York City\", \"Dr. Smith\" vs \"smith, j\") — no fuzzy "
    "match / record-linkage operator exists; use `__coder__`.",
    "**Cross-field logical consistency repair** (e.g. \"end_time must "
    "be ≥ start_time, swap or drop otherwise\", \"systolic ≥ diastolic\") "
    "— `consistency_check` only AUDITS rules; it does not REPAIR.  "
    "Send the repair step to `__coder__`.",
    "**Custom plotting / multi-panel figures / annotated heatmaps** — "
    "operators only emit CSVs/JSONs.  Any plot/figure/PNG step MUST be "
    "a `__coder__` step (matplotlib/seaborn).",
    "**Letter-from-options answer for multiple-choice** — operators "
    "produce numeric/statistical CSVs; if the user asks for an answer "
    "letter (A/B/C/D) or a categorical token, the FINAL extraction "
    "step must be `__coder__` and must explicitly map the numeric "
    "result to the required answer form.",
    "**Bootstrap CI for arbitrary estimands** — only the regression / "
    "tree / mediation operators ship with built-in bootstrap CI "
    "(see `linear_regression`, `hist_gradient_boosting`, `xgboost`, "
    "`lightgbm`, `mediation_analysis`, `g_formula_tmle`).  For other "
    "estimands, add a `__coder__` step.",
    "**Trivial column statistics on the raw data table** (missing-rate "
    "per column, value counts, simple group means) — the Coder writes "
    "`df.isna().mean()` / `df.groupby(...).mean()` inline in one line; "
    "there is intentionally **no `missing_summary` / count operator** "
    "exposed to the planner.  Skip the operator step and let the final "
    "`__coder__` step compute these directly from `data.csv`.",
    "**Plain logistic / random-forest fits** without bootstrap CI or CV "
    "scorecards — the Coder writes `LogisticRegression().fit(...)` / "
    "`RandomForestClassifier().fit(...)` inline.  Plain logistic_regression "
    "and random_forest operators are intentionally NOT exposed to the "
    "planner.  Reach for `hist_gradient_boosting`, `xgboost`, `lightgbm`, "
    "`svm_rbf` or `knn_k_selection` only when you need their built-in "
    "cross-validation + bootstrap CI artefacts; otherwise let the Coder "
    "do it.",
    "**Long↔wide reshape / pivot / melt** — no melt/pivot operator "
    "exists; route to `__coder__`.",
    "**Top-K / sort / threshold filter** on an upstream result table — "
    "no generic sort/top-K operator; do it in a `__coder__` step that "
    "reads the upstream CSV.",
]

_CAPABILITY_GAPS_ZH: List[str] = [
    "**格式 / 单位归一化** (如 \"22 lbs\" vs \"22 pounds\" vs \"weight = 22\"、"
    "\"2024/01/01\" vs \"Jan 1 2024\"、\"100,000\" vs \"100k\") —— **没有**任何"
    "算子能做。当任务出现 'inconsistent formatting' 或任意单位/格式变体时, 把清洗"
    "步骤交给 `__coder__`, 显式 hint 解析这些变体。",
    "**自由文本去重 / 模糊匹配** (如 \"NYC\" vs \"New York City\"、"
    "\"Dr. Smith\" vs \"smith, j\") —— 无模糊匹配/记录链接算子, 走 `__coder__`。",
    "**跨字段逻辑一致性的修复** (如 \"end_time ≥ start_time, 否则交换或丢弃\"、"
    "\"systolic ≥ diastolic\") —— `consistency_check` 仅**审计**规则、不**修复**, "
    "修复步骤走 `__coder__`。",
    "**自定义绘图 / 多面板图 / 带注释热图** —— 算子只产 CSV/JSON, 任何 plot/figure/"
    "PNG 步骤 MUST 用 `__coder__` (matplotlib/seaborn)。",
    "**选项字母答案 (A/B/C/D) 类多选题** —— 算子产数值/统计 CSV; 当题目要字母答案或"
    "分类 token 时, **最后**抽取答案的步骤必须是 `__coder__`, 并显式把数值结果映射"
    "成要求的答案形式。",
    "**任意估计量的 bootstrap CI** —— 只有 linear_regression / hist_gradient_boosting / "
    "xgboost / lightgbm / mediation_analysis / g_formula_tmle 自带 bootstrap CI; "
    "其它估计量需加 `__coder__`。",
    "**原始表的简单列统计** (每列缺失率 / value_counts / 简单分组均值) —— Coder 一行 "
    "`df.isna().mean()` / `df.groupby(...).mean()` 就能算; 我们**有意不**暴露 "
    "`missing_summary` 类算子给 planner。 跳过算子步, 让最后的 `__coder__` 直接读 "
    "`data.csv` 算。",
    "**朴素逻辑回归 / 随机森林**（没有 bootstrap CI 或 CV scorecard 要求）—— Coder 一行 "
    "`LogisticRegression().fit(...)` / `RandomForestClassifier().fit(...)` 就行。 朴素的 "
    "logistic_regression 和 random_forest 算子**有意不**暴露给 planner。 只有任务明确"
    "要 CV + bootstrap CI 时, 选 `hist_gradient_boosting` / `xgboost` / `lightgbm` / "
    "`svm_rbf` / `knn_k_selection`; 否则交给 Coder。",
    "**长↔宽表 reshape / pivot / melt** —— 无 melt/pivot 算子, 走 `__coder__`。",
    "**上游结果表的 Top-K / 排序 / 阈值过滤** —— 无通用 sort/topK 算子, 加一个 "
    "`__coder__` 步骤读上游 CSV 做。",
]


def _capability_gaps_markdown(lang: str = "en") -> str:
    items = _CAPABILITY_GAPS_EN if lang == "en" else _CAPABILITY_GAPS_ZH
    if lang == "en":
        header = ("### Operator catalog gaps — when in doubt, use "
                  "`__coder__`")
    else:
        header = "### 算子目录的能力空缺 —— 不确定时优先 `__coder__`"
    lines = [header, ""]
    for i, item in enumerate(items, 1):
        lines.append(f"{i}. {item}")
    return "\n".join(lines)


def render_catalog_markdown(catalog: List[Dict[str, Any]],
                              max_chars: int = 30000,
                              lang: str = "en") -> str:
    """Pretty, prompt-ready markdown rendering of the catalog grouped
    by buckets, followed by an explicit list of capability gaps
    (V8 Pattern C: routes unsupported tasks to ``__coder__`` instead
    of letting the planner hallucinate operators).  Trims at
    ``max_chars`` so we stay polite with token budget.
    """
    by_id = {c["id"]: c for c in catalog}

    lines: List[str] = []
    seen: set = set()
    for bucket in _BUCKETS:
        lines.append(f"### {bucket['label']}  ({bucket['tag']})")
        for sid in bucket["members"]:
            c = by_id.get(sid)
            if c is None:
                continue
            seen.add(sid)
            lines.append(f"- `{sid}` — {c.get('description','')}")
            roles = c.get("roles") or {}
            if roles:
                role_bits = []
                for rk, rv in roles.items():
                    optstr = "?" if rv.get("optional") else ""
                    role_bits.append(f"{rk}{optstr}:{rv['role']}")
                lines.append(f"    roles: {', '.join(role_bits)}")
            outs = c.get("outputs") or {}
            if outs:
                lines.append(
                    f"    outputs: "
                    f"{_output_with_kind(outs, c.get('output_kind') or {})}"
                )
        lines.append("")

    leftover = [c for c in catalog if c["id"] not in seen]
    if leftover:
        lines.append("### 其他")
        for c in leftover:
            lines.append(f"- `{c['id']}` — {c.get('description','')}")

    # V8 改法-2: output kind legend + hard rule so the planner stops
    # daisy-chaining a stats csv into the next data operator.
    lines.append("")
    lines.append(_OUTPUT_KIND_LEGEND_EN if lang == "en"
                  else _OUTPUT_KIND_LEGEND_ZH)

    # V8 Pattern C — capability gaps section so the planner stops
    # hallucinating operators for tasks like format normalization.
    lines.append("")
    lines.append(_capability_gaps_markdown(lang))

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... (catalog truncated)"
    return text
