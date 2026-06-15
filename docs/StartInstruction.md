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

## 配置 Cube Sandbox 环境变量

在项目根目录复制 [`.env.example`](../.env.example) 为 `.env`，或在启动前 `export`：

```bash
export CUBE_SANDBOX_ENABLED=1
export E2B_API_URL=http://127.0.0.1:3000
export E2B_API_KEY=e2b_000000
export CUBE_TEMPLATE_ID=tpl-78c1861fc2b54381947d33e2
export SANDBOX_WORKDIR=/home/user
export SANDBOX_TIMEOUT=600
# 可选：使用 Cube 内置 mkcert HTTPS 时
# export SSL_CERT_FILE=/root/.local/share/mkcert/rootCA.pem
```

启动前确认 Cube API 可达：

```bash
curl --noproxy '*' http://127.0.0.1:3000/health
```

> 若需回退到本地 subprocess 模式（不依赖沙箱），设置 `CUBE_SANDBOX_ENABLED=0`。详见 [`Cubesandbox-agent-integration.md`](Cubesandbox-agent-integration.md)。

## 启动后端指令

```bash
cd src
# 避免 HTTP 代理导致 E2B SDK 502
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
# 生产环境务必设置 JWT 签名密钥
export JWT_SECRET_KEY="your-production-secret"
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
