"""PC algorithm — constraint-based causal-graph discovery.

Closes the QRData causal-discovery-graph gap (≈150 questions in QRData
that ask "given this dataset, which DAG over the columns is most
likely?"). Uses ``causal-learn`` (the official, peer-reviewed Python
port of TETRAD; the same backend QRData's own AgentPC baseline uses).

Reference
---------
- Spirtes P & Glymour C (1991) "An algorithm for fast recovery of sparse
  causal graphs" *Social Science Computer Review* 9(1).
- Spirtes, Glymour, Scheines (2000) *Causation, Prediction and Search*
  (MIT Press), 2nd ed., chapter 5.
- Zheng Y et al. (2024) "Causal-Learn: Causal discovery in Python"
  *JMLR* 25:60-66 — covers the implementation we call.

Outputs
-------
- ``pc_adjacency.csv``
  square (n_var × n_var) adjacency matrix.  Entry ``A[i,j]`` encodes the
  edge mark **out of** node *i* into node *j* using causal-learn's
  endpoint convention (see ``CausalGraph.graph``):
      0 = no mark (no edge),
      1 = arrow head,   ( i o-> j )
     -1 = tail,         ( i o-- j  → directed: i  --> j   )
   This is the same convention the QRData reference solver consumes.
- ``pc_edges.csv``
  long form: every undirected/directed edge as (src, dst, orientation)
  with orientation ∈ {"-->","<--","---","<->"}.
- ``pc_summary.json``
  {n_variables, n_edges, n_directed, n_undirected,
   alpha, ci_test, runtime_seconds}.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract
from ._inputs import coerce_numeric_friendly, detect_column_kind


CONTRACT = SolverContract(
    name="causal_discovery_pc",
    capability="F_causal_graph",
    description=(
        "PC algorithm (Spirtes-Glymour 1991) for constraint-based causal "
        "DAG discovery from observational data. Returns the CPDAG "
        "(Markov equivalence class) over the supplied numeric columns; "
        "orientations are computed with Meek rules. Backed by "
        "causal-learn (the official open-source port of TETRAD). "
        "Significance level alpha controls edge inclusion; "
        "ci_test selects the conditional-independence oracle "
        "('fisherz' for Gaussian/linear, 'chisq' for discrete, "
        "'kci' for nonparametric)."
    ),
    roles={
        "variables": RoleSpec(Role.NUMERIC_LIST,
                                "numeric columns over which to discover "
                                "the causal graph (>=3 needed)"),
    },
    static_params={
        "alpha":  0.05,
        "ci_test": "fisherz",   # one of: fisherz | chisq | kci
        "stable": True,
    },
    output_files={
        "adjacency_csv": "pc_adjacency.csv",
        "edges_csv":     "pc_edges.csv",
        "summary_json":  "pc_summary.json",
    },
    output_kind={"adjacency_csv": "s", "edges_csv": "s", "summary_json": "s"},
)


def _adj_to_edges(adj: np.ndarray, names: List[str]) -> pd.DataFrame:
    """Convert causal-learn CausalGraph.graph endpoint matrix to edge rows.

    causal-learn convention (see ``causallearn.graph.GeneralGraph``):
      ``adj[j, i] = 1`` and ``adj[i, j] = -1`` → directed  i --> j
      ``adj[i, j] = -1`` and ``adj[j, i] = -1`` → undirected i --- j  (CPDAG)
      ``adj[i, j] = 1`` and ``adj[j, i] = 1``   → bi-directed  i <-> j
                                                  (latent confounder)
    """
    rows: List[Dict[str, Any]] = []
    n = adj.shape[0]
    seen = set()
    for i in range(n):
        for j in range(i + 1, n):
            key = (i, j)
            if key in seen:
                continue
            a_ij = int(adj[i, j])
            a_ji = int(adj[j, i])
            if a_ij == 0 and a_ji == 0:
                continue
            # causal-learn endpoint matrix encoding:
            #   adj[i, j] is the mark AT NODE i on the (i, j) edge.
            #     -1 = tail  (·—)
            #      1 = arrow head (→)
            # Therefore:
            #   adj[i, j] = -1 (tail at i) AND adj[j, i] = 1 (arrow at j)
            #     → i  --> j
            #   adj[i, j] = 1  (arrow at i) AND adj[j, i] = -1 (tail at j)
            #     → j  --> i   (rendered as "i <-- j")
            if a_ij == -1 and a_ji == 1:
                ori, src, dst = "-->", i, j
            elif a_ij == 1 and a_ji == -1:
                ori, src, dst = "<--", i, j
            elif a_ij == -1 and a_ji == -1:
                ori, src, dst = "---", i, j
            elif a_ij == 1 and a_ji == 1:
                ori, src, dst = "<->", i, j
            else:
                ori, src, dst = f"?({a_ij},{a_ji})", i, j
            rows.append({"src": names[src], "dst": names[dst],
                          "orientation": ori})
            seen.add(key)
    return pd.DataFrame(rows)


class CausalDiscoveryPCSolver:
    contract = CONTRACT

    def __init__(self, alpha: float = 0.05, ci_test: str = "fisherz",
                 stable: bool = True):
        self.alpha = float(alpha)
        self.ci_test = str(ci_test)
        self.stable = bool(stable)

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        from causallearn.search.ConstraintBased.PC import pc

        cols = list(mapping["variables"])
        if len(cols) < 3:
            raise ValueError(
                f"PC requires >=3 variables (got {len(cols)}); fewer "
                "variables → use pairwise correlation/regression instead.")
        missing = [c for c in cols if c not in df.columns]
        if missing:
            raise KeyError(f"PC: missing columns in df: {missing}")
        # Input robustness: messy-string coercion + inf removal +
        # constant-col handling (Fisher-Z CI test is undefined on
        # zero-variance cols, so we drop them with a warning rather
        # than crashing the whole call — common in real-world tables
        # where one of the chosen vars is e.g. an indicator that turns
        # out to be uniformly 0 in the filtered subset).
        sub = df[cols].copy()
        column_diagnostics: Dict[str, Any] = {}
        for c in cols:
            column_diagnostics[c] = detect_column_kind(sub[c])
            sub[c] = coerce_numeric_friendly(sub[c])
        sub = sub.replace([np.inf, -np.inf], np.nan).dropna()
        n = len(sub)
        if n < 50:
            raise ValueError(f"PC: n={n} too small after dropna; need >=50 "
                              "for reliable CI tests.")
        constant_cols: List[str] = [c for c in cols
                                     if sub[c].nunique() <= 1]
        if constant_cols:
            kept = [c for c in cols if c not in constant_cols]
            if len(kept) < 3:
                raise ValueError(
                    f"PC: after dropping constant column(s) "
                    f"{constant_cols}, only {len(kept)} variable(s) "
                    "remain (<3); cannot run causal discovery. Choose "
                    "additional non-constant columns and re-issue.")
            cols = kept
            sub = sub[cols]
            column_diagnostics["__dropped_constant_cols"] = constant_cols
        # Collinearity guard: Fisher-Z relies on a non-singular
        # correlation matrix.  When two columns are perfectly correlated
        # (or near-perfectly so) causal-learn raises "Data correlation
        # matrix is singular".  Drop the redundant member of each near-
        # duplicate pair (keep the first), which is exactly what an
        # analyst would do by hand.  Threshold |r|>=0.9999 is intentional
        # — it only flags near-perfect collinearity, not biological co-
        # expression.  Any column whose variance is effectively zero on
        # the surviving rows (after dropna) is also dropped.
        if self.ci_test == "fisherz" and len(cols) >= 2:
            std = sub.std(ddof=0)
            zero_var = std[std <= 1e-12].index.tolist()
            if zero_var:
                dropped_zv = list(zero_var)
                cols = [c for c in cols if c not in dropped_zv]
                sub = sub[cols]
                column_diagnostics.setdefault(
                    "__dropped_zero_variance_cols", []).extend(dropped_zv)
            if len(cols) >= 2:
                corr = sub.corr().abs()
                near_dup: List[str] = []
                for i, ci in enumerate(cols):
                    if ci in near_dup:
                        continue
                    for cj in cols[i + 1:]:
                        if cj in near_dup:
                            continue
                        try:
                            r = float(corr.loc[ci, cj])
                        except KeyError:
                            continue
                        if r >= 0.9999:
                            near_dup.append(cj)
                if near_dup:
                    column_diagnostics["__dropped_collinear_cols"] = near_dup
                    cols = [c for c in cols if c not in near_dup]
                    sub = sub[cols]
            if len(cols) < 3:
                raise ValueError(
                    f"PC: after dropping zero-variance / near-duplicate "
                    f"column(s) "
                    f"{column_diagnostics.get('__dropped_zero_variance_cols', [])}"
                    f" + {column_diagnostics.get('__dropped_collinear_cols', [])}"
                    f", only {len(cols)} variable(s) remain (<3); "
                    "cannot run causal discovery.")
        # Diagnostic: if the user picked Fisher-Z but the variables look
        # binary/discrete, the test is mis-specified.  We do not auto-
        # switch the CI test (that would silently change semantics);
        # instead we surface the warning so the planner can re-issue
        # with ci_test='chisq'.
        binary_cols = [c for c in cols
                        if column_diagnostics[c].get("is_binary")]
        discrete_cols = [c for c in cols
                          if column_diagnostics[c].get("is_discrete_low_cardinality")
                          and not column_diagnostics[c].get("is_binary")]

        data = sub.values.astype(float)
        t0 = time.time()
        # causal-learn's pc(...) returns a CausalGraph wrapper; the
        # endpoint matrix is on ``.G.graph``.
        cg = pc(data=data, alpha=self.alpha,
                 indep_test=self.ci_test,
                 stable=self.stable,
                 verbose=False, show_progress=False)
        runtime = time.time() - t0
        adj = np.asarray(cg.G.graph, dtype=int)

        # Save adjacency + edges + summary.
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        adj_df = pd.DataFrame(adj, index=cols, columns=cols)
        adj_path = out_dir / CONTRACT.output_files["adjacency_csv"]
        adj_df.to_csv(adj_path)

        edges_df = _adj_to_edges(adj, cols)
        edges_path = out_dir / CONTRACT.output_files["edges_csv"]
        edges_df.to_csv(edges_path, index=False)

        n_directed = int(((edges_df["orientation"] == "-->")
                          | (edges_df["orientation"] == "<--")).sum())
        n_undir = int((edges_df["orientation"] == "---").sum())
        n_bidir = int((edges_df["orientation"] == "<->").sum())

        summary: Dict[str, Any] = {
            "n_variables":      len(cols),
            "n_obs":            int(n),
            "n_edges":          int(len(edges_df)),
            "n_directed":       n_directed,
            "n_undirected":     n_undir,
            "n_bidirected":     n_bidir,
            "alpha":            self.alpha,
            "ci_test":          self.ci_test,
            "stable":           self.stable,
            "runtime_seconds":  float(runtime),
            "column_diagnostics": column_diagnostics,
        }
        # Surface mis-specified CI-test risk when ci_test='fisherz' (the
        # default) is used on binary/discrete variables.
        if self.ci_test == "fisherz" and (binary_cols or discrete_cols):
            summary["ci_test_warning"] = (
                f"ci_test='fisherz' is mis-specified for "
                f"binary={binary_cols} discrete={discrete_cols}; "
                "re-run with ci_test='chisq' for those columns."
            )
        sm_path = out_dir / CONTRACT.output_files["summary_json"]
        sm_path.write_text(json.dumps(summary, indent=2, default=str),
                            encoding="utf-8")

        return {
            "adjacency_csv": str(adj_path),
            "edges_csv":     str(edges_path),
            "summary_json":  str(sm_path),
            "edges_dict":    edges_df.to_dict(orient="records"),
            **summary,
        }


def get_solver(alpha: float = 0.05, ci_test: str = "fisherz",
               stable: bool = True) -> CausalDiscoveryPCSolver:
    return CausalDiscoveryPCSolver(alpha=alpha, ci_test=ci_test,
                                     stable=stable)


# ---------------------------------------------------------------------------
# Ground-truth selftest
# ---------------------------------------------------------------------------
def _gt_a_chain_skeleton() -> List[str]:
    """GT-A — chain X→Y→Z + isolated noise W.  Skeleton + isolation."""
    import tempfile
    rng = np.random.default_rng(2026)
    n = 3000
    X = rng.normal(0, 1, n)
    Y = 1.2 * X + rng.normal(0, 0.5, n)
    Z = 0.9 * Y + rng.normal(0, 0.5, n)
    W = rng.normal(0, 1, n)
    df = pd.DataFrame({"X": X, "Y": Y, "Z": Z, "W": W})
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver(alpha=0.01).run(
            df, ColumnMapping({"variables": ["X", "Y", "Z", "W"]}), Path(tmp))
        pairs = {tuple(sorted([e["src"], e["dst"]])) for e in out["edges_dict"]}
        if tuple(sorted(["X", "Y"])) not in pairs:
            diffs.append("[A] missing edge X-Y")
        if tuple(sorted(["Y", "Z"])) not in pairs:
            diffs.append("[A] missing edge Y-Z")
        if tuple(sorted(["X", "Z"])) in pairs:
            diffs.append("[A] spurious X-Z direct edge (mediator Y not "
                          "conditioned)")
        for v in ("X", "Y", "Z"):
            if tuple(sorted([v, "W"])) in pairs:
                diffs.append(f"[A] spurious W-{v} edge (W is noise)")
    return diffs


def _gt_b_v_structure_orientation() -> List[str]:
    """GT-B — v-structure (collider) X1 → Y ← X2 with X1 ⊥ X2.

    This is PC's *defining* capability: when two parents are
    unconditionally independent but become dependent conditional on the
    collider Y, PC orients X1→Y and X2→Y unambiguously.

        X1 ~ N(0, 1)      (independent)
        X2 ~ N(0, 1)      (independent)
        Y  = 0.9*X1 + 0.9*X2 + N(0, 0.4)
        Z  = 0.8*Y + N(0, 0.4)     (descendant — extra signal)
        n = 5000, seed = 7

    Strict assertions:
      - skeleton has X1-Y, X2-Y, Y-Z; NO X1-X2 edge
      - both X1→Y and X2→Y are oriented as DIRECTED (not just undirected)
        (Meek rule R1 + collider rule must fire on the v-structure)
    """
    import tempfile
    rng = np.random.default_rng(7)
    n = 5000
    X1 = rng.normal(0, 1, n)
    X2 = rng.normal(0, 1, n)
    Y = 0.9 * X1 + 0.9 * X2 + rng.normal(0, 0.4, n)
    Z = 0.8 * Y + rng.normal(0, 0.4, n)
    df = pd.DataFrame({"X1": X1, "X2": X2, "Y": Y, "Z": Z})
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        out = get_solver(alpha=0.01).run(
            df, ColumnMapping({"variables": ["X1", "X2", "Y", "Z"]}),
            Path(tmp))
        edges = out["edges_dict"]
        pairs = {tuple(sorted([e["src"], e["dst"]])): e for e in edges}
        # skeleton
        for need in (("X1", "Y"), ("X2", "Y"), ("Y", "Z")):
            if tuple(sorted(need)) not in pairs:
                diffs.append(f"[B] missing edge {need}")
        if tuple(sorted(["X1", "X2"])) in pairs:
            diffs.append("[B] spurious X1-X2 edge (they are independent)")
        # orientation: X1→Y and X2→Y must be DIRECTED
        for src, dst in (("X1", "Y"), ("X2", "Y")):
            e = pairs.get(tuple(sorted([src, dst])))
            if e is None:
                continue   # already reported above
            # Check the edge has the right orientation src->dst.
            if e["src"] == src and e["dst"] == dst:
                ori = e["orientation"]
            elif e["src"] == dst and e["dst"] == src:
                # in causal-learn convention orientation is given relative
                # to the (src, dst) pair we stored.
                ori = {"-->": "<--", "<--": "-->",
                        "---": "---", "<->": "<->"}.get(
                    e["orientation"], e["orientation"])
            else:
                ori = "?"
            if ori != "-->":
                diffs.append(f"[B] collider {src}→Y orientation NOT "
                              f"recovered (got {ori}); PC v-structure rule "
                              "must orient incoming edges into Y.")
    return diffs


def _gt_c_robustness() -> List[str]:
    """GT-C — input robustness: dtype coercion, constant col fail-fast,
    NaN handling, missing col KeyError."""
    import tempfile
    rng = np.random.default_rng(2026)
    n = 600
    X = rng.normal(0, 1, n); Y = 1.2 * X + rng.normal(0, 0.5, n)
    Z = 0.9 * Y + rng.normal(0, 0.5, n)
    diffs: List[str] = []

    # (1) string-encoded numeric + Int64
    df = pd.DataFrame({
        "X": [f"{v:.4f}" for v in X],
        "Y": pd.array(np.round(Y * 100).astype(np.int64), dtype="Int64"),
        "Z": Z.astype(float),
    })
    with tempfile.TemporaryDirectory() as tmp:
        try:
            out = get_solver().run(
                df, ColumnMapping({"variables": ["X", "Y", "Z"]}),
                Path(tmp))
            # Skeleton should still be X-Y-Z (scaling Y by 100 doesn't matter).
            pairs = {tuple(sorted([e["src"], e["dst"]]))
                      for e in out["edges_dict"]}
            for need in (("X", "Y"), ("Y", "Z")):
                if tuple(sorted(need)) not in pairs:
                    diffs.append(f"[C-coerce] missing {need} after dtype "
                                  "coercion")
        except Exception as e:
            diffs.append(f"[C-coerce] should accept dtype mix, raised "
                          f"{type(e).__name__}: {e}")

    # (2a) constant column on 3-var input must still raise (only 2 non-
    # constant cols left, <3).
    df2 = pd.DataFrame({"X": X, "Y": Y, "K": np.zeros_like(Y)})
    with tempfile.TemporaryDirectory() as tmp:
        try:
            get_solver().run(df2,
                              ColumnMapping({"variables": ["X", "Y", "K"]}),
                              Path(tmp))
            diffs.append("[C-const] constant column should raise ValueError"
                          " when <3 non-constant cols remain")
        except ValueError:
            pass
        except Exception as e:
            diffs.append(f"[C-const] expected ValueError, got "
                          f"{type(e).__name__}: {e}")
    # (2b) constant column on 4-var input MUST be dropped + run continues
    # on the remaining 3 non-constant cols (the QRData T05/T06/T07 case).
    df2b = pd.DataFrame({"X": X, "Y": Y, "Z": Z, "K": np.zeros_like(Y)})
    with tempfile.TemporaryDirectory() as tmp:
        try:
            out = get_solver().run(
                df2b,
                ColumnMapping({"variables": ["X", "Y", "Z", "K"]}),
                Path(tmp))
        except Exception as e:
            diffs.append(f"[C-const-drop] 4 vars w/ 1 constant should run "
                          f"on the remaining 3, got "
                          f"{type(e).__name__}: {e}")
        else:
            cd = out.get("column_diagnostics", {})
            dropped = cd.get("__dropped_constant_cols", [])
            if dropped != ["K"]:
                diffs.append("[C-const-drop] dropped list missing K, "
                              f"got {dropped!r}")
            if out["n_variables"] != 3:
                diffs.append(f"[C-const-drop] n_variables should be 3, "
                              f"got {out['n_variables']}")
            pairs = {tuple(sorted([e["src"], e["dst"]]))
                      for e in out["edges_dict"]}
            if tuple(sorted(["X", "Y"])) not in pairs:
                diffs.append("[C-const-drop] missing X-Y after constant-col "
                              "drop")

    # (3) missing column
    df3 = pd.DataFrame({"X": X, "Y": Y})
    with tempfile.TemporaryDirectory() as tmp:
        try:
            get_solver().run(df3,
                              ColumnMapping({"variables": ["X", "Y", "Q"]}),
                              Path(tmp))
            diffs.append("[C-missing] missing col should raise KeyError")
        except KeyError:
            pass
        except Exception as e:
            diffs.append(f"[C-missing] expected KeyError, got "
                          f"{type(e).__name__}")
    return diffs


def _gt_d_messy_and_binary() -> List[str]:
    """GT-D — (i) messy strings ($ / % / ,) should parse without crashing;
    (ii) when one column is BINARY and ci_test='fisherz' (default), the
    summary must include a `ci_test_warning` (mis-specification flag)."""
    import tempfile
    rng = np.random.default_rng(2026)
    n = 800
    X = rng.normal(0, 1, n)
    Y = 1.2 * X + rng.normal(0, 0.5, n)
    # Z is *binary* (0/1) — Fisher-Z is mis-specified.
    Z = (rng.uniform(0, 1, n) < 0.4).astype(int)
    df = pd.DataFrame({
        "X": [f"{v:.4f}" for v in X],     # numeric string
        "Y": [f"${v:,.2f}" if v >= 0 else f"-${-v:,.2f}" for v in Y],
        "Z": Z,
    })
    diffs: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            out = get_solver().run(
                df, ColumnMapping({"variables": ["X", "Y", "Z"]}),
                Path(tmp))
        except Exception as e:
            diffs.append(f"[D] should parse messy strings, raised "
                          f"{type(e).__name__}: {e}")
            return diffs
        # Skeleton X-Y should still appear (Y = 1.2 X + noise even after
        # currency parsing).
        pairs = {tuple(sorted([e["src"], e["dst"]]))
                  for e in out["edges_dict"]}
        if tuple(sorted(["X", "Y"])) not in pairs:
            diffs.append("[D] missing X-Y edge after string parsing")
        # Diagnostics: PC must report the binary mis-specification.
        if not out.get("ci_test_warning"):
            diffs.append("[D] ci_test_warning missing for binary Z + "
                          "fisherz default")
        cd = out.get("column_diagnostics", {})
        if not cd.get("Y", {}).get("had_currency"):
            diffs.append("[D] diagnostics missed currency on Y")
        if not cd.get("Z", {}).get("is_binary"):
            diffs.append("[D] diagnostics missed binary on Z")
    return diffs


def selftest() -> Dict[str, Any]:
    """4-scenario ground-truth suite for the PC algorithm.

      GT-A  chain skeleton + isolated noise        (Meek R1 / mediation)
      GT-B  v-structure collider orientation       (PC's defining ability)
      GT-C  input robustness (dtype, constant, missing, NaN)
      GT-D  messy strings + binary column warning (ci_test mis-spec)

    All four must pass for ok=True.
    """
    diffs = (_gt_a_chain_skeleton()
             + _gt_b_v_structure_orientation()
             + _gt_c_robustness()
             + _gt_d_messy_and_binary())
    return {
        "ok": len(diffs) == 0,
        "summary": ("4/4 scenarios pass: chain skeleton, v-structure, "
                    "input robustness, messy strings + binary "
                    "ci_test_warning"
                    if not diffs else f"{len(diffs)} mismatch(es)"),
        "details": {"diffs": diffs, "tested": ["causal_discovery_pc"],
                     "n_scenarios": 4},
    }
