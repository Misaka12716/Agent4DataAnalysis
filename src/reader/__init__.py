"""Reader package — lazy exports so `reader.file_types` does not require langgraph."""

__all__ = [
    "run_workspace_reader",
    "run_workspace_reader_sync",
    "run_workspace_reader_with_markdown_sync",
    "workspace_digest_to_markdown",
    "excel_schema_from_digest",
    "read_workspace_excel_schema_and_sample",
]


def __getattr__(name: str):
    if name in (
        "run_workspace_reader",
        "run_workspace_reader_sync",
        "run_workspace_reader_with_markdown_sync",
        "workspace_digest_to_markdown",
    ):
        from reader.agent import (
            run_workspace_reader,
            run_workspace_reader_sync,
            run_workspace_reader_with_markdown_sync,
            workspace_digest_to_markdown,
        )
        return {
            "run_workspace_reader": run_workspace_reader,
            "run_workspace_reader_sync": run_workspace_reader_sync,
            "run_workspace_reader_with_markdown_sync": run_workspace_reader_with_markdown_sync,
            "workspace_digest_to_markdown": workspace_digest_to_markdown,
        }[name]
    if name in ("excel_schema_from_digest", "read_workspace_excel_schema_and_sample"):
        from reader.legacy import excel_schema_from_digest, read_workspace_excel_schema_and_sample
        return {
            "excel_schema_from_digest": excel_schema_from_digest,
            "read_workspace_excel_schema_and_sample": read_workspace_excel_schema_and_sample,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
