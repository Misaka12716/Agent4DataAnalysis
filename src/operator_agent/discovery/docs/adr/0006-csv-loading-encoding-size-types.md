# CSV loading: encoding sniffing, size cap, type dispatch, NA handling

**Status**: accepted (2026-06-07)

The previous CSV loading path was effectively `pd.read_csv(path)` with
default arguments.  This produced four separate user-visible failures:
non-UTF-8 files crashed with cryptic decode errors, multi-GB uploads
exhausted memory, non-CSV uploads (TSV / Excel / GDS soft) crashed in
the parser, and pandas' default dtype inference silently dropped values
on mixed-type columns.  All of these surfaced to the UI as
`supervisor_uncaught:` traces, indistinguishable from real crashes.

This ADR fixes the loader.

## 1. Encoding: chardet sniff with explicit fallback chain

- Read the first 64 KB of the file, run `charset-normalizer`
  (preferred over `chardet` — pure-Python, MIT, actively maintained,
  better CJK detection), pick the highest-confidence encoding.
- If charset-normalizer's confidence < 0.5 OR the picked encoding fails
  to decode the full file, walk a fixed fallback chain in order:
  `["utf-8-sig", "utf-8", "gbk", "latin-1"]`.
- `latin-1` is the absolute last resort because it never raises but
  may produce garbage for non-Latin-1 bytes — we want it to be the
  *last* path, not the *first* path.
- The encoding actually used is recorded in the public blackboard
  `data_profile.encoding` so the user (and downstream stages) see
  whether sniffing succeeded or fell back.

Alternatives considered:
- **Pure fallback chain (no chardet)**: `latin-1` first-success masks
  real encoding bugs.  Rejected.
- **Demand explicit user-declared encoding**: friction for the 99%
  case.  We expose an override but don't require it.
- **UTF-8 only**: rejected on user feedback (mixed-encoding sources
  are normal in this user base).

## 2. Size cap: 500 MB hard, no chunking

- Files > 500 MB are rejected at the loader with a `DataLoadError`
  describing the cap.
- No soft-cap-with-chunking path: chunked reads would force every
  downstream stage (N2 dry-run, dataset_hash, verify operators) to
  switch to a streaming model, dramatically increasing complexity for
  a tiny user-fraction.  Users with > 500 MB files are doing data
  preprocessing, not hypothesis discovery — they belong upstream of
  this framework.
- Cap is configurable via `discovery/config.py`
  (`MAX_CSV_BYTES = 500 * 1024 * 1024`) so ops can tune per
  deployment.

## 3. File type: extension-based dispatch

- Dispatch table:
  - `.csv` → `pd.read_csv(sep=",")`
  - `.tsv`, `.txt` → `pd.read_csv(sep="\t")`
  - `.xlsx`, `.xls` → `pd.read_excel`
  - `.soft`, `.soft.gz` → `gds_soft_parser`
  - anything else → `DataLoadError("unsupported file type: {ext}")`
- Magic-byte sniffing was rejected: extension is correct on > 99% of
  uploads, and incorrect-extension cases are typically malicious or
  user-error that should fail loudly anyway.
- GDS soft files are routed through the existing
  `bio.gds_soft_parser` solver in *parser-only* mode (no operator
  pipeline), so the loader does not need its own GEO/GDS knowledge.

## 4. NA / dtype: read_csv with explicit na_values

- `pd.read_csv(..., na_values=NA_TOKENS, keep_default_na=True,
  low_memory=False)` where:
  ```
  NA_TOKENS = ["", "NA", "N/A", "n/a", "null", "NULL", "None",
               "NaN", "nan", "Inf", "-Inf", "inf", "-inf", "."]
  ```
- `low_memory=False` is critical to avoid pandas' chunk-based dtype
  inference, which is the root of "object instead of float64" mixed
  columns.
- We do **NOT** post-load coerce all object columns with
  `to_numeric(errors="coerce")`.  The coerce-rescue pattern is
  appealing but it silently drops genuine string values that happen to
  not parse as numbers (e.g. an open-ended response column), which
  produces bias in N3 hypothesis sampling.  If a column is mixed-type
  due to upstream sloppiness, surface it in the data profile (N1) and
  let the user / refine_stage decide rather than coercing.

## 5. Error class: `DataLoadError` is user-facing, not a system fault

- New exception `discovery.errors.DataLoadError` raised by the loader
  on any of: encoding fallback exhausted, size cap exceeded,
  unsupported extension, parser exception during read.
- The webapp catches `DataLoadError` *before* TopAgent is started and
  surfaces a user-friendly UI message identifying which check failed
  and (when relevant) suggesting a remedy ("file is GBK-encoded but
  not declared; try saving as UTF-8").
- `DataLoadError` is **not** wrapped into `supervisor_uncaught:` —
  the run never starts, so supervisor never sees it.  This separates
  "we couldn't even read your file" from "the agent crashed mid-run".

This is the same architectural principle that fixes H3 in ADR-0007:
exceptions whose root cause is **user input shape** belong to a
different error category from exceptions whose root cause is **agent /
system behavior**.

## 6. Load point: stays in webapp (NOT moved into TopAgent.pre-flight)

The natural-feeling alternative is to push CSV loading into
`TopAgent.start`'s pre-flight, so encoding failures become
clarification questions ("we can't read this; what encoding is it?").
We rejected this:

- TopAgent's value is **agent-vs-user dialogue inside a discovery
  run**.  CSV loading is plumbing that happens before the agent even
  starts thinking; coupling it to TopAgent expands TopAgent's surface
  for no reciprocal benefit.
- The webapp already has the right context (UI, file picker, error
  toast) to handle file-shape errors well; TopAgent sessions, when
  invoked from a CLI or SDK caller, would not.
- Keeping the loader as a standalone `data_io` utility means CLI / SDK
  callers can use the same hardened loader without booting TopAgent.

## 7. Implementation surface

- New module `discovery/data_io.py` exporting:
  - `load_dataset(path: str|Path) -> Tuple[pd.DataFrame, DataLoadInfo]`
  - `DataLoadError` exception
  - `DataLoadInfo` namedtuple (encoding, file_type, size_bytes,
    nrows, ncols, na_token_hits)
- `webapp.py` replaces its `pd.read_csv(...)` call with
  `data_io.load_dataset(...)` and catches `DataLoadError` to render a
  friendly error UI before any TopAgent / Supervisor code runs.
- New constants in `discovery/config.py`: `MAX_CSV_BYTES`,
  `NA_TOKENS`, `ENCODING_FALLBACK_CHAIN`.
