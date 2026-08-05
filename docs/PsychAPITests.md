# 2.1.4 Psych 后端接口测试文档

> **对应产品能力**：多维度分析与大模型能力支持（`/psych/*`）  
> **权威接口文档**：[PsychAPI.md](PsychAPI.md)  
> **联调 Demo**：[2.1.4FrontendIntegration.md](2.1.4FrontendIntegration.md)  
> **测试入口总览**：[Tests.md](Tests.md)

---

## 1. 目标与范围

Psych 测试分两层，**契约通过 ≠ 功能可用**：

| 层级 | 目录 | 含义 |
|------|------|------|
| **契约测（mock）** | `tests/analysis/test_psych_*.py` | 鉴权、校验、路由接线、错误码；**不**连真库/真 LLM |
| **功能集成测（真依赖）** | `tests/analysis/functional/` | 真 MySQL + 真 LLM；ingest→异步任务跑完→统计/ML/特征/DL/量表/导出 |

### 1.1 契约测

对 **2.1.4** 全部后端接口做 HTTP 契约级覆盖，验证：

- JWT 鉴权（缺 Token → 401）
- 正常业务路径（200/201 + `status=success`）
- 异常入参（Pydantic 422 / 业务 400/404/500）
- 边界条件（空列表、缺必填、超大上传 413、不存在资源等）

**包含**：[`src/backend/psych_routes.py`](../src/backend/psych_routes.py) 注册的全部 `/psych/*` 业务接口，以及 Demo 页 `GET /psych-app`、`/static/psych/*`。

契约测**有意不覆盖**（改由功能集成测覆盖）：真实 MySQL、真实 LLM、异步任务真正跑完、分片 `psych_ingest` 闭环。

### 1.2 功能集成测（默认执行）

前置（连不上则 **fail**，不 skip）：

- MySQL：仓库根 [`.env`](../.env) 的 `MYSQL_*`（见 [MySQL.md](MySQL.md)，默认 `localhost:3308` / `agent_platform`）
- LLM：`.env` 的 `OPENAI_API_KEY` / `OPENAI_API_BASE` / `DEFAULT_MODEL`（可用 Ollama）

覆盖：

- 用户隔离（A 的 dataset B 不可见）
- `datasets` 创建 → ingest → 轮询 task → preview/query
- 分片 `target=psych_ingest` init/put/complete → preview
- stats / pipeline / ml / features / dl 提交后轮询至 `success`
- 量表 PHQ9 parse/score/trend/compare
- 真 LLM：`extract` / `relate` / `query` / `qa`
- exports 下载；capabilities 列表/启停/编排
- 测试用户与 psych_* 行按 `user_id` teardown 清理

说明：本环境若 torch 导入链因 site-packages 权限失败，DL 用例会强制走 **sklearn 回退**（仍为真实 `train_text_model` / `infer`，非 mock service）。

---

## 2. 如何运行

在仓库根目录：

```bash
# 仅契约测（无外部依赖）
pytest tests/analysis/test_psych_*.py -q

# 仅功能集成测（需 MySQL + LLM）
pytest tests/analysis/functional/ -q

# analysis 全部（契约 + 功能 + template/dq213）
pytest tests/analysis/ -q

# 全仓库
pytest tests/ -q

# 仅筛选带 integration marker 的用例（功能套件均已标记；默认仍执行）
pytest tests/analysis/functional/ -m integration -q
```

---

## 3. 测试架构

```text
tests/analysis/
  conftest.py                 # psych_client / auth_headers（契约测）
  psych_test_helpers.py       # 契约测 App 工厂、样例、EXPECTED_PSYCH_PATHS
  test_psych_api.py           # 底层单元测 + 全路径注册断言
  test_psych_*_api.py         # 分模块 HTTP 契约测
  functional/                 # 功能集成测（真 MySQL + 真 LLM）
    conftest.py               # 停 pymysql mock、重连、LLM 探活、测试用户
    psych_functional_helpers.py
    test_psych_functional_data.py
    test_psych_functional_async.py
    test_psych_functional_llm_scales.py
```

**模式**（复用项目既有约定）：

1. `FastAPI()` + `register_psych_routes(app)` + `TestClient`
2. `create_access_token` + patch `RbacStore.get_user`
3. patch `backend.psych_*_service` 返回受控 `(data, err)`

成功响应约定：

```json
{ "status": "success", "data": { } }
```

---

## 4. 接口 × 场景矩阵

下列每个接口至少覆盖：**鉴权**、**正常**、**异常/边界**（表中列出示意用例名）。

### 4.1 公共（PsychAPI §2）

