# CubeProxy TLS 配置

CubeProxy 开箱即同时提供 **HTTPS（宿主机 443 端口）和 HTTP（宿主机 80 端口）** 两种访问方式。

> **说明：** TLS 配置仅影响客户端访问 CubeProxy 的方式。`E2B_API_URL` 始终指向 **Cube API Server**（默认端口 `3000`），与 CubeProxy 是独立的组件。

## 概述

**使用 E2B SDK 访问沙箱？**
E2B SDK 默认通过 HTTPS 与沙箱交互。Cube 已默认内置了 DNS 服务并预装了 `cube.app` 测试证书，无需手动配置证书即可直接使用 HTTPS。

**不使用 E2B SDK？**
也可以直接通过 HTTP 与沙箱交互。此时需要注意，每个请求的 `Host` 头部必须符合以下格式：

```
Host: <sandbox-service-port>-<sandboxId>-<domain>
```

其中 `<sandbox-service-port>` 是沙箱内业务服务监听的端口（如 `49999`），`<domain>` 默认为 `cube.app`，如果你配置了自定义域名（见下文方式 B），则替换为对应域名。示例：

```
Host: 49999-abc123def456-cube.app
```

---

## 方式 A — mkcert（自定义域名快速验证）

如果你在开发阶段需要为自定义域名配置 HTTPS，`mkcert` 可在几秒内生成本地可信证书。

```bash
mkcert -install
mkcert <your-host-ip-or-domain>
```

设置 `SSL_CERT_FILE`，让 E2B SDK 信任生成的 CA：

```bash
export SSL_CERT_FILE=$(mkcert -CAROOT)/rootCA.pem
```

> mkcert 证书只在运行过 `mkcert -install` 的机器上受信任，不适合生产环境或多人共享部署。

## 方式 B — 自有证书 / 域名（生产环境）

修改 CubeProxy 的 `nginx.conf`，使用正式证书和私钥：

```nginx
server {
    listen 443 ssl;
    server_name your.domain.com;

    ssl_certificate     /path/to/your/cert.pem;
    ssl_certificate_key /path/to/your/key.pem;
}
```

### 更新 sandbox 域名

使用自定义域名时，还需要在启动 Cube API Server 时告知其对外域名，否则 API 响应中 `domain` 字段仍会返回默认值 `cube.app`，导致客户端无法正确连接到沙箱。

通过启动参数或环境变量设置：

```bash
# 启动参数
./cube-api --sandbox-domain your.domain.com

# 或环境变量
export CUBE_API_SANDBOX_DOMAIN=your.domain.com
./cube-api
```

## 方式 C — 仅保留 HTTPS（关闭 HTTP）

CubeProxy 默认同时监听 HTTP 和 HTTPS。如需完全关闭 HTTP 端口、仅保留 HTTPS，删除 `nginx.conf` 中的 HTTP server block，并在 `docker-compose.yaml` 中去掉对应的端口映射即可。

> E2B SDK 仅使用 HTTPS，关闭 HTTP 不影响基于 SDK 的客户端。
