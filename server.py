"""FastAPI HTTP server — exposes the CIM pipeline for CRM integration."""

from __future__ import annotations

import logging
import sys

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
# Silence noisy third-party loggers
for _lib in ("anthropic", "httpx", "httpcore", "langgraph", "uvicorn.access"):
    logging.getLogger(_lib).setLevel(logging.WARNING)
log = logging.getLogger("cim_server")

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
import uvicorn
from pydantic import BaseModel
from typing import Any

from core.listing_context import build_listing_xml
from graph import cim_graph

app = FastAPI(title="CIM Generator API")


class CIMRequest(BaseModel):
    file_tree: Any
    listing_data: dict[str, Any] = {}
    listing_name: str = ""
    asking_price: str = ""


async def _run_pipeline(req: CIMRequest) -> dict:
    listing_xml = build_listing_xml(req.listing_data) if req.listing_data else ""
    initial_state = {
        "file_tree": req.file_tree,
        "listing_xml": listing_xml,
        "listing_name": req.listing_name,
        "asking_price": req.asking_price,
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
    log.info("CIM pipeline started — listing=%r", req.listing_name)
    try:
        result = await cim_graph.ainvoke(initial_state)
        log.info("CIM pipeline complete — listing=%r", req.listing_name)
        return result
    except Exception as exc:
        log.error("CIM pipeline failed — listing=%r: %s", req.listing_name, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/generate-cim/html")
async def generate_cim_html_endpoint(req: CIMRequest):
    """Returns JSON {"success": true, "html": "..."} so PHP can proxy without re-encoding."""
    final_state = await _run_pipeline(req)
    html = final_state.get("cim_output", "")
    if not html:
        raise HTTPException(status_code=500, detail="Pipeline produced no HTML output")
    errors = [e.model_dump() for e in final_state.get("errors", [])]
    return JSONResponse(content={"success": True, "html": html, "errors": errors})


@app.post("/generate-cim")
async def generate_cim_json(req: CIMRequest):
    """Returns the raw HTML as JSON for backward compatibility."""
    final_state = await _run_pipeline(req)
    html = final_state.get("cim_output", "")
    errors = [e.model_dump() for e in final_state.get("errors", [])]
    return JSONResponse(content={"html": html, "errors": errors})


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
