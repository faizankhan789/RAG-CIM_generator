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
import base64
import hashlib
import json
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

import pymupdf
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse, Response
import uvicorn
from pydantic import BaseModel

from core.listing_context import build_listing_xml
from core import db_log
from core import template_store
from core.llm import MODEL, audit_template_design, reset_token_counters, get_token_counts
from core.template_extractor import (
    SUPPORTED_EXTENSIONS as _SUPPORTED_TEMPLATE_EXTENSIONS,
    NoExtractableTextError,
    UnsupportedTemplateFileError,
    extract_style_profile,
)
from core.office_convert import LegacyConversionError, convert_legacy, convert_to_pdf
from core.lorem_preview import lorem_html, lorem_pdf, lorem_text
from core.templates import TEMPLATES
from graph import cim_graph


def _derive_crm_url(explicit_crm_url: str, callback_url: str) -> str:
    """Prefer an explicit crm_url; otherwise derive scheme://host from the
    callback URL — same fallback _run_job_pipeline has always used for
    scoping cim_generation_log rows, reused here for custom_templates rows."""
    if explicit_crm_url:
        return explicit_crm_url
    if callback_url:
        parsed = urlparse(callback_url)
        return f"{parsed.scheme}://{parsed.netloc}"
    return ""

_PREVIEWS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "previews")

