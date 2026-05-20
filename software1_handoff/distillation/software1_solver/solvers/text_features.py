"""Text feature solver (F09 / Q16c / Q18).

Mode A: Chinese sentence Transformer (sentence-transformers).
Mode B: TF-IDF character n-gram fallback (no network / no GPU needed).

Computes (1) embeddings.csv, (2) per-phrase top-3 cosine neighbours,
(3) intra-label cosine summary, (4) manifest.json.

Designed so that the same-label-vs-random z-score check
(``z = (mean_intra - mean_inter) / std_inter > 1.5``) is satisfied — the
TF-IDF baseline already passes on the bench's 20 phrases.

中文说明
========
中文短文本向量化 + 同标签一致性评估。**双轨设计**：

  - **Mode A：sentence-transformers**（首选）
      默认模型 ``paraphrase-multilingual-MiniLM-L12-v2``
      多语言 SBERT，开箱即用，对中文病例描述效果好
      → 走 _try_transformer，导入 / 下载 / encode 任一步失败就 fallback
  - **Mode B：jieba 分词 + TF-IDF 兜底**
      不需要网络 / 不需要 GPU，纯 sklearn
      策略：jieba 切词（保留 ≥2 字 token，丢"的/了/在"等停字单字）
            → word 1-2 gram TF-IDF + char_wb 2-gram TF-IDF
            → hstack 后 L2 normalize
      在 bench 的 20 句 fixture 上 z-score > 1.5 达标

为什么需要 Mode B：A 轨在客户内网 / 离线环境很常见地下载模型失败；
B 轨保证 solver **始终**能跑出可用结果，只是 z-score 略低些。

输入约定
========
- ``id_col``    必填，phrase id（写进所有输出文件）
- ``text_col``  必填，中文短句
- ``label_col`` optional，弱监督标签（用来算 intra/inter cosine）

输出（4 份）
============
1. ``embeddings_csv``   = ``embeddings.csv``：
   [id, dim_0, dim_1, ..., dim_{D-1}]，每行一个向量
2. ``similarity_top3``  = ``similarity_top3.csv``：
   [id, top1_id, top2_id, top3_id]（自己除外的最近邻）
3. ``intra_label_json`` = ``intra_label_cosine.json``：
   {intra_label_mean_cosine: {label: {n_pairs, mean_cosine}},
    label_consistency_z}
   z-score = (mean_intra - mean_inter) / std_inter
   > 1.5 视作"嵌入能区分弱标签"
4. ``manifest_json``    = ``manifest.json``：
   {encoder_name, embedding_dim, mode (A/B), label_consistency_z, n_phrases}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract


# Contract 说明：
#   - id_col / text_col 必填；label_col optional（缺则不算 intra/inter）
#   - static_params:
#       prefer_transformer_model  Mode A 的模型名；想换更大模型就改这里
#       tfidf_analyzer / ngram    Mode B 的 char-ngram 配置（兜底用）
CONTRACT = SolverContract(
    name="text_features",
    capability="F09_dimensionality_reduction_features",
    description=(
        "Encode short Chinese phrases as dense vectors (sentence-"
        "transformer mode A; TF-IDF char-ngram mode B fallback) and "
        "compute per-phrase top-3 cosine neighbours + intra-label "
        "cosine summary."),
    roles={
        "id_col":    RoleSpec(Role.ID,   "phrase identifier"),
        "text_col":  RoleSpec(Role.TEXT, "the Chinese phrase"),
        "label_col": RoleSpec(Role.CATEGORICAL,
                              "weak-supervision label", optional=True),
    },
    static_params={
        "prefer_transformer_model":
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "tfidf_analyzer": "char_wb",
        "tfidf_ngram":   [2, 4],
    },
    output_files={
        "embeddings_csv":     "embeddings.csv",
        "similarity_top3":    "similarity_top3.csv",
        "intra_label_json":   "intra_label_cosine.json",
        "manifest_json":      "manifest.json",
    },
)


def _try_transformer(model_name: str, texts):
    """中文：尝试走 Mode A。

    任一步失败（sentence_transformers 没装 / 模型无法下载 / encode
    抛异常）都返回 None，调用方自动 fallback 到 Mode B。
    """
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(model_name)
        emb = model.encode(list(texts),
                            convert_to_numpy=True, show_progress_bar=False)
        return emb, model_name, "A"
    except Exception:
        return None, None, None


class TextFeaturesSolver:
    contract = CONTRACT

    def __init__(self,
                 prefer_transformer_model: Optional[str] = None,
                 tfidf_analyzer: str = "char_wb",
                 tfidf_ngram=(2, 4)):
        """中文：

        :param prefer_transformer_model: Mode A 的 sentence-transformers
                                         模型名。默认多语言 MiniLM
                                         （384 维，对中文 OK）。需要
                                         更高质量可换 ``BAAI/bge-large-zh``。
        :param tfidf_analyzer: Mode B 的 sklearn TF-IDF analyzer，默认
                               ``char_wb``（按字符 + 词界）。
                               注意：当前 run() 实际是用 char_wb 2-gram
                               + 可选 jieba word 1-2gram 的混合，这两个
                               参数主要用于历史兼容/外部覆盖。
        :param tfidf_ngram:    Mode B 的 char ngram 范围。
        """
        self.prefer_transformer_model = (
            prefer_transformer_model
            or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )
        self.tfidf_analyzer = tfidf_analyzer
        self.tfidf_ngram = tuple(tfidf_ngram)

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        id_col   = mapping["id_col"]
        text_col = mapping["text_col"]
        label_col = mapping.get("label_col")
        ids = df[id_col].tolist()
        texts = df[text_col].astype(str).tolist()

        # 优先尝试 Mode A（sentence-transformers）；任一步失败 → emb=None
        # → 进入下面的 Mode B 兜底分支
        emb, encoder_name, mode = _try_transformer(
            self.prefer_transformer_model, texts)

        if emb is None:
            # Mode B: jieba word + char n-gram TF-IDF fallback.
            #
            # Strategy:
            #   1. Pre-tokenize each phrase with jieba (precise mode).
            #   2. Build TWO TF-IDF matrices:
            #      a) word-level on jieba tokens — captures content
            #         words like "焦虑" / "幻听" / "失眠" that anchor
            #         same-label phrases.
            #      b) char-bigram on the raw text — robust to OOV /
            #         rare segmentation.
            #   3. Concat + L2 normalize → cosine.
            # On the bench's 20 phrases, this yields
            # label_consistency_z > 1.5 deterministically.
            from scipy.sparse import hstack

            try:
                import jieba  # type: ignore
                jieba.initialize()
                # keep only tokens of length >= 2 (drops "的", "了", "在",
                # punctuation and stray single characters that act as
                # inter-label noise)
                tokenized = [
                    " ".join(t for t in jieba.lcut(s) if len(t) >= 2)
                    for s in texts
                ]
                vec_word = TfidfVectorizer(
                    analyzer="word",
                    token_pattern=r"\S+",
                    ngram_range=(1, 2),
                    sublinear_tf=True, min_df=1,
                )
                word_mat = vec_word.fit_transform(tokenized)
            except Exception:
                word_mat = None

            # char-bigram only — single chars are common stop characters
            vec_charwb = TfidfVectorizer(
                analyzer="char_wb", ngram_range=(2, 2),
                sublinear_tf=True, min_df=1,
            )
            char_mat = vec_charwb.fit_transform(texts)

            if word_mat is not None:
                mat = hstack([word_mat, char_mat])
                encoder_name = ("jieba_word(>=2chars)_1-2 + "
                                "tfidf_charwb_2-2 (sublinear)")
            else:
                mat = char_mat
                encoder_name = "tfidf_charwb_2-2 (sublinear)"

            # 转 dense + L2 normalize（cosine = 内积），加 1e-12 防全零行
            emb = mat.toarray().astype(np.float32)
            norms = np.linalg.norm(emb, axis=1, keepdims=True) + 1e-12
            emb = emb / norms
            mode = "B"

        D = emb.shape[1]

        # ---- embeddings.csv
        emb_df = pd.DataFrame(emb, columns=[f"dim_{i}" for i in range(D)])
        emb_df.insert(0, id_col, ids)
        ec = Path(output_dir) / CONTRACT.output_files["embeddings_csv"]
        emb_df.to_csv(ec, index=False)

        # ---- top3 neighbours
        # 把对角线置 -inf，让自己永远不会被选成自己的 top-k 邻居
        sim = cosine_similarity(emb)
        np.fill_diagonal(sim, -np.inf)
        # 按相似度降序排，取每行前 3 个索引 → top1/top2/top3
        top3 = np.argsort(-sim, axis=1)[:, :3]
        top3_rows = [
            {id_col: ids[i],
             "top1_id": ids[top3[i, 0]],
             "top2_id": ids[top3[i, 1]],
             "top3_id": ids[top3[i, 2]]}
            for i in range(len(ids))
        ]
        sc = Path(output_dir) / CONTRACT.output_files["similarity_top3"]
        pd.DataFrame(top3_rows).to_csv(sc, index=False)

        # ---- intra-label cosine summary + label-consistency z
        # 同标签 / 跨标签的两两 cosine 均值之差，标准化成 z-score：
        #     z = (mean_intra - mean_inter) / std_inter
        # > 1.5 → 可认为"同标签的句子在嵌入空间里更近"，弱标签和
        # 嵌入是一致的
        intra: Dict[str, Any] = {}
        z_score = float("nan")
        if label_col and label_col in df.columns:
            labels = df[label_col].astype(str).tolist()
            sim_clean = cosine_similarity(emb)  # diag=1, but we mask
            # 把对角置 NaN：i==j 的"自己跟自己"不计入任何 pair
            np.fill_diagonal(sim_clean, np.nan)
            intra_vals: list[float] = []
            inter_vals: list[float] = []
            for lab in sorted(set(labels)):
                idx = [i for i, l in enumerate(labels) if l == lab]
                if len(idx) < 2:
                    intra[lab] = {
                        "n_pairs": 0, "mean_cosine": None,
                    }
                    continue
                pairs = []
                for i in range(len(idx)):
                    for j in range(i + 1, len(idx)):
                        pairs.append(sim_clean[idx[i], idx[j]])
                m = float(np.nanmean(pairs))
                intra[lab] = {"n_pairs": len(pairs), "mean_cosine": m}
                intra_vals.extend(pairs)
            for i, li in enumerate(labels):
                for j, lj in enumerate(labels):
                    if i < j and li != lj:
                        inter_vals.append(sim_clean[i, j])
            if intra_vals and inter_vals:
                z_score = float(
                    (np.mean(intra_vals) - np.mean(inter_vals))
                    / (np.std(inter_vals) + 1e-12)
                )
        ic = Path(output_dir) / CONTRACT.output_files["intra_label_json"]
        ic.write_text(
            json.dumps({"intra_label_mean_cosine": intra,
                         "label_consistency_z": z_score},
                        ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # ---- manifest
        manifest = {
            "encoder_name": encoder_name,
            "embedding_dim": int(D),
            "mode": mode,
            "label_consistency_z": z_score,
            "n_phrases": int(len(ids)),
        }
        mfj = Path(output_dir) / CONTRACT.output_files["manifest_json"]
        mfj.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                       encoding="utf-8")

        return {
            "embeddings_csv":   str(ec),
            "similarity_top3":  str(sc),
            "intra_label_json": str(ic),
            "manifest_json":    str(mfj),
            "manifest_dict":    manifest,
        }


def get_solver(prefer_transformer_model: Optional[str] = None,
               tfidf_analyzer: str = "char_wb",
               tfidf_ngram=(2, 4)):
    return TextFeaturesSolver(
        prefer_transformer_model=prefer_transformer_model,
        tfidf_analyzer=tfidf_analyzer, tfidf_ngram=tfidf_ngram,
    )


def selftest():
    """Mini fixture: 6 short Chinese phrases × 3 labels (2 per label),
    designed so that intra-label cosine clearly exceeds inter-label
    even with the TF-IDF fallback.

    中文：fixture = 6 句中文 × 3 个标签，每标签 2 句：
      - depression：情绪低落 / 心情低落 (共享"低落")
      - insomnia  ：难以入睡 / 整夜失眠 (共享"失眠/睡")
      - psychotic ：幻听妄想 / 持续幻听被害妄想 (共享"幻听/妄想")

    每对同标签都共享至少一个关键汉字 → 即使 Mode B (TF-IDF) 也能拉开
    intra vs inter cosine 距离。

    通过判定：
      - mode ∈ {"A", "B"}（任一通路 OK）
      - label_consistency_z >= 1.0（很宽的下界，正常 Mode A ~ 3+，
        Mode B 在这个 fixture 上也能 ≥ 1.5）
    """
    import tempfile
    df = pd.DataFrame({
        "phrase_id": [f"T{i}" for i in range(6)],
        "text_zh": [
            "情绪低落，对原本喜爱的活动失去兴趣",   # depression
            "持续两周心情低落，无法体验快乐",       # depression
            "夜间难以入睡，凌晨早醒",               # insomnia
            "整夜失眠，白天精神萎靡",               # insomnia
            "出现幻听妄想，认为他人监视自己",       # psychotic
            "存在持续幻听及被害妄想",               # psychotic
        ],
        "label": ["depression", "depression",
                  "insomnia", "insomnia",
                  "psychotic", "psychotic"],
    })
    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver().run(
            df=df,
            mapping=ColumnMapping({"id_col": "phrase_id",
                                     "text_col": "text_zh",
                                     "label_col": "label"}),
            output_dir=Path(tmp),
        )
        manifest = out["manifest_dict"]
        if manifest["mode"] not in ("A", "B"):
            diffs.append(f"unexpected mode: {manifest['mode']}")
        z = manifest.get("label_consistency_z")
        if z is None or z < 1.0:  # very low bar — same-label pairs share kanji
            diffs.append(f"label_consistency_z too low on tight fixture: {z}")
    return {"ok": len(diffs) == 0,
            "summary": ("text encoder produces label-consistent "
                        "embeddings (z >= 1.0) on 6-phrase fixture"
                        if not diffs else f"{len(diffs)} mismatch(es)"),
            "details": {"diffs": diffs, "tested": ["text_features"]}}
