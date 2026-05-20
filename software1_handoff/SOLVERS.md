# Software 1 算子列表

本文档汇总 `software1_handoff` 当前在 **唯一注册表** 中登记的算子：`distillation/software1_pipeline_demo_app/registry.py`（函数 `_solvers()` / `list_solvers()`）。  
UI、JSON pipeline、`software1_agent` 等均只认可此处列出的 **算子 id**（须逐字匹配）。

**共计 39 个算子**（截至仓库当前版本）。

## 输入输出约定

以下字段来自各算子的 `SolverContract`（`distillation/software1_solver/contract.py`），与运行时 `make_solver(id).contract` 一致。
- **列映射（mapping）**：Planner / UI 产出的 JSON 里，每个 **role_key** 对应一类语义（`Role`）。多数情况下填 **DataFrame 中的列名字符串**；`numeric_list` / `item_group` 填 **列名数组**；`Role.PARAMS` 填 **Python 可 JSON 化的值**（文件路径字符串、`reference_ranges` 字典等），**不是**表头里的列名。
- **必填**：契约里 `optional=False` 的 role_key 必须在执行前解析到；`optional=True` 可由规则/LLM 省略，算子内部或 mapper 再补默认。
- **静态参数（static_params）**：构造期默认；pipeline / `make_solver` 的 `params` 可覆盖（见 `registry.make_solver`）。
- **输出**：`solver.run(..., output_dir)` 返回 **字典**，键名与下表「输出键」一致；值一般为写出文件的 **路径字符串**（相对 `output_dir` 或绝对路径，以实现为准）。契约中的 `output_files` 给出 **约定文件名**（磁盘上的 basename）。部分算子另在返回 dict 里附带 `*_dict` 等内存结构，调用方若以落盘为准，以下表文件为准即可。
- **`Role` 类型速查**（枚举值 → 含义）：
  - `binary_target`：0/1 或二分类目标列
  - `categorical`：分类列
  - `datetime`：时间列
  - `event_indicator`：事件示性 0/1
  - `id`：标识列（单列名）
  - `item_group`：有序条目列组（列名列表，如 P1..P7）
  - `numeric`：单列数值
  - `numeric_list`：多列数值（列名列表）
  - `numeric_target`：连续/多分类数值目标
  - `ordinal`：有序整数
  - `p_value`：p 值列（0..1）
  - `params`：非列名配置（路径、dict、超参等，写入 mapping）
  - `text`：文本列
  - `time_to_event`：生存时间（非负数值）

---

## 各算子输入输出明细

### `association_rules`

- **契约内名称**：`association_rules`
- **简介**：关联规则（FP-Growth）

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `items_col` | `text`（文本列） | 是 | ';'-separated antecedent items per row |
| `targets_col` | `text`（文本列） | 是 | ';'-separated consequent items per row |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| `min_confidence` | `0.3` |
| `min_support` | `0.05` |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `rules_csv` | `association_rules.csv` |

### `chi_square_independence`

- **契约内名称**：`chi_square_independence`
- **简介**：卡方独立性检验

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `col_col` | `categorical`（分类列） | 是 | second categorical column |
| `row_col` | `categorical`（分类列） | 是 | first categorical column |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| （无） | — |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `summary_json` | `chi2_summary.json` |
| `table_csv` | `contingency_table.csv` |

### `consistency_check`

- **契约内名称**：`consistency_check`
- **简介**：一致性检查（主键唯一/正则/值域/白名单）

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `allowed_values` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 否 | dict {col: [v1, v2, ...]} categorical |
| `id_col` | `id`（标识列（单列名）） | 否 | primary key column |
| `range_rules` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 否 | dict {col: (low, high)} numeric range |
| `regex_rules` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 否 | dict {col: regex} for str format check |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| （无） | — |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `issues_csv` | `consistency_issues.csv` |
| `summary_json` | `consistency_summary.json` |

### `cox_regression`

