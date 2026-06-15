# 启动指令

## 前置条件

1. **Cube Sandbox 已部署且控制面健康**（默认 `http://127.0.0.1:3000`）。部署步骤见 [`Cubesandbox-deploy.md`](Cubesandbox-deploy.md)。
2. 已制作沙箱模板并记录 `template_id`（示例：`tpl-78c1861fc2b54381947d33e2`）。
3. MySQL 已就绪（会话持久化依赖）。

## 配置环境并安装依赖

```
conda create -n agentPlatform python=3.13.7
conda activate agentPlatform
python -m pip install -r requirements.txt
```

## Cube Sandbox 配置（可选）

本地联调默认启用 Cube Sandbox，相关默认值已在 [`src/sandbox/config.py`](../src/sandbox/config.py) 中配置（经 [`src/configs/config.py`](../src/configs/config.py) 引用）。若 Cube 运行在 `http://127.0.0.1:3000` 且模板 ID 与前置条件中的示例一致，**无需额外配置环境变量**，安装依赖后可直接启动。

启动前确认 Cube API 可达：

```bash
curl --noproxy '*' http://127.0.0.1:3000/health
```

### 需要覆盖默认值时

- **改代码**（适合固定本机配置）：直接编辑 [`src/sandbox/config.py`](../src/sandbox/config.py) 中对应常量的默认值，例如 `CUBE_TEMPLATE_ID`、`E2B_API_URL`。
- **改环境变量**（适合临时覆盖）：启动前 `export`，或复制 [`.env.example`](../.env.example) 为 `.env` 后手动 `source`/export。注意：**后端不会自动加载 `.env`**，`python-dotenv` 仅用于 `tests/cubesandbox/` 下的示例脚本。

常用覆盖示例：

```bash
export CUBE_TEMPLATE_ID=<你的模板ID>
# 使用 Cube 内置 mkcert HTTPS 时
# export SSL_CERT_FILE=/root/.local/share/mkcert/rootCA.pem
```

> 若需回退到本地 subprocess 模式（不依赖沙箱），将 `CUBE_SANDBOX_ENABLED` 设为 `0`（改 `sandbox/config.py` 默认值或 `export CUBE_SANDBOX_ENABLED=0`）。详见 [`Cubesandbox-agent-integration.md`](Cubesandbox-agent-integration.md)。

## 启动后端指令

```bash
cd src
# 避免 HTTP 代理导致 E2B SDK 502
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
# 生产环境务必设置 JWT 签名密钥；本地开发可省略（将使用临时 dev 密钥）
# export JWT_SECRET_KEY="your-production-secret"
python -m uvicorn backend.server:app --host 0.0.0.0 --port 52716
```

## 启动前端指令

```
cd src
streamlit run frontend/frontend.py
```

## 相关文档

- Cube Sandbox 部署：[`Cubesandbox-deploy.md`](Cubesandbox-deploy.md)
- AgentPlatform 沙箱集成：[`Cubesandbox-agent-integration.md`](Cubesandbox-agent-integration.md)
- 大模型部署与配置：[`Models.md`](Models.md)
- SSE 详细说明：[`SSE_Details.md`](SSE_Details.md)
- 后端接口说明：[`BackendAPI.md`](BackendAPI.md)
