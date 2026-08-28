"""FastAPI HTTP server — exposes the CIM pipeline for CRM integration."""

from __future__ import annotations

import logging
import sys

import os

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
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
import uvicorn
from pydantic import BaseModel

from core.listing_context import build_listing_xml
from core import db_log
from core.llm import MODEL, reset_token_counters, get_token_counts
from core.templates import TEMPLATES
from graph import cim_graph

_PREVIEWS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "previews")

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
    listing_id: int = 0      # CRM listing ID — used to key the job
    username: str = ""       # CRM username who triggered the request
    crm_url: str = ""        # CRM instance URL (for multi-tenant tracking)
    template_id: str = "classic"  # Selected design template (see core/templates.py)


# ---------------------------------------------------------------------------
# Job tracking — in-memory, keyed by listing_id
# ---------------------------------------------------------------------------

@dataclass
class CIMJob:
    """Represents one CIM generation job. Multiple SSE connections can subscribe."""
    job_id: str
    listing_id: int
    status: str                          # "running" | "complete" | "error"
    events_buffer: list = field(default_factory=list)
    result_html: str = ""
    result_doc_id: Optional[int] = None
    completed_at: Optional[float] = None  # monotonic time when job finished
    _waiters: list = field(default_factory=list)  # asyncio.Queue per live connection

    def add_event(self, event: dict) -> None:
        """Append event to buffer and push to all active subscriber queues."""
        self.events_buffer.append(event)
        for q in list(self._waiters):   # copy avoids mutation-during-iteration issues
            q.put_nowait(event)

    def subscribe(self) -> asyncio.Queue:
        """Return a queue pre-loaded with all past events (replay) + future events."""
        q: asyncio.Queue = asyncio.Queue()
        for e in self.events_buffer:    # replay everything emitted so far
            q.put_nowait(e)
        self._waiters.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._waiters.remove(q)
        except ValueError:
            pass


# Global job store: listing_id → CIMJob
_jobs: dict[int, CIMJob] = {}


async def _expire_job(listing_id: int, delay: int = 3600) -> None:
    """Remove job from store after delay seconds (default 1 hour)."""
    await asyncio.sleep(delay)
    _jobs.pop(listing_id, None)
    log.debug("Job expired — listing_id=%s", listing_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _do_callback(
    callback_url: str, html: str, listing_name: str, listing_id: int, doc_id: int = 0
) -> int | None:
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
    log.debug("═══ CIM REQUEST ═══════════════════════════════")
    log.debug("  Listing name  : %s", listing_name or "(none)")
    log.debug("  Asking price  : %s", asking_price or "(none)")
    log.debug("  Logo URL      : %s", req.logo or "(none)")

    if req.listing_data:
        skip = {"Name", "name", "Asking Price", "c_listing_askingprice_c"}
        fields = {k: v for k, v in req.listing_data.items() if k not in skip and v not in (None, "", [])}
        log.debug("  Listing data fields (%d):", len(req.listing_data))
        for k, v in fields.items():
            log.debug("    %-30s: %s", k, str(v)[:120])

    log.debug("  Listing files (%d):", len(req.listing_files))
    for f in req.listing_files:
        log.debug("    [%s] %s", f.mimeType or "?", f.name)

    log.debug("  Gallery images (%d):", len(req.gallery_images))
    for url in req.gallery_images:
        log.debug("    %s", url.split("?")[0])
    log.debug("═══════════════════════════════════════════════")


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
        "template_id": req.template_id or "classic",
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


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


# ---------------------------------------------------------------------------
# Background pipeline runner — independent of any SSE connection
# ---------------------------------------------------------------------------

async def _run_job_pipeline(req: CIMRequest, job: CIMJob) -> None:
    """
    Runs the CIM pipeline as a background task.
    Emits events to the job's buffer/waiters regardless of SSE connections.
    Survives browser tab closure because it's an independent asyncio Task.
    """
    listing_name = req.listing_data.get("Name") or req.listing_data.get("name", "Listing")
    t_pipeline_start = time.monotonic()
    log.info("━━━ PIPELINE START  listing_id=%s  name=%r ━━━", job.listing_id, listing_name)

    reset_token_counters()
    # Derive crm_url from callback_url if not explicitly provided
    crm_url = req.crm_url
    if not crm_url and req.callback_url:
        parsed = urlparse(req.callback_url)
        crm_url = f"{parsed.scheme}://{parsed.netloc}"
    db_row_id = await db_log.log_start(
        username=req.username or "unknown",
        crm_url=crm_url or "unknown",
        listing_id=str(req.listing_id),
        listing_name=listing_name,
    )

    job.add_event({
        "step": 1, "label": "Downloading",
        "message": "Retrieving and cataloguing source documents...",
        "status": "in_progress",
    })

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
        job.add_event({"step": step, "label": label, "message": msg, "status": "in_progress"})

    try:
        result = await pipeline_task
        html = result.get("cim_output", "")
        errors = [e.model_dump() for e in result.get("errors", [])]
        doc_id = None
        if req.callback_url:
            try:
                doc_id = await _do_callback(req.callback_url, html, listing_name, req.listing_id)
            except Exception as cb_exc:
                log.error("CIM callback raised: %s", cb_exc)
        total_elapsed = time.monotonic() - t_pipeline_start
        job.result_html = html
        job.result_doc_id = doc_id
        job.status = "complete"
        job.completed_at = time.monotonic()
        job.add_event({
            "step": 5, "label": "Complete",
            "message": "CIM successfully generated.",
            "status": "complete",
            "html": html,
            "doc_id": doc_id,
            "errors": errors,
            "timing": {"total_seconds": round(total_elapsed, 2)},
        })
        in_tok, out_tok = get_token_counts()
        if db_row_id:
            await db_log.log_complete(db_row_id, MODEL, in_tok, out_tok)
        log.info(
            "━━━ PIPELINE COMPLETE  listing_id=%s  doc_id=%s  total=%.2fs  tokens=%d ━━━",
            job.listing_id, doc_id, total_elapsed, in_tok + out_tok,
        )
    except Exception as exc:
        total_elapsed = time.monotonic() - t_pipeline_start
        log.error("━━━ PIPELINE ERROR  listing_id=%s  elapsed=%.2fs  %s ━━━", job.listing_id, total_elapsed, exc)
        job.status = "error"
        job.add_event({"step": 0, "label": "Error", "message": str(exc), "status": "error"})
        if db_row_id:
            await db_log.log_error(db_row_id)
    finally:
        # Keep job in memory for 1 hour so reconnects can get the result
        asyncio.create_task(_expire_job(job.listing_id, delay=3600))


# ---------------------------------------------------------------------------
# SSE fan-out generator — used by both initial and reconnecting connections
# ---------------------------------------------------------------------------

async def _fan_out_stream(job: CIMJob):
    """
    SSE generator: replays all past events then streams future ones.
    Works for both the initial connection and any reconnecting browser.
    Unsubscribes cleanly when the connection closes.
    """
    q = job.subscribe()
    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=120)
                yield _sse(event)
                if event.get("status") in ("complete", "error"):
                    break
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"   # keep the connection alive
    finally:
        job.unsubscribe(q)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/job/status/{listing_id}")