- **契约内名称**：`cox_regression`
- **简介**：Cox 比例风险模型

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `covariates` | `numeric_list`（多列数值（列名列表）） | 是 | all covariate columns (will be passed to Cox as-is) |
| `event_col` | `event_indicator`（事件示性 0/1） | 是 | 1 = event, 0 = censored |
| `id_col` | `id`（标识列（单列名）） | 否 | patient identifier |
| `stratify_col` | `binary_target`（0/1 或二分类目标列） | 否 | binary covariate to use for the log-rank / KM split (typically a treatment / intervention flag) |
| `time_col` | `time_to_event`（生存时间（非负数值）） | 是 | time to event/censoring (days) |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| `penalizer` | `0.001` |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `coefficients_csv` | `cox_coefficients.csv` |
| `metrics_json` | `cox_metrics.json` |

### `describe_full`

- **契约内名称**：`describe_full`
- **简介**：描述性统计（均值/标准差/分位数/偏度/峰度/IQR/MAD）

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `numeric_columns` | `numeric_list`（多列数值（列名列表）） | 否 | numeric columns to summarise (default: all numeric) |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| （无） | — |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `stats_csv` | `describe_full.csv` |

### `distribution_histogram`

- **契约内名称**：`distribution_histogram`
- **简介**：等距直方图（长表：bin_left/bin_right/count/density）

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `numeric_columns` | `numeric_list`（多列数值（列名列表）） | 否 | numeric columns to histogram |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| `n_bins` | `20` |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `hist_csv` | `distribution_histogram.csv` |

### `fillna_median`

- **契约内名称**：`fillna_median`
- **简介**：数值列中位数填补（非数值列直通）

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `numeric_columns` | `numeric_list`（多列数值（列名列表）） | 否 | the numeric columns to fill (others pass through) |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| （无） | — |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `filled_csv` | `filled.csv` |

### `gds_soft_parser`

- **契约内名称**：`gds_soft_parser`
- **简介**：GEO SOFT/GDS 解析（输出 expression / sample_groups / annotation 三张 csv）

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `soft_path` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 是 | Absolute or relative path to the .soft / .gds file.  Required. |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| `soft_path` | `None` |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `annotation_csv` | `annotation.csv` |
| `expression_matrix_csv` | `expression_matrix.csv` |
| `sample_groups_csv` | `sample_groups.csv` |

### `hclust_samples`

- **契约内名称**：`hclust_samples`
- **简介**：样本层次聚类（默认 correlation+average，可切 ward+euclidean）

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `gene_matrix_csv` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 是 | Path to a gene_matrix.csv (first column = gene_symbol, rest are sample columns). |
| `method` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 否 | Linkage method: single/complete/average/ward (default: average).  ward requires euclidean metric. |
| `metric` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 否 | Distance metric: correlation/euclidean/cosine/... (default: correlation). |
| `n_clusters` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 否 | Number of flat clusters to extract (default: 2). |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| `method` | `'average'` |
| `metric` | `'correlation'` |
| `n_clusters` | `2` |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `cluster_assignments_csv` | `cluster_assignments.csv` |
| `linkage_csv` | `linkage.csv` |

### `hist_gradient_boosting`

- **契约内名称**：`hist_gbdt_cv`
- **简介**：sklearn HistGradientBoosting + CV

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `feature_columns` | `numeric_list`（多列数值（列名列表）） | 是 | numeric / 0-1 feature columns to use as predictors |
| `id_col` | `id`（标识列（单列名）） | 是 | patient identifier |
| `target_col` | `binary_target`（0/1 或二分类目标列） | 否 | 0/1 outcome column (or None + external_label_csv) |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| `cv_folds` | `5` |
| `external_label_csv` | `None` |
| `random_state` | `42` |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `metrics_json` | `hist_gbdt_cv_metrics.json` |
| `predictions_csv` | `hist_gbdt_cv_predictions.csv` |

### `kendall_correlation`

- **契约内名称**：`kendall_correlation`
- **简介**：Kendall tau 相关

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `numeric_columns` | `numeric_list`（多列数值（列名列表）） | 是 | numeric columns to rank-correlate |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| （无） | — |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `matrix_csv` | `kendall_matrix.csv` |
| `pairs_csv` | `kendall_pairs.csv` |

