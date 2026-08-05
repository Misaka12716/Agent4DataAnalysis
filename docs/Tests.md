# 测试说明

本文档说明仓库 [`tests/`](../tests/) 目录下各测试模块的用途、共享配置与运行方式。

`tests/` 按**业务类别**分子目录（`auth/`、`project/`、`upload/`、`reader/`、`analysis/`、`agent/`、`runtime/`）；根级保留 [`conftest.py`](../tests/conftest.py) 与 [`fixtures/`](../tests/fixtures/)。默认不连真实 MySQL、默认关闭 Cube 沙箱；绝大多数用例为单测或带 Store mock 的轻量 API 测。例外：[`tests/analysis/functional/`](../tests/analysis/functional/) 的 2.1.4 Psych 功能集成测**默认连** `.env` 中的 MySQL 与 LLM；另有可选 live 沙箱用例。

对话数据分析的**效果对比实验**为独立脚本（需运行中后端 + LLM），**不纳入默认 pytest**，见 [§6](#6-对话数据分析实验)。

---

## 1. 如何运行

在仓库根目录（推荐使用 conda 环境 `agentPlatform`）：

```bash
# 默认全量（live 沙箱用例会 skip，除非设置了环境变量）
pytest tests/

# 排除名称含 live 的用例
pytest tests/ -k "not live"

# 含真实 Cube 沙箱 live 用例（另需 CUBE_TEMPLATE_ID 及沙箱凭证）
RUN_CUBE_SANDBOX_INTEGRATION=1 pytest tests/ -k live

# 只跑某一业务目录
pytest tests/auth/ -q
```

配置见根目录 [`pytest.ini`](../pytest.ini)：`testpaths = tests`，`pythonpath = src`，并注册 `integration` marker。

---

## 2. 共享配置

### [`conftest.py`](../tests/conftest.py)

| 行为 | 说明 |
|------|------|
| `sys.path` | 将 `src/` 加入路径，便于从仓库根运行 |
| `pymysql.connect` stub | 启动时 patch，避免 import 阶段连真实 MySQL |
| `disable_sandbox_by_default`（autouse） | 设置 `CUBE_SANDBOX_ENABLED=0` |
| `isolated_workspaces` | 将工作区根隔离到 pytest `tmp_path` |
| `enable_sandbox` | 打开沙箱相关环境变量（供沙箱测使用） |

### `integration` marker

标记可能依赖外部服务的用例；其中 `test_live_sandbox_roundtrip` 仍需 `RUN_CUBE_SANDBOX_INTEGRATION=1` 才会真正执行。

---

## 3. 目录结构与各测试文件

```text
tests/
  conftest.py
    fixtures/                 # 演示 / 验收样例；table/、imaging/、text/ 会被部分 pytest 引用
    table/                  # mixed-types.{csv,tsv,xlsx,xls}、large-dataset.csv
    imaging/                # 患者CT.dcm
    text/                   # sample.{txt,md,json,yaml,xml,html,log}
    conversation_analysis/  # 对话分析实验素材（不进默认 pytest）
  auth/
  project/
  upload/
  reader/
  analysis/
  agent/
  runtime/
```

### 鉴权与权限 — `auth/`

| 文件 | 用途 |
|------|------|
| [`test_auth.py`](../tests/auth/test_auth.py) | JWT 创建/解码/过期；`assert_session_access` 会话归属（成功 / 403 / 404） |
| [`test_rbac.py`](../tests/auth/test_rbac.py) | 用户封禁、角色权限解析、owner/admin 全量权限、项目与 session 访问断言 |
| [`test_collaboration.py`](../tests/auth/test_collaboration.py) | 项目成员协同 API：按手机号查找/加人、拒绝把 owner 当成员、成员只读列表、共享标记 |
| [`test_session_access_list.py`](../tests/auth/test_session_access_list.py) | 可访问会话列表（自有+共享合并、按项目过滤）、`can_manage_project`、admin 列项目 |
| [`test_access_log_filter.py`](../tests/auth/test_access_log_filter.py) | 抑制成功的 `GET /auth/me` 访问日志 |

### 项目与工作区 — `project/`

| 文件 | 用途 |
|------|------|
| [`test_project_api.py`](../tests/project/test_project_api.py) | 项目 API：创建/列表/详情、归档恢复、上传资产、会话上传改名、tree、rename；FastAPI TestClient + Store mock |
| [`test_session_delete_file.py`](../tests/project/test_session_delete_file.py) | 会话工作区文件删除：成功、404、路径穿越、系统文件/目录拒绝、无 `data_delete` 403、资产清理 |
| [`test_project_lifecycle.py`](../tests/project/test_project_lifecycle.py) | `promote_session_outputs` 沉淀到 `outputs/`；`snapshot_project_on_archive` 归档快照 |
| [`test_workspace_user_isolation.py`](../tests/project/test_workspace_user_isolation.py) | 工作区路径布局、多用户隔离、DB 路径优先、项目目录结构与从绝对路径解析 session |

### 上传 — `upload/`

| 文件 | 用途 |
|------|------|
| [`test_upload_naming.py`](../tests/upload/test_upload_naming.py) | 上传文件名去重（冲突追加 `(N)`）、路径穿越剥离、非法字符安全化；含中文 DICOM 文件名 |
| [`test_upload_file_types.py`](../tests/upload/test_upload_file_types.py) | 扩展名白名单、分类、MIME 猜测与 Reader 分类一致性；表格四格式（csv/tsv/xlsx/xls）；文本七格式（txt/md/json/yaml/xml/html/log）；含中文 `.dcm` / 表格/文本样例名 |
| [`test_chunked_upload.py`](../tests/upload/test_chunked_upload.py) | 分片上传服务层 + session/resources 路由冒烟；表格四格式与文本七格式 init；含 `large-dataset.csv` 多片合并与中文 DICOM init |
| [`test_format_registry.py`](../tests/upload/test_format_registry.py) | FormatRegistry：内置 PDF、自定义规则（如 `.mdx`）、禁止删除内置规则 |

### Reader — `reader/`

| 文件 | 用途 |
|------|------|
| [`test_reader_handlers.py`](../tests/reader/test_reader_handlers.py) | 各格式解析：CSV/TSV/GBK、xlsx、xls（xlrd）、docx、pdf、text 七格式、png；`mixed-types.{csv,tsv,xlsx,xls}` 与 `large-dataset.csv`、`imaging/患者CT.dcm`、`text/sample.*` 真实样例 digest；依赖检查 |

### 分析能力 — `analysis/`

| 文件 | 用途 |
|------|------|
| [`test_template_api.py`](../tests/analysis/test_template_api.py) | 模板 list/create 鉴权（普通用户 vs admin）；`/analysis/template-run` 鉴权与资产登记 |
| [`test_psych_api.py`](../tests/analysis/test_psych_api.py) | `/psych` 统计/ML 目录≥10、量表计分、DL sklearn 回退、能力 bootstrap、**全路径注册断言** |
| [`test_psych_*_api.py`](../tests/analysis/) | **2.1.4 全量 HTTP 契约测**（health/tasks、datasets、pipeline、stats、variables、llm、export、ml、features、dl、capabilities、scales、demo）；共享 [`psych_test_helpers.py`](../tests/analysis/psych_test_helpers.py) |
| [`functional/`](../tests/analysis/functional/) | **2.1.4 功能集成测**（真 MySQL + 真 LLM；ingest/异步任务/量表/导出等）；`@pytest.mark.integration`，**默认执行** |
| [`test_dq213_api.py`](../tests/analysis/test_dq213_api.py) | 2.1.3 质控：QC/PHI/timeline API |

完整接口×场景矩阵见专文：[**PsychAPITests.md**](PsychAPITests.md)。  
运行契约：`pytest tests/analysis/test_psych_*.py -q`；运行功能：`pytest tests/analysis/functional/ -q`。

### 编排与 Agent — `agent/`

| 文件 | 用途 |
|------|------|
| [`test_orchestrator_routing.py`](../tests/agent/test_orchestrator_routing.py) | 编排 `clamp_route`、Coder 修正上限、无数据强制 reporter、可分析工作区判断 |
| [`test_session_memory_prompt.py`](../tests/agent/test_session_memory_prompt.py) | SESSION_MEMORY prompt 去掉 digest 第 4 节；复用 workspace_digest 不重复调 Reader |
| [`test_coder_files_detail_cap.py`](../tests/agent/test_coder_files_detail_cap.py) | Coder `files_detail` 超大 JSON 预览截断与长度上限 |

### Runtime 与沙箱 — `runtime/`

| 文件 | 用途 |
|------|------|
| [`test_runtime_local.py`](../tests/runtime/test_runtime_local.py) | 本地 Runtime：读写、跑 Python、超时、路径穿越与命令注入拒绝（真实本地 subprocess） |
| [`test_runtime_factory.py`](../tests/runtime/test_runtime_factory.py) | Runtime 工厂：默认 local、开沙箱用 adapter、失败回退、实例缓存 |
| [`test_sandbox_integration.py`](../tests/runtime/test_sandbox_integration.py) | Worker/写文件经 mock 沙箱；`test_live_sandbox_roundtrip`（`@pytest.mark.integration`）可选真连 Cube |

### 演示数据与可引用样例

[`fixtures/`](../tests/fixtures/) 存放手工验收 / 上传用的样例文件。临床导入模板等仍以手工验收为主；**`table/`、`imaging/`、`text/` 下文件会被** `reader/` / `upload/` 测试直接引用。

| 文件 | 说明 |
|------|------|
| `mental_health_sample.xlsx` | 横截面心理健康样例 |
| `mental_health_longitudinal_sample.xlsx` | 纵向心理健康样例 |
| `correlation_clinical_sample.csv` | 相关分析临床样例 |
| `risk_training_sample.csv` | 风险训练样例 |
| `reference_import_template.csv` | 参考范围导入模板 |
| `followup_import_template.csv` | 随访导入模板 |
| `table/mixed-types.csv` | 混合类型列（~500 行）；首行为 Sample-Files 注释行（Reader 默认把第 0 行当表头，不会按 `#` 跳过） |
| `table/mixed-types.tsv` / `.xlsx` / `.xls` | 由 `mixed-types.csv` 派生（见 `scripts/generate_table_format_fixtures.py`）；四格式上传白名单与 Reader digest 覆盖 |
| `table/large-dataset.csv` | 大表（~10MB / ~10 万行）；分片上传与大表 digest 验收 |
| `imaging/患者CT.dcm` | DICOM CT（~12MB，中文文件名）；imaging 分类与 pydicom digest |
| `text/sample.{txt,md,json,yaml,xml,html,log}` | 小文本样例（见 `scripts/generate_text_format_fixtures.py`）；文本七格式上传白名单与 Reader digest 覆盖 |

对话分析实验素材目录见 [`fixtures/conversation_analysis/`](../tests/fixtures/conversation_analysis/)（外部导入，不进默认 pytest）。

---

## 4. 分类速览

| 类型 | 位置 |
|------|------|
| 纯单元（无 HTTP / 无真实 DB） | `auth/`（部分）、`upload/`、`agent/`、`project/test_project_lifecycle`、`project/test_workspace_user_isolation`、`runtime/test_runtime_factory`；部分 `reader/` |
| 轻量 API（FastAPI TestClient + Store mock） | `project/test_project_api`、`project/test_session_delete_file`、`auth/test_collaboration`、`analysis/test_template_api` 等 |
| 需本地执行环境 | `runtime/test_runtime_local`（真实 `python3`）；`reader/test_reader_handlers`（可选 xlrd / python-docx / reportlab 等） |
| 外部集成 | `runtime/test_sandbox_integration` 中的 live Cube；**以及** `analysis/functional`（真 MySQL + 真 LLM，默认执行） |
| 对话分析效果对比 | **独立脚本** [`scripts/run_conversation_analysis_experiment.py`](../scripts/run_conversation_analysis_experiment.py)，见 §6 |

仓库内**没有**浏览器级或全链路 e2e 套件纳入默认 pytest。

---

## 5. 相关文档

- 启动与环境：[StartInstruction.md](StartInstruction.md)
- 鉴权：[AUTH.md](AUTH.md)
- 权限：[RBAC.md](RBAC.md)
- 产品前端对接：[2.1.1FrontendIntegrationGuide.md](2.1.1FrontendIntegrationGuide.md)
- 后端 API（含 `/run-analysis`）：[BackendAPI.md](BackendAPI.md)
- **2.1.4 Psych 接口测试**：[PsychAPITests.md](PsychAPITests.md)（对应 [PsychAPI.md](PsychAPI.md)）

---

## 6. 对话数据分析实验

用于对比**单文件**与**多文件组合**场景下，会话主链路 `POST /run-analysis`（SSE）的输出效果。依赖运行中的 FastAPI 与 LLM，**不**通过 `pytest tests/` 执行。

### 素材目录

```text
tests/fixtures/conversation_analysis/
  README.md
  prompts/default.txt
  cases/single/<case_id>/   # meta.json + 数据文件（外部导入）
  cases/multi/<case_id>/
  results/                  # 运行产物（gitignore）
```

约定与示例见 [`tests/fixtures/conversation_analysis/README.md`](../tests/fixtures/conversation_analysis/README.md)。

### 运行

```bash
# 需后端已启动；用已有 Bearer Token
export TOKEN="<access_token>"
python scripts/run_conversation_analysis_experiment.py --token "$TOKEN"

# 仅校验素材与 meta，不调 API
python scripts/run_conversation_analysis_experiment.py --dry-run

# 只跑单文件或只跑多文件
python scripts/run_conversation_analysis_experiment.py --token "$TOKEN" --only single
```

常用参数 / 环境变量：`BASE_URL`（默认 `http://localhost:52716`）、`--cases-dir`、`--prompt-file`、`--only single|multi|all`、`--dry-run`。

结果写入 `tests/fixtures/conversation_analysis/results/<run_id>/`：`manifest.json`、`summary.md`，以及每案的 `events.jsonl` / `report.md` / `snapshot.json`。
