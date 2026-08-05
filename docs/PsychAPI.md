# 2.1.4 多维度分析与大模型能力支持 — 前后端对接接口文档

> **文档定位**：本章覆盖 `/psych/*` 精神专科多维度分析与大模型支撑后端能力，专供前后端联调。  
> **鉴权 / 项目 / RBAC**：见 [`2.1.1FrontendIntegrationGuide.md`](2.1.1FrontendIntegrationGuide.md)、[`AUTH.md`](AUTH.md)、[`RBAC.md`](RBAC.md)。  
> **临床既有接口**：见 [`ClinicalAPI.md`](ClinicalAPI.md)。  
> **大文件 ingest**：推荐分片协议 [`ChunkedUploadFrontend.md`](ChunkedUploadFrontend.md)（`target=psych_ingest`）；`POST /psych/datasets/{id}/ingest` 整文件上传已 deprecated。  
> **联调 Demo**：`http://<host>:52716/psych-app`（说明见 [`2.1.4FrontendIntegration.md`](2.1.4FrontendIntegration.md)）。

---

## 1. 基本信息

| 项 | 值 |
|---|---|
| 服务 | FastAPI，与主平台共用同一进程 |
| Base URL | `http://<host>:52716` |
| 数据格式 | `application/json`（上传/下载除外） |
| 鉴权 | JWT Bearer：`Authorization: Bearer <access_token>` |
| 字符编码 | UTF-8 |
| 源码路由 | [`src/backend/psych_routes.py`](../src/backend/psych_routes.py) |

### 1.1 接口类型图例

| 类型标签 | 含义 |
|---|---|
| 通用能力 | 任务、健康检查、能力注册/编排/升级、分析参数 |
| 数据处理能力 | 数据集、变量、量表结构化、导出 |
| 算法能力 | 统计、管线、ML、特征挖掘、深度学习 |
| 大模型能力 | 语义提取、关联、自然语言检索、分析问答 |

### 1.2 通用响应

成功：

```json
{ "status": "success", "data": { } }
```

业务错误（多数）：

```json
{ "detail": "错误信息字符串" }
```

鉴权失败：

```json
{ "detail": { "code": 6, "msg": "unauthorized" } }
```

### 1.3 异步任务约定

耗时分析（统计、ML 训练、管线运行、DL 训练、ingest 等）统一走 `psych_tasks`：

1. `POST` 提交 → 返回 `{ task_id, status: "pending", module, ... }`
2. `GET /psych/tasks/{task_id}` 轮询，直到 `success` / `failed` / `cancelled`
3. 结果在 `result_json`；统计明细另见 `GET /psych/stats/results/{task_id}`
4. 取消：`POST /psych/tasks/{task_id}/cancel`

任务状态：`pending` → `running` → `success` | `failed` | `cancelled`。

### 1.4 十二模块总览（需求 ↔ 接口）

| 模块 | 名称 | 接口类型 | 主要路径 |
|---|---|---|---|
| 1 | 精神专科多类型数据一体化处理分析 | 数据处理 | `/psych/datasets*` |
| 2 | 统计分析工具与分析管线适配 | 算法 | `/psych/pipelines*`、`/psych/param-templates*` |
| 3 | 一键式统计分析 | 算法 | `/psych/stats/*` |
| 4 | 变量管理 | 数据处理 | `/psych/variables*`、`/psych/var-categories*` |
| 5 | 融合大语言模型 | 大模型 | `/psych/llm/*` |
| 6 | 关键分析参数调整与结果导出 | 通用 / 数据处理 | `/psych/analysis-params`、`/psych/exports*` |
| 7 | 内置机器学习算法库 | 算法 | `/psych/ml/*` |
| 8 | 精神专科数据特征挖掘 | 算法 / 数据处理 | `/psych/features*` |
| 9 | 深度学习算法能力 | 算法 | `/psych/dl/*` |
| 10 | 算法与大模型模块化管理 | 通用能力 | `/psych/capabilities`、`PUT`、`compose` |
| 11 | 服务期内能力升级服务 | 线下人工 | 无系统接口（每季度人工迭代算法库与大模型能力） |
| 12 | 量表智能结构化与分析 | 数据处理 / 算法 | `/psych/scales/*` |
| — | 公共 | 通用能力 | `/psych/health`、`/psych/tasks*` |