### `knn_k_selection`

- **契约内名称**：`knn_k_selection`
- **简介**：KNN + K 网格搜索

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `feature_columns` | `numeric_list`（多列数值（列名列表）） | 是 | numeric feature columns |
| `id_col` | `id`（标识列（单列名）） | 是 | patient identifier |
| `target_col` | `numeric_target`（连续/多分类数值目标） | 否 | multi-class target column (only matched when col name looks like target/y/label) |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| `cv_folds` | `5` |
| `external_label_csv` | `None` |
| `k_grid` | `[1, 3, 5, 7, 9, 11, 13, 15]` |
| `random_state` | `42` |
| `test_size` | `0.2` |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `metrics_json` | `metrics.json` |
| `predictions_csv` | `predictions.csv` |

### `kruskal_wallis`

- **契约内名称**：`kruskal_wallis`
- **简介**：Kruskal-Wallis 检验

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `group_col` | `categorical`（分类列） | 是 | categorical group label |
| `value_col` | `numeric`（单列数值） | 是 | numeric outcome |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| （无） | — |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `summary_json` | `kruskal_summary.json` |

### `lightgbm`

- **契约内名称**：`lightgbm_cv`
- **简介**：LightGBM + CV（需安装 lightgbm）

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `feature_columns` | `numeric_list`（多列数值（列名列表）） | 是 | numeric / 0-1 feature columns to use as predictors |
| `id_col` | `id`（标识列（单列名）） | 是 | patient identifier |
| `target_col` | `binary_target`（0/1 或二分类目标列） | 否 | 0/1 outcome column (or None + external_label_csv) |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| `cv_folds` | `5` |
| `external_label_csv` | `None` |
| `random_state` | `42` |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `metrics_json` | `lightgbm_cv_metrics.json` |
| `predictions_csv` | `lightgbm_cv_predictions.csv` |

### `limma_deg_two_group`

- **契约内名称**：`limma_deg_two_group`
- **简介**：两组差异表达分析（含 Smyth EB 方差收缩，moderated t）

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `gene_matrix_csv` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 是 | Path to a gene_matrix.csv (first column = gene_symbol, rest are sample columns). |
| `group_a` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 否 | Group label considered the reference (denominator of logFC).  Optional; if omitted the alphabetically smaller group is used. |
| `group_b` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 否 | Group label considered the test (numerator of logFC).  Optional. |
| `group_field` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 否 | Which column in sample_groups_csv to use for grouping: 'group' (subset id) or 'group_description'.  Default: 'group_description' (more human-readable). |
| `moderation` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 否 | Whether to apply Smyth empirical-Bayes variance moderation (default: True).  Set False to get vanilla pooled t-test. |
| `sample_groups_csv` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 是 | Path to sample_groups.csv with columns sample_id + group (or group_description).  Must reference exactly 2 groups. |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| `group_field` | `'group_description'` |
| `moderation` | `True` |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `deg_table_csv` | `deg_table.csv` |

### `logistic_regression`

- **契约内名称**：`logistic_regression_cv`
- **简介**：逻辑回归 + 分层 CV（输出预测概率 CSV）

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `feature_columns` | `numeric_list`（多列数值（列名列表）） | 是 | the numeric / 0-1 feature columns to use as predictors |
| `id_col` | `id`（标识列（单列名）） | 是 | patient identifier |
| `target_col` | `binary_target`（0/1 或二分类目标列） | 否 | the 0/1 outcome column (if present in the input dataframe; otherwise pass external_label_csv via static_params) |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| `cv_folds` | `5` |
| `external_label_csv` | `None` |
| `random_state` | `42` |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `metrics_json` | `metrics.json` |
| `predictions_csv` | `predictions.csv` |

### `mann_whitney_u_test`

- **契约内名称**：`mann_whitney_u_test`
- **简介**：Mann-Whitney U

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `group_col` | `binary_target`（0/1 或二分类目标列） | 是 | the 0/1 group label |
| `value_col` | `numeric`（单列数值） | 是 | the numeric outcome |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| （无） | — |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `summary_json` | `mann_whitney_summary.json` |

