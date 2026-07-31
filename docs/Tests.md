# 测试说明

本文档说明仓库 [`tests/`](../tests/) 目录下各测试模块的用途、共享配置与运行方式。

`tests/` 为**扁平目录**（无 `unit/` / `integration/` / `e2e/` 子目录）。默认不连真实 MySQL、默认关闭 Cube 沙箱；绝大多数用例为单测或带 Store mock 的轻量 API 测。唯一可选的外部依赖是 live 沙箱用例。

---

## 1. 如何运行

在仓库根目录：

```bash
# 默认全量（live 沙箱用例会 skip，除非设置了环境变量）
pytest tests/

# 排除名称含 live 的用例
pytest tests/ -k "not live"

# 含真实 Cube 沙箱 live 用例（另需 CUBE_TEMPLATE_ID 及沙箱凭证）
RUN_CUBE_SANDBOX_INTEGRATION=1 pytest tests/ -k live
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

## 3. 各测试文件说明

### 鉴权与权限

| 文件 | 用途 |
|------|------|
| [`test_auth.py`](../tests/test_auth.py) | JWT 创建/解码/过期；`assert_session_access` 会话归属（成功 / 403 / 404） |
| [`test_rbac.py`](../tests/test_rbac.py) | 用户封禁、角色权限解析、owner/admin 全量权限、项目与 session 访问断言 |
| [`test_collaboration.py`](../tests/test_collaboration.py) | 项目成员协同 API：按手机号查找/加人、拒绝把 owner 当成员、成员只读列表、共享标记 |
| [`test_session_access_list.py`](../tests/test_session_access_list.py) | 可访问会话列表（自有+共享合并、按项目过滤）、`can_manage_project`、admin 列项目 |

### 项目与工作区

| 文件 | 用途 |
|------|------|
| [`test_project_api.py`](../tests/test_project_api.py) | 项目 API：创建/列表/详情、归档恢复、上传资产、会话上传改名、tree、rename；FastAPI TestClient + Store mock |
| [`test_project_lifecycle.py`](../tests/test_project_lifecycle.py) | `promote_session_outputs` 沉淀到 `outputs/`；`snapshot_project_on_archive` 归档快照 |
| [`test_workspace_user_isolation.py`](../tests/test_workspace_user_isolation.py) | 工作区路径布局、多用户隔离、DB 路径优先、项目目录结构与从绝对路径解析 session |

### 模板

| 文件 | 用途 |
|------|------|
| [`test_template_api.py`](../tests/test_template_api.py) | 模板 list/create 鉴权（普通用户 vs admin）；`/analysis/template-run` 鉴权与资产登记 |

### 精神专科分析 `/psych`

| 文件 | 用途 |
|------|------|
| [`test_psych_api.py`](../tests/test_psych_api.py) | 统计/ML 目录≥10、量表计分、DL sklearn 回退、能力 bootstrap、路由注册 |

### 上传与 Reader

| 文件 | 用途 |
|------|------|
| [`test_upload_naming.py`](../tests/test_upload_naming.py) | 上传文件名去重（冲突追加 `(N)`）、路径穿越剥离、非法字符安全化 |
| [`test_upload_file_types.py`](../tests/test_upload_file_types.py) | 扩展名白名单、分类、MIME 猜测与 Reader 分类一致性 |
| [`test_format_registry.py`](../tests/test_format_registry.py) | FormatRegistry：内置 PDF、自定义规则（如 `.mdx`）、禁止删除内置规则 |
| [`test_reader_handlers.py`](../tests/test_reader_handlers.py) | 各格式解析：CSV/TSV/GBK、xlsx、xls（可选）、docx、pdf、text/json、png；依赖检查 |

### 编排与 Agent 辅助逻辑

| 文件 | 用途 |
|------|------|
| [`test_orchestrator_routing.py`](../tests/test_orchestrator_routing.py) | 编排 `clamp_route`、Coder 修正上限、无数据强制 reporter、可分析工作区判断 |
| [`test_session_memory_prompt.py`](../tests/test_session_memory_prompt.py) | SESSION_MEMORY prompt 去掉 digest 第 4 节；复用 workspace_digest 不重复调 Reader |
| [`test_coder_files_detail_cap.py`](../tests/test_coder_files_detail_cap.py) | Coder `files_detail` 超大 JSON 预览截断与长度上限 |

### Runtime 与沙箱

| 文件 | 用途 |
|------|------|
| [`test_runtime_local.py`](../tests/test_runtime_local.py) | 本地 Runtime：读写、跑 Python、超时、路径穿越与命令注入拒绝（真实本地 subprocess） |
| [`test_runtime_factory.py`](../tests/test_runtime_factory.py) | Runtime 工厂：默认 local、开沙箱用 adapter、失败回退、实例缓存 |
| [`test_sandbox_integration.py`](../tests/test_sandbox_integration.py) | Worker/写文件经 mock 沙箱；`test_live_sandbox_roundtrip`（`@pytest.mark.integration`）可选真连 Cube |

### 演示数据（非 pytest 夹具）

[`fixtures/`](../tests/fixtures/) 存放手工验收 / 上传用的样例文件，**一般不被测试代码直接引用**：

| 文件 | 说明 |
|------|------|
| `mental_health_sample.xlsx` | 横截面心理健康样例 |
| `mental_health_longitudinal_sample.xlsx` | 纵向心理健康样例 |
| `correlation_clinical_sample.csv` | 相关分析临床样例 |
| `risk_training_sample.csv` | 风险训练样例 |
| `reference_import_template.csv` | 参考范围导入模板 |
| `followup_import_template.csv` | 随访导入模板 |

---

## 4. 分类速览

| 类型 | 文件 |
|------|------|
| 纯单元（无 HTTP / 无真实 DB） | `test_auth`、`test_rbac`、`test_session_access_list`、`test_upload_*`、`test_format_registry`、`test_orchestrator_routing`、`test_session_memory_prompt`、`test_coder_files_detail_cap`、`test_project_lifecycle`、`test_workspace_user_isolation`、`test_runtime_factory`；部分 `test_reader_handlers` |
| 轻量 API（FastAPI TestClient + Store mock） | `test_project_api`、`test_collaboration`、`test_template_api` |
| 需本地执行环境 | `test_runtime_local`（真实 `python3`）；`test_reader_handlers`（可选 xlrd / python-docx / reportlab 等） |
| 外部集成 | 仅 `test_sandbox_integration` 中的 live Cube 用例 |

仓库内**没有**浏览器级或全链路 e2e 套件。

---

## 5. 相关文档

- 启动与环境：[StartInstruction.md](StartInstruction.md)
- 鉴权：[AUTH.md](AUTH.md)
- 权限：[RBAC.md](RBAC.md)
- 产品前端对接：[2.1.1FrontendIntegrationGuide.md](2.1.1FrontendIntegrationGuide.md)
