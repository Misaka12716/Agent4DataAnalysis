# -*- coding: utf-8 -*-
"""Blackboard data-plane for the discovery framework.

Two flavours, same machinery (:class:`_BaseBlackboard`):

- :class:`PublicBlackboard` — one per run; holds the requirement summary,
  data profile + cleaning suggestions, and the full hypothesis set.
- :class:`PrivateBlackboard` — one per hypothesis lane (keyed by
  ``hypothesis_id``); holds that lane's verify/refine/review state.

Backend = in-process dict + JSON file persistence (no database), so runs are
resumable and reproducible (V8 §9).

Hard rules enforced here
------------------------
- **Single-writer**: a key is owned by the first producer that writes it;
  a different producer writing the same key raises
  :class:`ProvenanceConflictError`.
- **Provenance**: every ``put`` stamps ``{producer, timestamp, seed,
  version}`` (version is a per-key write counter) for §F checksums.
- **References, not payloads**: :meth:`register_artifact` stores only a
  path + a short summary, never the product itself.
- **compress() keeps numbers verbatim**: prose may be shortened, but
  ``effect`` / ``ci`` / ``p`` / ``n`` (and friends) plus artifact paths
  survive byte-for-byte (V8 §11).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

PathLike = Union[str, Path]

# Keys whose values must NEVER be touched by compress() — the credibility
# numbers (V8 §11) and any path/version fields.  Matching is by dict key
# name; once a key matches, its entire subtree is preserved verbatim.
_PRESERVE_KEYS = frozenset({
    "effect", "effect_size", "effect_type",
    "ci", "confidence_interval",
    "p", "p_value",
    "n", "n_obs", "sample_size",
    "seed", "dataset_hash",
    "operator_versions",
    "artifact_paths", "artifact_path", "path", "paths",
    "statistical_evidence", "reproducibility",
})

# Prose strings longer than this get compressed; shorter ones are kept.
_PROSE_MAX_CHARS = 280


class BlackboardError(RuntimeError):
    """Base class for blackboard misuse."""


class ProvenanceConflictError(BlackboardError):
    """Raised when a producer writes a key owned by a different producer."""


def _jsonify(value: Any) -> Any:
    """Best-effort conversion to a JSON-safe structure.

    Dataclasses (anything exposing ``to_dict``) are converted; dict/list
    are recursed; primitives pass through.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _jsonify(to_dict())
    if isinstance(value, dict):
        return {str(k): _jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonify(v) for v in value]
    # Fallback: stringify unknown objects so persistence never blows up.
    return str(value)


def _summarize_prose(text: str, llm: Any = None) -> str:
    """Compress one prose string.  Uses ``llm`` if supplied & usable,
    otherwise truncates with a marker.  Never raises.
    """
    if llm is not None:
        try:
            available = getattr(llm, "is_available", None)
            if available is None or available():
                chat_json = getattr(llm, "chat_json", None)
                if callable(chat_json):
                    out = chat_json(
                        system=("Compress the prose to <=2 sentences. "
                                "Keep all numbers, names and paths verbatim. "
                                'Reply JSON {"summary": "..."}.'),
                        user=text,
                        max_tokens=200,
                        stage="bb_compress",
                    )
                    summ = (out or {}).get("summary")
                    if isinstance(summ, str) and summ.strip():
                        return summ.strip()
        except Exception:
            pass  # fall through to truncation
    head = text[:_PROSE_MAX_CHARS].rstrip()
    return f"{head}… [compressed {len(text)}→{len(head)} chars]"


def _compress_node(node: Any, llm: Any, preserve: bool) -> Any:
    if isinstance(node, dict):
        return {
            k: _compress_node(v, llm, preserve or k in _PRESERVE_KEYS)
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_compress_node(v, llm, preserve) for v in node]
    if isinstance(node, str) and not preserve and len(node) > _PROSE_MAX_CHARS:
        return _summarize_prose(node, llm)
    return node