### `metadata_parser`

- **契约内名称**：`metadata_parser`
- **简介**：元数据解析（列类型/缺失/建议；输出 JSON）

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| （无） | — | — | 使用整张输入表，无需列映射。 |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| `sample_topk` | `3` |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `metadata_json` | `metadata.json` |

### `missing_summary`

- **契约内名称**：`missing_summary`
- **简介**：缺失率汇总（每列 n_missing / missing_rate / n_unique）

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| （无） | — | — | 使用整张输入表，无需列映射。 |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| （无） | — |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `summary_csv` | `missing_summary.csv` |

### `multiple_correction`

- **契约内名称**：`multiple_correction`
- **简介**：多重比较校正（Bonferroni + BH-FDR）

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `p_value_col` | `p_value`（p 值列（0..1）） | 是 | raw p-value column |
| `test_id_col` | `id`（标识列（单列名）） | 是 | test identifier |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| `alpha` | `0.05` |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `corrected_csv` | `pvalues_corrected.csv` |
| `summary_json` | `summary.json` |

### `normality_test`

- **契约内名称**：`normality_test`
- **简介**：正态性检验（Shapiro + KS）

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `test_columns` | `numeric_list`（多列数值（列名列表）） | 是 | the numeric columns to test for normality |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| `alpha` | `0.05` |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `results_csv` | `normality_results.csv` |

### `oneway_anova`

- **契约内名称**：`oneway_anova`
- **简介**：单因素方差分析

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `group_col` | `categorical`（分类列） | 是 | categorical group label |
| `value_col` | `numeric`（单列数值） | 是 | numeric outcome |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| （无） | — |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `summary_json` | `anova_summary.json` |

### `outlier_iqr_flag`

- **契约内名称**：`outlier_iqr_flag`
- **简介**：IQR 异常值标记（Tukey fences）

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `id_col` | `id`（标识列（单列名）） | 否 | subject identifier |
| `numeric_columns` | `numeric_list`（多列数值（列名列表）） | 是 | numeric columns to flag |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| `k` | `1.5` |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `flags_csv` | `iqr_outlier_flags.csv` |

### `panss_factor_score`

- **契约内名称**：`panss_factor_score`
- **简介**：PANSS 因子/总分（需提供 item 列分组 mapping）

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `general_items` | `item_group`（有序条目列组（列名列表，如 P1..P7）） | 是 | the 16 PANSS General Psychopathology items (G1..G16); each Likert 1..7 |
| `id_col` | `id`（标识列（单列名）） | 是 | patient identifier |
| `negative_items` | `item_group`（有序条目列组（列名列表，如 P1..P7）） | 是 | the 7 PANSS Negative scale items (N1..N7); each Likert 1..7 |
| `positive_items` | `item_group`（有序条目列组（列名列表，如 P1..P7）） | 是 | the 7 PANSS Positive scale items (P1..P7); each Likert 1..7 |
| `time_col` | `numeric`（单列数值） | 否 | visit week / time index (integer); set as second key column |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| （无） | — |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `scored_csv` | `panss_scored.csv` |

### `panss_trajectory_responder`

- **契约内名称**：`panss_trajectory_responder`
- **简介**：PANSS 访视变化 / 应答者

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `baseline_col` | `numeric`（单列数值） | 是 | baseline total score |
| `endpoint_col` | `numeric`（单列数值） | 是 | endpoint total score to compute change against |
| `id_col` | `id`（标识列（单列名）） | 是 | patient identifier |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| `responder_threshold_pct` | `30` |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `trajectory_csv` | `panss_trajectory.csv` |

### `pathway_enrichment_fisher`