| 接口 | 正常 | 鉴权 | 异常/边界 |
|---|---|---|---|
| `GET /psych/health` | `test_health_ok` | `test_health_requires_auth` | — |
| `GET /psych/tasks` | `test_list_tasks_ok` | `test_list_tasks_requires_auth` | `test_list_tasks_db_error`（500） |
| `GET /psych/tasks/{task_id}` | `test_get_task_ok` | `test_get_task_requires_auth` | `test_get_task_not_found`（404） |
| `POST /psych/tasks/{task_id}/cancel` | `test_cancel_task_ok` | `test_cancel_task_requires_auth` | `test_cancel_task_rejected`（400） |

文件：[`test_psych_common_api.py`](../tests/analysis/test_psych_common_api.py)

### 4.2 模块1 数据集（§3）

| 接口 | 正常 | 鉴权 | 异常/边界 |
|---|---|---|---|
| `POST /psych/datasets` | `test_create_dataset_ok`（201） | `test_create_dataset_requires_auth` | 缺 `name`→422；业务错误→400 |
| `GET /psych/datasets` | `test_list_datasets_ok` | `test_list_datasets_requires_auth` | DB 错误→500 |
| `GET /psych/datasets/{id}` | `test_get_dataset_ok` | `test_get_dataset_requires_auth` | 不存在→404 |
| `POST .../ingest` | `test_ingest_ok`（201） | `test_ingest_requires_auth` | 缺文件→422；业务错误→400；超限→413 |
| `GET .../preview` | `test_preview_ok` | `test_preview_requires_auth` | 无数据→400 |
| `GET .../query` | `test_query_ok` | `test_query_requires_auth` | `limit=1` 边界 |

文件：[`test_psych_datasets_api.py`](../tests/analysis/test_psych_datasets_api.py)

### 4.3 模块2 管线（§4）

| 接口 | 正常 | 鉴权 | 异常/边界 |
|---|---|---|---|
| `GET /psych/pipelines/methods` | `test_pipeline_methods_ok` | `test_pipeline_methods_requires_auth` | — |
| `POST /psych/pipelines` | `test_create_pipeline_ok` | `test_create_pipeline_requires_auth` | 缺 `steps`→422 |
| `GET /psych/pipelines` | `test_list_pipelines_ok` | — | DB→500 |
| `POST /psych/pipelines/{id}/run` | `test_run_pipeline_ok`（201，含 `task_id`） | `test_run_pipeline_requires_auth` | 管线不存在→400 |
| `POST /psych/param-templates` | `test_save_param_template_ok` | — | 缺字段→422 |
| `GET /psych/param-templates` | `test_list_param_templates_ok` | `test_list_param_templates_requires_auth` | `module` 过滤 |

文件：[`test_psych_pipeline_api.py`](../tests/analysis/test_psych_pipeline_api.py)

### 4.4 模块3 统计（§5）

| 接口 | 正常 | 鉴权 | 异常/边界 |
|---|---|---|---|
| `GET /psych/stats/methods` | `test_stats_methods_ok` | `test_stats_methods_requires_auth` | — |
| `POST /psych/stats/run` | `test_stats_run_ok`（201） | `test_stats_run_requires_auth` | 空/缺 `method_ids`→422；业务→400 |
| `GET /psych/stats/results/{task_id}` | `test_stats_results_ok` | `test_stats_results_requires_auth` | 不存在→404 |

文件：[`test_psych_stats_api.py`](../tests/analysis/test_psych_stats_api.py)

### 4.5 模块4 变量（§6）

| 接口 | 正常 | 鉴权 | 异常/边界 |
|---|---|---|---|
| `POST /psych/variables` | `test_create_variable_ok` | `test_create_variable_requires_auth` | 缺 `var_name`→422 |
| `GET /psych/variables` | `test_list_variables_ok` | `test_list_variables_requires_auth` | `dataset_id` 过滤 |
| `PUT /psych/variables/{id}` | `test_update_variable_ok` | — | 不存在→400 |
| `DELETE /psych/variables/{id}` | `test_delete_variable_ok` | `test_delete_variable_requires_auth` | — |
| `POST /psych/variables/batch` | `test_batch_variables_ok` | — | 缺 `items`→422 |
| `POST /psych/variables/mapping` | `test_variable_mapping_ok` | — | 缺 mapping→400 |
| `GET .../dictionary/export` | `test_dictionary_export_ok` | `test_dictionary_export_requires_auth` | — |
| var-categories CRUD | create/list/update/delete `*_ok` | `test_categories_require_auth` | 缺 name→422；删除失败→400 |

