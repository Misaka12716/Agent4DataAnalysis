import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from distillation.software1_solver.solvers.bio import (
    soft_parser, probe_to_gene, limma_deg, pca_decomposition,
    hierarchical_cluster, pathway_enrichment,
)

mods = [soft_parser, probe_to_gene, limma_deg, pca_decomposition,
        hierarchical_cluster, pathway_enrichment]
n_ok = 0
for m in mods:
    r = m.selftest()
    short = m.__name__.split(".")[-1]
    status = "OK  " if r["ok"] else "FAIL"
    print(f"{short:30s} {status}  {r['summary']}")
    n_ok += int(r["ok"])
print(f"\n{n_ok}/{len(mods)} bio selftests passed")