- **契约内名称**：`pathway_enrichment_fisher`
- **简介**：通路富集（超几何 / Fisher exact）；自带 MSigDB Hallmark 2020

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `adj_p_threshold` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 否 | Alternative selection: use all genes with adj_p_value < threshold.  If both top_k and adj_p_threshold are provided, the SMALLER resulting target list is used. |
| `case_insensitive` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 否 | Match gene names case-insensitively (helpful when the GMT is HUMAN uppercase and the data is MOUSE mixed-case).  Default: True. |
| `deg_table_csv` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 是 | Path to a deg_table.csv with at least gene_symbol + adj_p_value columns (e.g. output of limma_deg_two_group). |
| `gene_set_db_path` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 否 | Path to a GMT file (term\tdescription\tgene1\tgene2...). Default: bundled MSigDB Hallmark 2020. |
| `min_overlap` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 否 | Drop terms with overlap < min_overlap (default: 2). |
| `top_k` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 否 | Use the top-K rows of deg_table (sorted by adj_p_value) as the target gene list.  Default: 200. |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| `case_insensitive` | `True` |
| `min_overlap` | `2` |
| `top_k` | `200` |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `enrichment_csv` | `enrichment.csv` |

### `pca_decompose`

- **契约内名称**：`pca_decompose`
- **简介**：样本×基因表达矩阵 PCA

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `gene_matrix_csv` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 是 | Path to a gene_matrix.csv (first column = gene_symbol, rest are sample columns). |
| `n_components` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 否 | Number of PCs to keep (default: min(n_samples-1, 5)) |
| `sample_groups_csv` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 否 | Optional sample_groups.csv to copy 'group' / 'group_description' onto pca_scores.csv for plotting. |
| `standardize` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 否 | If True (default), z-score each gene before PCA. |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| `standardize` | `True` |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `pca_loadings_csv` | `pca_loadings.csv` |
| `pca_scores_csv` | `pca_scores.csv` |
| `pca_variance_csv` | `pca_variance.csv` |

### `pearson_correlation`

- **契约内名称**：`pearson_correlation`
- **简介**：Pearson 相关矩阵 + 配对长表

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `numeric_columns` | `numeric_list`（多列数值（列名列表）） | 是 | the numeric columns whose pairwise correlations to compute |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| （无） | — |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `matrix_csv` | `pearson_matrix.csv` |
| `pairs_csv` | `pearson_pairs.csv` |

### `probe_deg_collapse_to_gene`

- **契约内名称**：`probe_deg_collapse_to_gene`
- **简介**：把 probe 级 DEG 表按 min(adj_p) 取每个基因最佳 probe 收敛到基因级（GEO2R 推荐做法）

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `annotation_csv` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 否 | Optional path to annotation CSV providing probe→gene mapping.  Required only if deg_table_csv lacks a gene_symbol column. |
| `annotation_probe_col` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 否 | Column name in annotation_csv holding probe IDs.  Default: auto-detect. |
| `deg_table_csv` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 是 | Path to a probe-level DEG table CSV with at least adj_p_value, p_value, logFC columns and a probe identifier. |
| `drop_unmapped` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 否 | Whether to drop rows whose probe has no gene symbol.  Default: True. |
| `gene_col` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 否 | Column name in annotation_csv holding gene symbols.  Default: auto-detect. |
| `probe_col` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 否 | Column name in deg_table_csv holding probe IDs.  Default: auto-detect. |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| `drop_unmapped` | `True` |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `gene_deg_table_csv` | `gene_deg_table.csv` |

### `probe_to_gene_collapse`

- **契约内名称**：`probe_to_gene_collapse`
- **简介**：探针级表达矩阵聚合到基因级（max/mean/median）

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `annotation_csv` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 是 | Path to an annotation csv with probe_id + gene_symbol columns (extra columns ignored). |
| `expression_matrix_csv` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 是 | Path to a probe-level expression matrix csv (first column = probe_id, remaining columns = numeric sample values). |
| `gene_symbol_col` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 否 | Column name in annotation_csv that holds the gene symbol (default: 'Gene symbol'). |
| `method` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 否 | Aggregation method: 'max' / 'mean' / 'median' (default: max) |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| `gene_symbol_col` | `'Gene symbol'` |
| `method` | `'max'` |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `gene_matrix_csv` | `gene_matrix.csv` |

### `propensity_score_matching`