app = FastAPI(title="CIM Generator API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _warm_template_store_connection() -> None:
    """Open template_store's shared DB connection eagerly at boot instead of
    letting the first real /template/saved(-related) request pay the
    ~1.5-2s TCP+TLS handshake to the remote managed MySQL. Best-effort — a
    failure here is logged by warm_connection() itself and never blocks
    startup; the first real call just falls back to connecting normally."""
    await asyncio.get_event_loop().run_in_executor(None, template_store.warm_connection)


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
    custom_template: dict[str, Any] | None = None  # Extracted from an uploaded template file, see core/template_extractor.py
    featured_image: dict[str, Any] | None = None  # User-uploaded photo to feature in the CIM: {"b64", "mime", "label"}


# ---------------------------------------------------------------------------
# Job tracking — in-memory, keyed by listing_id
# ---------------------------------------------------------------------------

@dataclass
class CIMJob:
    """Represents one CIM generation job. Multiple SSE connections can subscribe."""
    job_id: str
    listing_id: int
    status: str                          # "running" | "complete" | "error" | "cancelled"
    events_buffer: list = field(default_factory=list)
    result_html: str = ""
    result_doc_id: Optional[int] = None
    completed_at: Optional[float] = None  # monotonic time when job finished
    task: Optional[asyncio.Task] = None            # outer supervisor task (_run_job_pipeline)
    pipeline_task: Optional[asyncio.Task] = None   # inner LangGraph ainvoke task
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
    log.debug("  Featured image: %s", "yes" if req.featured_image else "(none)")

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
        "custom_template": req.custom_template,
        "featured_image": req.featured_image,
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
    crm_url = _derive_crm_url(req.crm_url, req.callback_url)
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
    job.pipeline_task = pipeline_task   # exposed so /job/terminate can cancel it

    progress_steps = [
        (2, "Processing",  "Extracting and analysing content across all documents..."),
        (3, "Generating",  "Synthesising findings and structuring the investment narrative..."),
        (4, "Rendering",   "Composing the Confidential Information Memorandum..."),
    ]

    try:
        for step, label, msg in progress_steps:
            try:
                await asyncio.wait_for(asyncio.shield(pipeline_task), timeout=50)
            except asyncio.TimeoutError:
                pass
            if pipeline_task.done():
                break
            job.add_event({"step": step, "label": label, "message": msg, "status": "in_progress"})

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
    except asyncio.CancelledError:
        total_elapsed = time.monotonic() - t_pipeline_start
        log.info("━━━ PIPELINE CANCELLED  listing_id=%s  elapsed=%.2fs ━━━", job.listing_id, total_elapsed)
        if not pipeline_task.done():
            pipeline_task.cancel()
        if job.status != "cancelled":   # /job/terminate may have already set this
            job.status = "cancelled"
            job.add_event({
                "step": 0, "label": "Terminated",
                "message": "Generation terminated by user.",
                "status": "cancelled",
            })
        if db_row_id:
            await db_log.log_error(db_row_id)
        raise
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
                if event.get("status") in ("complete", "error", "cancelled"):
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

    - If a job is already RUNNING for req.listing_id: reconnects (fan-out) to
      the existing pipeline without restarting it — avoids double-billing an
      in-flight generation. All past events are replayed so the browser
      catches up.
    - If a job exists but is complete/error/cancelled: it's replaced by a new
      job (a finished job has nothing left to stream, and the caller may have
      picked a different template — never silently replay a stale result).
    - Otherwise: starts a new background pipeline task and streams its events.
    """
    listing_id = req.listing_id

    if listing_id and listing_id in _jobs and _jobs[listing_id].status == "running":
        job = _jobs[listing_id]
        log.info("Reconnecting to existing running job — listing_id=%s", listing_id)
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
    job.task = asyncio.create_task(_run_job_pipeline(req, job))

    return StreamingResponse(
        _fan_out_stream(job),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/job/terminate/{listing_id}")
async def job_terminate(listing_id: int):
    """
    Cancels a running CIM job on user request. Aborts the in-flight LangGraph
    pipeline (including any Claude API call in progress) immediately.
    No-op if the job isn't currently "running" — safe to call more than once.
    """
    job = _jobs.get(listing_id)
    if not job:
        raise HTTPException(status_code=404, detail="No job found for this listing")
    if job.status != "running":
        return JSONResponse({"success": False, "status": job.status, "message": "Job is not running"})

    log.info("Job termination requested — listing_id=%s job_id=%s", listing_id, job.job_id)
    if job.pipeline_task and not job.pipeline_task.done():
        job.pipeline_task.cancel()
    if job.task and not job.task.done():
        job.task.cancel()

    job.status = "cancelled"
    job.add_event({
        "step": 0, "label": "Terminated",
        "message": "Generation terminated by user.",
        "status": "cancelled",
    })
    return JSONResponse({"success": True, "status": "cancelled"})


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


# Upper bounds for a Word/PowerPoint template rendered to PDF (see template_upload):
# Claude accepts at most 100 PDF pages on 200K-context models, and the whole request
# (PDF base64 + listing images) must stay under 32 MB — 15 MB raw leaves headroom.
_MAX_RENDERED_PDF_PAGES = 100
_MAX_RENDERED_PDF_BYTES = 15 * 1024 * 1024


# Content-type fallback for when a filename arrives without a usable extension —
# extension is still checked first (see template_upload below).
_TEMPLATE_CONTENT_TYPE_EXT = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.ms-powerpoint": "ppt",
    "text/html": "html",
    "application/xhtml+xml": "html",
    "text/xml": "xml",
    "application/xml": "xml",
}


@app.post("/template/upload")
async def template_upload(
    file: UploadFile = File(...),
    callback_url: str = Form(""),
    crm_url: str = Form(""),
    username: str = Form(""),
):
    """Extract a CIM template style (colors/fonts/layout) from an uploaded
    PDF, Word (.docx), PowerPoint (.pptx), HTML, or XML file — deterministic, no LLM — then run
    one dedicated LLM design-audit pass over the same file for a much richer
    design spec (see core.llm.audit_template_design). The audit runs once
    here, at upload time, not per-generation: its result is merged into the
    template dict and round-trips through both the frontend's opaque
    custom_template pass-through and core/template_store.py's persistence,
    so a saved/reused template gets full audit fidelity on every future
    generation for free — no re-auditing, no re-upload.

    callback_url/crm_url/username are optional and only used to attribute
    the audit's real token cost in template_audit_log (see
    core/template_store.py's log_template_audit) — same _derive_crm_url
    fallback as every other cost-tracking call in this file. An older
    frontend that doesn't send them still works fine; the cost is just
    logged under crm_url="unknown".
    """
    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _SUPPORTED_TEMPLATE_EXTENSIONS:
        ext = _TEMPLATE_CONTENT_TYPE_EXT.get((file.content_type or "").split(";")[0].strip().lower(), ext)
    if ext not in _SUPPORTED_TEMPLATE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Upload a PDF, Word (.docx), PowerPoint (.pptx), HTML, or XML file.",
        )
    file_bytes = await file.read()

    if ext in ("doc", "ppt"):
        # Legacy binary formats — python-docx/python-pptx can't open them at all, and
        # neither can Claude's document-vision path later at generation time. Convert
        # ONCE, here, to the modern format: extraction below, the stored file_b64/
        # file_ext, the design audit, and the generation-time reference attachment
        # all then transparently use the converted docx/pptx — no special-casing
        # needed anywhere else in the upload/reuse pipeline.
        try:
            file_bytes, ext = await asyncio.get_event_loop().run_in_executor(
                None, convert_legacy, file_bytes, ext
            )
        except LegacyConversionError as exc:
            log.error("template_upload: legacy conversion failed: %s", exc)
            raise HTTPException(
                status_code=400,
                detail=(
                    "Couldn't convert this legacy file — it may be corrupted or "
                    "password-protected. Please save it as .docx/.pptx or PDF and re-upload."
                ),
            )

    try:
        # Always hand the extractor the RESOLVED ext, never the raw filename: the
        # filename may have no extension (type came from the content-type fallback
        # above), and after a legacy conversion ext is docx/pptx, not doc/ppt.
        template, warnings = extract_style_profile(file_bytes, f"upload.{ext}")
    except NoExtractableTextError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except UnsupportedTemplateFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Word/PowerPoint: Claude's document vision only accepts PDF, so these used to
    # reach Claude as flattened text + a few embedded images — their real layout
    # was never visible. Render them to PDF once, here, and store THAT as the
    # reference file: the design audit below, the generation-time attachment and
    # the saved-template preview then all see the real pages with no other code
    # changes. Best-effort — on failure the original docx/pptx is kept and the
    # old text+images path still works.
    ref_bytes, ref_ext = file_bytes, ext
    if ext in ("docx", "pptx"):
        try:
            pdf_bytes = await asyncio.get_event_loop().run_in_executor(
                None, convert_to_pdf, file_bytes, ext
            )
            try:
                with pymupdf.open(stream=pdf_bytes, filetype="pdf") as rendered:
                    pages = rendered.page_count
            except Exception as exc:
                raise LegacyConversionError(f"rendered PDF is unreadable: {exc}") from exc
            if pages > _MAX_RENDERED_PDF_PAGES or len(pdf_bytes) > _MAX_RENDERED_PDF_BYTES:
                # Claude's PDF limits (100 pages on 200K-context models, 32 MB per
                # request incl. images) — past them every generation from this
                # template would fail, so keep the original text+images path.
                log.error("template_upload: rendered PDF too large (%d pages, %d bytes), keeping .%s",
                          pages, len(pdf_bytes), ext)
            else:
                ref_bytes, ref_ext = pdf_bytes, "pdf"
                template["source_ext"] = ext
        except LegacyConversionError as exc:
            log.error("template_upload: PDF render of .%s failed, keeping original: %s", ext, exc)
    # Carry the reference file back to the client so it round-trips into the later
    # /generate-cim call's custom_template dict — core/llm.py attaches it as
    # an actual reference (vision document for PDF, plain text otherwise) so
    # Claude can see the real layout, not just the deterministic extraction
    # above. See core/llm.py:generate_cim_html.
    file_b64 = base64.standard_b64encode(ref_bytes).decode("utf-8")
    template["file_b64"] = file_b64
    template["file_ext"] = ref_ext
    # Scoped reset/read around just the audit call below — a request-local
    # token count, same ContextVar mechanism _run_job_pipeline uses per CIM
    # generation, just borrowed here for a single audit call instead of a
    # whole pipeline. Isolated per-asyncio-task, so this never interferes
    # with a concurrent /generate-cim/stream's own counting.
    reset_token_counters()
    try:
        template["design_audit"] = await audit_template_design(file_b64, ref_ext)
    except Exception as exc:
        # Best-effort fidelity upgrade — never fail the upload over it. The
        # deterministic extraction above still works fine on its own.
        log.error("template_upload: design audit failed: %s", exc)
        template["design_audit"] = None

    # Cost-logging is separate from the audit's own success/failure above —
    # a logging hiccup here must never discard an already-successful audit
    # result, and get_token_counts() still returns whatever the audit call
    # actually spent even if it errored out partway through the API call.
    try:
        in_tok, out_tok = get_token_counts()
        if in_tok or out_tok:
            resolved_crm_url = _derive_crm_url(crm_url, callback_url) or "unknown"
            await template_store.log_template_audit(
                resolved_crm_url, username or "unknown", MODEL, in_tok, out_tok,
            )
    except Exception as exc:
        log.error("template_upload: cost logging failed: %s", exc)

    return JSONResponse(content={"template": template, "warnings": warnings})


class SaveTemplateRequest(BaseModel):
    name: str
    template: dict[str, Any]
    callback_url: str = ""
    crm_url: str = ""
    username: str = ""


@app.post("/template/save")
async def save_custom_template(req: SaveTemplateRequest):
    """Persist an already-extracted template (the exact dict /template/upload
    returned) so it can be reused from the template picker without
    re-uploading the file. Fed back into custom_template unchanged on reuse —
    see core/template_store.py."""
    crm_url = _derive_crm_url(req.crm_url, req.callback_url) or "unknown"
    row_id = await template_store.save_template(crm_url, req.username or "unknown", req.name, req.template)
    if row_id is None:
        raise HTTPException(status_code=500, detail="Failed to save template")
    return JSONResponse(content={"id": row_id, "name": req.name})


@app.get("/template/saved")
async def list_saved_templates(callback_url: str = "", crm_url: str = ""):
    """Lightweight list (id + name only) for the template picker grid."""
    resolved = _derive_crm_url(crm_url, callback_url) or "unknown"
    templates = await template_store.list_templates(resolved)
    return JSONResponse(content={"templates": templates})


@app.get("/template/saved/{template_id}")
async def get_saved_template(template_id: int, callback_url: str = "", crm_url: str = ""):
    """Fetch one saved template's full dict — same shape as /template/upload's
    response — for reuse as custom_template in a generation request."""
    resolved = _derive_crm_url(crm_url, callback_url) or "unknown"
    template = await template_store.get_template(template_id, resolved)
    if template is None:
        raise HTTPException(status_code=404, detail="Saved template not found")
    return JSONResponse(content={"template": template})


# Lorem previews are built on first view and kept in memory (keyed by the file itself),
# so re-opening a preview is instant and nothing extra is stored in the DB.
_lorem_preview_cache: "OrderedDict[str, tuple[bytes, str]]" = OrderedDict()
_LOREM_PREVIEW_CACHE_MAX = 32


def _text_preview_page(text: str) -> bytes:
    import html as _html
    return (
        "<div style='font-family:sans-serif;padding:2rem;white-space:pre-wrap;line-height:1.6;'>"
        "<p style='color:#6b7280;font-style:italic;margin-bottom:1rem;'>"
        "Live preview isn't available for this file type — showing its text layout instead.</p>"
        f"{_html.escape(text)}</div>"
    ).encode("utf-8")


def _build_lorem_preview(raw_bytes: bytes, file_ext: str) -> tuple[bytes, str]:
    """The template's own design with all its text swapped for lorem ipsum
    (core/lorem_preview.py). Returns (content, media_type)."""
    if file_ext == "pdf":
        try:
            return lorem_pdf(raw_bytes), "application/pdf"
        except Exception as exc:
            # Never fall back to the original file — that would show its real content.
            log.error("preview_saved_template: lorem PDF failed: %s", exc)
            return (b"<div style='font-family:sans-serif;padding:2rem;color:#6b7280;'>"
                    b"No preview available for this template.</div>"), "text/html"
    if file_ext in ("html", "htm"):
        return lorem_html(raw_bytes.decode("utf-8", errors="ignore")).encode("utf-8"), "text/html"
    if file_ext in ("docx", "pptx"):
        # Saved before Word/PowerPoint were rendered to PDF at upload — render now.
        try:
            return lorem_pdf(convert_to_pdf(raw_bytes, file_ext)), "application/pdf"
        except LegacyConversionError as exc:
            log.error("preview_saved_template: PDF render failed, text fallback: %s", exc)
    try:
        if file_ext in ("docx", "doc"):
            from core.docx_style_extractor import extract_plain_text
            text = extract_plain_text(raw_bytes)
        elif file_ext in ("pptx", "ppt"):
            from core.pptx_style_extractor import extract_plain_text
            text = extract_plain_text(raw_bytes)
        else:
            text = raw_bytes.decode("utf-8", errors="ignore")
    except Exception as exc:
        log.error("preview_saved_template: text extraction failed: %s", exc)
        text = "(Could not read this file's content for preview.)"
    return _text_preview_page(lorem_text(text)), "text/html"


@app.get("/template/saved/{template_id}/preview")
async def preview_saved_template(template_id: int, callback_url: str = "", crm_url: str = ""):
    """Preview for the picker (previewSavedCustomTemplate in view.php): the saved
    template's own design with its text replaced by lorem ipsum, so the original
    company's content never shows. Generation still uses the real file."""
    resolved = _derive_crm_url(crm_url, callback_url) or "unknown"
    template = await template_store.get_template(template_id, resolved)
    if template is None:
        raise HTTPException(status_code=404, detail="Saved template not found")

    file_b64 = template.get("file_b64") or ""
    file_ext = (template.get("file_ext") or "").lower()
    if not file_b64:
        return HTMLResponse(
            "<div style='font-family:sans-serif;padding:2rem;color:#6b7280;'>"
            "No preview available for this template.</div>"
        )

    key = hashlib.sha256(f"{file_ext}:{file_b64}".encode("utf-8")).hexdigest()
    cached = _lorem_preview_cache.get(key)
    if cached is None:
        raw_bytes = base64.standard_b64decode(file_b64)
        cached = await asyncio.get_event_loop().run_in_executor(None, _build_lorem_preview, raw_bytes, file_ext)
        _lorem_preview_cache[key] = cached
        while len(_lorem_preview_cache) > _LOREM_PREVIEW_CACHE_MAX:
            _lorem_preview_cache.popitem(last=False)
    else:
        _lorem_preview_cache.move_to_end(key)
    content, media_type = cached
    return Response(content=content, media_type=media_type)


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