---

## 2. 公共接口（通用能力）

### 2.1 `GET /psych/health`

| 字段 | 内容 |
|---|---|
| 归属模块 | 公共 |
| 接口类型 | 通用能力 |
| 接口用途 | 返回 psych 能力注册统计，用于联调探活 |
| 请求参数 | 无（需鉴权） |
| 返回参数 | `service`、`capabilities_total`、`capabilities_enabled`、`by_kind` |
| 对应功能场景 | 页面加载时确认后端 psych 模块可用 |

**返回示例**：

```json
{
  "status": "success",
  "data": {
    "service": "psych",
    "capabilities_total": 20,
    "capabilities_enabled": 18,
    "by_kind": { "stats": 5, "ml": 10, "llm": 2, "dl": 2, "scale": 1 }
  }
}
```

**异常说明**：401 未授权。

---

### 2.2 `GET /psych/tasks`

| 字段 | 内容 |
|---|---|
| 归属模块 | 公共 |
| 接口类型 | 通用能力 |
| 接口用途 | 列出当前用户异步任务 |
| 请求参数 | Query：`module?`（如 `stats`/`ml`/`dl`）、`limit?`（默认 50） |
| 返回参数 | `data.tasks[]`：任务记录列表 |
| 对应功能场景 | 任务中心、历史分析回顾 |

**异常说明**：401；500 数据库错误。

---

### 2.3 `GET /psych/tasks/{task_id}`

| 字段 | 内容 |
|---|---|
| 归属模块 | 公共 |
| 接口类型 | 通用能力 |
| 接口用途 | 查询单个任务状态与结果 |
| 请求参数 | Path：`task_id` |
| 返回参数 | `task_id`、`status`、`module`、`method_id`、`result_json`、`error_message`、`artifact_path`、时间戳等 |
| 对应功能场景 | 轮询统计/训练等异步作业 |

**返回示例**（成功后）：

```json
{
  "status": "success",
  "data": {
    "task_id": "task_xxxx",
    "status": "success",
    "module": "stats",
    "result_json": { "method_ids": ["describe_full"], "batch": { "ok_count": 1 } }
  }
}
```

**异常说明**：404 任务不存在或不属于当前用户；401。

---

### 2.4 `POST /psych/tasks/{task_id}/cancel`

| 字段 | 内容 |
|---|---|
| 归属模块 | 公共 |
| 接口类型 | 通用能力 |
| 接口用途 | 请求取消进行中的任务 |
| 请求参数 | Path：`task_id`；无 body |
| 返回参数 | 更新后的任务行 |
| 对应功能场景 | 用户主动中止长时分析 |
| 异常说明 | 400 无法取消；404；401 |

---

## 3. 模块1 — 精神专科多类型数据一体化处理分析（数据处理）

支持病历文本、精神科评估、量表与心理测评、医嘱用药、检验检查、随访等多类型数据接入与预览查询。

`source_type` 枚举：`text` | `scale` | `assessment` | `order` | `medication` | `lab` | `exam` | `followup` | `mixed` | `table`。

### 3.1 `POST /psych/datasets`

| 字段 | 内容 |
|---|---|
| 接口用途 | 创建数据集元数据 |
| 请求方式 | POST |
| 请求参数 | Body JSON |

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| name | string | 是 | 数据集名称 |
| source_type | string | 否 | 默认 `mixed`，见上表枚举 |
| project_id | int | 否 | 关联项目 |
| description | string | 否 | 描述 |

**返回示例**：

```json
{
  "status": "success",
  "data": {
    "id": 1,
    "name": "抑郁队列基线",
    "source_type": "mixed",
    "status": "active",
    "row_count": 0
  }
}
```

**异常说明**：400 `name 不能为空` / `source_type 无效`；401。  
**功能场景**：建立分析数据集容器后再 ingest。

---

### 3.2 `GET /psych/datasets`