async def job_status(listing_id: int):
    """
    Check whether a CIM generation job exists for this listing.
    Called by the browser before starting a new generation — if a job is
    already running or complete, the browser will reconnect instead of
    starting from scratch.
    """
    job = _jobs.get(listing_id)
    if not job:
        return JSONResponse({"exists": False})
    age_seconds = (time.monotonic() - job.completed_at) if job.completed_at is not None else None
    return JSONResponse({
        "exists": True,
        "job_id": job.job_id,
        "status": job.status,          # "running" | "complete" | "error"
        "steps_done": len(job.events_buffer),
        "doc_id": job.result_doc_id,
        "age_seconds": age_seconds,    # seconds since completion, null if still running
    })


@app.post("/generate-cim/stream")
async def generate_cim_stream(req: CIMRequest):
    """
    Streams SSE progress events while running the CIM pipeline.

    - If a job already exists for req.listing_id: reconnects (fan-out) to the
      existing pipeline without restarting it.  All past events are replayed
      immediately so the browser catches up.
    - Otherwise: starts a new background pipeline task and streams its events.
    """
    listing_id = req.listing_id

    if listing_id and listing_id in _jobs:
        job = _jobs[listing_id]
        log.info("Reconnecting to existing job — listing_id=%s status=%s", listing_id, job.status)
        return StreamingResponse(
            _fan_out_stream(job),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # New job
    job_id = str(uuid.uuid4())
    job = CIMJob(job_id=job_id, listing_id=listing_id, status="running")
    if listing_id:
        _jobs[listing_id] = job
        log.info("New CIM job — listing_id=%s job_id=%s", listing_id, job_id)

    # Pipeline runs independently — survives browser disconnect
    asyncio.create_task(_run_job_pipeline(req, job))

    return StreamingResponse(
        _fan_out_stream(job),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _run_pipeline(req: CIMRequest) -> dict:
    listing_name = req.listing_data.get("Name") or req.listing_data.get("name", "")
    log.debug("CIM pipeline started — listing=%r", listing_name)
    try:
        result = await cim_graph.ainvoke(_build_initial_state(req))
        log.debug("CIM pipeline complete — listing=%r", listing_name)
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


@app.get("/template-preview/{template_id}")
async def template_preview(template_id: str):
    """Serve a pre-generated demo CIM HTML for a design template (see scripts/generate_template_previews.py)."""
    if template_id not in TEMPLATES:
        raise HTTPException(status_code=404, detail="Unknown template_id")
    path = os.path.join(_PREVIEWS_DIR, f"{template_id}.html")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Preview not generated yet")
    with open(path, "r") as f:
        html = f.read()
    return HTMLResponse(content=html)


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8002))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
