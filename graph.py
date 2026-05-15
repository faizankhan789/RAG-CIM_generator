"""
CIM LangGraph pipeline definition.

Topology:
                       ┌─── pdf_node ────────┐
                       │                     │
  ingest_node ─────────┼─── spreadsheet_node ┼─── aggregator_node ─── formatter_node ─── END
                       │                     │
                       ├─── image_node ───────┤
                       │                     │
                       └─── ppt_node ─────────┘

All four processor nodes run in parallel (fan-out).
aggregator_node is the fan-in point — LangGraph waits for all four to finish.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from nodes.aggregator import aggregator_node
from nodes.formatter import formatter_node
from nodes.image import image_node
from nodes.ingest import ingest_node
from nodes.pdf import pdf_node
from nodes.ppt import ppt_node
from nodes.spreadsheet import spreadsheet_node
from state import CIMState

_PROCESSOR_NODES = ["pdf_node", "spreadsheet_node", "image_node", "ppt_node"]


def build_graph() -> StateGraph:
    builder = StateGraph(CIMState)

    # Register all nodes
    builder.add_node("ingest", ingest_node)
    builder.add_node("pdf_node", pdf_node)
    builder.add_node("spreadsheet_node", spreadsheet_node)
    builder.add_node("image_node", image_node)
    builder.add_node("ppt_node", ppt_node)
    builder.add_node("aggregator", aggregator_node)
    builder.add_node("formatter", formatter_node)

    # START → ingest
    builder.add_edge(START, "ingest")

    # ingest → all four processors in parallel (fan-out)
    for node_name in _PROCESSOR_NODES:
        builder.add_edge("ingest", node_name)

    # All four processors → aggregator (fan-in — LangGraph waits for all)
    for node_name in _PROCESSOR_NODES:
        builder.add_edge(node_name, "aggregator")

    # aggregator → formatter → END
    builder.add_edge("aggregator", "formatter")
    builder.add_edge("formatter", END)

    return builder.compile()


# Singleton compiled graph — import and use directly
cim_graph = build_graph()
