# psych/dl/models.py — 轻量 CNN / Transformer（可选 torch）

from __future__ import annotations

import json
import logging
import pickle
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

DL_MODELS = {
    "text_cnn": {
        "name_zh": "文本CNN分类",
        "modality": "text",
        "framework": "torch",
    },
    "text_transformer": {
        "name_zh": "文本Transformer分类",
        "modality": "text",
        "framework": "torch",
    },
}


def list_dl_models() -> List[Dict[str, Any]]:
    return [{"model_id": k, **v} for k, v in DL_MODELS.items()]


def _torch_available() -> bool:
    try:
        import torch  # noqa: F401

        return True
    except ImportError:
        return False


def _build_vocab(texts: List[str], max_vocab: int = 5000) -> Dict[str, int]:
    from collections import Counter

    cnt: Counter = Counter()
    for t in texts:
        for tok in str(t).lower().split():
            cnt[tok] += 1
    vocab = {"<pad>": 0, "<unk>": 1}
    for w, _ in cnt.most_common(max_vocab - 2):
        vocab[w] = len(vocab)
    return vocab


def _encode(texts: List[str], vocab: Dict[str, int], max_len: int = 64) -> np.ndarray:
    rows = []
    for t in texts:
        ids = [vocab.get(tok, 1) for tok in str(t).lower().split()][:max_len]
        ids = ids + [0] * (max_len - len(ids))
        rows.append(ids)
    return np.asarray(rows, dtype=np.int64)


def train_text_model(
    model_id: str,
    texts: List[str],
    labels: List[int],
    output_dir: str,
    epochs: int = 3,
    max_len: int = 64,
) -> Tuple[Dict[str, Any], Optional[str]]:
    if model_id not in DL_MODELS:
        return {}, f"未知 DL 模型: {model_id}"
    if len(texts) != len(labels) or not texts:
        return {}, "texts 与 labels 长度须一致且非空"
    if not _torch_available():
        # 无 torch 时提供 sklearn 回退（Bag of Words + LR），保证接口可用
        return _fallback_train(model_id, texts, labels, output_dir)

    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset

    vocab = _build_vocab(texts)
    X = _encode(texts, vocab, max_len=max_len)
    y = np.asarray(labels, dtype=np.int64)
    n_class = int(max(y)) + 1 if len(y) else 2

    class TextCNN(nn.Module):
        def __init__(self):
            super().__init__()
            self.emb = nn.Embedding(len(vocab), 64, padding_idx=0)
            self.conv = nn.Conv1d(64, 64, kernel_size=3, padding=1)
            self.fc = nn.Linear(64, n_class)

        def forward(self, x):
            e = self.emb(x).transpose(1, 2)
            h = torch.relu(self.conv(e)).mean(dim=2)
            return self.fc(h)

    class TinyTransformer(nn.Module):
        def __init__(self):
            super().__init__()
            self.emb = nn.Embedding(len(vocab), 64, padding_idx=0)
            layer = nn.TransformerEncoderLayer(d_model=64, nhead=4, batch_first=True, dim_feedforward=128)
            self.enc = nn.TransformerEncoder(layer, num_layers=2)
            self.fc = nn.Linear(64, n_class)

        def forward(self, x):
            h = self.enc(self.emb(x))
            return self.fc(h.mean(dim=1))

    model = TextCNN() if model_id == "text_cnn" else TinyTransformer()
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    loss_fn = nn.CrossEntropyLoss()
    ds = TensorDataset(torch.from_numpy(X), torch.from_numpy(y))
    loader = DataLoader(ds, batch_size=min(32, len(ds)), shuffle=True)

    model.train()
    last_loss = 0.0
    for _ in range(max(1, int(epochs))):
        for xb, yb in loader:
            opt.zero_grad()
            logits = model(xb)
            loss = loss_fn(logits, yb)
            loss.backward()
            opt.step()
            last_loss = float(loss.item())

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    weights = out / f"{model_id}.pt"
    torch.save(model.state_dict(), weights)
    meta = {
        "model_id": model_id,
        "vocab": vocab,
        "max_len": max_len,
        "n_class": n_class,
        "framework": "torch",
        "weights": str(weights),
        "train_loss": last_loss,
        "n_samples": len(texts),
    }
    meta_path = out / f"{model_id}_meta.pkl"
    with open(meta_path, "wb") as f:
        pickle.dump(meta, f)
    with open(out / f"{model_id}_metrics.json", "w", encoding="utf-8") as f:
        json.dump({"train_loss": last_loss, "n_samples": len(texts), "n_class": n_class}, f)
    return {"meta_path": str(meta_path), "metrics": {"train_loss": last_loss, "n_class": n_class}}, None


