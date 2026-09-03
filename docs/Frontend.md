# Web 前端说明

AgentPlatform 前端位于 [`web/`](../web/)，基于 **Vue 3 + Vite + TypeScript + Element Plus**。

## 功能（MVP）

| 页面 | 路由 | 说明 |
|------|------|------|
| 项目列表 | `/` | 查看/创建项目 |
| 分析工作区 | `/projects/:projectId` | 会话管理、文件上传、SSE 流式分析 |

## 开发

### 前置

- **Node.js >= 18**（Vite 5 要求）
- 后端已启动（默认 `http://127.0.0.1:52716`）

### 启动

```bash
cd web
npm install
npm run dev
```

访问 `http://localhost:5173`。Vite 已将 `/project`、`/session`、`/run-analysis`、`/upload` 代理到后端。

### 环境变量

| 变量 | 说明 | 默认 |
|------|------|------|
| `VITE_API_BASE_URL` | API 根路径 | 空（使用相对路径 + 代理） |

直连远程 API 时可在 `web/.env.development` 设置，例如：

```bash
VITE_API_BASE_URL=http://your-host:52716
```

## 生产部署

### 方式一：后端托管（推荐单机部署）

```bash
cd web && npm run build
bash scripts/start.sh
```

构建产物在 `web/dist/`。若目录存在，[`src/backend/frontend_static.py`](../src/backend/frontend_static.py) 会：

- 挂载 `/assets` 静态资源
- 对 `/projects/*` 等前端路由返回 `index.html`（SPA fallback）

访问 `http://<host>:52716/`。

### 方式二：Nginx 独立托管

将 `web/dist/` 部署到 Nginx，API 反代到后端 `52716`。

## SSE 消费约定

分析流通过 `POST /run-analysis` 返回 `text/event-stream`。前端实现见 [`web/src/api/analysis.ts`](../web/src/api/analysis.ts)：

- 使用 `fetch` + `ReadableStream` 逐行解析 `data: {...}`
- `type=report_chunk` 的 `content` 拼接为报告正文
- `streaming_ended` 表示正常结束；`error` / `streaming_error` 表示失败

完整事件说明见 [`SSE_Details.md`](SSE_Details.md)。

## 演示数据

```bash
bash scripts/init-platform.sh --demo
```

可选写入演示会话与示例 CSV 文件。
