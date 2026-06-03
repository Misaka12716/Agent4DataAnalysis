# 启动指令
## 配置环境并安装依赖
```
conda create -n agentPlatform python=3.13.7
conda activate agentPlatform
python -m pip install -r requirements.txt
```

## 启动后端指令
```
cd src 
python -m uvicorn backend.server:app --host 0.0.0.0 --port 52716
```

## 启动前端指令
```
cd src
streamlit run frontend/frontend.py
```

## 相关文档
- 大模型部署与配置：[`docs/Models.md`](Models.md)
- SSE 详细说明：[`docs/SSE_Details.md`](SSE_Details.md)
- 后端接口说明：[`docs/BackendAPI.md`](BackendAPI.md)