文件：[`test_psych_variables_api.py`](../tests/analysis/test_psych_variables_api.py)

### 4.6 模块5 大模型（§7）

| 接口 | 正常 | 鉴权 | 异常/边界 |
|---|---|---|---|
| `POST /psych/llm/extract` | `test_llm_extract_ok`（201） | `test_llm_extract_requires_auth` | 缺 text→422；空文本业务→500 |
| `POST /psych/llm/relate` | `test_llm_relate_ok` | `test_llm_relate_requires_auth` | 缺 entities→422；LLM 不可用→500 |
| `POST /psych/llm/query` | `test_llm_query_ok` | `test_llm_query_requires_auth` | 缺 query→422 |
| `POST /psych/llm/qa` | `test_llm_qa_ok` | `test_llm_qa_requires_auth` | 缺 question→422；timeout→500 |

文件：[`test_psych_llm_api.py`](../tests/analysis/test_psych_llm_api.py)

### 4.7 模块6 参数与导出（§8）

| 接口 | 正常 | 鉴权 | 异常/边界 |
|---|---|---|---|
| `GET /psych/analysis-params` | `test_get_analysis_params_ok` | `test_get_analysis_params_requires_auth` | DB→500 |
| `PUT /psych/analysis-params` | `test_put_analysis_params_ok` | `test_put_analysis_params_requires_auth` | 缺 scope→422 |
| `POST /psych/exports` | `test_create_export_ok`（201） | `test_create_export_requires_auth` | 缺 kind→422；无数据→400 |
| `GET /psych/exports/{id}/download` | `test_download_export_ok`（文件流） | `test_download_export_requires_auth` | 不存在→404 |

文件：[`test_psych_export_api.py`](../tests/analysis/test_psych_export_api.py)

### 4.8 模块7 ML（§9）

| 接口 | 正常 | 鉴权 | 异常/边界 |
|---|---|---|---|
| `GET /psych/ml/algorithms` | `test_ml_algorithms_ok` | `test_ml_algorithms_requires_auth` | — |
| `POST /psych/ml/train` | `test_ml_train_ok`（201） | `test_ml_train_requires_auth` | 缺 algo→422；算法不存在→400 |
| `POST /psych/ml/predict` | `test_ml_predict_ok` | `test_ml_predict_requires_auth` | 缺 model_id→422；模型不存在→400 |
| `GET /psych/ml/models` | `test_ml_list_models_ok` | `test_ml_models_require_auth` | DB→500 |
| `GET /psych/ml/models/{id}` | `test_ml_get_model_ok` | 同上 | 不存在→404 |

文件：[`test_psych_ml_api.py`](../tests/analysis/test_psych_ml_api.py)

### 4.9 模块8 特征（§10）

| 接口 | 正常 | 鉴权 | 异常/边界 |
|---|---|---|---|
| `POST /psych/features/extract` | `test_features_extract_ok`（201） | `test_features_extract_requires_auth` | 缺 type→422；数据集不存在→400 |
| `GET /psych/features` | `test_list_features_ok` | `test_list_features_requires_auth` | DB→500 |
| `GET /psych/features/{id}` | `test_get_feature_ok` | `test_get_feature_requires_auth` | 不存在→404 |
| `GET /psych/features/{id}/download` | `test_download_feature_ok`（文件流）；`test_download_feature_json_ok` | `test_download_feature_requires_auth` | 不存在→404；坏 format→400 |

文件：[`test_psych_features_api.py`](../tests/analysis/test_psych_features_api.py)

### 4.10 模块9 DL（§11）

| 接口 | 正常 | 鉴权 | 异常/边界 |
|---|---|---|---|
| `GET /psych/dl/models` | `test_dl_models_ok` | `test_dl_models_requires_auth` | — |
| `POST /psych/dl/train` | `test_dl_train_ok`（201） | `test_dl_train_requires_auth` | 缺字段→422；长度不一致→400 |
| `POST /psych/dl/infer` | `test_dl_infer_ok` | `test_dl_infer_requires_auth` | 缺 meta→422；文件不存在→400 |

文件：[`test_psych_dl_api.py`](../tests/analysis/test_psych_dl_api.py)

### 4.11 模块10 能力管理（§12）

| 接口 | 正常 | 鉴权 | 异常/边界 |
|---|---|---|---|
| `GET /psych/capabilities` | `test_list_capabilities_ok` | `test_list_capabilities_requires_auth` | DB→500 |
| `PUT /psych/capabilities/{id}` | `test_update_capability_ok` | `test_update_capability_requires_auth` | 不存在→400 |
| `POST /psych/capabilities/compose` | `test_compose_ok`（201） | `test_compose_requires_auth` | 缺 ids→422；不可用→400 |