| 字段 | 内容 |
|---|---|
| 接口用途 | 列出当前用户数据集 |
| 请求参数 | Query：`limit?`（默认 50） |
| 返回参数 | `data.datasets[]` |
| 异常说明 | 401；500 |
| 功能场景 | 数据集选择器 |

---

### 3.3 `GET /psych/datasets/{dataset_id}`

| 字段 | 内容 |
|---|---|
| 接口用途 | 数据集详情（含 `file_path`、`schema_json`） |
| 请求参数 | Path：`dataset_id` |
| 异常说明 | 404 `数据集不存在`；401 |

---

### 3.4 `POST /psych/datasets/{dataset_id}/ingest`（deprecated）

| 字段 | 内容 |
|---|---|
| 接口用途 | 整文件上传并解析入库（≤200MB）；**大文件请改用分片 `target=psych_ingest`** |
| 请求方式 | POST `multipart/form-data` |
| 请求参数 | Form：`file`（必填）、`record_type?`（默认 `row`）、`patient_key_col?` |
| 返回参数 | 含 ingest/task 信息；响应可能带 deprecated 提示字段 |
| 异常说明 | 413 超过 200MB；400 数据集/解析错误；401 |
| 功能场景 | 小样本 CSV/表格快速导入 |

---

### 3.5 `GET /psych/datasets/{dataset_id}/preview`

| 字段 | 内容 |
|---|---|
| 接口用途 | 预览数据表前 N 行 |
| 请求参数 | Query：`n_rows?`（默认 20） |
| 返回参数 | 列与行预览 |
| 异常说明 | 400 未 ingest；401 |
| 功能场景 | 导入后字段核对 |

---

### 3.6 `GET /psych/datasets/{dataset_id}/query`

| 字段 | 内容 |
|---|---|
| 接口用途 | 按患者键/记录类型查询已索引记录 |
| 请求参数 | Query：`patient_key?`、`record_type?`、`limit?`（默认 100） |
| 返回参数 | 记录列表 |
| 功能场景 | 按患者查看评估/随访等多类型记录 |

---

## 4. 模块2 — 统计分析工具与分析管线适配（算法）

支持主流统计方法目录适配、管线步骤编排、参数模板保存与调用。

### 4.1 `GET /psych/pipelines/methods`

| 字段 | 内容 |
|---|---|
| 接口用途 | 列出可编排进管线的方法/求解器 |
| 请求参数 | 无 |
| 返回参数 | 方法目录对象 |
| 功能场景 | 管线设计器可选步骤列表 |

---

### 4.2 `POST /psych/pipelines`

| 字段 | 内容 |
|---|---|
| 接口用途 | 创建分析管线 |
| 请求参数 | Body |

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| name | string | 是 | 管线名称 |
| steps | array | 是 | `[{method_id, solver_id?, mapping?, params?}, ...]` |

**返回示例**：`{ "status": "success", "data": { "id": 1, "name": "...", "steps_json": [...] } }`（HTTP 201）

**异常说明**：400；401。  
**功能场景**：保存「质控→描述统计→相关分析」等固定流程。

---

### 4.3 `GET /psych/pipelines`

列出当前用户管线。返回 `data.pipelines[]`。

---

### 4.4 `POST /psych/pipelines/{pipe_id}/run`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| dataset_id | int | 否* | 与 file_path 二选一 |
| file_path | string | 否* | 工作区文件路径 |

返回异步 `task_id`（HTTP 201）。轮询 `/psych/tasks/{task_id}`。  
**功能场景**：一键跑完整分析管线。

---

### 4.5 `POST /psych/param-templates`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| module | string | 是 | 如 `stats` / `ml` |
| method_id | string | 是 | 方法标识 |
| name | string | 是 | 模板名 |
| params | object | 否 | 参数字典 |
| is_default | bool | 否 | 是否默认 |

**功能场景**：保存常用统计/ML 参数预设。

---

### 4.6 `GET /psych/param-templates`

Query：`module?`。返回 `data.templates[]`。

---

## 5. 模块3 — 一键式统计分析（算法）