- **契约内名称**：`propensity_score_matching`
- **简介**：倾向得分 1:1 匹配

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `covariate_columns` | `numeric_list`（多列数值（列名列表）） | 是 | the numeric covariates to balance |
| `id_col` | `id`（标识列（单列名）） | 是 | subject identifier |
| `treatment_col` | `binary_target`（0/1 或二分类目标列） | 是 | 0 = control, 1 = treated |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| `caliper` | `None` |
| `random_state` | `42` |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `balance_csv` | `balance_after.csv` |
| `matched_pairs_csv` | `matched_pairs.csv` |

### `random_forest`

- **契约内名称**：`random_forest_cv`
- **简介**：随机森林 + CV

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `feature_columns` | `numeric_list`（多列数值（列名列表）） | 是 | numeric / 0-1 feature columns to use as predictors |
| `id_col` | `id`（标识列（单列名）） | 是 | patient identifier |
| `target_col` | `binary_target`（0/1 或二分类目标列） | 否 | 0/1 outcome column (or None + external_label_csv) |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| `cv_folds` | `5` |
| `external_label_csv` | `None` |
| `random_state` | `42` |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `metrics_json` | `random_forest_cv_metrics.json` |
| `predictions_csv` | `random_forest_cv_predictions.csv` |

### `reference_range_flag`

- **契约内名称**：`reference_range_flag`
- **简介**：参考区间异常标记（LLM/UI 提供 reference_ranges 字典）

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `id_col` | `id`（标识列（单列名）） | 是 | subject identifier |
| `lab_columns` | `numeric_list`（多列数值（列名列表）） | 是 | the laboratory measurement columns to flag |
| `reference_ranges` | `params`（非列名配置（路径、dict、超参等，写入 mapping）） | 是 | reference_ranges: an object {"<lab_col>": {"low": <number>, "high": <number>}, ...} listing the lab columns to flag and their adult reference intervals. |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| `reference_ranges` | `{}` |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `flags_csv` | `lab_flags.csv` |

### `spearman_correlation`

- **契约内名称**：`spearman_correlation`
- **简介**：Spearman 秩相关

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `numeric_columns` | `numeric_list`（多列数值（列名列表）） | 是 | numeric columns to rank-correlate |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| （无） | — |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `matrix_csv` | `spearman_matrix.csv` |
| `pairs_csv` | `spearman_pairs.csv` |

### `svm_rbf`

- **契约内名称**：`svm_rbf_classifier`
- **简介**：RBF SVM + GridSearchCV

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `feature_columns` | `numeric_list`（多列数值（列名列表）） | 是 | the 8 numeric feature columns |
| `id_col` | `id`（标识列（单列名）） | 是 | patient identifier |
| `target_col` | `binary_target`（0/1 或二分类目标列） | 否 | 0/1 target if present in input |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| `C_grid` | `[0.1, 1, 10]` |
| `cv_folds` | `5` |
| `external_label_csv` | `None` |
| `gamma_grid` | `['scale', 0.1, 1.0]` |
| `random_state` | `42` |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `metrics_json` | `metrics.json` |
| `predictions_csv` | `predictions.csv` |

### `text_features`

- **契约内名称**：`text_features`
- **简介**：文本 TF-IDF / 可选向量编码

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `id_col` | `id`（标识列（单列名）） | 是 | phrase identifier |
| `label_col` | `categorical`（分类列） | 否 | weak-supervision label |
| `text_col` | `text`（文本列） | 是 | the Chinese phrase |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| `prefer_transformer_model` | `'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'` |
| `tfidf_analyzer` | `'char_wb'` |
| `tfidf_ngram` | `[2, 4]` |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `embeddings_csv` | `embeddings.csv` |
| `intra_label_json` | `intra_label_cosine.json` |
| `manifest_json` | `manifest.json` |
| `similarity_top3` | `similarity_top3.csv` |

### `time_series_features`

- **契约内名称**：`time_series_features`
- **简介**：纵向数据 → 受试者级时序特征

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `id_col` | `id`（标识列（单列名）） | 是 | subject identifier |
| `time_col` | `numeric`（单列数值） | 是 | numeric time index (visit week, day, …) |
| `value_columns` | `numeric_list`（多列数值（列名列表）） | 是 | numeric measurement columns to summarise |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| （无） | — |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `features_csv` | `ts_features.csv` |