模块11（服务期内能力升级）为线下人工服务，无系统接口与对应用例。

文件：[`test_psych_capabilities_api.py`](../tests/analysis/test_psych_capabilities_api.py)

### 4.12 模块12 量表（§14）

| 接口 | 正常 | 鉴权 | 异常/边界 |
|---|---|---|---|
| `GET /psych/scales/forms` | `test_scale_forms_ok` | `test_scale_forms_requires_auth` | DB→500 |
| `POST /psych/scales/parse` | `test_scale_parse_ok` | `test_scale_parse_requires_auth` | 缺 raw→422；未知量表→400 |
| `POST /psych/scales/score` | `test_scale_score_ok`（201） | `test_scale_score_requires_auth` | 缺 patient_key→422 |
| `GET /psych/scales/scores` | `test_scale_scores_list_ok` | `test_scale_scores_list_requires_auth` | limit 过滤 |
| `GET /psych/scales/trend` | `test_scale_trend_ok` | `test_scale_trend_requires_auth` | 缺 query→422 |
| `POST /psych/scales/compare` | `test_scale_compare_ok` | `test_scale_compare_requires_auth` | 缺 group_b→422；样本不足→400 |
| `GET /psych/scales/export` | `test_scale_export_ok` | `test_scale_export_requires_auth` | 无数据→400 |

文件：[`test_psych_scales_api.py`](../tests/analysis/test_psych_scales_api.py)

### 4.13 Demo

| 接口 | 用例 |
|---|---|
| `GET /psych-app` | `test_psych_app_page_ok`（无需鉴权） |
| `GET /static/psych/app.js` / `styles.css` | `test_psych_static_*_ok` |
| 磁盘 UI + 路由注册 | `test_psych_demo_ui_files_exist_on_disk` |

文件：[`test_psych_demo_api.py`](../tests/analysis/test_psych_demo_api.py)

### 4.14 单元与注册完整性

| 用例 | 说明 |
|---|---|
| `test_stats_catalog_has_at_least_10` | 统计目录 ≥10 |
| `test_ml_registry_has_at_least_10_and_extensible` | ML 目录 ≥10 且可扩展 |
| `test_scale_score_phq9` / `test_scale_parse_and_score_service` | PHQ9 计分 |
| `test_dl_fallback_train_infer` | 无 torch 时 sklearn 回退 |
| `test_capability_bootstrap_list` | 能力 bootstrap 含 stats/ml/llm/dl |
| `test_psych_routes_register` | **全量路径** ⊆ 注册表（`EXPECTED_PSYCH_PATHS`） |

文件：[`test_psych_api.py`](../tests/analysis/test_psych_api.py)

### 4.15 功能集成测矩阵（真 MySQL + 真 LLM）

| 文件 | 覆盖 |
|------|------|
| [`test_psych_functional_data.py`](../tests/analysis/functional/test_psych_functional_data.py) | health；用户隔离；ingest→preview/query；分片 psych_ingest |
| [`test_psych_functional_async.py`](../tests/analysis/functional/test_psych_functional_async.py) | stats/pipeline/ml/features/dl 任务跑完；cancel |
| [`test_psych_functional_llm_scales.py`](../tests/analysis/functional/test_psych_functional_llm_scales.py) | variables；scales；真 LLM 四接口；exports |

基建：[`functional/conftest.py`](../tests/analysis/functional/conftest.py)（`enable_real_pymysql` / LLM ping / 测试用户清理）。

---

## 5. 覆盖清单核对

`EXPECTED_PSYCH_PATHS`（见 [`psych_test_helpers.py`](../tests/analysis/psych_test_helpers.py)）与 `register_psych_routes` 对齐，包含全部业务 path 模板及 `/psych-app`。CI/本地通过 `test_psych_routes_register` 防止漏注册。

- **契约层**：异步接口断言返回含 `task_id`；不保证任务真正 success。
- **功能层**：轮询 `GET /psych/tasks/{task_id}` 直至真实 `success`/`failed`/`cancelled`。

---

## 6. 相关文档

- 接口全文：[PsychAPI.md](PsychAPI.md)
- 前端联调：[2.1.4FrontendIntegration.md](2.1.4FrontendIntegration.md)
- 分片上传：[ChunkedUploadFrontend.md](ChunkedUploadFrontend.md)
- 鉴权 / RBAC：[AUTH.md](AUTH.md)、[RBAC.md](RBAC.md)
- 仓库测试总说明：[Tests.md](Tests.md)
