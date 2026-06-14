# 裸金属 / 物理机部署

> **适用场景：** 已有支持 KVM 的 x86_64 Linux 机器（`/dev/kvm` 可用），例如物理机、裸金属服务器、或已开启嵌套虚拟化的云服务器。
>
> 如果你用的是**普通云服务器**（`/dev/kvm` 不可用），无需裸金属 —— 通过 PVM 即可在普通云服务器上启用 KVM，请参阅[快速开始](./quickstart.md)。

## 前置条件

- **x86_64** 架构的 Linux 机器
- `/dev/kvm` 存在且可读写（`ls -la /dev/kvm`）
- 有 **root 权限**
- **Docker** 已安装并正常运行
- 可访问互联网（用于下载发布包、拉取 Docker 镜像）
- 内存 ≥ 8 GB，磁盘空余 ≥ 50 GB

::: warning 以 root 身份执行所有操作
本文档中的所有命令均需在 **root** 用户下执行。请先切换到 root：

```bash
sudo su root
```

:::

## 第一步：安装

以 root 身份执行：

```bash
curl -sL https://cnb.cool/CubeSandbox/CubeSandbox/-/git/raw/master/deploy/one-click/online-install.sh | MIRROR=cn bash
```

::: details 安装了哪些组件
- E2B 兼容 REST API 监听在 `3000` 端口
- CubeMaster、Cubelet、network-agent、CubeShim 作为宿主机进程运行
- MySQL 和 Redis 通过 Docker Compose 管理
- CubeProxy 提供 TLS（mkcert）和 CoreDNS 域名路由（`cube.app`）
:::

## 第二步：制作模板

安装完成后，使用预构建镜像创建代码解释器模板：

```bash
cubemastercli tpl create-from-image \
  --image cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-code:latest \
  --writable-layer-size 1G \
  --expose-port 49999 \
  --expose-port 49983 \
  --probe 49999
```

> **镜像仓库说明：** 国内优先使用 `cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-code:latest`；境外访问推荐使用 `cube-sandbox-int.tencentcloudcr.com/cube-sandbox/sandbox-code:latest`。

然后监控构建进度：

```bash
cubemastercli tpl watch --job-id <job_id>
```

⚠️ 注意：由于镜像比较大，下载、解压、模板制作过程可能比较久，请耐心等待。

等待上述命令结束，模板状态变为 `READY`。

记录输出中的**模板 ID** (`template_id`)，下一步会用到。

完整的模板创建流程和更多参数说明，请参阅[从 OCI 镜像制作模板](./tutorials/template-from-image.md)。

## 第三步：运行第一段 Agent 代码

安装 Python SDK：

```bash
yum install -y python3 python3-pip
pip config set global.index-url https://mirrors.ustc.edu.cn/pypi/simple

pip install e2b-code-interpreter
```

设置环境变量：

```bash
export E2B_API_URL="http://127.0.0.1:3000"
export E2B_API_KEY="e2b_000000"
export CUBE_TEMPLATE_ID="<你的模板ID>"
export SSL_CERT_FILE="/root/.local/share/mkcert/rootCA.pem"
```

| 变量 | 说明 |
|------|------|
| `E2B_API_URL` | 将 E2B SDK 请求指向本地 Cube Sandbox，而非 E2B 官方云服务 |
| `E2B_API_KEY` | SDK 强制非空校验，本地部署填任意字符串即可 |
| `CUBE_TEMPLATE_ID` | 第二步获取的模板 ID |
| `SSL_CERT_FILE` | mkcert 签发的 CA 根证书路径，沙箱 HTTPS 连接需要 |

在隔离沙箱中运行代码：

```python
import os
from e2b_code_interpreter import Sandbox  # 直接使用 E2B SDK！

# CubeSandbox 在底层无缝接管了所有的请求
with Sandbox.create(template=os.environ["CUBE_TEMPLATE_ID"]) as sandbox:
    result = sandbox.run_code("print('Hello from Cube Sandbox, safely isolated!')")
    print(result)
```

更多端到端示例，请参阅[示例项目](./tutorials/examples.md)。

## 常见问题

### Redis 端口 6379 已被占用

Cube Sandbox 默认将 Redis 容器映射到宿主机 `127.0.0.1:6379`。若机器上已有其他 Redis 实例（或其他服务）占用该端口，`cube-sandbox-redis.service` 会启动失败。

**排查占用情况：**

```bash
sudo ss -tlnp | grep ':6379'
# 或
sudo lsof -i :6379
```

**解决思路：** 将 Cube Sandbox 的 Redis 改映射到宿主机其他端口（下文以 `6380` 为例）。容器内 Redis 仍监听 `6379`，仅宿主机映射端口发生变化。

#### 1. 修改 Redis 端口映射

编辑 Compose 模板，将宿主机端口改为 `6380`：

```bash
sudo sed -i 's/127.0.0.1:__REDIS_PORT__:6379/127.0.0.1:6380:6379/' \
  /usr/local/services/cubetoolbox/support/docker-compose.yaml.template
```

验证修改是否生效：

```bash
grep "6380" /usr/local/services/cubetoolbox/support/docker-compose.yaml.template
# 应看到：- "127.0.0.1:6380:6379"
```

> **可选：** 也可在 `/usr/local/services/cubetoolbox/.one-click.env` 中设置 `CUBE_SANDBOX_REDIS_PORT=6380`，由安装脚本在渲染模板时自动替换 `__REDIS_PORT__` 占位符，无需直接改模板。

