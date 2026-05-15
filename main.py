"""
CIM Generator — CLI entry point.

Usage:
    python main.py --input files.json [--output result.json] [--pretty]

    Or pipe JSON directly:
        echo '{"url": "https://..."}' | python main.py

Input format (nested file tree):
    {
      "data_room": {
        "financials": [
          {"url": "https://example.com/financials.xlsx", "label": "P&L 2023"},
          {"url": "https://example.com/balance.xlsx"}
        ],
        "deck": {"url": "https://example.com/pitch.pptx", "label": "Pitch Deck"},
        "contracts": "https://example.com/msa.pdf"
      }
    }

Environment variables (see .env.example):
    CIM_LLM            — "openai" or "anthropic" (omit for heuristic mode)
    FILE_AUTH_HEADER   — Authorization header value for protected file URLs
    MAX_FILE_BYTES     — Max download size per file (default 2GB)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("cim_main")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate a structured CIM JSON from a nested file tree of document URLs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--input", "-i",
        type=Path,
        help="Path to JSON file containing the nested file tree. "
             "If omitted, reads from stdin.",
    )
    p.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Write JSON output to this file (default: stdout).",
    )
    p.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON output.",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p.parse_args()


async def run(file_tree: dict) -> dict:
    from graph import cim_graph

    initial_state = {
        "file_tree": file_tree,
        "listing_xml": "",
        "listing_name": "",
        "asking_price": "",
        "pdf_files": [],
        "spreadsheet_files": [],
        "image_files": [],
        "ppt_files": [],
        "extracted": [],
        "errors": [],
        "all_findings": [],
        "all_images": [],
        "cim_output": "",
    }

    log.info("Starting CIM pipeline...")
    final_state = await cim_graph.ainvoke(initial_state)
    log.info("Pipeline complete.")

    html = final_state.get("cim_output", "")
    if not html:
        return {"error": "Pipeline produced no output"}

    return {"html": html}


def main() -> None:
    args = _parse_args()
    logging.getLogger().setLevel(args.log_level)

    # Load input
    if args.input:
        with open(args.input) as f:
            file_tree = json.load(f)
    elif not sys.stdin.isatty():
        file_tree = json.load(sys.stdin)
    else:
        log.error("No input provided. Use --input or pipe JSON via stdin.")
        sys.exit(1)

    result = asyncio.run(run(file_tree))

    indent = 2 if args.pretty else None
    output_json = json.dumps(result, indent=indent, default=str)

    if args.output:
        args.output.write_text(output_json)
        log.info("Output written to %s", args.output)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
