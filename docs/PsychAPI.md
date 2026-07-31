# 精神专科多维度分析 API — 接口文档（给前端对接用）

> 本文档覆盖 `/psych/*` 精神专科多维度分析与大模型支撑后端能力。鉴权、项目与 RBAC 见 [`2.1.1FrontendIntegrationGuide.md`](2.1.1FrontendIntegrationGuide.md)、[`AUTH.md`](AUTH.md)、[`RBAC.md`](RBAC.md)。临床既有接口见 [`ClinicalAPI.md`](ClinicalAPI.md)。

---

## 1. 基本信息

| 项 | 值 |
|---|---|
| 服务 | FastAPI，与主平台共用同一进程 |
| Base URL | `http://<host>:52716` |
| 数据格式 | `application/json`（上传/下载除外） |
| 鉴权 | JWT Bearer：`Authorization: Bearer <access_token>` |
| 字符编码 | UTF-8 |

### 1.1 通用响应

成功：

```json
{ "status": "success", "data": { } }
```

错误（多数）：

```json
{ "detail": "错误信息字符串" }
```

鉴权失败：

```json
{ "detail": { "code": 6, "msg": "unauthorized" } }
```

### 1.2 异步任务约定

耗时分析统一走 `psych_tasks`：

1. `POST` 提交 → 返回 `{ task_id, status: "pending", ... }`
2. `GET /psych/tasks/{task_id}` 轮询，直到 `success` / `failed` / `cancelled`
3. 结果在 `result_json`；统计明细另见 `GET /psych/stats/results/{task_id}`

---

## 2. 公共接口

### 2.1 `GET /psych/health`

返回能力注册统计。

### 2.2 `GET /psych/tasks`

Query：`module?`、`limit?`（默认 50）

### 2.3 `GET /psych/tasks/{task_id}`

### 2.4 `POST /psych/tasks/{task_id}/cancel`

---

## 3. ▲一键统计分析（模块3）

### 3.1 `GET /psych/stats/methods`

返回 ≥10 类方法：`describe_full`、`groupby_stat`、`pearson/spearman/kendall_correlation`、`welch_t_test`、`mann_whitney_u_test`、`chi_square_independence`、`oneway_anova`、`kruskal_wallis`，以及 `normality_test`、`proportion_ci`。

### 3.2 `POST /psych/stats/run`

```json
{
  "method_ids": ["describe_full", "pearson_correlation"],
  "dataset_id": 1,
  "file_path": null,
  "mappings": { "describe_full": { "numeric_columns": ["HAMD_total", "age"] } },
  "params_by_method": {}
}
```

`data` 示例：`{ "task_id": "task_...", "status": "pending", "module": "stats" }`

### 3.3 `GET /psych/stats/results/{task_id}`

`data.task` + `data.results[]`（每方法 `summary_json` / `tables_json`）

---

## 4. ▲机器学习算法库（模块7）

内置算法：`logistic_regression`、`random_forest`、`xgboost`、`lightgbm`、`svm_rbf`、`knn_k_selection`、`cox_regression`、`hist_gradient_boosting`、`linear_regression`、`lasso_cv_select`。扩展入口：`psych.ml.register_algo()`。

### 4.1 `GET /psych/ml/algorithms`

### 4.2 `POST /psych/ml/train`

```json
{
  "algo_id": "logistic_regression",
  "dataset_id": 1,
  "mapping": {
    "id_col": "patient_id",
    "feature_columns": ["age", "HAMD_total"],
    "target_col": "relapse"
  },
  "model_name": "lr_relapse_v1",
  "sync_resource": true
}
```

异步返回 `task_id`；完成后 `result_json` 含 `psych_model_id`、`metrics`、`model_path`。

### 4.3 `POST /psych/ml/predict`

```json
{ "model_id": 1, "dataset_id": 1 }
```

或传 `rows: [{...}, ...]`。

### 4.4 `GET /psych/ml/models` / `GET /psych/ml/models/{model_id}`

---

## 5. 多类型数据一体化（模块1）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/psych/datasets` | body: `{name, source_type, project_id?, description?}` |
| GET | `/psych/datasets` | 列表 |
| GET | `/psych/datasets/{id}` | 详情 |
| POST | `/psych/datasets/{id}/ingest` | multipart：`file` + `record_type?` + `patient_key_col?` |
| GET | `/psych/datasets/{id}/preview` | `n_rows?` |
| GET | `/psych/datasets/{id}/query` | `patient_key?` `record_type?` `limit?` |

`source_type`：`text|scale|assessment|order|medication|lab|exam|followup|mixed|table`

---

## 6. 分析管线适配（模块2）