#### 2. 删除已生成的 Compose 文件

systemd 启动 Redis 时会从模板重新渲染 `docker-compose.yaml`，需先删除旧文件：

```bash
sudo rm -f /usr/local/services/cubetoolbox/support/docker-compose.yaml
```

#### 3. 同步更新依赖 Redis 的组件配置

以下组件通过 `127.0.0.1:<端口>` 连接 Redis，端口须与上一步保持一致：

| 文件 | 需修改的字段 |
|------|-------------|
| `/usr/local/services/cubetoolbox/CubeMaster/conf.yaml` | `redis`、`redis_read`、`redis_write` 下的 `nodes` |
| `/usr/local/services/cubetoolbox/cubeproxy/global.conf` | `redis_port` |

示例（将 `6379` 替换为 `6380`）：

```bash
sudo sed -i 's/127.0.0.1:6379/127.0.0.1:6380/g' \
  /usr/local/services/cubetoolbox/CubeMaster/conf.yaml

sudo sed -i 's/set $redis_port "6379"/set $redis_port "6380"/' \
  /usr/local/services/cubetoolbox/cubeproxy/global.conf
```

#### 4. 重启相关服务

```bash
sudo systemctl restart cube-sandbox-redis.service
sudo systemctl restart cube-sandbox-cubemaster.service
sudo systemctl restart cube-sandbox-cube-proxy.service
```

#### 5. 确认服务正常

```bash
docker ps | grep cube-sandbox-redis
sudo ss -tlnp | grep ':6380'
```

### MySQL 端口 3306 已被占用

Cube Sandbox 默认将 MySQL 容器映射到宿主机 `127.0.0.1:3306`。若机器上已有 `mysqld` 或其他服务占用该端口，`cube-sandbox-mysql.service` 会启动失败，进而导致 CubeMaster 无法启动、`cubemastercli` 报 502。

**排查占用情况：**

```bash
sudo ss -tlnp | grep ':3306'
# 或
sudo lsof -i :3306
```

改端口前，先确认目标端口空闲（共享机器上 `3307`、`3308` 也可能已被占用）：

```bash
for p in 3307 3308 3309; do
  ss -tln | grep -q ":$p " && echo "port $p: IN USE" || echo "port $p: free"
done
```

**解决思路：** 将 Cube Sandbox 的 MySQL 改映射到宿主机**空闲**端口（下文以 `3309` 为例）。容器内 MySQL 仍监听 `3306`，仅宿主机映射端口发生变化。

#### 一键修复（推荐）

项目内提供了修复脚本，会自动修改配置、重启服务并做健康检查：

```bash
sudo bash scripts/fix-cube-mysql-port.sh 3309
```

将 `3309` 替换为上一步确认的空闲端口即可。

#### 手动修复步骤

##### 1. 修改 MySQL 端口映射

编辑 Compose 模板，将宿主机端口改为空闲端口（示例 `3309`）：

```bash
sudo sed -i 's/127.0.0.1:__MYSQL_PORT__:3306/127.0.0.1:3309:3306/' \
  /usr/local/services/cubetoolbox/support/docker-compose.yaml.template
```

若模板中已是硬编码端口（如 `3307`），可改为：

```bash
sudo sed -i 's/127.0.0.1:330[0-9]*:3306/127.0.0.1:3309:3306/' \
  /usr/local/services/cubetoolbox/support/docker-compose.yaml.template
```

验证：

```bash
grep "3309" /usr/local/services/cubetoolbox/support/docker-compose.yaml.template
# 应看到：- "127.0.0.1:3309:3306"
```

> **可选：** 在 `/usr/local/services/cubetoolbox/.one-click.env` 中设置 `CUBE_SANDBOX_MYSQL_PORT=3309`，供安装脚本渲染模板时使用。

##### 2. 同步 CubeMaster 数据库连接地址

```bash
sudo sed -i 's/127.0.0.1:3306/127.0.0.1:3309/g' \
  /usr/local/services/cubetoolbox/CubeMaster/conf.yaml
```

涉及字段：`ossdb_config.addr`、`instance_db_config.addr`。

##### 3. 删除已生成的 Compose 文件并清理旧容器

```bash
sudo rm -f /usr/local/services/cubetoolbox/support/docker-compose.yaml
sudo docker rm -f cube-sandbox-mysql 2>/dev/null || true
```

##### 4. 重启 MySQL 并启动控制面

```bash
sudo systemctl restart cube-sandbox-mysql.service
sudo systemctl start cube-sandbox-control.target
```

##### 5. 确认服务正常

```bash
sudo systemctl status cube-sandbox-mysql.service
sudo ss -tlnp | grep ':3309'
curl --noproxy '*' http://127.0.0.1:8089/notify/health   # CubeMaster
curl --noproxy '*' http://127.0.0.1:3000/health           # CubeAPI
```

MySQL 修复后，执行模板创建时需绕过 HTTP 代理：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  cubemastercli tpl create-from-image \
  --image cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-code:latest \
  --writable-layer-size 1G \
  --expose-port 49999 \
  --expose-port 49983 \
  --probe 49999
```

## 下一步

- [从 OCI 镜像制作模板](./tutorials/template-from-image.md) — 自定义沙箱运行环境
- [多机集群部署](./multi-node-deploy.md) — 扩展到多台机器
- [HTTPS 证书与域名解析](./https-and-domain.md) — TLS 配置选项
- [鉴权](./authentication.md) — 启用 API 鉴权
