# 启动指令

## 前置条件

1. **MySQL 已就绪**（会话持久化依赖）。
2. **默认使用本地 Runtime**（会话工作区 `tmp/workspaces/<user_id>/<project_id>/sessions/<session_id>/`），无需 Cube Sandbox 即可创建会话、上传与分析。
3. **Python 分工**：
   - 主服务：conda `agentPlatform`（`/opt/miniconda/envs/agentPlatform`）
   - Worker：conda `agentPlatform-runner`（本机 `/data/pjw/.conda/envs/agentPlatform-runner`），由 `.env` 的 `RUNNER_PYTHON` 指定；依赖见 [`requirements-runner.txt`](../requirements-runner.txt)
   - **conda `base`（`/opt/miniconda`）是根环境，不可改名，勿当作专用 Runner**
4. 若需可选 Cube Sandbox 隔离执行，见下方「Cube Sandbox 配置（可选）」。

## 配置环境并安装依赖

### 1) 主服务环境（FastAPI / LangGraph / Reader）

```
conda create -n agentPlatform python=3.13.7
conda activate agentPlatform
python -m pip install -r requirements.txt
```

> 主服务依赖含上传文件解析库：`python-docx`（DOCX）、`pypdf`/`PyPDF2`（PDF）、`pydicom`（DICOM）、`openpyxl`/`xlrd`（Excel）、`Pillow`（图片）。若按 `src/requirements.txt` 安装，请确保与根目录清单一致。

### 2) Runner 执行环境（Worker 运行的 Python 代码）

专用命名环境 `agentPlatform-runner`（与主服务隔离）。在仓库根 `.env` 设置：

```bash
# .env
RUNNER_PYTHON=/data/pjw/.conda/envs/agentPlatform-runner/bin/python

# 重建（本机 / 盘空间不足时勿 clone base，按清单安装即可）：
# bash scripts/setup-runner-env.sh
# 或：
# conda create -y -p /data/pjw/.conda/envs/agentPlatform-runner python=3.9
# /data/pjw/.conda/envs/agentPlatform-runner/bin/python -m pip install -r requirements-runner.txt

bash scripts/diagnose-runner-env.sh
```

> Worker 只使用 `RUNNER_PYTHON`，不会使用主环境中的 langchain/fastapi 等包。conda **base 不可 `conda rename`**。

## 本地 Runtime（默认）

执行层位于 [`src/runtime/`](../src/runtime/)，通过 `ensure_runtime(session_id)` 统一读写文件与运行 `python3` 脚本。

- **工作区路径**：项目 `tmp/workspaces/<user_id>/<project_id>/`；会话 `.../sessions/<session_id>/`（legacy 旧路径只读兼容，迁移见 `scripts/migrate-legacy-sessions.sh`）
- **代码执行**：Worker 使用 `.env` 中的 `RUNNER_PYTHON`（本机为 `agentPlatform-runner`），与 FastAPI 主环境隔离
- 默认 `CUBE_SANDBOX_ENABLED=0`（见 [`src/runtime/config.py`](../src/runtime/config.py)）

**诊断脚本**：

```bash
bash scripts/diagnose-runner-env.sh   # Runner 环境（pandas 等）
bash scripts/diagnose-runtime.sh      # 工作区 write/run
```

## Cube Sandbox 配置（可选）

仅在需要 MicroVM 隔离时启用：设置 `CUBE_SANDBOX_ENABLED=1` 并部署 Cube（见 [`Cubesandbox-deploy.md`](Cubesandbox-deploy.md)）。

启动前确认 Cube API 可达：

```bash
curl --noproxy '*' http://127.0.0.1:3000/health
```

**沙箱故障一键诊断**（启用沙箱且创建会话失败时）：

```bash
bash scripts/diagnose-cube-sandbox.sh
```

### 需要覆盖默认值时