### `welch_t_test`

- **契约内名称**：`welch_t_test`
- **简介**：Welch t 检验（双样本不等方差）

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `group_col` | `binary_target`（0/1 或二分类目标列） | 是 | the 0/1 group label |
| `value_col` | `numeric`（单列数值） | 是 | the numeric outcome |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| （无） | — |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `summary_json` | `welch_t_summary.json` |

### `xgboost`

- **契约内名称**：`xgboost_cv`
- **简介**：XGBoost + CV（需安装 xgboost）

**列 / 参数槽（roles）**

| role_key | Role | 必填 | 说明 |
|----------|------|:----:|------|
| `feature_columns` | `numeric_list`（多列数值（列名列表）） | 是 | numeric / 0-1 feature columns to use as predictors |
| `id_col` | `id`（标识列（单列名）） | 是 | patient identifier |
| `target_col` | `binary_target`（0/1 或二分类目标列） | 否 | 0/1 outcome column (or None + external_label_csv) |

**静态参数（static_params，可被 `make_solver` 的 params 覆盖）**

| 参数 | 默认值 |
|------|--------|
| `cv_folds` | `5` |
| `external_label_csv` | `None` |
| `random_state` | `42` |

**输出键 → 约定写出文件**

| 返回 dict 键 | 约定文件名 |
|--------------|-------------|
| `metrics_json` | `xgboost_cv_metrics.json` |
| `predictions_csv` | `xgboost_cv_predictions.csv` |


---

## 数据治理与数据质量

| 算子 id | 说明 |
|--------|------|
| `missing_summary` | 缺失率汇总（每列 n_missing / missing_rate / n_unique） |
| `fillna_median` | 数值列中位数填补（非数值列直通） |
| `outlier_iqr_flag` | IQR 异常值标记（Tukey fences，默认 k=1.5，可通过 `params.k` 覆盖） |
| `metadata_parser` | 元数据解析（列类型/缺失/建议；输出 JSON） |
| `consistency_check` | 一致性检查（主键唯一/正则/值域/白名单） |

## 描述统计与分布

| 算子 id | 说明 |
|--------|------|
| `describe_full` | 描述性统计（均值/标准差/分位数/偏度/峰度/IQR/MAD） |
| `distribution_histogram` | 等距直方图（长表：bin_left/bin_right/count/density；默认 20 桶，可通过 `params.n_bins` 覆盖） |

## 相关分析

| 算子 id | 说明 |
|--------|------|
| `pearson_correlation` | Pearson 相关矩阵 + 配对长表 |
| `spearman_correlation` | Spearman 秩相关 |
| `kendall_correlation` | Kendall tau 相关 |

## 正态性与多重比较

| 算子 id | 说明 |
|--------|------|
| `normality_test` | 正态性检验（Shapiro + KS；默认 α=0.05，可通过 `params.alpha` 覆盖） |
| `multiple_correction` | 多重比较校正（Bonferroni + BH-FDR） |

## 假设检验

| 算子 id | 说明 |
|--------|------|
| `welch_t_test` | Welch t 检验（双样本不等方差） |
| `mann_whitney_u_test` | Mann-Whitney U |
| `chi_square_independence` | 卡方独立性检验 |
| `oneway_anova` | 单因素方差分析 |
| `kruskal_wallis` | Kruskal-Wallis 检验 |

## 回归与机器学习

| 算子 id | 说明 |
|--------|------|
| `logistic_regression` | 逻辑回归 + 分层 CV（输出预测概率 CSV） |
| `random_forest` | 随机森林 + CV |
| `hist_gradient_boosting` | sklearn HistGradientBoosting + CV |
| `xgboost` | XGBoost + CV（**需安装** `xgboost`） |
| `lightgbm` | LightGBM + CV（**需安装** `lightgbm`） |
| `svm_rbf` | RBF SVM + GridSearchCV |
| `knn_k_selection` | KNN + K 网格搜索 |

