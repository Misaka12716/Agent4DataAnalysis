"""Solver registry for the interactive pipeline demo.

Maps stable string ids → ``get_solver`` factories (and optional kwargs).
All implementation lives under ``operator_library.solvers``.

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
    from operator_pipeline import solver_overlays as _overlays
    from operator_library.solvers import (
        association_rules,
        causal_pair_test,
        column_stat,
        correlation,
        cox_regression,
        data_governance,
        data_quality_check,
        descriptive_stats,
        encode_categorical,
        groupby_stat,
        hypothesis_tests,
        knn_classifier,
        linear_regression,
        logistic_regression,
        metadata_parser,
        multiple_correction,
        normality_test,
        panss_factor_score,
        panss_trajectory_responder,
        proportion_ci,
        reference_range_flag,
        risk_difference_ci,
        svm_classifier,
        text_features,
        time_series_features,
        time_series_lag,
        tree_models,
        propensity_score_matching,
        radar_typed_task,
    )
    from operator_library.solvers.bio import (
        mediation_analysis as _bio_med,
        pgx_interaction as _bio_pgx,
        mendelian_randomization as _bio_mr,
        edger as _bio_edger,
        deseq2 as _bio_deseq2,
        combat_batch_correction as _bio_combat,
        
        soft_parser as _bio_soft,
        probe_to_gene as _bio_p2g,
        limma_deg as _bio_limma,
        pca_decomposition as _bio_pca,
        hierarchical_cluster as _bio_hc,
        pathway_enrichment as _bio_enrich,
        probe_deg_collapse as _bio_pdc,
    )
    from operator_library.solvers.cheminformatics import (
        morgan_fingerprint as _chem_mfp,
        molecular_descriptors as _chem_mdesc,
        substructure_filter as _chem_sf,
        tanimoto_similarity as _chem_ts,
        molecular_property_predict as _chem_mpp,
    )
    from operator_library.solvers.biosignal import (
        ecg_hrv_analysis as _bio_ecg,
        eda_analysis as _bio_eda,
        eog_analysis as _bio_eog,
        event_related_analysis as _bio_erp,
    )
    from operator_library.solvers.single_cell import (
        gene_filter_normalize as _sc_gfn,
        highly_variable_genes as _sc_hvg,
        dim_reduction as _sc_dr,
        clustering as _sc_cl,
        marker_genes as _sc_mg,
    )
    from operator_library.solvers import (
        feature_selection as _gen_fs,
        normalize_scale as _gen_ns,
    )

    # V8 additions — see docs/v8_AGENT_DESIGN.md §B.1
    from operator_library.solvers.v8 import (
        network_meta_analysis as _v8_nma,
        irt_calibration as _v8_irt,
        latent_growth_curve as _v8_lgcm,
        prs_x_env_interaction as _v8_prsenv,
        bayesian_hierarchical_glm as _v8_bayes,
        ordinal_regression as _v8_ord,
        g_formula_tmle as _v8_tmle,
        symptom_network_analysis as _v8_net,
        joint_longitudinal_survival as _v8_joint,
        disparate_impact_audit as _v8_fair,
        # V8.1 — operator-gap closure (W29..W33).
        instrumental_variable_2sls as _v8_iv2sls,
        causal_discovery_pc as _v8_pc,
        causal_discovery_lingam as _v8_lingam,
        survival_kaplan_meier as _v8_km,
        ts_arima_forecast as _v8_arima,
        # V8.2 — bio transcriptomics (B1..B5).
        differential_expression_limma as _v8_de,
        pathway_enrichment_ora as _v8_ora,
        gsea_preranked as _v8_gsea,
        gene_set_score as _v8_ssgsea,
        lasso_cv_select as _v8_lasso,
        # V8.3 — bio transcriptomics gap-closure (B6..B8).
        batch_effect_detect as _v8_bed,
        lmm_select as _v8_lmm,
        residualization_regress as _v8_resid,
    )
    return {
        "missing_summary": (lambda: data_governance.get_missing_summary_solver(),
                           "缺失率汇总（每列 n_missing / missing_rate / n_unique）"),
        "data_imputation": (lambda: data_governance.get_data_imputation_solver(),
                             "多策略缺失/占位符处理：method=median/mean/mode/"
                             "constant/ffill/bfill/drop_row/none，自动识别 "
                             "-9999 等 sentinel；求描述统计请用 drop_row。"),
        "outlier_iqr_flag": (lambda: data_governance.get_outlier_iqr_solver(1.5),
                             "IQR 异常值标记（Tukey fences）"),
        "describe_full": (lambda: descriptive_stats.get_describe_solver(),
                          "描述性统计（均值/标准差/分位数/偏度/峰度/IQR/MAD）"),
        "distribution_histogram": (lambda: descriptive_stats.get_histogram_solver(20),
                                    "等距直方图（长表：bin_left/bin_right/count/density）"),
        "column_stat": (lambda: column_stat.get_solver(),
                          "单列单值统计：mean/median/q{N}/proportion_in_range/top_k_value/"
                          "mode/sum/count/std/var/min/max；支持 subset_query 与 weight_col。"),
        "groupby_stat": (lambda: groupby_stat.get_solver(),
                          "按一列分组对另一列求一个统计量；stat 同 column_stat；输出每组一行。"),
        "radar_typed_task": (lambda: radar_typed_task.get_solver(),
                             "RADAR 任务级带类型合同算子；通过 task_id 选择任务，输出标量答案 JSON 和合同诊断。"),
        "proportion_ci": (lambda: proportion_ci.get_solver(),
                            "二项比例置信区间（Wilson/Wald/精确）；可吃 0/1 列或直接传 n,k。"),
        "linear_regression": (lambda: linear_regression.get_solver(),
                                "连续结局 OLS 回归；输出系数表+95%CI+R²/RMSE。"),
        "risk_difference_ci": (lambda: risk_difference_ci.get_solver(),
                                 "RD/RR/OR + 95%CI；可吃逐行二元列或 2x2 cell counts。"),
        "encode_categorical": (lambda: encode_categorical.get_solver(),
                                 "分类列编码（auto/onehot/label）；不传列时自动检测 object/category。"),
        "causal_pair_test": (lambda: causal_pair_test.get_solver(),
                               "两变量因果方向筛查（Granger/lagged corr）；时间序列友好。"),
        "time_series_lag": (lambda: time_series_lag.get_solver(),
                              "为某列追加 lag_K 列；支持按 id 分组、按 time 排序。"),
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
                                 "逻辑回归 + 分层 CV (输出预测 CSV + V8 Pattern E bootstrap CI)"),
        "random_forest": (lambda: tree_models.get_random_forest_solver(),
                          "随机森林 + CV (含 V8 Pattern E bootstrap CI: 指标 + 特征重要度)"),
        "hist_gradient_boosting": (lambda: tree_models.get_hist_gbdt_solver(),
                                    "sklearn HistGradientBoosting + CV (V8 Pattern E bootstrap CI)"),
        "xgboost": (lambda: tree_models.get_xgboost_solver(),  # may raise if missing
                    "XGBoost + CV (V8 Pattern E bootstrap CI; 需 xgboost)"),
        "lightgbm": (lambda: tree_models.get_lightgbm_solver(),
                     "LightGBM + CV (V8 Pattern E bootstrap CI; 需 lightgbm)"),
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
                                  "[ONLY for GEO SOFT pipeline: needs a SEPARATE "
                                  "expression_long_csv (gene × sample long-form) "
                                  "AND a SEPARATE sample_groups_csv mapping sample→"
                                  "group_description.  DO NOT use on a single sample×"
                                  "gene table with a binary group column — use "
                                  "differential_expression_limma instead]  Two-group "
                                  "moderated-t DE (Smyth empirical-Bayes shrinkage)."),
        "pca_decompose": (lambda: _bio_pca.get_solver(),
                            "样本×基因表达矩阵 PCA"),
        "hclust_samples": (lambda: _bio_hc.get_solver(),
                             "样本层次聚类（默认 correlation+average，可切 ward+euclidean）"),
        "pathway_enrichment_fisher": (lambda: _bio_enrich.get_solver(),
                                         "[Older GEO-SOFT-pipeline ORA: needs a DEG "
                                         "table in the GEO SOFT pipeline's specific "
                                         "schema.  For a generic DE table (gene_id + "
                                         "adj_p_value + log2FoldChange) prefer "
                                         "pathway_enrichment_ora.]  Hypergeometric / "
                                         "Fisher's exact pathway enrichment with "
                                         "bundled MSigDB Hallmark 2020."),
        "mediation_analysis": (lambda: _bio_med.get_solver(), "中介分析 (Baron-Kenny + Bootstrap)"),
        "pgx_interaction": (lambda: _bio_pgx.get_solver(), "药物基因组交互检验 (drug x SNP scan)"),
        "mendelian_randomization": (lambda: _bio_mr.get_solver(), "孟德尔随机化 (IVW/MR-Egger/加权中位数)"),
        "edger_de": (lambda: _bio_edger.get_solver(),
                       "[ONLY for GEO SOFT pipeline: needs SEPARATE counts long-form "
                       "+ sample_groups CSV.  For a single sample×gene table use "
                       "differential_expression_limma]  edgeR DE (TMM+QLF)."),
        "deseq2_de": (lambda: _bio_deseq2.get_solver(),
                       "[ONLY for GEO SOFT pipeline: needs SEPARATE counts long-form "
                       "+ sample_groups CSV.  For a single sample×gene table use "
                       "differential_expression_limma (which also wraps PyDESeq2)]  "
                       "DESeq2 NB-Wald."),
        "combat_batch_correction": (lambda: _bio_combat.get_solver(), "ComBat/ComBat_seq 批次校正"),
        "probe_deg_collapse_to_gene": (lambda: _bio_pdc.get_solver(),
                                          "把 probe 级 DEG 表按 min(adj_p) 取每个基因最佳 probe 收敛到基因级（GEO2R 推荐做法）"),
        # ---- Cheminformatics (rdkit) ----
        "morgan_fingerprint": (lambda: _chem_mfp.get_solver(),
                               "Morgan/ECFP4 分子指纹 (SMILES -> 2048-bit binary vector); 输出 fingerprints.csv + stats.json"),
        "molecular_descriptors": (lambda: _chem_mdesc.get_solver(),
                                   "分子描述符计算 (SMILES -> 26 RDKit descriptors: MolWt, MolLogP, TPSA, etc.); 输出 descriptors.csv"),
        "substructure_filter": (lambda: _chem_sf.get_solver("pains_brenk"),
                                 "PAINS/Brenk 子结构过滤 (移除 undesirable compounds); 输出 clean.csv + flagged.csv + filter_stats.json"),
        "tanimoto_similarity": (lambda: _chem_ts.get_solver(),
                                 "Tanimoto/Jaccard 分子相似度矩阵; 输入 SMILES 或 fingerprint CSV; 输出 similarity_matrix.csv + pairs.csv"),
        "molecular_property_predict": (lambda: _chem_mpp.get_solver(),
                                        "QSAR 分子性质预测 (训练+预测): SMILES→ECFP 或数值特征, 自动判别分类/回归, "
                                        "sklearn (RF 默认, 不需 deepchem), 有 holdout test 则训练后预测、否则给无泄漏 CV 预测; "
                                        "输出 property_predictions.csv + property_metrics.json"),
        # ---- Biosignal (neurokit2) ----
        "ecg_hrv_analysis": (lambda: _bio_ecg.get_solver(),
                              "ECG 心率变异性分析 (RMSSD/SDNN/pNN50/LF/HF); 输出 hrv_metrics.csv + hrv_timecourse.csv"),
        "eda_analysis": (lambda: _bio_eda.get_solver(),
                          "皮肤电活动分析 (EDA/GSR tonic/phasic 分解 + SCR 峰值); 输出 eda_metrics.csv + eda_peaks.csv"),
        "eog_analysis": (lambda: _bio_eog.get_solver(),
                          "眼电信号分析 (EOG blink detection + blink rate); 输出 eog_metrics.csv + eog_events.csv"),
        "event_related_analysis": (lambda: _bio_erp.get_solver(),
                                    "事件相关 biosignal epoch 提取与分析; 输出 epochs.csv + epoch_stats.csv"),
        # ---- Single-cell (scanpy) ----
        "gene_filter_normalize": (lambda: _sc_gfn.get_solver(),
                                   "单细胞 RNA-seq QC + 归一化 (filter_genes/cells + normalize_total + log1p); 输出 normalized.h5ad"),
        "highly_variable_genes": (lambda: _sc_hvg.get_solver(),
                                   "高变基因选择 (seurat_v3/seurat); 输出 hvg.h5ad + hvg_list.csv"),
        "sc_dim_reduction": (lambda: _sc_dr.get_solver(),
                              "单细胞降维 (PCA + UMAP/t-SNE); 输出 reduced.h5ad + embeddings.csv + variance.csv"),
        "sc_clustering": (lambda: _sc_cl.get_solver(),
                           "单细胞 Leiden/Louvain 聚类 (KMeans 兜底); 输出 clustered.h5ad + clusters.csv"),
        "sc_marker_genes": (lambda: _sc_mg.get_solver(),
                             "单细胞 marker 基因发现 (rank_genes_groups, Wilcoxon/t-test/logreg); 输出 markers_long.csv + top_markers.csv"),
        # ---- General-purpose new operators ----
        "feature_selection": (lambda: _gen_fs.get_solver(),
                               "特征选择 (SFS forward/backward + RF importance + mutual info); 输出 selected_features.csv + selection_report.json"),
        "normalize_scale": (lambda: _gen_ns.get_solver(),
                             "特征缩放/归一化 (z-score/minmax/robust/maxabs); 输出 scaled.csv + scaler_params.json"),

        # ---- V8 W19..W28 ----
        "network_meta_analysis": (lambda: _v8_nma.get_solver(),
                                   "频率学派随机效应 NMA (Lu-Ades + DerSimonian-Laird); 输出 league table + SUCRA 排名"),
        "irt_calibration": (lambda: _v8_irt.get_solver(),
                             "2-参数 IRT 项目反应理论 (girth/Bock-Aitkin EM); 输出 a/b 参数 + 受试 theta + Mantel-Haenszel DIF"),
        "latent_growth_curve": (lambda: _v8_lgcm.get_solver(),
                                 "潜变量增长曲线模型 (LMM 随机截距+斜率, REML); 含 GMM 轨迹分类"),
        "prs_x_env_interaction": (lambda: _v8_prsenv.get_solver(),
                                   "PRS × 环境因素交互效应回归; 输出主效+交互+ simple slopes + LR 检验"),
        "bayesian_hierarchical_glm": (lambda: _v8_bayes.get_solver(),
                                       "经验贝叶斯分层正态模型 (James-Stein/Morris 1983 收缩估计); 小亚组分析"),
        "ordinal_regression": (lambda: _v8_ord.get_solver(),
                                "比例几率模型 (累积 logit, statsmodels.OrderedModel); 含 Brant 检验"),
        "g_formula_tmle": (lambda: _v8_tmle.get_solver(),
                            "因果推断 ATE (G-formula + IPTW + TMLE 三估计器; zepid 后端)"),
        "symptom_network_analysis": (lambda: _v8_net.get_solver(),
                                      "症状网络分析 (Graphical Lasso 偏相关网络 + 中心性 + 桥梁强度)"),
        "joint_longitudinal_survival": (lambda: _v8_joint.get_solver(),
                                         "纵向+生存联合模型 (Tsiatis 两阶段近似: LMM + Cox 基线/斜率 BLUP)"),
        "disparate_impact_audit": (lambda: _v8_fair.get_solver(),
                                    "公平性审计: DPD + DI 4/5 法则 + 等机会差 + Brier 校准差 (fairlearn 后端)"),
        # ---- V8.1 W29..W33: operator-gap closure ----
        "instrumental_variable_2sls": (lambda: _v8_iv2sls.get_solver(),
                                        "工具变量 2SLS 因果估计 (linearmodels.IV2SLS)；"
                                        "输出系数+95%CI+一阶段F+Wu-Hausman 内生性检验；"
                                        "适用：连续 outcome + 内生 treatment + ≥1 工具变量。"),
        "causal_discovery_pc": (lambda: _v8_pc.get_solver(),
                                 "PC 算法因果 DAG 发现 (Spirtes-Glymour, causal-learn 后端)；"
                                 "返回 CPDAG (Markov 等价类)+邻接矩阵+有向/无向边；"
                                 "适用：观测数据 ≥3 个连续变量、需要图骨架。"),
        "causal_discovery_lingam": (lambda: _v8_lingam.get_solver(),
                                     "DirectLiNGAM 完全定向 DAG 发现 (Shimizu 2011, lingam 后端)；"
                                     "返回因果序+加权邻接矩阵+边表；"
                                     "适用：线性关系 + 非高斯噪声、需要唯一边方向。"),
        "survival_kaplan_meier": (lambda: _v8_km.get_solver(),
                                   "Kaplan-Meier 非参生存曲线 + 中位生存 + log-rank 双组比较 "
                                   "(lifelines)；适用：只看曲线/中位/两组检验，不调协变量。"),
        "ts_arima_forecast": (lambda: _v8_arima.get_solver(),
                               "ARIMA 自动定阶预测 (BIC 网格 + ADF/KPSS 选 d, statsmodels)；"
                               "输出 h 步预测均值+95% PI+Ljung-Box 残差检验；"
                               "适用：单变量时间序列预测且需要不确定度带。"),
        # ---- V8.2 B1..B4: transcriptomics (RNA-seq / microarray) ----
        "differential_expression_limma": (lambda: _v8_de.get_solver(),
                                            "Bulk RNA-seq 差异表达 (univariate per-gene)。"
                                            "输入：sample×gene 表 (id+trait+基因表达列)；"
                                            "二元 trait → PyDESeq2 NB-Wald (默认 method=auto，整数 counts 自动用) 或 "
                                            "Welch t-test on log2(x+1)；"
                                            "连续 trait (≥3 数值水平，自动识别 '1.55 cm'/'1,55' 等) → "
                                            "Spearman ρ 相关 (per-gene)，t-stat + p + BH-FDR；"
                                            "输出每基因 log2FC（连续模式为 ρ） + p + BH-FDR；"
                                            "适用：univariate per-gene 显著性测试。"
                                            "如果题目要求多变量联合选基因 (Lasso 等)，请改用 lasso_cv_select。"),
        "pathway_enrichment_ora": (lambda: _v8_ora.get_solver(),
                                    "通路过表达分析 ORA (超几何/Fisher 精确检验)；"
                                    "输入：DE 表 (gene_id + adj_p_value + log2FoldChange)；"
                                    "自动从 adj_p<阈值 抽 hit 名单，对每个 gene set 做超几何；"
                                    "输出 p+BH-FDR+odds ratio+overlap genes；"
                                    "适用：DE 之后查 KEGG/Hallmark/GO 通路富集。"),
        "gsea_preranked": (lambda: _v8_gsea.get_solver(),
                            "预排序 GSEA (Subramanian 2005, gseapy.prerank 后端)；"
                            "输入：整条排好序的 (gene_id, score) 列表 (score 通常是 log2FC)；"
                            "输出每通路 ES+NES+nominal p+FDR q+leading edge；"
                            "适用：用全部基因信号 (比 ORA 强)，找 top/bottom 富集的通路。"),
        "gene_set_score": (lambda: _v8_ssgsea.get_solver(),
                            "单样本通路评分 ssGSEA (Barbie 2009)；"
                            "输入：sample×gene 表达矩阵；输出 sample×pathway 评分矩阵；"
                            "无需 phenotype 标签，适用：通路评分做下游聚类/相关/分层。"),
        "lasso_cv_select": (lambda: _v8_lasso.get_solver(),
                              "多变量 L1 (Lasso) 基因/特征联合选择 (sklearn LassoCV / L1-LogisticRegressionCV)。"
                              "输入：sample×gene 表 (id+target+基因表达列)，target 可二元/连续；"
                              "默认 drop Age/Gender 协变量（按 paper regress.py L24 约定）；"
                              "自动 StandardScaler 标准化 → CV 选 alpha (默认网格 [1e-6..1])；"
                              "α 顶到网格边界且 nnz=0 时自动退一档重试 + univariate fallback；"
                              "输出非零系数基因，按 |coef| 排序，含 rank。"
                              "适用：unconditional GTA 题；question 要求多变量联合 / 稀疏 / parsimonious / "
                              "Lasso 风格的基因 panel；feature 数 ≫ 样本数；或需要与 GenoTEX paper "
                              "(LassoCV ground truth) 对齐时。"
                              "对比：differential_expression_limma = per-gene 独立 univariate；"
                              "lasso_cv_select = 所有基因联合多变量 L1；"
                              "lmm_select = lasso_cv_select 的 batch-aware 版（有 batch 效应时用）；"
                              "residualization_regress = lasso_cv_select 的 condition-aware 版"
                              "（conditional GTA 题 / 有 covariate Z 要先剥离时用）。"),
        # ---- V8.3 B6..B8: transcriptomics gap-closure ----
        "batch_effect_detect": (lambda: _v8_bed.get_solver(),
                                  "PCA 特征值 gap 检测 batch / platform 效应 (paper "
                                  "statistics.py L152-179 的 detect_batch_effect 移植)。"
                                  "输入：sample×gene 表；自动 drop trait/id/Age/Gender；"
                                  "对中心化的 XXᵀ 求 top-10 特征值，按最大值归一化，"
                                  "任一连续 gap > 200/n_samples 即判定 has_batch_effect=True。"
                                  "输出 JSON：{has_batch_effect, max_gap, threshold, "
                                  "eigenvalues_normalised, ...}。"
                                  "适用：在跑 lasso_cv_select / lmm_select 之前先做这一步决定走哪个；"
                                  "paper regress.py L29-33 的路由信号。"),
        "lmm_select": (lambda: _v8_lmm.get_solver(),
                         "batch 调整后的多变量 L1 基因选择 (frequentist 两步近似 paper sparse_lmm.LMM)。"
                         "Step1：将 trait y 对 batch 哑变量做 OLS 取残差 y_resid；"
                         "Step2：在 (X, y_resid) 上跑 LassoCV / L1-LogisticRegressionCV。"
                         "batch_strategy: 'explicit' 用 batch_col；'pca1_quantile' (默认) 用 PC1 分位数代理；"
                         "'none' 退化为 lasso_cv_select。"
                         "同样默认 drop Age/Gender，含 α-边界自适应 + univariate fallback。"
                         "适用：batch_effect_detect 标记 has_batch_effect=True，"
                         "或已知数据来自多 platform/cohort/scan-date 的 GTA 任务。"),
        "residualization_regress": (lambda: _v8_resid.get_solver(),
                                       "covariate 调整后的多变量 L1 基因选择 (paper ResidualizationRegressor "
                                       "statistics.py L182-260 的等价实现)。"
                                       "Step1：对 [1|Z] 做闭式 pinv OLS 拟合 y，取残差 e_Y = y − ŷ；"
                                       "Step2：在 (X, e_Y) 上跑 LassoCV / L1-LogisticRegressionCV。"
                                       "Z 支持单列 / 多列；自动按 binary/continuous/categorical 编码；"
                                       "condition_cols 缺省时退化为 lasso_cv_select。"
                                       "适用：conditional GTA 题（Height|Age, Cancer|Gender, "
                                       "Diabetes|Hypertension 等），需要去掉 covariate 影响只看基因主效应。"),
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
    if solver_id == "outlier_iqr_flag" and (
        params.get("k") is not None
        or params.get("sentinel_values") is not None
    ):
        from operator_library.solvers import data_governance
        k = float(params["k"]) if params.get("k") is not None else 1.5
        return data_governance.get_outlier_iqr_solver(
            k=k,
            sentinel_values=params.get("sentinel_values"),
        )
    if solver_id == "data_imputation" and (
        params.get("method") is not None
        or params.get("constant_value") is not None
        or params.get("sentinel_values") is not None
    ):
        from operator_library.solvers import data_governance
        return data_governance.get_data_imputation_solver(
            method=str(params.get("method") or "median"),
            constant_value=params.get("constant_value"),
            sentinel_values=params.get("sentinel_values"),
        )
    if solver_id == "distribution_histogram" and (
        params.get("n_bins") is not None
        or params.get("bin_range") is not None
    ):
        from operator_library.solvers import descriptive_stats
        # V8 Phase 2 §3.3: bin_range 是新加的可选 static_param
        # ([low, high])；planner 给出后传到工厂。
        br = params.get("bin_range")
        if br is not None:
            try:
                br = (float(br[0]), float(br[1]))
            except Exception:
                br = None
        return descriptive_stats.get_histogram_solver(
            n_bins=int(params.get("n_bins") or 20),
            bin_range=br,
        )
    # _truthy is needed by some special-case factory branches below; define
    # it up-front so the order of branches doesn't depend on it.
    def _truthy(v, default=True):
        if v is None:
            return default
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    if solver_id == "column_stat":
        from operator_library.solvers import column_stat as _cs
        _k = params.get("k")
        return _cs.get_solver(
            stat=str(params.get("stat") or "mean"),
            subset_query=params.get("subset_query") or None,
            value_min=(float(params["value_min"])
                       if params.get("value_min") is not None else None),
            value_max=(float(params["value_max"])
                       if params.get("value_max") is not None else None),
            k=(int(_k) if _k is not None else None),
        )
    if solver_id == "groupby_stat":
        from operator_library.solvers import groupby_stat as _gs
        _k = params.get("k")
        return _gs.get_solver(
            stat=str(params.get("stat") or "mean"),
            subset_query=params.get("subset_query") or None,
            value_min=(float(params["value_min"])
                       if params.get("value_min") is not None else None),
            value_max=(float(params["value_max"])
                       if params.get("value_max") is not None else None),
            k=(int(_k) if _k is not None else None),
        )
    if solver_id == "radar_typed_task":
        from operator_library.solvers import radar_typed_task as _rtt
        return _rtt.get_solver(task_id=str(params.get("task_id") or ""))
    if solver_id == "proportion_ci":
        from operator_library.solvers import proportion_ci as _pc
        _nt = params.get("n_trials")
        _ns = params.get("n_successes")
        return _pc.get_solver(
            alpha=float(params.get("alpha") or 0.05),
            method=str(params.get("method") or "wilson"),
            n_trials=(int(_nt) if _nt is not None else None),
            n_successes=(int(_ns) if _ns is not None else None),
            subset_query=params.get("subset_query") or None,
        )
    if solver_id == "linear_regression":
        from operator_library.solvers import linear_regression as _lr
        return _lr.get_solver(
            add_intercept=_truthy(params.get("add_intercept"), True),
            robust_se=(str(params["robust_se"])
                       if params.get("robust_se") else None),
            standardize_features=_truthy(
                params.get("standardize_features"), False),
        )
    if solver_id == "risk_difference_ci":
        from operator_library.solvers import risk_difference_ci as _rd
        return _rd.get_solver(
            alpha=float(params.get("alpha") or 0.05),
            n_treated_event=params.get("n_treated_event"),
            n_treated_no_event=params.get("n_treated_no_event"),
            n_control_event=params.get("n_control_event"),
            n_control_no_event=params.get("n_control_no_event"),
            haldane_correction=_truthy(
                params.get("haldane_correction"), True),
        )
    if solver_id == "encode_categorical":
        from operator_library.solvers import encode_categorical as _ec
        return _ec.get_solver(
            method=str(params.get("method") or "auto"),
            max_onehot_cardinality=int(params.get("max_onehot_cardinality") or 5),
            drop_first=_truthy(params.get("drop_first"), False),
        )
    if solver_id == "causal_pair_test":
        from operator_library.solvers import causal_pair_test as _cp
        return _cp.get_solver(
            method=str(params.get("method") or "auto"),
            max_lag=int(params.get("max_lag") or 3),
            alpha=float(params.get("alpha") or 0.05),
        )
    if solver_id == "time_series_lag":
        from operator_library.solvers import time_series_lag as _tsl
        _lags = params.get("lags")
        if _lags is None:
            _lags = [1]
        try:
            _lags = [int(x) for x in _lags]
        except Exception:
            _lags = [1]
        return _tsl.get_solver(
            lags=_lags,
            fill_value=params.get("fill_value"),
        )
    if solver_id == "normality_test" and params.get("alpha") is not None:
        from operator_library.solvers import normality_test as nt
        return nt.get_solver(float(params["alpha"]))
    if solver_id == "association_rules":
        from operator_library.solvers import association_rules as ar
        return ar.get_solver(
            float(params.get("min_support", 0.05)),
            float(params.get("min_confidence", 0.3)),
        )
    #处理组合算子
    if solver_id == "probe_to_gene_collapse":
        from operator_library.solvers.bio import probe_to_gene as p2g
        return p2g.get_solver(
            method=str(params.get("method") or "max"),
            gene_symbol_col=str(params.get("gene_symbol_col") or "Gene symbol"),
        )
    if solver_id == "limma_deg_two_group":
        from operator_library.solvers.bio import limma_deg as ld
        return ld.get_solver(
            moderation=_truthy(params.get("moderation"), True),
            group_field=str(params.get("group_field") or "group_description"),
        )
    if solver_id == "pca_decompose":
        from operator_library.solvers.bio import pca_decomposition as pca
        return pca.get_solver(
            standardize=_truthy(params.get("standardize"), True),
        )
    if solver_id == "hclust_samples":
        from operator_library.solvers.bio import hierarchical_cluster as hc
        return hc.get_solver(
            method=str(params.get("method") or "average"),
            metric=str(params.get("metric") or "correlation"),
            n_clusters=int(params.get("n_clusters") or 2),
        )
    if solver_id == "pathway_enrichment_fisher":
        from operator_library.solvers.bio import pathway_enrichment as pe
        return pe.get_solver(
            top_k=int(params.get("top_k") or 200),
            case_insensitive=_truthy(params.get("case_insensitive"), True),
            min_overlap=int(params.get("min_overlap") or 2),
            gene_set_db_path=params.get("gene_set_db_path") or None,
        )
    if solver_id == "probe_deg_collapse_to_gene":
        from operator_library.solvers.bio import probe_deg_collapse as pdc
        return pdc.get_solver(
            drop_unmapped=_truthy(params.get("drop_unmapped"), True),
        )    # ---- new 6 bio solvers ----
    if solver_id == "mediation_analysis":
        from operator_library.solvers.bio import mediation_analysis as _ma
        return _ma.get_solver(
            n_bootstrap=int(params.get("n_bootstrap") or 1000),
            ci_level=float(params.get("ci_level") or 0.95),
            random_state=int(params.get("random_state") or 42),
        )
    if solver_id == "pgx_interaction":
        from operator_library.solvers.bio import pgx_interaction as _pgx
        return _pgx.get_solver(
            outcome_type=str(params.get("outcome_type") or "binary"),
            snp_coding=str(params.get("snp_coding") or "additive"),
            multiple_test=str(params.get("multiple_test") or "fdr_bh"),
            alpha=float(params.get("alpha") or 0.05),
        )
    if solver_id == "mendelian_randomization":
        from operator_library.solvers.bio import mendelian_randomization as _mr
        return _mr.get_solver(
            random_state=int(params.get("random_state") or 42),
        )
    if solver_id == "edger_de":
        from operator_library.solvers.bio import edger as _edger
        return _edger.get_solver(
            test_method=str(params.get("test_method") or "qlf"),
            group_field=str(params.get("group_field") or "group_description"),
            alpha=float(params.get("alpha") or 0.05),
            min_count=int(params.get("min_count") or 10),
            min_total_count=int(params.get("min_total_count") or 15),
        )
    if solver_id == "deseq2_de":
        from operator_library.solvers.bio import deseq2 as _deseq2
        return _deseq2.get_solver(
            alpha=float(params.get("alpha") or 0.05),
            lfc_threshold=float(params.get("lfc_threshold") or 0.0),
            independent_filtering=_truthy(params.get("independent_filtering"), True),
            min_count=int(params.get("min_count") or 10),
        )
    if solver_id == "combat_batch_correction":
        from operator_library.solvers.bio import combat_batch_correction as _cb
        return _cb.get_solver(
            data_type=str(params.get("data_type") or "microarray"),
            par_prior=_truthy(params.get("par_prior"), True),
            mean_only=_truthy(params.get("mean_only"), False),
        )

    # ---- V8 W19..W28 parametric factories ----
    if solver_id == "network_meta_analysis":
        from operator_library.solvers.v8 import network_meta_analysis as _nma
        return _nma.get_solver(
            reference_treatment=params.get("reference_treatment"),
            smaller_is_better=_truthy(params.get("smaller_is_better"), True),
            n_sim_sucra=int(params.get("n_sim_sucra") or 5000),
            random_state=int(params.get("random_state") or 42),
        )
    if solver_id == "irt_calibration":
        from operator_library.solvers.v8 import irt_calibration as _irt
        return _irt.get_solver(
            min_obs_per_item=int(params.get("min_obs_per_item") or 30),
        )
    if solver_id == "latent_growth_curve":
        from operator_library.solvers.v8 import latent_growth_curve as _lgcm
        return _lgcm.get_solver(
            gmm_k_grid=list(params.get("gmm_k_grid") or [1, 2, 3, 4]),
            min_obs_per_subject=int(params.get("min_obs_per_subject") or 3),
            random_state=int(params.get("random_state") or 42),
        )
    if solver_id == "prs_x_env_interaction":
        from operator_library.solvers.v8 import prs_x_env_interaction as _prs
        return _prs.get_solver(
            outcome_type=str(params.get("outcome_type") or "linear"),
            standardize_prs=_truthy(params.get("standardize_prs"), True),
            center_env=_truthy(params.get("center_env"), True),
            random_state=int(params.get("random_state") or 42),
        )
    if solver_id == "bayesian_hierarchical_glm":
        from operator_library.solvers.v8 import bayesian_hierarchical_glm as _bh
        return _bh.get_solver(
            min_group_size=int(params.get("min_group_size") or 2),
        )
    if solver_id == "ordinal_regression":
        from operator_library.solvers.v8 import ordinal_regression as _ord
        return _ord.get_solver(link=str(params.get("link") or "logit"))
    if solver_id == "g_formula_tmle":
        from operator_library.solvers.v8 import g_formula_tmle as _gt
        return _gt.get_solver(
            n_bootstrap=int(params.get("n_bootstrap") or 1000),
            random_state=int(params.get("random_state") or 42),
            trim_ps=float(params.get("trim_ps") or 0.02),
        )
    if solver_id == "symptom_network_analysis":
        from operator_library.solvers.v8 import symptom_network_analysis as _sn
        return _sn.get_solver(
            min_obs=int(params.get("min_obs") or 100),
            standardize=_truthy(params.get("standardize"), True),
        )
    if solver_id == "joint_longitudinal_survival":
        from operator_library.solvers.v8 import joint_longitudinal_survival as _jm
        return _jm.get_solver(
            min_obs_per_subject=int(params.get("min_obs_per_subject") or 2),
            random_state=int(params.get("random_state") or 42),
        )
    if solver_id == "disparate_impact_audit":
        from operator_library.solvers.v8 import disparate_impact_audit as _di
        return _di.get_solver(
            threshold=float(params.get("threshold") or 0.5),
            favorable_label=int(params.get("favorable_label") or 1),
            di_pass_ratio=float(params.get("di_pass_ratio") or 0.8),
        )

    # ---- Cheminformatics parametric factories ----
    if solver_id == "morgan_fingerprint":
        from operator_library.solvers.cheminformatics import morgan_fingerprint as _mfp
        return _mfp.get_solver(
            radius=int(params.get("radius") or 2),
            n_bits=int(params.get("n_bits") or 2048),
            use_features=_truthy(params.get("use_features"), False),
        )
    if solver_id == "molecular_property_predict":
        from operator_library.solvers.cheminformatics import molecular_property_predict as _mpp
        return _mpp.get_solver(
            model=str(params.get("model") or "auto"),
            task=str(params.get("task") or "auto"),
            test_csv=params.get("test_csv") or None,
            radius=int(params.get("radius") or 2),
            n_bits=int(params.get("n_bits") or 2048),
            cv_folds=int(params.get("cv_folds") or 5),
            random_state=int(params.get("random_state") or 42),
        )
    if solver_id == "substructure_filter":
        from operator_library.solvers.cheminformatics import substructure_filter as _sf
        return _sf.get_solver(filter_sets=str(params.get("filter_sets") or "pains_brenk"))
    if solver_id == "tanimoto_similarity":
        from operator_library.solvers.cheminformatics import tanimoto_similarity as _ts
        return _ts.get_solver(
            radius=int(params.get("radius") or 2),
            n_bits=int(params.get("n_bits") or 2048),
            min_similarity=float(params.get("min_similarity") or 0.0),
        )
    # ---- Biosignal parametric factories ----
    if solver_id == "ecg_hrv_analysis":
        from operator_library.solvers.biosignal import ecg_hrv_analysis as _ecg
        return _ecg.get_solver(
            sampling_rate=int(params.get("sampling_rate") or 250),
            window_seconds=int(params.get("window_seconds") or 300),
            overlap_seconds=int(params.get("overlap_seconds") or 30),
        )
    if solver_id == "eda_analysis":
        from operator_library.solvers.biosignal import eda_analysis as _eda
        return _eda.get_solver(sampling_rate=int(params.get("sampling_rate") or 250))
    if solver_id == "eog_analysis":
        from operator_library.solvers.biosignal import eog_analysis as _eog
        return _eog.get_solver(
            sampling_rate=int(params.get("sampling_rate") or 100),
            method=str(params.get("method") or "neurokit"),
        )
    if solver_id == "event_related_analysis":
        from operator_library.solvers.biosignal import event_related_analysis as _erp
        return _erp.get_solver(
            sampling_rate=int(params.get("sampling_rate") or 250),
            epoch_start_s=float(params.get("epoch_start_s") or -0.5),
            epoch_end_s=float(params.get("epoch_end_s") or 2.0),
        )
    # ---- Single-cell parametric factories ----
    if solver_id == "gene_filter_normalize":
        from operator_library.solvers.single_cell import gene_filter_normalize as _gfn
        return _gfn.get_solver(
            min_genes_per_cell=int(params.get("min_genes_per_cell") or 200),
            min_cells_per_gene=int(params.get("min_cells_per_gene") or 3),
            target_sum=float(params.get("target_sum") or 1e4),
        )
    if solver_id == "highly_variable_genes":
        from operator_library.solvers.single_cell import highly_variable_genes as _hvg
        return _hvg.get_solver(
            n_top_genes=int(params.get("n_top_genes") or 2000),
            flavor=str(params.get("flavor") or "seurat_v3"),
        )
    if solver_id == "sc_dim_reduction":
        from operator_library.solvers.single_cell import dim_reduction as _dr
        return _dr.get_solver(
            n_pcs=int(params.get("n_pcs") or 50),
            n_neighbors=int(params.get("n_neighbors") or 15),
            method=str(params.get("method") or "umap"),
        )
    if solver_id == "sc_clustering":
        from operator_library.solvers.single_cell import clustering as _cl
        return _cl.get_solver(
            resolution=float(params.get("resolution") or 0.8),
            method=str(params.get("method") or "leiden"),
            n_clusters_kmeans=int(params.get("n_clusters_kmeans") or 8),
            label_key=str(params.get("label_key") or "cluster"),
        )
    if solver_id == "sc_marker_genes":
        from operator_library.solvers.single_cell import marker_genes as _mg
        return _mg.get_solver(
            groupby=str(params.get("groupby") or "cluster"),
            method=str(params.get("method") or "wilcoxon"),
            n_top=int(params.get("n_top") or 10),
            use_raw=bool(params.get("use_raw") or False),
        )
    # ---- General-purpose parametric factories ----
    if solver_id == "feature_selection":
        from operator_library.solvers import feature_selection as _fs
        return _fs.get_solver(
            n_features_to_select=int(params.get("n_features_to_select") or 10),
            method=str(params.get("method") or "sfs_forward"),
            cv_folds=int(params.get("cv_folds") or 5),
            random_state=int(params.get("random_state") or 42),
        )
    if solver_id == "normalize_scale":
        from operator_library.solvers import normalize_scale as _ns
        return _ns.get_solver(method=str(params.get("method") or "standard"))

    # ---- V8.2/V8.3 transcriptomics-regression family ----
    # Each of these solvers exposes a rich keyword-only get_solver signature
    # (see operator_library/solvers/v8/{lasso_cv_select,lmm_select,
    # residualization_regress,batch_effect_detect}.py).  Without an explicit
    # branch here the planner/driver injected params would be silently
    # dropped by the generic `factory()` fallback below — that bug masked
    # the entire GenoTEX prior-informed alpha tuning + max_features cap
    # injection, so we wire them through verbatim.
    if solver_id == "lasso_cv_select":
        from operator_library.solvers.v8 import lasso_cv_select as _v8_lcs
        _alphas = params.get("alphas")
        _maxf = params.get("max_features")
        _cov = params.get("covariate_cols")
        _prior = params.get("prior_related_genes")
        return _v8_lcs.get_solver(
            alphas=list(_alphas) if _alphas else None,
            cv=int(params.get("cv") or 5),
            max_iter=int(params.get("max_iter") or 20000),
            max_features=(int(_maxf) if _maxf is not None else 1000),
            standardize=_truthy(params.get("standardize"), True),
            random_state=int(params.get("random_state") or 42),
            binary_backend=str(params.get("binary_backend") or "logistic"),
            drop_covariates=_truthy(params.get("drop_covariates"), True),
            covariate_cols=list(_cov) if _cov else None,
            alpha_boundary_adaptive=_truthy(
                params.get("alpha_boundary_adaptive"), True),
            univariate_fallback=_truthy(
                params.get("univariate_fallback"), True),
            fallback_top_k=int(params.get("fallback_top_k") or 50),
            alpha_tuning=str(params.get("alpha_tuning") or "cv"),
            prior_related_genes=list(_prior) if _prior else None,
        )
    if solver_id == "lmm_select":
        from operator_library.solvers.v8 import lmm_select as _v8_lmm
        _alphas = params.get("alphas")
        _maxf = params.get("max_features")
        _cov = params.get("covariate_cols")
        return _v8_lmm.get_solver(
            alphas=list(_alphas) if _alphas else None,
            cv=int(params.get("cv") or 5),
            max_iter=int(params.get("max_iter") or 20000),
            max_features=(int(_maxf) if _maxf is not None else 1000),
            standardize=_truthy(params.get("standardize"), True),
            random_state=int(params.get("random_state") or 42),
            binary_backend=str(params.get("binary_backend") or "logistic"),
            drop_covariates=_truthy(params.get("drop_covariates"), True),
            covariate_cols=list(_cov) if _cov else None,
            batch_strategy=str(params.get("batch_strategy")
                               or "pca1_quantile"),
            n_surrogate_batches=int(params.get("n_surrogate_batches") or 3),
            alpha_boundary_adaptive=_truthy(
                params.get("alpha_boundary_adaptive"), True),
            univariate_fallback=_truthy(
                params.get("univariate_fallback"), True),
            fallback_top_k=int(params.get("fallback_top_k") or 50),
        )
    if solver_id == "residualization_regress":
        from operator_library.solvers.v8 import (
            residualization_regress as _v8_rr,
        )
        _alphas = params.get("alphas")
        _maxf = params.get("max_features")
        _cov = params.get("covariate_cols")
        return _v8_rr.get_solver(
            alphas=list(_alphas) if _alphas else None,
            cv=int(params.get("cv") or 5),
            max_iter=int(params.get("max_iter") or 20000),
            max_features=(int(_maxf) if _maxf is not None else 1000),
            standardize=_truthy(params.get("standardize"), True),
            random_state=int(params.get("random_state") or 42),
            binary_backend=str(params.get("binary_backend") or "logistic"),
            drop_covariates_from_genes=_truthy(
                params.get("drop_covariates_from_genes"), True),
            covariate_cols=list(_cov) if _cov else None,
            alpha_boundary_adaptive=_truthy(
                params.get("alpha_boundary_adaptive"), True),
            univariate_fallback=_truthy(
                params.get("univariate_fallback"), True),
            fallback_top_k=int(params.get("fallback_top_k") or 50),
        )
    if solver_id == "batch_effect_detect":
        from operator_library.solvers.v8 import batch_effect_detect as _v8_bed
        _cov = params.get("covariate_cols")
        return _v8_bed.get_solver(
            gap_threshold_numerator=float(
                params.get("gap_threshold_numerator") or 200.0),
            top_k_eigvals=int(params.get("top_k_eigvals") or 10),
            drop_covariates=_truthy(params.get("drop_covariates"), True),
            covariate_cols=list(_cov) if _cov else None,
        )


    try:
        return factory()
    except Exception as e:
        raise RuntimeError(f"failed to instantiate {solver_id!r}: {e}") from e

