"""FastAPI HTTP server — exposes the CIM pipeline for CRM integration."""

from __future__ import annotations

import logging
import sys

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("cim.log", encoding="utf-8"),
    ],
)
# Silence noisy third-party loggers
for _lib in ("anthropic", "httpx", "httpcore", "langgraph", "uvicorn.access"):
    logging.getLogger(_lib).setLevel(logging.WARNING)
log = logging.getLogger("cim_server")

import asyncio
import json

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, StreamingResponse
import uvicorn
from pydantic import BaseModel
from typing import Any

from core.listing_context import build_listing_xml
from graph import cim_graph

app = FastAPI(title="CIM Generator API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ListingFile(BaseModel):
    name: str
    url: str
    mimeType: str = ""


class CIMRequest(BaseModel):
    listing_data: dict[str, Any] = {}
    logo: str = ""
    gallery_images: list[str] = []
    listing_files: list[ListingFile] = []
    callback_url: str = ""   # PHP endpoint to POST finished HTML to
    listing_id: int = 0      # CRM listing ID for the callback relationship


async def _do_callback(callback_url: str, html: str, listing_name: str, listing_id: int, doc_id: int = 0) -> int | None:
    """POST finished CIM HTML to PHP so it's saved even if the browser tab was closed."""
    if not callback_url or not html:
        return None
    import httpx
    payload = {"html": html, "name": listing_name, "listing_id": listing_id}
    if doc_id:
        payload["doc_id"] = doc_id
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(callback_url, json=payload, timeout=30)
            data = resp.json()
            if data.get("success"):
                log.info("CIM callback saved — doc_id=%s", data.get("doc_id"))
                return data.get("doc_id")
            else:
                log.error("CIM callback error: %s", data.get("error"))
    except Exception as exc:
        log.error("CIM callback failed: %s", exc)
    return None


def _build_file_tree(req: CIMRequest) -> list[dict]:
    tree = []
    for f in req.listing_files:
        tree.append({"type": "file", "name": f.name, "url": f.url, "content": "", "mime_type": f.mimeType})
    for url in req.gallery_images:
        name = url.split("/")[-1].split("?")[0] or "gallery_image.jpg"
        tree.append({"type": "file", "name": name, "url": url, "content": "", "mime_type": "image/jpeg"})
    return tree


def _log_request_details(req: CIMRequest, listing_name: str, asking_price: str) -> None:
    log.info("═══ CIM REQUEST ═══════════════════════════════")
    log.info("  Listing name  : %s", listing_name or "(none)")
    log.info("  Asking price  : %s", asking_price or "(none)")
    log.info("  Logo URL      : %s", req.logo or "(none)")

    if req.listing_data:
        skip = {"Name", "name", "Asking Price", "c_listing_askingprice_c"}
        fields = {k: v for k, v in req.listing_data.items() if k not in skip and v not in (None, "", [])}
        log.info("  Listing data fields (%d):", len(req.listing_data))
        for k, v in fields.items():
            log.info("    %-30s: %s", k, str(v)[:120])

    log.info("  Listing files (%d):", len(req.listing_files))
    for f in req.listing_files:
        log.info("    [%s] %s", f.mimeType or "?", f.name)

    log.info("  Gallery images (%d):", len(req.gallery_images))
    for url in req.gallery_images:
        log.info("    %s", url.split("?")[0])
    log.info("═══════════════════════════════════════════════")


def _build_initial_state(req: CIMRequest) -> dict:
    listing_xml = build_listing_xml(req.listing_data) if req.listing_data else ""
    listing_name = req.listing_data.get("Name") or req.listing_data.get("name", "")
    asking_price = str(req.listing_data.get("Asking Price") or req.listing_data.get("c_listing_askingprice_c", ""))
    _log_request_details(req, listing_name, asking_price)
    return {
        "file_tree": _build_file_tree(req),
        "listing_xml": listing_xml,
        "listing_name": listing_name,
        "asking_price": asking_price,
        "logo_url": req.logo or "",
        "pdf_files": [],
        "spreadsheet_files": [],
        "image_files": [],
        "ppt_files": [],
        "word_files": [],
        "extracted": [],
        "errors": [],
        "all_findings": [],
        "all_images": [],
        "cim_output": "",
    }



_STEPS = [
    (1, "Downloading",  "Retrieving and cataloguing all source documents from the provided file tree."),
    (2, "Processing",   "Extracting and analysing content across all uploaded documents in parallel."),
    (3, "Generating",   "Synthesising key findings and structuring the investment narrative."),
    (4, "Rendering",    "Composing and formatting the Confidential Information Memorandum."),
    (5, "Returning",    "Packaging and delivering the completed memorandum."),
]


@app.post("/generate-cim/stream")
async def generate_cim_stream(req: CIMRequest):
    """Streams SSE progress events while running the real CIM pipeline."""

    async def event_stream():
        def sse(payload: dict) -> str:
            return f"data: {json.dumps(payload)}\n\n"

        yield sse({"step": 1, "label": "Downloading", "message": "Retrieving and cataloguing source documents...", "status": "in_progress"})

        pipeline_task = asyncio.create_task(cim_graph.ainvoke(_build_initial_state(req)))

        progress_steps = [
            (2, "Processing",  "Extracting and analysing content across all documents..."),
            (3, "Generating",  "Synthesising findings and structuring the investment narrative..."),
            (4, "Rendering",   "Composing the Confidential Information Memorandum..."),
        ]
        for step, label, msg in progress_steps:
            await asyncio.sleep(50)
            if pipeline_task.done():
                break
            yield sse({"step": step, "label": label, "message": msg, "status": "in_progress"})

        try:
            result = await pipeline_task
            html = result.get("cim_output", "")
            errors = [e.model_dump() for e in result.get("errors", [])]
            listing_name = req.listing_data.get("Name") or req.listing_data.get("name", "Listing")
            doc_id = None
            if req.callback_url:
                try:
                    doc_id = await _do_callback(req.callback_url, html, listing_name, req.listing_id)
                except Exception as cb_exc:
                    log.error("CIM callback raised in stream: %s", cb_exc)
            yield sse({"step": 5, "label": "Complete", "message": "CIM successfully generated.", "status": "complete", "html": html, "doc_id": doc_id, "errors": errors})
        except Exception as exc:
            log.error("Stream pipeline failed: %s", exc)
            yield sse({"step": 0, "label": "Error", "message": str(exc), "status": "error"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _run_pipeline(req: CIMRequest) -> dict:
    listing_name = req.listing_data.get("Name") or req.listing_data.get("name", "")
    log.info("CIM pipeline started — listing=%r", listing_name)
    try:
        result = await cim_graph.ainvoke(_build_initial_state(req))
        log.info("CIM pipeline complete — listing=%r", listing_name)
        return result
    except Exception as exc:
        log.error("CIM pipeline failed — listing=%r: %s", listing_name, exc)
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
    import os
    port = int(os.environ.get("PORT", 8002))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
