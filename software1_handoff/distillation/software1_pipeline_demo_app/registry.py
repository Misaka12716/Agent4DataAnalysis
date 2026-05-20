"""Solver registry for the interactive pipeline demo.

Maps stable string ids → ``get_solver`` factories (and optional kwargs).
All implementation lives under ``distillation.software1_solver.solvers``.

中文说明
========
UI / ``agent`` / ``run_spec`` 唯一认可的算子 id → 工厂函数表。
新增算子：在此注册 +（可选）``make_solver`` 里对带构造参数的工厂做分支；
并让 ``software1_agent.catalog`` 的生信分桶包含新 id。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

# Import factories only inside resolver to keep import graph small for tooling


def _solvers():
    from distillation.software1_pipeline_demo_app import solver_overlays as _overlays
    from distillation.software1_solver.solvers import (
        association_rules,
        correlation,
        cox_regression,
        data_governance,
        data_quality_check,
        descriptive_stats,
        hypothesis_tests,
        knn_classifier,
        logistic_regression,
        metadata_parser,
        multiple_correction,
        normality_test,
        panss_factor_score,
        panss_trajectory_responder,
        reference_range_flag,
        svm_classifier,
        text_features,
        time_series_features,
        tree_models,
        propensity_score_matching,
    )
    from distillation.software1_solver.solvers.bio import (
        soft_parser as _bio_soft,
        probe_to_gene as _bio_p2g,
        limma_deg as _bio_limma,
        pca_decomposition as _bio_pca,
        hierarchical_cluster as _bio_hc,
        pathway_enrichment as _bio_enrich,
        probe_deg_collapse as _bio_pdc,
    )
    return {
        "missing_summary": (lambda: data_governance.get_missing_summary_solver(),
                           "缺失率汇总（每列 n_missing / missing_rate / n_unique）"),
        "fillna_median": (lambda: data_governance.get_fillna_median_solver(),
                          "数值列中位数填补（非数值列直通）"),
        "outlier_iqr_flag": (lambda: data_governance.get_outlier_iqr_solver(1.5),
                             "IQR 异常值标记（Tukey fences）"),
        "describe_full": (lambda: descriptive_stats.get_describe_solver(),
                          "描述性统计（均值/标准差/分位数/偏度/峰度/IQR/MAD）"),
        "distribution_histogram": (lambda: descriptive_stats.get_histogram_solver(20),
                                    "等距直方图（长表：bin_left/bin_right/count/density）"),
        "pearson_correlation": (lambda: correlation.get_pearson_solver(),
                                "Pearson 相关矩阵 + 配对长表"),
        "spearman_correlation": (lambda: correlation.get_spearman_solver(),
                                 "Spearman 秩相关"),
        "kendall_correlation": (lambda: correlation.get_kendall_solver(),
                                "Kendall tau 相关"),
        "normality_test": (lambda: normality_test.get_solver(0.05),
                           "正态性检验（Shapiro + KS）"),
        "multiple_correction": (lambda: multiple_correction.get_solver(0.05),
                                "多重比较校正（Bonferroni + BH-FDR）"),
        "metadata_parser": (lambda: metadata_parser.get_solver(),
                            "元数据解析（列类型/缺失/建议；输出 JSON）"),
        "consistency_check": (lambda: data_quality_check.get_consistency_solver(),
                              "一致性检查（主键唯一/正则/值域/白名单）"),
        "welch_t_test": (lambda: hypothesis_tests.get_welch_solver(),
                         "Welch t 检验（双样本不等方差）"),
        "mann_whitney_u_test": (lambda: hypothesis_tests.get_mannwhitney_solver(),
                                 "Mann-Whitney U"),
        "chi_square_independence": (lambda: hypothesis_tests.get_chi2_solver(),
                                     "卡方独立性检验"),
        "oneway_anova": (lambda: hypothesis_tests.get_anova_solver(),
                         "单因素方差分析"),
        "kruskal_wallis": (lambda: hypothesis_tests.get_kruskal_solver(),
                           "Kruskal-Wallis 检验"),
        "logistic_regression": (lambda: logistic_regression.get_solver(),
                                 "逻辑回归 + 分层 CV（输出预测概率 CSV）"),
        "random_forest": (lambda: tree_models.get_random_forest_solver(),
                          "随机森林 + CV"),
        "hist_gradient_boosting": (lambda: tree_models.get_hist_gbdt_solver(),
                                    "sklearn HistGradientBoosting + CV"),
        "xgboost": (lambda: tree_models.get_xgboost_solver(),  # may raise if missing
                    "XGBoost + CV（需安装 xgboost）"),
        "lightgbm": (lambda: tree_models.get_lightgbm_solver(),
                     "LightGBM + CV（需安装 lightgbm）"),
        "svm_rbf": (lambda: svm_classifier.get_solver(),
                    "RBF SVM + GridSearchCV"),
        "knn_k_selection": (lambda: knn_classifier.get_solver(),
                             "KNN + K 网格搜索"),
        "cox_regression": (lambda: cox_regression.get_solver(),
                           "Cox 比例风险模型"),
        "association_rules": (lambda: association_rules.get_solver(0.05, 0.3),
                               "关联规则（FP-Growth）"),
        "reference_range_flag": (lambda: _overlays.get_reference_range_flag_overlay(),
                                  "参考区间异常标记（LLM/UI 提供 reference_ranges 字典）"),
        "panss_factor_score": (lambda: panss_factor_score.get_solver(),
                                 "PANSS 因子/总分（需提供 item 列分组 mapping）"),
        "panss_trajectory_responder": (lambda: panss_trajectory_responder.get_solver(30.0),
                                         "PANSS 访视变化 / 应答者"),
        "time_series_features": (lambda: time_series_features.get_solver(),
                                  "纵向数据 → 受试者级时序特征"),
        "propensity_score_matching": (lambda: propensity_score_matching.get_solver(),
                                       "倾向得分 1:1 匹配"),
        "text_features": (lambda: text_features.get_solver(),
                          "文本 TF-IDF / 可选向量编码"),
        "gds_soft_parser": (lambda: _bio_soft.get_solver(),
                              "GEO SOFT/GDS 解析（输出 expression / sample_groups / annotation 三张 csv）"),
        "probe_to_gene_collapse": (lambda: _bio_p2g.get_solver(),
                                     "探针级表达矩阵聚合到基因级（max/mean/median）"),
        "limma_deg_two_group": (lambda: _bio_limma.get_solver(),
                                  "两组差异表达分析（含 Smyth EB 方差收缩，moderated t）"),
        "pca_decompose": (lambda: _bio_pca.get_solver(),
                            "样本×基因表达矩阵 PCA"),
        "hclust_samples": (lambda: _bio_hc.get_solver(),
                             "样本层次聚类（默认 correlation+average，可切 ward+euclidean）"),
        "pathway_enrichment_fisher": (lambda: _bio_enrich.get_solver(),
                                         "通路富集（超几何 / Fisher exact）；自带 MSigDB Hallmark 2020"),
        "probe_deg_collapse_to_gene": (lambda: _bio_pdc.get_solver(),
                                          "把 probe 级 DEG 表按 min(adj_p) 取每个基因最佳 probe 收敛到基因级（GEO2R 推荐做法）"),
    }


def list_solvers() -> List[Tuple[str, str]]:
    """(id, description_zh) sorted by id."""
    reg = _solvers()
    return sorted((k, v[1]) for k, v in reg.items())


def make_solver(solver_id: str, params: Optional[Dict[str, Any]] = None):
    reg = _solvers()
    if solver_id not in reg:
        raise KeyError(f"unknown solver: {solver_id!r}. "
                       f"Choose one of: {', '.join(sorted(reg))}")
    params = params or {}
    factory = reg[solver_id][0]

    # Light special-casing for parameterized factories
    if solver_id == "outlier_iqr_flag" and params.get("k") is not None:
        from distillation.software1_solver.solvers import data_governance
        return data_governance.get_outlier_iqr_solver(float(params["k"]))
    if solver_id == "distribution_histogram" and params.get("n_bins") is not None:
        from distillation.software1_solver.solvers import descriptive_stats
        return descriptive_stats.get_histogram_solver(int(params["n_bins"]))
    if solver_id == "normality_test" and params.get("alpha") is not None:
        from distillation.software1_solver.solvers import normality_test as nt
        return nt.get_solver(float(params["alpha"]))
    if solver_id == "association_rules":
        from distillation.software1_solver.solvers import association_rules as ar
        return ar.get_solver(
            float(params.get("min_support", 0.05)),
            float(params.get("min_confidence", 0.3)),
        )
    #处理组合算子
    def _truthy(v, default=True):
        if v is None:
            return default
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    if solver_id == "probe_to_gene_collapse":
        from distillation.software1_solver.solvers.bio import probe_to_gene as p2g
        return p2g.get_solver(
            method=str(params.get("method") or "max"),
            gene_symbol_col=str(params.get("gene_symbol_col") or "Gene symbol"),
        )
    if solver_id == "limma_deg_two_group":
        from distillation.software1_solver.solvers.bio import limma_deg as ld
        return ld.get_solver(
            moderation=_truthy(params.get("moderation"), True),
            group_field=str(params.get("group_field") or "group_description"),
        )
    if solver_id == "pca_decompose":
        from distillation.software1_solver.solvers.bio import pca_decomposition as pca
        return pca.get_solver(
            standardize=_truthy(params.get("standardize"), True),
        )
    if solver_id == "hclust_samples":
        from distillation.software1_solver.solvers.bio import hierarchical_cluster as hc
        return hc.get_solver(
            method=str(params.get("method") or "average"),
            metric=str(params.get("metric") or "correlation"),
            n_clusters=int(params.get("n_clusters") or 2),
        )
    if solver_id == "pathway_enrichment_fisher":
        from distillation.software1_solver.solvers.bio import pathway_enrichment as pe
        return pe.get_solver(
            top_k=int(params.get("top_k") or 200),
            case_insensitive=_truthy(params.get("case_insensitive"), True),
            min_overlap=int(params.get("min_overlap") or 2),
            gene_set_db_path=params.get("gene_set_db_path") or None,
        )
    if solver_id == "probe_deg_collapse_to_gene":
        from distillation.software1_solver.solvers.bio import probe_deg_collapse as pdc
        return pdc.get_solver(
            drop_unmapped=_truthy(params.get("drop_unmapped"), True),
        )

    try:
        return factory()
    except Exception as e:
        raise RuntimeError(f"failed to instantiate {solver_id!r}: {e}") from e
