#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Cube Sandbox 诊断：检查控制面/数据面并尝试 Sandbox.create。"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, List, Optional

# 允许从仓库根或 src 目录运行
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SRC = os.path.join(_REPO_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    fix: str = ""


@dataclass
class Report:
    results: List[CheckResult] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str, fix: str = "") -> None:
        self.results.append(CheckResult(name, ok, detail, fix))

    def print_report(self) -> int:
        fails = 0
        print("=" * 60)
        print("Cube Sandbox 诊断报告")
        print("=" * 60)
        for r in self.results:
            mark = "OK" if r.ok else "FAIL"
            if not r.ok:
                fails += 1
            print(f"\n[{mark}] {r.name}")
            print(f"      {r.detail}")
            if r.fix:
                print(f"      建议: {r.fix}")
        print("\n" + "=" * 60)
        if fails:
            print(f"共 {fails} 项未通过。优先修复标记为 FAIL 的项后重试。")
        else:
            print("全部检查通过。")
        print("=" * 60)
        return 1 if fails else 0


def _tcp_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_get(url: str, timeout: float = 5.0) -> tuple[int, str]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(512).decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read(512).decode("utf-8", errors="replace")
    except Exception as e:
        return -1, str(e)


def _systemctl_active(unit: str) -> str:
    try:
        out = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return (out.stdout or out.stderr or "").strip() or "unknown"
    except Exception as e:
        return f"error: {e}"


def _port_listener(port: int) -> str:
    try:
        out = subprocess.run(
            ["ss", "-tln"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in (out.stdout or "").splitlines():
            if f":{port} " in line or line.rstrip().endswith(f":{port}"):
                return line.strip()
    except Exception:
        pass
    return ""


def main() -> int:
    from sandbox.config import CUBE_TEMPLATE_ID, E2B_API_KEY, E2B_API_URL

    report = Report()
    api_url = E2B_API_URL.rstrip("/")

    # 1. Cube API
    code, body = _http_get(f"{api_url}/health")
    report.add(
        "Cube API 控制面",
        code == 200,
        f"GET {api_url}/health => {code} {body[:120]}",
        "" if code == 200 else "确认 cube-sandbox-cube-api.service 运行: sudo systemctl restart cube-sandbox-cube-api.service",
    )

    # 2. 关键 systemd 单元
    units = [
        ("cube-sandbox-cubemaster.service", "CubeMaster"),
        ("cube-sandbox-network-agent.service", "network-agent"),
        ("cube-sandbox-cubelet.service", "Cubelet"),
        ("cube-sandbox-cube-proxy.service", "CubeProxy"),
        ("cube-sandbox-coredns.service", "CoreDNS"),
    ]
    for unit, label in units:
        st = _systemctl_active(unit)
        ok = st == "active"
        fix = ""
        if not ok:
            fix = f"sudo systemctl restart {unit} && sudo systemctl status {unit}"
        report.add(f"服务 {label}", ok, f"{unit}: {st}", fix)

    proxy_active = _systemctl_active("cube-sandbox-cube-proxy.service") == "active"

    # 3. 数据面端口（cube-proxy 使用 host 网络，需独占 80/443 或自定义端口）
    p443 = _port_listener(443)
    p80 = _port_listener(80)
    report.add(
        "端口 443 (CubeProxy HTTPS)",
        proxy_active and bool(p443),
        p443 or "未监听 :443",
        "" if (proxy_active and p443) else (
            "CubeProxy 未监听 443。sudo systemctl restart cube-sandbox-cube-proxy.service；"
            "若 80/443 被其他进程占用: sudo bash scripts/fix-cube-proxy-port.sh"
        ),
    )
    if p80 and not proxy_active:
        report.add(
            "端口 80 冲突",
            False,
            f"80 已被占用且 cube-proxy 未 active，可能导致 proxy 启动失败:\n{p80}",
            "sudo bash scripts/fix-cube-proxy-port.sh 8443 8080",
        )
    elif not p80 and not proxy_active:
        report.add(
            "端口 80 (CubeProxy HTTP)",
            False,
            "未监听 :80 且 cube-proxy 未运行",
            "sudo systemctl restart cube-sandbox-cube-proxy.service",
        )

    # 4. 模板
    try:
        out = subprocess.run(
            ["cubemastercli", "tpl", "list"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        tpl_ok = CUBE_TEMPLATE_ID in (out.stdout or "")
        report.add(
            "沙箱模板",
            tpl_ok,
            f"配置 CUBE_TEMPLATE_ID={CUBE_TEMPLATE_ID}\n{(out.stdout or out.stderr or '')[:400]}",
            "cubemastercli tpl watch 直至 READY，并更新 src/sandbox/config.py 中的 CUBE_TEMPLATE_ID",
        )
    except FileNotFoundError:
        report.add("沙箱模板", False, "cubemastercli 未安装", "按 Cubesandbox-deploy.md 安装 Cube Sandbox")
    except Exception as e:
        report.add("沙箱模板", False, str(e), "")

    # 5. Sandbox.create（核心）
    create_ok = False
    create_detail = ""
    create_fix = ""
    try:
        import sandbox.session_manager  # noqa: F401 — 设置 E2B 环境变量

        os.environ.setdefault("E2B_API_URL", E2B_API_URL)
        os.environ.setdefault("E2B_API_KEY", E2B_API_KEY)
        from e2b_code_interpreter import Sandbox

        t0 = time.time()
        sb = Sandbox.create(template=CUBE_TEMPLATE_ID, timeout=600)
        elapsed = time.time() - t0
        create_ok = True
        create_detail = f"sandbox_id={sb.sandbox_id} 耗时 {elapsed:.1f}s"
        # envd 探测
        try:
            envd_url = getattr(sb, "envd_api_url", "?")
            running = sb.is_running()
            create_detail += f"; envd_api_url={envd_url}; is_running={running}"
            if not running:
                create_ok = False
                create_fix = "沙箱已创建但 envd 不可达，重启 cube-sandbox-cube-proxy.service"
        except Exception as e2:
            create_ok = False
            create_detail += f"; envd check failed: {e2}"
            create_fix = "sudo systemctl restart cube-sandbox-cube-proxy.service"
        try:
            sb.kill()
        except Exception:
            pass
    except Exception as e:
        err = str(e)
        create_detail = err
        if "EnsureNetwork" in err or "network-agent" in err:
            create_fix = (
                "network-agent 组网超时。依次执行:\n"
                "  sudo systemctl restart cube-sandbox-network-agent.service\n"
                "  sudo systemctl restart cube-sandbox-cube-proxy.service\n"
                "  sudo systemctl restart cube-sandbox-cubemaster.service\n"
                "若 cube-proxy 因 80/443 端口冲突启动失败: sudo bash scripts/fix-cube-proxy-port.sh"
            )
        elif "130547" in err:
            create_fix = create_fix or "CubeMaster 网络错误 130547，检查 network-agent 与 cube-proxy"
        else:
            create_fix = "见 docs/Cubesandbox-deploy.md 排查控制面组件"

    report.add("Sandbox.create 实测", create_ok, create_detail, create_fix)

    # 6. AgentPlatform 降级提示
    if not create_ok:
        report.add(
            "AgentPlatform 降级模式",
            True,
            "控制面不可用时，后端会自动使用 tmp/workspaces/ 本地镜像（上传/分析仍可用）",
            "修复 Cube 后无需改代码；或设置 CUBE_SANDBOX_ENABLED=0 永久本地模式",
        )

    return report.print_report()


if __name__ == "__main__":
    sys.exit(main())
