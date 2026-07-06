"""V8 operator additions (W19-W28).

10 new solvers added in V8 to fix the operator whitelist bias documented
in docs/v8_AGENT_DESIGN.md §0.5 / §B:

    W19  network_meta_analysis        — multi-treatment comparison (B1)
    W20  irt_calibration              — item response theory (B15)
    W21  latent_growth_curve          — longitudinal trajectory (B/A8)
    W22  prs_x_env_interaction        — PRS × environment (B5)
    W23  bayesian_hierarchical_glm    — group shrinkage (B8)
    W24  ordinal_regression           — Likert / proportional-odds (B15)
    W25  g_formula_tmle               — causal inference, RWE (B11)
    W26  symptom_network_analysis     — partial-correlation network (B15)
    W27  joint_longitudinal_survival  — longitudinal + Cox joint (A5+A8)
    W28  disparate_impact_audit       — fairness audit (auto-run)

V8.1 — June 2026 operator-gap closure (covers QRData causal-discovery
and IV families + RDAB time-series):

    W29  instrumental_variable_2sls   — IV / 2SLS causal estimator
    W30  causal_discovery_pc          — PC algorithm CPDAG discovery
    W31  causal_discovery_lingam      — DirectLiNGAM fully-oriented DAG
    W32  survival_kaplan_meier        — KM curves + log-rank
    W33  ts_arima_forecast            — auto-ARIMA (BIC) forecast + PI

V8.2 — June 2026 bio operators (transcriptomics over CSV/Excel
inputs; no PyTorch dependency; required by GenoMAS / BioAgent Bench
tabular workflows):

    B1   differential_expression_limma — DESeq2 NB-Wald / Welch t-test
                                          per-gene DE on bulk RNA-seq
    B2   pathway_enrichment_ora        — hypergeometric / Fisher's
                                          exact ORA on a hit list
    B3   gsea_preranked                — pre-ranked GSEA (gseapy)
    B4   gene_set_score                — single-sample ssGSEA scoring
    B5   lasso_cv_select               — multivariate L1 gene selection
                                          (sklearn LassoCV / LogReg-CV)

V8.3 — June 2026 GenoTEX paper alignment (tools/statistics.py
parity; ALL tabular in/out, no R/PyTorch deps):

    B6   batch_effect_detect           — PCA eigenvalue-gap detector
                                          (port of paper L152-179)
    B7   lmm_select                    — batch-adjusted L1 gene
                                          selection (frequentist
                                          two-step approx of
                                          sparse_lmm.LMM)
    B8   residualization_regress       — covariate-adjusted L1 gene
                                          selection (port of paper
                                          ResidualizationRegressor
                                          L182-260, conditional GTA)
"""
