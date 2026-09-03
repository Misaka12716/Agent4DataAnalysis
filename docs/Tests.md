# 测试说明

`tests/` 按业务类别分子目录，默认不连真实 MySQL、默认关闭 Cube 沙箱。

## 运行

```bash
pytest tests/agent tests/runtime tests/reader tests/upload tests/project
pytest tests/ -k "not live"
```

配置见根目录 [`pytest.ini`](../pytest.ini)。

## 目录结构

```text
tests/
  agent/       # 编排、Supervisor、Coder 等
  project/     # 项目与会话生命周期
  reader/      # 文件读取
  runtime/     # 本地/Cube 执行层
  upload/      # 上传与格式识别
  fixtures/    # 样例数据（table/、text/）
```

## 共享配置

[`conftest.py`](../tests/conftest.py) 提供：

- `sys.path` 注入 `src/`
- `pymysql.connect` stub，避免 import 阶段连真实 MySQL
- `disable_sandbox_by_default`：设置 `CUBE_SANDBOX_ENABLED=0`
- `isolated_workspaces`：工作区隔离到 pytest `tmp_path`

可选 live 沙箱用例需设置 `RUN_CUBE_SANDBOX_INTEGRATION=1`。