## 生存分析与其他统计

| 算子 id | 说明 |
|--------|------|
| `cox_regression` | Cox 比例风险模型 |
| `association_rules` | 关联规则（FP-Growth；默认 min_support=0.05、min_confidence=0.3，可通过 `params` 覆盖） |

## 临床与纵向数据

| 算子 id | 说明 |
|--------|------|
| `reference_range_flag` | 参考区间异常标记（需通过 UI/LLM 等提供 `reference_ranges` 等 overlay 参数） |
| `panss_factor_score` | PANSS 因子/总分（需提供条目列分组 mapping） |
| `panss_trajectory_responder` | PANSS 访视变化 / 应答者（默认阈值等见实现） |
| `time_series_features` | 纵向数据 → 受试者级时序特征 |
| `propensity_score_matching` | 倾向得分 1:1 匹配 |

## 文本

| 算子 id | 说明 |
|--------|------|
| `text_features` | 文本 TF-IDF / 可选向量编码（中文场景常需 **jieba**） |

## 生信（Bio）

| 算子 id | 说明 |
|--------|------|
| `gds_soft_parser` | GEO SOFT/GDS 解析（输出 expression / sample_groups / annotation 等 CSV） |
| `probe_to_gene_collapse` | 探针级表达矩阵聚合到基因级（max/mean/median 等，见 `make_solver` 的 `params`） |
| `limma_deg_two_group` | 两组差异表达（Smyth EB、moderated t；`moderation`、`group_field` 等见 `make_solver`） |
| `probe_deg_collapse_to_gene` | probe 级 DEG 表按 min(adj_p) 收敛到基因级（GEO2R 常见做法） |
| `pca_decompose` | 样本×基因表达矩阵 PCA（`standardize` 等见 `make_solver`） |
| `hclust_samples` | 样本层次聚类（`method` / `metric` / `n_clusters` 等见 `make_solver`） |
| `pathway_enrichment_fisher` | 通路富集（超几何 / Fisher exact；自带 MSigDB Hallmark 2020；`top_k` 等见 `make_solver`） |

---

## 按 id 排序（速查）

1. `association_rules`
2. `chi_square_independence`
3. `consistency_check`
4. `cox_regression`
5. `describe_full`
6. `distribution_histogram`
7. `fillna_median`
8. `gds_soft_parser`
9. `hclust_samples`
10. `hist_gradient_boosting`
11. `kendall_correlation`
12. `knn_k_selection`
13. `kruskal_wallis`
14. `lightgbm`
15. `limma_deg_two_group`
16. `logistic_regression`
17. `mann_whitney_u_test`
18. `metadata_parser`
19. `missing_summary`
20. `multiple_correction`
21. `normality_test`
22. `oneway_anova`
23. `outlier_iqr_flag`
24. `panss_factor_score`
25. `panss_trajectory_responder`
26. `pathway_enrichment_fisher`
27. `pca_decompose`
28. `pearson_correlation`
29. `probe_deg_collapse_to_gene`
30. `probe_to_gene_collapse`
31. `propensity_score_matching`
32. `random_forest`
33. `reference_range_flag`
34. `spearman_correlation`
35. `svm_rbf`
36. `text_features`
37. `time_series_features`
38. `welch_t_test`
39. `xgboost`

---

## 维护说明

- **新增或下线算子**：请只改 `registry.py` 中 `_solvers()`，并同步更新本文件上半部分的分类表与速查列表；**契约明细**（下文「各算子输入输出明细」）须与各模块中的 `CONTRACT` / `SolverContract` 一致，可本地用 `make_solver(id).contract` 对照修订。
- **契约与列角色**：各算子输入以 `SolverContract`（`distillation/software1_solver/contract.py`）为准，列名由 mapper 绑定到 **Role**，而非写死在算子内。
- **输出键**：`run()` 除表列文件外，部分算子还返回 `metrics_dict`、`summary_dict` 等，完整键名以实现代码为准；本文件「输出键」列与 `contract.output_files` 对齐。
