"""Software 1 operator framework — handoff package.

This is a slimmed-down version of the in-repo distillation/ package.
Legacy routing / bm25 / macros submodules are NOT shipped in the
handoff; this __init__ deliberately exports nothing so importing
``distillation`` is side-effect-free.
"""