内置 ≥10 类方法，覆盖描述性统计、相关性、差异性等。目录见 `GET /psych/stats/methods`。

方法清单（`method_id`）：

| method_id | 中文名 | 类别 |
|---|---|---|
| describe_full | 描述性统计 | descriptive |
| groupby_stat | 分组统计 | descriptive |
| pearson_correlation | Pearson相关 | correlation |
| spearman_correlation | Spearman相关 | correlation |
| kendall_correlation | Kendall相关 | correlation |
| welch_t_test | Welch t检验 | difference |
| mann_whitney_u_test | Mann-Whitney U检验 | difference |
| chi_square_independence | 卡方独立性检验 | difference |
| oneway_anova | 单因素方差分析 | difference |
| kruskal_wallis | Kruskal-Wallis检验 | difference |
| normality_test | 正态性检验 | descriptive |
| proportion_ci | 比例置信区间 | descriptive |

### 5.1 `GET /psych/stats/methods`

| 字段 | 内容 |
|---|---|
| 接口用途 | 获取统计方法目录与 params_schema |
| 返回参数 | `data.methods[]`：`method_id`、`name_zh`、`category`、`solver_id`、`params_schema` |
| 功能场景 | 一键分析页勾选方法 |

---

### 5.2 `POST /psych/stats/run`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| method_ids | string[] | 是 | 至少 1 个，须在目录内 |
| dataset_id | int | 否* | 与 file_path 二选一 |
| file_path | string | 否* | |
| mappings | object | 否 | 按 method_id 的列映射，如 `{ "describe_full": { "numeric_columns": ["HAMD_total"] } }` |
| params_by_method | object | 否 | 按方法附加参数 |

**返回示例**（HTTP 201）：

```json
{
  "status": "success",
  "data": {
    "task_id": "task_xxxx",
    "status": "pending",
    "module": "stats"
  }
}
```

**异常说明**：400 `method_ids 不能为空` / `未知统计方法` / `需提供 dataset_id 或 file_path` / `数据集尚未关联数据文件`；401。  
**功能场景**：临床常用量化指标一键计算。

---

### 5.3 `GET /psych/stats/results/{task_id}`

| 字段 | 内容 |
|---|---|
| 接口用途 | 读取任务级统计明细 |
| 返回参数 | `data.task` + `data.results[]`（每方法 `summary_json` / `tables_json`） |
| 异常说明 | 404；401 |
| 功能场景 | 结果表、图表数据源 |

---

## 6. 模块4 — 变量管理（数据处理）

支持自定义创建、分类管理、批量编辑、字段映射、数据字典导出。

### 6.1 `POST /psych/variables`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| var_name | string | 是 | 变量名 |
| display_name | string | 否 | 显示名 |
| dataset_id | int | 否 | 所属数据集 |
| category | string | 否 | 分类名 |
| dtype | string | 否 | 数据类型 |
| dict_code | string | 否 | 字典编码 |
| mapping | object | 否 | 字段映射 |
| relations | any | 否 | 关联维护 |
| description | string | 否 | 说明 |

**功能场景**：登记 HAMD_total 等分析变量。

---

### 6.2 `GET /psych/variables`

Query：`dataset_id?`。返回 `data.variables[]`。

---

### 6.3 `PUT /psych/variables/{var_id}` / `DELETE /psych/variables/{var_id}`

更新（body 为字段补丁）或删除变量。异常：404/400。

---

### 6.4 `POST /psych/variables/batch`

Body：`{ "items": [ { ...变量字段... }, ... ] }`。批量创建/编辑。  
**功能场景**：从 schema 批量导入变量定义。

---

### 6.5 `POST /psych/variables/mapping`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| var_id 或 id | int | 是 | 变量 ID |
| mapping 或 mapping_json | object | 是 | 映射内容 |

**异常说明**：400 `需提供 var_id 与 mapping`。

---

### 6.6 `GET /psych/variables/dictionary/export`

Query：`dataset_id?`、`format=json|csv`（默认 json）。  
返回标准化数据字典。  
**功能场景**：导出后供线下 Python/R 使用。

