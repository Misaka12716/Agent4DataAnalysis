"""DirectLiNGAM — score-based causal discovery for linear non-Gaussian SEMs.

Unlike PC (which returns only a CPDAG and may leave many edges
unoriented), DirectLiNGAM identifies the **full** DAG direction by
exploiting non-Gaussianity of the noise terms.  This is exactly the
setting QRData's pairwise-causal-discovery questions target ("given
two variables, which is the cause?").

References
----------
- Shimizu S et al. (2011) "DirectLiNGAM: A direct method for learning a
  linear non-Gaussian structural equation model" *JMLR* 12:1225-1248.
- Shimizu S et al. (2006) "A linear non-Gaussian acyclic model for
  causal discovery" *JMLR* 7:2003-2030.

Outputs
-------
- ``lingam_adjacency.csv``
  square (n_var × n_var) signed weight matrix.  ``B[i,j] = w`` means
  ``X_j = ... + w * X_i + noise_j``; convention from Shimizu et al.
  (``model.adjacency_matrix_``).
- ``lingam_order.csv``
  inferred causal order (rows = step index, columns = variable name).
  Variables earlier in the order are ancestors of variables later.
- ``lingam_edges.csv``
  long form: src --> dst with the estimated linear effect weight.
- ``lingam_summary.json``
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract
from ._inputs import coerce_numeric_friendly, detect_column_kind


CONTRACT = SolverContract(
    name="causal_discovery_lingam",
    capability="F_causal_pair",
    description=(
        "DirectLiNGAM (Shimizu et al. 2011) for fully-oriented causal-DAG "
        "discovery from observational data assuming linear relations and "
        "non-Gaussian additive noise.  Returns a complete causal order "
        "AND signed edge weights — unlike PC which leaves CPDAG ambiguity."
        " Use when (i) variables look continuous-non-Gaussian (e.g. "
        "right-skewed counts, log-rates) AND (ii) you need a UNIQUE edge "
        "direction for downstream pairwise causal questions."
    ),
    roles={
        "variables": RoleSpec(Role.NUMERIC_LIST,
                                "numeric columns over which to fit the "
                                "DirectLiNGAM DAG (>=2 needed)"),
    },
    static_params={
        "random_state": 42,
        "edge_threshold": 0.05,   # |w| below this → drop edge in long form
    },
    output_files={
        "adjacency_csv": "lingam_adjacency.csv",
        "order_csv":     "lingam_order.csv",
        "edges_csv":     "lingam_edges.csv",
        "summary_json":  "lingam_summary.json",
    },
    output_kind={"adjacency_csv": "s", "order_csv": "s",
                  "edges_csv": "s", "summary_json": "s"},
)


class CausalDiscoveryLiNGAMSolver:
    contract = CONTRACT

    def __init__(self, random_state: int = 42, edge_threshold: float = 0.05):
        self.random_state = int(random_state)
        self.edge_threshold = float(edge_threshold)

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        import lingam

        cols = list(mapping["variables"])
        if len(cols) < 2:
            raise ValueError(f"LiNGAM requires >=2 variables, got {len(cols)}")
        missing = [c for c in cols if c not in df.columns]
        if missing:
            raise KeyError(f"LiNGAM: missing columns in df: {missing}")
        sub = df[cols].copy()
        column_diagnostics: Dict[str, Any] = {}
        for c in cols:
            column_diagnostics[c] = detect_column_kind(sub[c])
            sub[c] = coerce_numeric_friendly(sub[c])
        sub = sub.replace([np.inf, -np.inf], np.nan).dropna()
        n = len(sub)
        if n < 50:
            raise ValueError(f"LiNGAM: n={n} too small after dropna; "
                              "need >=50 for stable ICA-based scoring.")
        for c in cols:
            if sub[c].nunique() <= 1:
                raise ValueError(f"LiNGAM: column {c!r} is constant — "
                                  "ICA-based identification undefined.")
        # LiNGAM theory: continuous + non-Gaussian.  Binary / low-card
        # discrete violates this.  We log a warning rather than refuse —
        # algorithm still runs but the planner should know.
        binary_cols = [c for c in cols
                        if column_diagnostics[c].get("is_binary")]
        discrete_cols = [c for c in cols
                          if column_diagnostics[c].get("is_discrete_low_cardinality")
                          and not column_diagnostics[c].get("is_binary")]

        model = lingam.DirectLiNGAM(random_state=self.random_state)
        model.fit(sub.values.astype(float))

        # adjacency_matrix_[i, j] = effect of variable j on variable i
        # (NB. row=destination, col=source).  We transpose for the more
        # standard "row=source -> col=destination" we expose downstream.
        adj_raw = np.asarray(model.adjacency_matrix_)
        adj = adj_raw.T   # now adj[i, j] = effect of i (source) ON j (dst)

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        adj_df = pd.DataFrame(adj, index=cols, columns=cols)
        adj_path = out_dir / CONTRACT.output_files["adjacency_csv"]
        adj_df.to_csv(adj_path)

        # Causal order
        order_idx = list(model.causal_order_)
        order_names = [cols[i] for i in order_idx]
        ord_df = pd.DataFrame({"step": list(range(len(order_names))),
                                "variable": order_names})
        ord_path = out_dir / CONTRACT.output_files["order_csv"]
        ord_df.to_csv(ord_path, index=False)

        # Long-form edges
        rows: List[Dict[str, Any]] = []
        for i, src in enumerate(cols):
            for j, dst in enumerate(cols):
                w = float(adj[i, j])
                if abs(w) >= self.edge_threshold and i != j:
                    rows.append({"src": src, "dst": dst, "weight": w})
        edges_df = pd.DataFrame(rows)
        ed_path = out_dir / CONTRACT.output_files["edges_csv"]
        edges_df.to_csv(ed_path, index=False)

        summary: Dict[str, Any] = {
            "n_variables":   len(cols),
            "n_obs":         int(n),
            "n_edges":       int(len(edges_df)),
            "causal_order":  order_names,
            "edge_threshold": self.edge_threshold,
            "column_diagnostics": column_diagnostics,
        }
        if binary_cols or discrete_cols:
            summary["lingam_assumption_warning"] = (
                f"DirectLiNGAM assumes continuous non-Gaussian inputs; "
                f"binary={binary_cols} discrete={discrete_cols} violate "
                "this — the recovered order/weights may be unreliable.  "
                "Use a categorical-aware method or pre-discretize first.")
        sm_path = out_dir / CONTRACT.output_files["summary_json"]
        sm_path.write_text(json.dumps(summary, indent=2, default=str),
                            encoding="utf-8")

        return {
            "adjacency_csv": str(adj_path),
            "order_csv":     str(ord_path),
            "edges_csv":     str(ed_path),
            "summary_json":  str(sm_path),
            "edges_dict":    edges_df.to_dict(orient="records"),
            **summary,
        }


def get_solver(random_state: int = 42, edge_threshold: float = 0.05
               ) -> CausalDiscoveryLiNGAMSolver:
    return CausalDiscoveryLiNGAMSolver(random_state=random_state,
                                          edge_threshold=edge_threshold)


# ---------------------------------------------------------------------------
# Ground-truth selftest
# ---------------------------------------------------------------------------
def _gt_a_3var_uniform() -> List[str]:
    """GT-A — 3-var SEM, uniform noise; recover order + weights."""
    import tempfile
    rng = np.random.default_rng(2026)
    n = 2000
    X1 = rng.uniform(-1, 1, n)
    X2 = 0.8 * X1 + rng.uniform(-0.5, 0.5, n)
    X3 = -0.6 * X1 + 0.9 * X2 + rng.uniform(-0.5, 0.5, n)
    df = pd.DataFrame({"X1": X1, "X2": X2, "X3": X3})
    true_w = {("X1", "X2"): 0.8, ("X2", "X3"): 0.9, ("X1", "X3"): -0.6}
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver().run(
            df, ColumnMapping({"variables": ["X1", "X2", "X3"]}), Path(tmp))
        if list(out["causal_order"]) != ["X1", "X2", "X3"]:
            diffs.append(f"[A] order={out['causal_order']} expected "
                          "[X1,X2,X3]")
        adj_df = pd.read_csv(out["adjacency_csv"], index_col=0)
        for (s, d), w_true in true_w.items():
            w_est = float(adj_df.loc[s, d])
            tol = 0.15 if abs(w_true) <= 0.8 else 0.20
            if abs(w_est - w_true) > tol:
                diffs.append(f"[A] {s}->{d}: est={w_est:+.3f} expected "
                              f"{w_true:+.3f} (tol ±{tol})")
        for s, d in [("X2", "X1"), ("X3", "X1"), ("X3", "X2")]:
            w = float(adj_df.loc[s, d])
            if abs(w) > 0.15:
                diffs.append(f"[A] spurious {s}->{d} w={w:+.3f}")
    return diffs


def _gt_b_5var_scalability() -> List[str]:
    """GT-B — 5-variable SEM, recover the full causal order.

    DAG:
        A → B → C → D
        A → E,  C → E       (E is a sink with 2 parents)
    Topological orders:  [A, B, C, D, E] (D and E both leaf-like; either
    can come last). We accept any order where every (parent, child) is
    respected.
    """
    import tempfile
    rng = np.random.default_rng(13)
    n = 3000
    A = rng.uniform(-1, 1, n)
    B = 0.7 * A + rng.uniform(-0.4, 0.4, n)
    C = 0.6 * B + rng.uniform(-0.4, 0.4, n)
    D = 0.5 * C + rng.uniform(-0.4, 0.4, n)
    E = 0.4 * A + 0.4 * C + rng.uniform(-0.4, 0.4, n)
    df = pd.DataFrame({"A": A, "B": B, "C": C, "D": D, "E": E})
    parents = {"A": [], "B": ["A"], "C": ["B"],
                "D": ["C"], "E": ["A", "C"]}
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver().run(
            df, ColumnMapping({"variables": list(df.columns)}), Path(tmp))
        order = list(out["causal_order"])
        pos = {v: i for i, v in enumerate(order)}
        for child, ps in parents.items():
            for p in ps:
                if pos[p] >= pos[child]:
                    diffs.append(f"[B] order violation: parent {p} "
                                  f"at pos {pos[p]} >= child {child} at "
                                  f"pos {pos[child]} (order={order})")
    return diffs


def _gt_c_gaussian_graceful() -> List[str]:
    """GT-C — Gaussian noise: LiNGAM is theoretically unidentified, but
    must not CRASH.  We just assert the call succeeds and returns
    SOMETHING.  (Strong claim: in Gaussian-noise data LiNGAM may pick
    the wrong order; that's acceptable theory — we only test for
    graceful execution here.)"""
    import tempfile
    rng = np.random.default_rng(101)
    n = 2000
    X1 = rng.normal(0, 1, n)
    X2 = 0.8 * X1 + rng.normal(0, 0.5, n)
    X3 = 0.6 * X2 + rng.normal(0, 0.5, n)
    df = pd.DataFrame({"X1": X1, "X2": X2, "X3": X3})
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            out = get_solver().run(
                df, ColumnMapping({"variables": ["X1", "X2", "X3"]}),
                Path(tmp))
            if not isinstance(out.get("causal_order"), list):
                diffs.append("[C] Gaussian: no causal_order returned")
        except Exception as e:
            diffs.append(f"[C] Gaussian-noise must not crash, got "
                          f"{type(e).__name__}: {e}")
    return diffs


def _gt_d_robustness() -> List[str]:
    """GT-D — input robustness."""
    import tempfile
    rng = np.random.default_rng(5)
    n = 500
    X1 = rng.uniform(-1, 1, n)
    X2 = 0.8 * X1 + rng.uniform(-0.5, 0.5, n)
    X3 = -0.6 * X1 + 0.9 * X2 + rng.uniform(-0.5, 0.5, n)
    diffs: List[str] = []

    # (1) dtype coercion
    df = pd.DataFrame({
        "X1": [f"{v:.4f}" for v in X1],
        "X2": pd.array(np.round(X2 * 100).astype(np.int64), dtype="Int64"),
        "X3": X3.astype(float),
    })
    with tempfile.TemporaryDirectory() as tmp:
        try:
            out = get_solver().run(
                df, ColumnMapping({"variables": ["X1", "X2", "X3"]}),
                Path(tmp))
            if list(out["causal_order"]) != ["X1", "X2", "X3"]:
                # Scaling X2 by 100 shouldn't change the order.
                diffs.append(f"[D-coerce] order={out['causal_order']} "
                              "should still be [X1,X2,X3]")
        except Exception as e:
            diffs.append(f"[D-coerce] raised {type(e).__name__}: {e}")

    # (2) constant col fail-fast
    df2 = pd.DataFrame({"X1": X1, "X2": X2, "K": np.zeros_like(X3)})
    with tempfile.TemporaryDirectory() as tmp:
        try:
            get_solver().run(df2,
                              ColumnMapping({"variables": ["X1", "X2", "K"]}),
                              Path(tmp))
            diffs.append("[D-const] constant col should ValueError")
        except ValueError:
            pass
        except Exception as e:
            diffs.append(f"[D-const] expected ValueError, got "
                          f"{type(e).__name__}")

    # (3) missing col
    df3 = pd.DataFrame({"X1": X1, "X2": X2})
    with tempfile.TemporaryDirectory() as tmp:
        try:
            get_solver().run(df3,
                              ColumnMapping({"variables": ["X1", "X2", "Q"]}),
                              Path(tmp))
            diffs.append("[D-missing] missing col should KeyError")
        except KeyError:
            pass
        except Exception as e:
            diffs.append(f"[D-missing] expected KeyError, got "
                          f"{type(e).__name__}")
    return diffs


def _gt_e_messy_and_binary() -> List[str]:
    """GT-E — messy strings + binary column must trigger
    lingam_assumption_warning."""
    import tempfile
    rng = np.random.default_rng(2026)
    n = 800
    X1 = rng.uniform(-1, 1, n)
    X2 = 0.8 * X1 + rng.uniform(-0.5, 0.5, n)
    # X3 is binary — violates LiNGAM assumption.
    X3 = (rng.uniform(0, 1, n) < 0.4).astype(int)
    df = pd.DataFrame({
        "X1": [f"{v:.4f}" for v in X1],          # numeric string
        # ×5000 so the resulting magnitudes (~|5000|) actually exhibit
        # thousands separators when format-stringified.
        "X2": [f"{v * 5000:,.4f}" for v in X2],
        "X3": X3,
    })
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            out = get_solver().run(
                df, ColumnMapping({"variables": ["X1", "X2", "X3"]}),
                Path(tmp))
        except Exception as e:
            diffs.append(f"[E] should parse messy strings, raised "
                          f"{type(e).__name__}: {e}")
            return diffs
        if not out.get("lingam_assumption_warning"):
            diffs.append("[E] lingam_assumption_warning missing for "
                          "binary X3")
        cd = out.get("column_diagnostics", {})
        if not cd.get("X2", {}).get("had_thousands"):
            diffs.append("[E] diagnostics missed thousands on X2")
        if not cd.get("X3", {}).get("is_binary"):
            diffs.append("[E] diagnostics missed binary on X3")
    return diffs


def selftest() -> Dict[str, Any]:
    """5-scenario ground-truth suite for DirectLiNGAM.

      GT-A  3-var SEM, uniform noise   (order + edge weights + no reverse)
      GT-B  5-var SEM scalability      (full topological order respected)
      GT-C  Gaussian noise graceful    (no crash; identifiability lost)
      GT-D  input robustness           (dtype, constant, missing)
      GT-E  messy strings + binary     (warning surfaced)

    All five must pass for ok=True.
    """
    diffs = (_gt_a_3var_uniform() + _gt_b_5var_scalability()
             + _gt_c_gaussian_graceful() + _gt_d_robustness()
             + _gt_e_messy_and_binary())
    return {
        "ok": len(diffs) == 0,
        "summary": ("5/5 scenarios pass: 3-var SEM, 5-var topological, "
                    "Gaussian graceful, input robustness, messy strings + "
                    "binary warning"
                    if not diffs else f"{len(diffs)} mismatch(es)"),
        "details": {"diffs": diffs,
                    "tested": ["causal_discovery_lingam"],
                    "n_scenarios": 5},
    }