| 方法 | 路径 |
|---|---|
| GET | `/psych/pipelines/methods` |
| POST | `/psych/pipelines` | body: `{name, steps:[{method_id, solver_id?, mapping?, params?}]}` |
| GET | `/psych/pipelines` |
| POST | `/psych/pipelines/{id}/run` | `{dataset_id?, file_path?}` |
| POST | `/psych/param-templates` | `{module, method_id, name, params, is_default?}` |
| GET | `/psych/param-templates?module=` |

---

## 7. 变量管理（模块4）

| 方法 | 路径 |
|---|---|
| CRUD | `/psych/variables`、`PUT/DELETE /psych/variables/{id}` |
| POST | `/psych/variables/batch` | `{items:[...]}` |
| POST | `/psych/variables/mapping` | `{var_id, mapping}` |
| GET | `/psych/variables/dictionary/export?format=json\|csv` |
| CRUD | `/psych/var-categories` |

---

## 8. 融合大模型（模块5）

| 方法 | 路径 | body 要点 |
|---|---|---|
| POST | `/psych/llm/extract` | `{text, extract_type?, dataset_id?, record_id?}` |
| POST | `/psych/llm/relate` | `{entities, question?}` |
| POST | `/psych/llm/query` | `{query, dataset_id?, schema_hint?}` |
| POST | `/psych/llm/qa` | `{question, context?, dataset_id?, task_id?}` |

依赖平台 LLM 配置（见 [`Models.md`](Models.md)）。

---

## 9. 参数调整与导出（模块6）

| 方法 | 路径 |
|---|---|
| GET/PUT | `/psych/analysis-params` | PUT body: `{scope: qc\|stats\|text\|ml\|dl\|general, items:{k:v}}` |
| POST | `/psych/exports` | `{kind, format: csv\|parquet\|json\|rds_compat, task_id?, dataset_id?, data?}` |
| GET | `/psych/exports/{export_id}/download` | 文件流 |

`rds_compat`：写出 CSV/JSON + `*_rds_manifest.json`，便于 R `fread` / `jsonlite`。

---

## 10. 特征挖掘（模块8）

| 方法 | 路径 |
|---|---|
| POST | `/psych/features/extract` | `{feature_type: stat\|ts\|text, dataset_id?, file_path?, ...}` |
| GET | `/psych/features` | |
| GET | `/psych/features/{id}` | |

---

## 11. 深度学习（模块9）

可选依赖见仓库根 `requirements-dl.txt`（`torch`）。未安装时自动 sklearn 回退。

| 方法 | 路径 |
|---|---|
| GET | `/psych/dl/models` | `text_cnn` / `text_transformer` |
| POST | `/psych/dl/train` | `{model_id, texts:[], labels:[], epochs?}` → 异步任务 |
| POST | `/psych/dl/infer` | `{meta_path, texts:[]}`（`meta_path` 须在用户 psych 目录内） |

---

## 12. 能力模块化与升级预留（模块10+12）

| 方法 | 路径 |
|---|---|
| GET | `/psych/capabilities?kind=` |
| PUT | `/psych/capabilities/{capability_id}` | `{enabled?, version?, meta_json?}` |
| POST | `/psych/capabilities/compose` | `{capability_ids:[], name?}` → 生成管线 |
| POST | `/psych/capabilities/upgrade` | `{capability_id, to_ver, note?}` |
| GET | `/psych/capabilities/changelog` | |

---

## 13. 量表智能结构化（模块11）

内置：`PHQ9`、`GAD7`、`HAMD`、`HAMA`、`PANSS`。

| 方法 | 路径 |
|---|---|
| GET | `/psych/scales/forms` |
| POST | `/psych/scales/parse` | `{scale_code, raw, patient_key?, dataset_id?}` |
| POST | `/psych/scales/score` | `{scale_code, item_scores, patient_key, dataset_id?}` |
| GET | `/psych/scales/scores` | |
| GET | `/psych/scales/trend?patient_key=&scale_code=` |
| POST | `/psych/scales/compare` | `{scale_code, group_a:[], group_b:[]}` |
| GET | `/psych/scales/export` | |

---

## 14. 数据库表（新增）

权威 DDL：[`src/db/psych_schema.py`](../src/db/psych_schema.py)；运维脚本：[`scripts/sql/psych_tables.sql`](../scripts/sql/psych_tables.sql)。

主要表：`psych_datasets`、`psych_data_records`、`psych_tasks`、`psych_stats_results`、`psych_ml_models`、`psych_variables`、`psych_param_templates`、`psych_analysis_params`、`psych_features`、`psych_scale_forms`、`psych_scale_scores`、`psych_llm_extractions`、`psych_exports`、`psych_capabilities`、`psych_pipelines`、`psych_capability_changelog`。

---

## 15. 联调提示

1. 先 `POST /psych/datasets` + `ingest` 拿到 `dataset_id` / `file_path`
2. 再调 `/psych/stats/run` 或 `/psych/ml/train`
3. 轮询 `/psych/tasks/{task_id}`
4. 导出用 `/psych/exports`
