from langgraph.graph import END, StateGraph

from reader.nodes import (
    merge_digest_node,
    process_files_node,
    scan_workspace_node,
    synthesize_markdown_node,
)
from reader.state import ReaderState

_compiled_reader_graph = None


def build_reader_graph():
    g = StateGraph(ReaderState)
    g.add_node("scan_workspace", scan_workspace_node)
    g.add_node("process_files", process_files_node)
    g.add_node("merge_digest", merge_digest_node)
    g.add_node("synthesize_markdown", synthesize_markdown_node)
    g.set_entry_point("scan_workspace")
    g.add_edge("scan_workspace", "process_files")
    g.add_edge("process_files", "merge_digest")
    g.add_edge("merge_digest", "synthesize_markdown")
    g.add_edge("synthesize_markdown", END)
    return g.compile()


def get_reader_graph():
    global _compiled_reader_graph
    if _compiled_reader_graph is None:
        _compiled_reader_graph = build_reader_graph()
    return _compiled_reader_graph