def _fallback_train(
    model_id: str, texts: List[str], labels: List[int], output_dir: str
) -> Tuple[Dict[str, Any], Optional[str]]:
    from sklearn.feature_extraction.text import CountVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    pipe = Pipeline(
        [
            ("vec", CountVectorizer(max_features=5000)),
            ("clf", LogisticRegression(max_iter=500)),
        ]
    )
    pipe.fit(texts, labels)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    meta_path = out / f"{model_id}_meta.pkl"
    with open(meta_path, "wb") as f:
        pickle.dump({"model_id": model_id, "framework": "sklearn_fallback", "pipeline": pipe}, f)
    acc = float(pipe.score(texts, labels))
    return {
        "meta_path": str(meta_path),
        "metrics": {"train_accuracy": acc, "framework": "sklearn_fallback", "note": "torch 未安装，已回退"},
    }, None


def infer_text_model(meta_path: str, texts: List[str]) -> Tuple[Dict[str, Any], Optional[str]]:
    if not texts:
        return {}, "texts 不能为空"
    path = Path(meta_path)
    if not path.is_file():
        return {}, f"模型不存在: {meta_path}"
    with open(path, "rb") as f:
        meta = pickle.load(f)

    if meta.get("framework") == "sklearn_fallback":
        pipe = meta["pipeline"]
        pred = pipe.predict(texts).tolist()
        proba = None
        if hasattr(pipe, "predict_proba"):
            try:
                proba = pipe.predict_proba(texts).tolist()
            except Exception:
                pass
        return {"predictions": pred, "probabilities": proba, "framework": "sklearn_fallback"}, None

    if not _torch_available():
        return {}, "需要安装 torch 才能加载该模型"
    import torch
    import torch.nn as nn

    vocab = meta["vocab"]
    max_len = int(meta.get("max_len") or 64)
    n_class = int(meta.get("n_class") or 2)
    model_id = meta.get("model_id")
    X = _encode(texts, vocab, max_len=max_len)

    class TextCNN(nn.Module):
        def __init__(self):
            super().__init__()
            self.emb = nn.Embedding(len(vocab), 64, padding_idx=0)
            self.conv = nn.Conv1d(64, 64, kernel_size=3, padding=1)
            self.fc = nn.Linear(64, n_class)

        def forward(self, x):
            e = self.emb(x).transpose(1, 2)
            h = torch.relu(self.conv(e)).mean(dim=2)
            return self.fc(h)

    class TinyTransformer(nn.Module):
        def __init__(self):
            super().__init__()
            self.emb = nn.Embedding(len(vocab), 64, padding_idx=0)
            layer = nn.TransformerEncoderLayer(d_model=64, nhead=4, batch_first=True, dim_feedforward=128)
            self.enc = nn.TransformerEncoder(layer, num_layers=2)
            self.fc = nn.Linear(64, n_class)

        def forward(self, x):
            h = self.enc(self.emb(x))
            return self.fc(h.mean(dim=1))

    model = TextCNN() if model_id == "text_cnn" else TinyTransformer()
    model.load_state_dict(torch.load(meta["weights"], map_location="cpu"))
    model.eval()
    with torch.no_grad():
        logits = model(torch.from_numpy(X))
        pred = logits.argmax(dim=1).tolist()
        proba = torch.softmax(logits, dim=1).tolist()
    return {"predictions": pred, "probabilities": proba, "framework": "torch"}, None