- **改环境变量（推荐）**：编辑仓库根目录 [`.env`](../.env)（可从 [`.env.example`](../.env.example) 复制）。启动时由 `backend.server` / `configs.config` 自动 `load_dotenv`（`override=False`，已有进程环境变量优先）。
- **改代码默认值**：仅当需要改无 env 覆盖的硬编码路径等时，编辑 [`src/sandbox/config.py`](../src/sandbox/config.py) 或 [`src/runtime/config.py`](../src/runtime/config.py)。

常用覆盖示例：

```bash
# 写入 .env，或启动前 export：
export RUNNER_PYTHON=/data/pjw/.conda/envs/agentPlatform-runner/bin/python
export CUBE_SANDBOX_ENABLED=1
export CUBE_TEMPLATE_ID=<你的模板ID>
```

详见 [`Cubesandbox-agent-integration.md`](Cubesandbox-agent-integration.md)。

## 启动

在**仓库根目录**执行：

```bash
# 首次初始化（模板 + fixtures）
bash scripts/init-platform.sh

# 日常启动
bash scripts/start.sh                     # 仅后端 52716
bash scripts/start.sh --with-frontend     # 后端 + 联调前端 8501

# 查看 / 停止
bash scripts/status.sh
bash scripts/stop.sh              # 仅停后端
bash scripts/stop.sh --all        # 后端 + 联调前端
```

| 脚本 | 说明 |
|------|------|
| [`scripts/init-platform.sh`](../scripts/init-platform.sh) | 首次初始化：fixtures + 模板种子（`--acceptance` 含验收账号） |
| [`scripts/start.sh`](../scripts/start.sh) | 停旧进程并后台启动（`--with-frontend` / `--foreground`） |
| [`scripts/stop.sh`](../scripts/stop.sh) | 停止服务（`--all` 含前端） |
| [`scripts/status.sh`](../scripts/status.sh) | 进程与健康检查 |

日志目录：`tmp/logs/`（`backend.log`、`frontend.log`）。

健康检查：

```bash
curl http://localhost:52716/health
```

### 联调前端说明

| 项 | 说明 |
|----|------|
| 访问地址 | `http://<主机>:8501`（8501 仅作联调示例，正式运行可不开） |
| 验收登录 | 可选：`bash scripts/init-platform.sh --acceptance` + `ACCEPTANCE_MODE=1 bash scripts/start.sh --with-frontend`，侧栏 `13800000000` / `888888` |
| 分析入口 | 「模板分析」页选模板跑分析；「流式分析」页跑 LLM SSE |
| 正式前端 | 按 [`TemplateAPI.md`](TemplateAPI.md) 对接 HTTP；Streamlit 不是产品前端 |
| 个人资源管理 | 原生 HTML：`http://<主机>:52716/resources/ui`（`/static/resources`）；API 见 [`ResourceAPI.md`](ResourceAPI.md) |

**演示数据**：运行 `bash scripts/init-platform.sh` 后位于 `tests/fixtures/`（横截面 baseline 样本 + 纵向随访样本）

模板 API 详见 [`TemplateAPI.md`](TemplateAPI.md)。个人资源管理 API 详见 [`ResourceAPI.md`](ResourceAPI.md)。

> 资源管理额外依赖：主环境需安装 `nibabel`（NIfTI 预览）。资源磁盘目录默认 `tmp/resources/<user_id>/`，可用环境变量 `RESOURCES_ROOT` 覆盖。

## 相关文档

- 个人资源管理 API：[`ResourceAPI.md`](ResourceAPI.md)
- 模板分析 API：[`TemplateAPI.md`](TemplateAPI.md)
- Cube Sandbox 部署：[`Cubesandbox-deploy.md`](Cubesandbox-deploy.md)
- 与 AgentPlatform 集成：[`Cubesandbox-agent-integration.md`](Cubesandbox-agent-integration.md)
- 后端 API：[`BackendAPI.md`](BackendAPI.md)