---

### 6.7 变量分类 CRUD

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/psych/var-categories` | body：`{name, parent_id?, sort_order?}` |
| GET | `/psych/var-categories` | 列表 |
| PUT | `/psych/var-categories/{cat_id}` | 更新 |
| DELETE | `/psych/var-categories/{cat_id}` | 删除 |

---

## 7. 模块5 — 融合大语言模型（大模型能力）

依赖平台 LLM 配置（见 [`Models.md`](Models.md)）。未配置时返回 500 `大模型调用失败: ...`。

### 7.1 `POST /psych/llm/extract`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| text | string | 是 | 非结构化临床文本 |
| extract_type | string | 否 | 默认 `clinical_entities` |
| dataset_id | int | 否 | 关联数据集 |
| record_id | int | 否 | 关联记录 |

**返回示例**（HTTP 201）：含提取实体/结构化字段。  
**功能场景**：病历语义解析与关键信息提取。

---

### 7.2 `POST /psych/llm/relate`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| entities | object | 是 | 实体集合 |
| question | string | 否 | 关联分析问题 |

**功能场景**：诊疗信息关联分析。

---

### 7.3 `POST /psych/llm/query`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| query | string | 是 | 自然语言检索 |
| dataset_id | int | 否 | |
| schema_hint | any | 否 | 表结构提示 |

**功能场景**：自然语言式数据检索。

---

### 7.4 `POST /psych/llm/qa`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| question | string | 是 | 分析问题 |
| context | string | 否 | 附加上下文 |
| dataset_id | int | 否 | |
| task_id | string | 否 | 关联既有分析任务结果 |

**功能场景**：交互式分析问答。

---

## 8. 模块6 — 关键分析参数调整与结果导出（通用 / 数据处理）

### 8.1 `GET /psych/analysis-params`

Query：`scope?`（`qc` | `stats` | `text` | `ml` | `dl` | `general`）。  
返回 `data.params[]`。

---

### 8.2 `PUT /psych/analysis-params`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| scope | string | 是 | 见上枚举 |
| items | object | 是 | `{ "key": value, ... }` 非空 |

**返回示例**：`{ "scope": "stats", "params": [ ... ] }`  
**异常说明**：400 `scope 无效` / `items 必须为非空对象`。  
**功能场景**：调整质控阈值、统计显著性水平、文本解析参数等。

---

### 8.3 `POST /psych/exports`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| kind | string | 是 | 导出种类标识（如 `stats_table` / `dataset`） |
| format | string | 否 | `csv` \| `parquet` \| `json` \| `rds_compat`（默认 csv） |
| task_id | string | 否 | 从任务结果导出 |
| dataset_id | int | 否 | 从数据集导出 |
| data | list\|object | 否 | 直接传入数据 |
| note | string | 否 | 备注 |

`rds_compat`：写出 CSV/JSON + `*_rds_manifest.json`，便于 R `fread` / `jsonlite`。

**返回示例**（HTTP 201）：`{ "export_id": "exp_xxxx", "format": "csv", ... }`

---

### 8.4 `GET /psych/exports/{export_id}/download`

文件流下载。异常：404。  
**功能场景**：统计报表、结构化数据集导出到本地做 Python/R 深度分析。

---

## 9. 模块7 — 内置机器学习算法库（算法）

内置 ≥10 种算法（可 `psych.ml.register_algo()` 扩展）：

`logistic_regression`、`random_forest`、`xgboost`、`lightgbm`、`svm_rbf`、`knn_k_selection`、`cox_regression`、`hist_gradient_boosting`、`linear_regression`、`lasso_cv_select`。

### 9.1 `GET /psych/ml/algorithms`

返回 `data.algorithms[]`（`algo_id`、`name_zh`、`task_type`、`params_schema`）。

---

### 9.2 `POST /psych/ml/train`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| algo_id | string | 是 | 算法 ID |
| dataset_id | int | 否* | 与 file_path 二选一 |
| file_path | string | 否* | |
| mapping | object | 否 | 如 `{id_col, feature_columns, target_col}` |
| params | object | 否 | 算法超参 |
| model_name | string | 否 | 模型名称 |
| sync_resource | bool | 否 | 默认 true，同步到资源模型库 |

异步返回 `task_id`（HTTP 201）。完成后 `result_json` 含 `psych_model_id`、`metrics`、`model_path`。  
**功能场景**：复发预测等分类/回归/生存建模。

---

### 9.3 `POST /psych/ml/predict`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| model_id | int | 是 | 已训练模型 ID |
| dataset_id | int | 否 | |
| file_path | string | 否 | |
| rows | object[] | 否 | 直接传入待预测行 |

---

### 9.4 `GET /psych/ml/models` / `GET /psych/ml/models/{model_id}`

列表与详情。异常：404。

---

## 10. 模块8 — 精神专科数据特征挖掘（算法 / 数据处理）

覆盖统计特征、时序特征、文本语义特征。

### 10.1 `POST /psych/features/extract`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| feature_type | string | 是 | `stat` \| `ts` \| `text` |
| dataset_id | int | 否* | |
| file_path | string | 否* | |
| feature_set_name | string | 否 | 特征集名称 |
| mapping | object | 否 | 列映射 |
| params | object | 否 | 额外参数 |

HTTP 201。**功能场景**：为 ML/DL 训练准备特征表。

---

### 10.2 `GET /psych/features` / `GET /psych/features/{feat_id}`

列表（Query：`dataset_id?`）与详情。异常：404。

---

### 10.3 `GET /psych/features/{feat_id}/download`

导出已落盘的特征矩阵（统计 / 时序 / 文本语义结果通用），与详情中 `feature_matrix_path` 指向的内容一致。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| feat_id | int | 是 | 路径参数，特征集 ID |
| format | string | 否 | `csv`（默认，直出原文件）\| `json`（同一矩阵转 records） |

**响应**：文件流（非 JSON 包装）；仍需 `Authorization: Bearer`，无需 `Content-Type`。  
建议文件名：`{feature_set_name}_{feature_type}.{csv|json}`。

**异常说明**：404 `特征集不存在` / `特征矩阵文件不存在`；400 `format 无效`。  
**功能场景**：本地化留存与二次分析；适配前端 blob 下载。

---

## 11. 模块9 — 深度学习算法能力（算法）

内置 ≥2 类：`text_cnn`、`text_transformer`。可选依赖见仓库根 `requirements-dl.txt`（`torch`）。未安装时自动 sklearn 回退。

### 11.1 `GET /psych/dl/models`

返回 `data.models[]`：`model_id`、`name_zh`、`modality`、`framework`。

---

### 11.2 `POST /psych/dl/train`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| model_id | string | 是 | `text_cnn` 或 `text_transformer` |
| texts | string[] | 是 | 文本样本 |
| labels | int[] | 是 | 与 texts 等长标签 |
| epochs | int | 否 | 默认 3 |

异步 `task_id`（HTTP 201）。  
**功能场景**：病历文本分类等 DL 训练。

---

### 11.3 `POST /psych/dl/infer`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| meta_path | string | 是 | 须在用户 psych 目录内的模型元数据路径 |
| texts | string[] | 是 | 待推理文本 |

返回 `predictions` 等。异常：400 路径非法/模型不存在。

---

## 12. 模块10 — 算法与大模型模块化管理（通用能力）

支持算法/大模型能力的模块化配置、统一管理、按需调用与组合编排。

### 12.1 `GET /psych/capabilities`

Query：`kind?`（如 `stats`/`ml`/`llm`/`dl`/`scale`）。  
返回 `data.capabilities[]`（`capability_id`、`kind`、`version`、`enabled`、`impl_ref`、`meta_json`）。

---

### 12.2 `PUT /psych/capabilities/{capability_id}`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| enabled | bool | 否 | 启停 |
| version | string | 否 | 版本号 |
| meta_json | any | 否 | 元数据 |

**异常说明**：400 `无更新字段` / 能力不存在。  
**功能场景**：按业务场景开关某类算法或 LLM 能力。

---

### 12.3 `POST /psych/capabilities/compose`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| capability_ids | string[] | 是 | 已启用能力 ID 列表 |
| name | string | 否 | 生成管线名称 |

将能力编排为一条 `psych_pipelines` 记录（HTTP 201）。  
**异常说明**：400 `capability_ids 不能为空` / `能力已关闭`。  
**功能场景**：灵活组合「统计+特征+ML」部署。

---

## 13. 模块11 — 服务期内能力升级服务（线下人工）

本条款仅为**线下人工服务承诺**：服务期内由人工持续迭代升级算法库与大模型能力、适配精神专科临床新需求与新场景，约定每三个月完成一次版本迭代与能力更新。

**无系统接口、无前端页面、无定时/自动升级逻辑。** 能力清单与启停编排见模块10（`/psych/capabilities*`）。已有库若残留历史表，可手工执行：`DROP TABLE IF EXISTS psych_capability_changelog`。

---

## 14. 模块12 — 量表智能结构化与分析（数据处理 / 算法）

内置：`PHQ9`、`GAD7`、`HAMD`、`HAMA`、`PANSS`。支持结构化汇总、自动评分、趋势、分组对比、条目/总分导出。

### 14.1 `GET /psych/scales/forms`

返回 `data.forms[]`（量表定义、条目、计分规则）。

---

### 14.2 `POST /psych/scales/parse`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| scale_code | string | 是 | 如 `PHQ9` |
| raw | any | 是 | dict / 分数数组 / JSON 字符串 / 逗号分隔 |
| patient_key | string | 否 | |
| dataset_id | int | 否 | |

返回结构化 `item_scores`。异常：400 `未知量表`。

---

### 14.3 `POST /psych/scales/score`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| scale_code | string | 是 | |
| item_scores | object | 是 | 条目分 |
| patient_key | string | 是 | 患者键 |
| dataset_id | int | 否 | |

HTTP 201，返回 `total`、子分、入库 `id`。  
**功能场景**：自动评分入库。

---

### 14.4 `GET /psych/scales/scores`

Query：`scale_code?`、`patient_key?`、`dataset_id?`、`limit?`（默认 200）。  
返回 `data.scores[]`。

---

### 14.5 `GET /psych/scales/trend`

Query：`patient_key`（必填）、`scale_code`（必填）。  
返回时间序列总分，用于趋势图。

---

### 14.6 `POST /psych/scales/compare`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| scale_code | string | 是 | |
| group_a | string[] | 是 | 患者键组 A |
| group_b | string[] | 是 | 患者键组 B |

**功能场景**：治疗组 vs 对照组量表总分对比。

---

### 14.7 `GET /psych/scales/export`

Query：`scale_code?`、`dataset_id?`。  
导出条目级与总分级精细化数据。  
**功能场景**：支撑后续统计与建模。

---

## 15. 数据库表（新增）

权威 DDL：[`src/db/psych_schema.py`](../src/db/psych_schema.py)；运维脚本：[`scripts/sql/psych_tables.sql`](../scripts/sql/psych_tables.sql)。

主要表：`psych_datasets`、`psych_data_records`、`psych_tasks`、`psych_stats_results`、`psych_ml_models`、`psych_variables`、`psych_param_templates`、`psych_analysis_params`、`psych_features`、`psych_scale_forms`、`psych_scale_scores`、`psych_llm_extractions`、`psych_exports`、`psych_capabilities`、`psych_pipelines`。

---

## 16. 推荐联调顺序

1. 登录拿 JWT → `GET /psych/health`
2. `POST /psych/datasets` + ingest（或分片 `psych_ingest`）→ `preview`
3. （可选）变量字典 `/psych/variables*`
4. `POST /psych/stats/run` → 轮询 task → `GET /psych/stats/results/{task_id}`
5. `/psych/ml/train` 或 `/psych/features/extract` / `/psych/dl/train`
6. `/psych/scales/parse` + `score` + `trend` / `compare`
7. `/psych/llm/*`（需 LLM 配置）
8. `/psych/exports` 下载；`/psych/capabilities` 管理与编排

同源示意 Demo：`http://<host>:52716/psych-app`。
