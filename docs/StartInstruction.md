# 启动指令
## 配置环境并安装依赖
```
conda create -n agentPlatform python=3.13.7
conda activate agentPlatform
python -m pip install -r requirements.txt
```

## 安装IPython内核
```
python -m ipykernel install --user --name agentPlatform --display-name "Python (agentPlatform)"
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
