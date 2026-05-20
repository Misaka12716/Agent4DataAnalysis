"""Bioinformatics-native operators (SOFT parsing, DEG, PCA, hclust, enrichment).

These complement the generic 32 operators with bio-specific shapes
(probe matrices, gene aggregation, moderated t, hypergeometric tests).
All follow the standard ``SolverContract`` / ``Role`` pattern so they
plug into the agent + demo registry transparently.
"""