class _BaseBlackboard:
    """Shared in-process dict + JSON persistence + provenance + compress."""

    def __init__(self, name: str, path: Optional[PathLike] = None) -> None:
        self.name = name
        self.path: Optional[Path] = Path(path) if path is not None else None
        # key -> {"value": <json-safe>, "provenance": {...}}
        self._entries: Dict[str, Dict[str, Any]] = {}
        # path -> short summary
        self._artifacts: Dict[str, str] = {}

    # ---------------------- core read/write ----------------------
    def put(self, key: str, value: Any, producer: str,
            seed: Optional[int] = None, version: Optional[int] = None) -> None:
        """Write ``key`` with provenance.  Enforces single-writer.

        Raises :class:`ProvenanceConflictError` if ``key`` is already owned
        by a different producer.
        """
        existing = self._entries.get(key)
        if existing is not None:
            owner = existing["provenance"].get("producer")
            if owner != producer:
                raise ProvenanceConflictError(
                    f"key {key!r} is owned by {owner!r}; "
                    f"{producer!r} may not overwrite it (single-writer)."
                )
            next_version = (existing["provenance"].get("version") or 0) + 1
        else:
            next_version = 1
        self._entries[key] = {
            "value": _jsonify(value),
            "provenance": {
                "producer": producer,
                "timestamp": time.time(),
                "seed": seed,
                "version": version if version is not None else next_version,
            },
        }

    def get(self, key: str, default: Any = None) -> Any:
        entry = self._entries.get(key)
        return entry["value"] if entry is not None else default

    def get_provenance(self, key: str) -> Optional[Dict[str, Any]]:
        entry = self._entries.get(key)
        return dict(entry["provenance"]) if entry is not None else None

    def has(self, key: str) -> bool:
        return key in self._entries

    def keys(self) -> List[str]:
        return list(self._entries.keys())

    def owner_of(self, key: str) -> Optional[str]:
        entry = self._entries.get(key)
        return entry["provenance"].get("producer") if entry else None

    # ---------------------- artifacts ----------------------
    def register_artifact(self, path: PathLike, summary: str) -> str:
        """Register a large product by path + short summary (no payload).

        Returns the stringified path.  The summary is truncated to keep the
        blackboard lean.
        """
        sp = str(path)
        short = (summary or "").strip()
        if len(short) > _PROSE_MAX_CHARS:
            short = short[:_PROSE_MAX_CHARS].rstrip() + "…"
        self._artifacts[sp] = short
        return sp

    def artifacts(self) -> Dict[str, str]:
        return dict(self._artifacts)

    # ---------------------- compression ----------------------
    def compress(self, llm: Any = None) -> None:
        """Compress prose in stored values in place.

        Numbers (effect/ci/p/n/seed/…) and artifact paths are preserved
        verbatim; only long free-text strings are shortened.  ``llm`` is an
        optional object exposing ``is_available()`` + ``chat_json(...)``
        (e.g. :mod:`operator_pipeline.llm_client`); when absent or
        unavailable, falls back to deterministic truncation.
        """
        for key, entry in self._entries.items():
            entry["value"] = _compress_node(entry["value"], llm, preserve=False)

    # ---------------------- lifecycle ----------------------
    def clear(self) -> None:
        """Drop all entries + artifacts (public-blackboard lifecycle)."""
        self._entries.clear()
        self._artifacts.clear()

    # ---------------------- serialisation ----------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "entries": json.loads(json.dumps(self._entries)),
            "artifacts": dict(self._artifacts),
        }

    def _load_state(self, d: Dict[str, Any]) -> None:
        self.name = d.get("name", self.name)
        self._entries = dict(d.get("entries") or {})
        self._artifacts = dict(d.get("artifacts") or {})

    def save_json(self, path: Optional[PathLike] = None) -> Path:
        target = Path(path) if path is not None else self.path
        if target is None:
            raise BlackboardError(
                "save_json needs a path (none given and self.path is None)")
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2)
        self.path = target
        return target

    def load_json(self, path: Optional[PathLike] = None) -> "_BaseBlackboard":
        source = Path(path) if path is not None else self.path
        if source is None:
            raise BlackboardError(
                "load_json needs a path (none given and self.path is None)")
        with open(source, "r", encoding="utf-8") as fh:
            self._load_state(json.load(fh))
        self.path = source
        return self


class PublicBlackboard(_BaseBlackboard):
    """The single shared blackboard for a run."""

    def __init__(self, path: Optional[PathLike] = None,
                 name: str = "public") -> None:
        super().__init__(name=name, path=path)


class PrivateBlackboard(_BaseBlackboard):
    """A per-hypothesis lane blackboard, keyed by ``hypothesis_id``."""

    def __init__(self, hypothesis_id: str,
                 path: Optional[PathLike] = None) -> None:
        super().__init__(name=f"lane:{hypothesis_id}", path=path)
        self.hypothesis_id = hypothesis_id

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["hypothesis_id"] = self.hypothesis_id
        return d

    def _load_state(self, d: Dict[str, Any]) -> None:
        super()._load_state(d)
        self.hypothesis_id = d.get("hypothesis_id", self.hypothesis_id)


__all__ = [
    "PublicBlackboard",
    "PrivateBlackboard",
    "BlackboardError",
    "ProvenanceConflictError",
]
