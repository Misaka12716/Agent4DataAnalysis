# -*- coding: utf-8 -*-
"""Operator-retrieval front-ends for the Stage-3 planner (V8 retrieval study).

Problem
-------
The Stage-3 operator selector renders the ENTIRE ~70-operator catalog into
its prompt on every call (see ``catalog.render_catalog_markdown``).  That
burns context budget and dilutes a small planner model's attention, so it
retreats to the ``__coder__`` fallback ("operator marginalization").

Fix direction
-------------
Retrieve only the top-k operators relevant to ``(task + dataframe profile)``
and show ONLY those to the planner.  This module hosts **two independent,
swappable retrieval front-ends** that are compared head-to-head — they are
NEVER fused with each other:

  - ``graphrag``       — a training-free re-implementation of *Graph
                         RAG-Tool Fusion* (Lumer et al. 2025, the
                         Graph-RAG-Tool-Fusion-ToolLinkOS repo, which ships
                         only the dataset, not code): vector-retrieval seeds
                         + graph traversal that pulls in each seed's
                         dependency operators.
  - ``graphtoolcall``  — the off-the-shelf ``graph_tool_call`` PyPI package
                         (``pip install graph-tool-call``, zero-dependency
                         core): hybrid BM25 + graph traversal (PRECEDES /
                         REQUIRES / COMPLEMENTARY) fused via weighted RRF.

Both are **training-free**.  ``sentence-transformers`` is not importable in
this environment (torch too old), so the "vector" signal uses an
off-the-shelf TF-IDF vectorizer (scikit-learn) for ``graphrag`` and the
zero-dependency BM25 that ships inside ``graph_tool_call`` for
``graphtoolcall``.  No embedding model is trained or downloaded.

Switch
------
``OP_RETRIEVAL = none | graphrag | graphtoolcall``  (V8 §G default is now
``graphtoolcall`` after the head-to-head study found it wins on accuracy
+ token cost + freeloader-rate; see docs/V8_RETRIEVAL_INTEGRATION_REPORT.md
and docs/V8_OPERATOR_CLEANUP_AND_PIPELINE.md).  ``OP_RETRIEVAL_K`` overrides
top-k (default 12).  Set ``OP_RETRIEVAL=none`` to fall back to the full-
catalog baseline.  The ``__coder__`` fallback is unaffected: it is not a
catalog operator and is always available to the planner via the prompt
rules + always run last by the benchmark drivers.

The operator index + dependency edges are built ONCE from the live registry
(:func:`operator_pipeline.registry.list_solvers`) and the planner catalog
descriptors (:func:`operator_agent.catalog.build_catalog_for_planner`), then
cached for the process.  Edges are derived from simple, documented
heuristics (see :func:`_dependency_edges`).
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Any, Dict, List, Tuple

from operator_pipeline.registry import list_solvers
from operator_agent.catalog import (
    _HIDDEN_FROM_PLANNER,
    build_catalog_for_planner,
    _BUCKETS,
)


# ---------------------------------------------------------------------------
# Switch helpers
# ---------------------------------------------------------------------------
def get_method() -> str:
    """Return the active retrieval method: ``none`` | ``graphrag`` |
    ``graphtoolcall``.

    V8 §G — the default is now ``graphtoolcall`` (head-to-head winner of
    the V8 retrieval study; see docs/V8_RETRIEVAL_INTEGRATION_REPORT.md).
    Unknown / empty values resolve to the default.  Set
    ``OP_RETRIEVAL=none`` to fall back to the full-catalog baseline.
    """
    m = (os.environ.get("OP_RETRIEVAL", "graphtoolcall")
         or "graphtoolcall").strip().lower()
    return m if m in ("none", "graphrag", "graphtoolcall") else "graphtoolcall"


def get_top_k(default: int = 12) -> int:
    try:
        return max(1, int(os.environ.get("OP_RETRIEVAL_K", str(default))))
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Operator-index construction (shared by BOTH front-ends — index only, not
# retrieval logic).  Each operator becomes a (id, text, params) descriptor.
# ---------------------------------------------------------------------------
# Cleaning / data-governance operators.  Benchmarks (esp. RADAR) ship messy
# data, so these PRECEDE analysis operators in the dependency graph.
# V8 §G — missing_summary was hidden as a freeloader (planner used to
# schedule it 17×/30 items but the Coder consumed it only 1×).  We drop
# it from the cleaning set so graphrag's "precedes" expansion no longer
# injects it back into the planner catalog.
_CLEANING_OPS = {
    "metadata_parser", "data_imputation",
    "outlier_iqr_flag", "consistency_check", "encode_categorical",
    "normalize_scale", "feature_selection",
}

# Documented domain pipelines: producer -> consumer.  A consumer REQUIRES its
# upstream producer; equivalently the producer PRECEDES the consumer.  Kept
# deliberately short and only for chains where an operator literally consumes
# another operator's table output.
_PRECEDES_CHAINS: List[List[str]] = [
    # bioinformatics expression -> DEG -> gene-collapse -> enrichment
    ["gds_soft_parser", "probe_to_gene_collapse", "limma_deg_two_group",
     "probe_deg_collapse_to_gene", "pathway_enrichment_fisher"],
    ["gds_soft_parser", "edger_de", "probe_deg_collapse_to_gene",
     "pathway_enrichment_fisher"],
    ["gds_soft_parser", "deseq2_de", "probe_deg_collapse_to_gene",
     "pathway_enrichment_fisher"],
    ["gds_soft_parser", "combat_batch_correction", "limma_deg_two_group"],
    ["gds_soft_parser", "pca_decompose"],
    ["gds_soft_parser", "hclust_samples"],
    # single-cell scanpy chain
    ["gene_filter_normalize", "highly_variable_genes", "sc_dim_reduction",
     "sc_clustering", "sc_marker_genes"],
    # cheminformatics fingerprint -> similarity / QSAR
    ["morgan_fingerprint", "tanimoto_similarity"],
    ["morgan_fingerprint", "molecular_property_predict"],
    ["molecular_descriptors", "molecular_property_predict"],
]

_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9]+")


def _id_to_words(op_id: str) -> str:
    return op_id.replace("_", " ")


@lru_cache(maxsize=1)
def _operator_index() -> Tuple[Tuple[str, str, Tuple[str, ...]], ...]:
    """Return a tuple of ``(op_id, doc_text, param_names)`` for every
    registered operator.

    ``doc_text`` deliberately leads with the (English) operator id and its
    de-underscored form plus the (English) capability-bucket tag, so a BM25 /
    TF-IDF retriever gets a strong English signal even though many contract
    descriptions are Chinese.
    """
    catalog = {c["id"]: c for c in build_catalog_for_planner()}
    # bucket tag per operator (english keyword signal)
    bucket_of: Dict[str, str] = {}
    for b in _BUCKETS:
        for m in b["members"]:
            bucket_of.setdefault(m, b["tag"])

    out: List[Tuple[str, str, Tuple[str, ...]]] = []
    for op_id, zh_desc in list_solvers():
        # V8 §G — keep the retrieval index in sync with the planner-visible
        # catalog.  Freeloader operators hidden from the planner must NOT be
        # candidates for BM25 / TF-IDF retrieval either, otherwise the
        # ``graphrag`` / ``graphtoolcall`` fronts could re-surface them
        # through the dependency graph.
        if op_id in _HIDDEN_FROM_PLANNER:
            continue
        c = catalog.get(op_id, {})
        roles = c.get("roles") or {}
        role_words = " ".join(
            f"{rk} {(rv or {}).get('desc', '')}" for rk, rv in roles.items()
        )
        parts = [
            op_id, _id_to_words(op_id),
            bucket_of.get(op_id, ""),
            str(c.get("capability") or ""),
            str(c.get("name") or ""),
            str(c.get("description") or ""),
            str(zh_desc or ""),
            role_words,
        ]
        doc = " ".join(p for p in parts if p)
        param_names = tuple(roles.keys())
        out.append((op_id, doc, param_names))
    return tuple(out)


@lru_cache(maxsize=1)
def _dependency_edges() -> Tuple[Tuple[str, str, str], ...]:
    """Derive ``(source, target, relation)`` dependency edges from simple,
    documented heuristics.  Relations use ``graph_tool_call`` vocabulary:

    - ``precedes``      — source should/can run before target.
    - ``requires``      — source consumes target's output (target is a
                          prerequisite of source).
    - ``complementary`` — source & target are interchangeable / used together
                          (same capability bucket).
    """
    ids = {op_id for op_id, _, _ in _operator_index()}
    edges: List[Tuple[str, str, str]] = []

    analysis = [i for i in ids if i not in _CLEANING_OPS]
    # cleaning PRECEDES analysis (messy-data-first heuristic)
    for c in _CLEANING_OPS:
        if c not in ids:
            continue
        for a in analysis:
            edges.append((c, a, "precedes"))

    # domain chains: producer precedes consumer; consumer requires producer
    for chain in _PRECEDES_CHAINS:
        for i in range(len(chain) - 1):
            src, dst = chain[i], chain[i + 1]
            if src in ids and dst in ids:
                edges.append((src, dst, "precedes"))
                edges.append((dst, src, "requires"))

    # within-bucket complementary edges
    for b in _BUCKETS:
        members = [m for m in b["members"] if m in ids]
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                edges.append((members[i], members[j], "complementary"))

    return tuple(edges)


@lru_cache(maxsize=1)
def _prereq_map() -> Dict[str, List[str]]:
    """op_id -> list of prerequisite op_ids (predecessors via requires/precedes).

    Domain ``requires`` edges and cleaning ``precedes`` edges both contribute,
    so an analysis operator's prerequisites include its upstream producer(s)
    and the cleaning operators.  Used by the ``graphrag`` front-end.
    """
    pre: Dict[str, List[str]] = {}
    for src, dst, rel in _dependency_edges():
        if rel == "requires":
            # src requires dst  → dst is prerequisite of src
            pre.setdefault(src, []).append(dst)
        elif rel == "precedes":
            # src precedes dst  → src is prerequisite of dst
            pre.setdefault(dst, []).append(src)
    return pre


# ---------------------------------------------------------------------------
# Front-end #1: graphrag  (vector seeds + dependency graph traversal)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _tfidf():
    """Build a TF-IDF matrix over operator docs (training-free vectorizer)."""
    from sklearn.feature_extraction.text import TfidfVectorizer

    idx = _operator_index()
    ids = [op_id for op_id, _, _ in idx]
    docs = [doc for _, doc, _ in idx]
    vec = TfidfVectorizer(lowercase=True, token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z0-9]+\b")
    mat = vec.fit_transform(docs)
    return ids, vec, mat


def _vector_scores(query: str) -> List[Tuple[str, float]]:
    """Cosine similarity of ``query`` against each operator doc."""
    from sklearn.metrics.pairwise import cosine_similarity

    ids, vec, mat = _tfidf()
    qv = vec.transform([query])
    sims = cosine_similarity(qv, mat)[0]
    ranked = sorted(zip(ids, sims), key=lambda kv: kv[1], reverse=True)
    return ranked


def _retrieve_graphrag(query: str, k: int) -> List[str]:
    """Graph RAG-Tool Fusion (re-implemented, training-free).

    1. Vector retrieval (TF-IDF cosine) → ranked seed operators.
    2. Graph traversal → for each seed (in score order) add up to 3 of its
       highest-scoring prerequisite operators (dependency edges), so the
       planner also sees the cleaning / upstream operators a seed depends on.
    3. Truncate the (seeds + dependencies) union to ``k``.
    """
    ranked = _vector_scores(query)
    score_of = dict(ranked)
    prereqs = _prereq_map()

    result: List[str] = []

    def _add(op: str) -> None:
        if op not in result:
            result.append(op)

    seeds = [op for op, _ in ranked]
    for seed in seeds:
        if len(result) >= k:
            break
        _add(seed)
        # graph traversal: pull in the seed's top prerequisites (dependencies)
        deps = sorted(
            prereqs.get(seed, []),
            key=lambda d: score_of.get(d, 0.0),
            reverse=True,
        )[:3]
        for d in deps:
            if len(result) >= k:
                break
            _add(d)
    return result[:k]


# ---------------------------------------------------------------------------
# Front-end #2: graphtoolcall  (graph_tool_call package — BM25 + graph + RRF)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _build_tool_graph():
    """Build a ``graph_tool_call.ToolGraph`` over our operators (BM25 + graph,
    no embedding — zero extra dependencies). Package is a normal pip
    dependency (see requirements.txt); no vendoring/sys.path surgery needed."""
    from graph_tool_call import ToolGraph

    tg = ToolGraph()
    for op_id, doc, param_names in _operator_index():
        params = None
        if param_names:
            params = {
                "type": "object",
                "properties": {p: {"type": "string"} for p in param_names},
            }
        # Use the rich doc text as the description so BM25 indexes the English
        # id tokens + capability keywords (not just the Chinese contract blurb).
        tg.add_tool_simple(op_id, doc, params)

    valid = {op_id for op_id, _, _ in _operator_index()}
    for src, dst, rel in _dependency_edges():
        if src in valid and dst in valid:
            try:
                tg.add_relation(src, dst, rel)
            except Exception:
                pass
    return tg


def _retrieve_graphtoolcall(query: str, k: int) -> List[str]:
    """Retrieve via the graph_tool_call hybrid engine (BM25 + graph + RRF)."""
    tg = _build_tool_graph()
    tools = tg.retrieve(query, top_k=k)
    return [t.name for t in tools]


# ---------------------------------------------------------------------------
# Generic BM25 candidate ranking (reuses graph_tool_call's retrieval engine
# as a plain text ranker -- NOT operator retrieval). Built for the "model
# picks the wrong one among several semantically-similar text candidates"
# failure mode: instead of dumping every candidate label/column value in a
# workbook into the prompt (or a single difflib closest-match guess), rank
# ALL real candidates against the target description with BM25 and keep only
# the top-k, so a small model sees a short, relevance-sorted shortlist
# instead of either everything or a single arbitrary pick.
# ---------------------------------------------------------------------------
def bm25_rank_candidates(query: str, candidates: List[str], top_k: int = 5
                         ) -> List[str]:
    """Rank ``candidates`` by BM25 relevance to ``query``; return the top_k
    strings, most relevant first. Falls back to difflib if graph_tool_call
    is not importable, and to the first top_k candidates if even that fails
    -- ranking must never raise or block the caller."""
    query = (query or "").strip()
    cands = [c for c in dict.fromkeys(str(c) for c in candidates if str(c).strip())]
    if not query or not cands:
        return cands[:top_k]
    try:
        from graph_tool_call import ToolGraph
        tg = ToolGraph()
        for i, c in enumerate(cands):
            tg.add_tool_simple(f"cand_{i}", c)
        hits = tg.retrieve(query, top_k=top_k)
        by_name = {f"cand_{i}": c for i, c in enumerate(cands)}
        ranked = [by_name[t.name] for t in hits if t.name in by_name]
        if ranked:
            return ranked
    except Exception:
        pass
    try:
        import difflib
        close = difflib.get_close_matches(query, cands, n=top_k, cutoff=0.0)
        if close:
            return close
    except Exception:
        pass
    return cands[:top_k]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def retrieve_operator_ids(query: str, *, method: str | None = None,
                          k: int | None = None) -> List[str]:
    """Return an ordered list of operator ids relevant to ``query``.

    ``method`` defaults to the ``OP_RETRIEVAL`` env switch.  For ``none`` an
    empty list is returned (caller keeps the full catalog).
    """
    method = method or get_method()
    k = k or get_top_k()
    query = (query or "").strip()
    if not query or method == "none":
        return []
    try:
        if method == "graphrag":
            return _retrieve_graphrag(query, k)
        if method == "graphtoolcall":
            return _retrieve_graphtoolcall(query, k)
    except Exception:
        # Retrieval must never break planning — fall back to full catalog.
        return []
    return []
