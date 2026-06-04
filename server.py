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
    mimeType: str


class CIMRequest(BaseModel):
    listing_data: dict[str, Any] = {}
    logo: str = ""
    gallery_images: list[str] = []
    listing_files: list[ListingFile] = []


def _build_initial_state(req: CIMRequest) -> dict:
    listing_xml = build_listing_xml(req.listing_data) if req.listing_data else ""
    return {
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



_STEPS = [
    (1, "Downloading",  "Retrieving and cataloguing all source documents from the provided file tree."),
    (2, "Processing",   "Extracting and analysing content across all uploaded documents in parallel."),
    (3, "Generating",   "Synthesising key findings and structuring the investment narrative."),
    (4, "Rendering",    "Composing and formatting the Confidential Information Memorandum."),
    (5, "Returning",    "Packaging and delivering the completed memorandum."),
]


@app.post("/generate-cim/stream")
async def generate_cim_stream(req: CIMRequest):
    """Streams SSE progress events (simulated) with a 15-second delay between steps."""

    async def event_stream():
        def sse(payload: dict) -> str:
            return f"data: {json.dumps(payload)}\n\n"

        for num, label, msg in _STEPS:
            yield sse({"step": num, "label": label, "message": msg, "status": "in_progress"})
            await asyncio.sleep(1)

        sample_html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>Busy Bike Shop &amp; Mechanic — Confidential Information Memorandum</title>
  <style>
    body{font-family:Georgia,serif;max-width:900px;margin:40px auto;padding:0 24px;color:#1a1a1a;line-height:1.7}
    h1{font-size:2rem;border-bottom:3px solid #2c3e50;padding-bottom:8px;color:#2c3e50}
    h2{font-size:1.4rem;color:#2c3e50;margin-top:2rem}
    h3{font-size:1.1rem;color:#555}
    .cover{text-align:center;padding:60px 0 40px;border-bottom:1px solid #ddd;margin-bottom:40px}
    .cover .sub{font-size:1rem;color:#777;margin-top:8px}
    .kpi-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:24px 0}
    .kpi-card{background:#f4f7fb;border-left:4px solid #2c3e50;padding:16px;border-radius:4px}
    .kpi-card .label{font-size:.8rem;text-transform:uppercase;letter-spacing:.05em;color:#777}
    .kpi-card .value{font-size:1.6rem;font-weight:bold;color:#2c3e50;margin-top:4px}
    table{width:100%;border-collapse:collapse;margin:16px 0;font-size:.95rem}
    th{background:#2c3e50;color:#fff;padding:10px 12px;text-align:left}
    td{padding:9px 12px;border-bottom:1px solid #e0e0e0}
    tr:nth-child(even) td{background:#f9f9f9}
    .section{margin-bottom:40px}
    .highlight{background:#fffbe6;border-left:4px solid #f0c040;padding:12px 16px;border-radius:4px;margin:16px 0}
    .disclaimer{font-size:.8rem;color:#999;border-top:1px solid #ddd;margin-top:60px;padding-top:16px}
    ul li{margin-bottom:6px}
  </style>
</head>
<body>

<div class="cover">
  <h1>Busy Bike Shop &amp; Mechanic</h1>
  <div class="sub">Confidential Information Memorandum &mdash; June 2026</div>
  <div class="sub">Ref. ID: BBS12345AS &nbsp;|&nbsp; Hartford County, Connecticut &nbsp;|&nbsp; Strictly Confidential</div>
</div>

<div class="section">
  <h2>Executive Summary</h2>
  <p>
    Busy Bike Shop &amp; Mechanic ("the Company" or "Wheels Bicycle") is a well-established specialty
    bicycle retailer and full-service repair centre located on a high-visibility main road in an
    upscale, affluent community in northern New Jersey. Founded in 1986 and operating continuously
    from the same location for nearly four decades, the business has cultivated a loyal customer base
    and a strong internet sales presence that extends its reach well beyond the local market.
  </p>
  <p>
    The Company offers a full line of bicycles, accessories, and cycling apparel across 5,000 sq ft
    of retail and workshop space, supported by a team of 4 full-time and 2 part-time employees. The
    current owner is seeking to retire after an exceptional run, presenting a rare opportunity to
    acquire a profitable, turnkey operation with immediate cash flow.
  </p>
  <div class="highlight">
    <strong>Asking Price:</strong> $300,000 &nbsp;|&nbsp; <strong>Down Payment:</strong> $25,000
    &nbsp;|&nbsp; <strong>Terms:</strong> $250,000 at 8% over 5 years
  </div>
</div>

<div class="section">
  <h2>Key Financial Highlights</h2>
  <div class="kpi-grid">
    <div class="kpi-card"><div class="label">Gross Revenue</div><div class="value">$495,000</div></div>
    <div class="kpi-card"><div class="label">Net Cash Flow</div><div class="value">$95,350</div></div>
    <div class="kpi-card"><div class="label">Owners Cash Flow</div><div class="value">$398,761</div></div>
    <div class="kpi-card"><div class="label">Gross Profit</div><div class="value">$331,650</div></div>
    <div class="kpi-card"><div class="label">Net Profit</div><div class="value">$348,761</div></div>
    <div class="kpi-card"><div class="label">Monthly Revenue</div><div class="value">$41,250</div></div>
  </div>

  <h3>Income &amp; Expense Summary</h3>
  <table>
    <thead><tr><th>Line Item</th><th>Amount</th></tr></thead>
    <tbody>
      <tr><td>Sales</td><td>$500,000</td></tr>
      <tr><td>Other Income</td><td>$25,000</td></tr>
      <tr><td>Less Sales Tax</td><td>($30,000)</td></tr>
      <tr><td>Gross Revenue</td><td>$495,000</td></tr>
      <tr><td>Cost of Goods Sold (33%)</td><td>($163,350)</td></tr>
      <tr><td>Gross Profit</td><td>$331,650</td></tr>
      <tr><td>Total Expenses</td><td>($680,411)</td></tr>
      <tr><td>Officer Salary (Add-back)</td><td>$50,000</td></tr>
      <tr><td>Net Cash Flow</td><td><strong>$95,350</strong></td></tr>
      <tr><td>Owners Cash Flow</td><td><strong>$398,761</strong></td></tr>
    </tbody>
  </table>
</div>

<div class="section">
  <h2>Business Overview</h2>
  <p>
    The Company operates from a 5,000 sq ft premises with 50 parking spaces, open Monday through
    Friday 9:00 am to 5:00 pm. The facility is fully equipped for both retail sales and professional
    bicycle servicing, with a dedicated workshop staffed by experienced mechanics. Real estate is
    available and lease documentation is on file; the current lease runs from March 2018 through
    May 2027 with a 3% annual rental increase clause. The landlord is George Smith.
  </p>
  <ul>
    <li>Full line of bicycles: road, mountain, hybrid, electric, and youth models</li>
    <li>Complete accessories range: helmets, apparel, components, and nutrition</li>
    <li>Professional repair and maintenance service for all brands</li>
    <li>Strong and growing internet sales channel providing geographic diversification</li>
    <li>Established supplier relationships built over 28+ years of continuous operation</li>
  </ul>
</div>

<div class="section">
  <h2>Real Estate &amp; Lease</h2>
  <table>
    <thead><tr><th>Detail</th><th>Information</th></tr></thead>
    <tbody>
      <tr><td>Store Size</td><td>5,000 sq ft</td></tr>
      <tr><td>Annual Rent</td><td>$99,000</td></tr>
      <tr><td>CAM Charges</td><td>$3,000</td></tr>
      <tr><td>Lease Term</td><td>3 years (renewable)</td></tr>
      <tr><td>Contract Start</td><td>March 6, 2018</td></tr>
      <tr><td>Contract End</td><td>May 9, 2027</td></tr>
      <tr><td>Rental Increase</td><td>3% per annum</td></tr>
      <tr><td>Real Estate Available</td><td>Yes</td></tr>
      <tr><td>Lease Copy Available</td><td>Yes</td></tr>
      <tr><td>Parking Spaces</td><td>50</td></tr>
    </tbody>
  </table>
</div>

<div class="section">
  <h2>Assets Included</h2>
  <table>
    <thead><tr><th>Asset Category</th><th>Estimated Value</th></tr></thead>
    <tbody>
      <tr><td>Fixtures &amp; Fittings</td><td>$79,177</td></tr>
      <tr><td>Leasehold Improvements</td><td>$44,708</td></tr>
      <tr><td>FF&amp;E</td><td>$150,000</td></tr>
      <tr><td>Inventory / Stock (included in price)</td><td>$340,000</td></tr>
      <tr><td>Leased Equipment</td><td>$39,775</td></tr>
    </tbody>
  </table>
</div>

<div class="section">
  <h2>Investment Highlights</h2>
  <ul>
    <li><strong>Proven track record:</strong> Nearly 40 years of continuous operation from the same high-traffic location with strong community recognition.</li>
    <li><strong>Immediate cash flow:</strong> Owners Cash Flow of $398,761 provides a compelling return on the $300,000 asking price.</li>
    <li><strong>Relocatable &amp; home-based eligible:</strong> Flexible operational structure opens additional strategic options for an incoming owner.</li>
    <li><strong>Internet sales channel:</strong> Established online presence diversifies revenue and supports margin expansion.</li>
    <li><strong>Experienced staff:</strong> 4 full-time and 2 part-time employees; seller will provide transition support and training.</li>
    <li><strong>Favourable lease:</strong> Long-term lease through May 2027 with predictable 3% annual escalation and real estate purchase option.</li>
  </ul>
</div>

<div class="section">
  <h2>Reason for Sale &amp; Transition</h2>
  <p>
    The owner, Lawrence Michael, is selling to retire after nearly four decades at the helm of the
    business. The seller has indicated a willingness to provide full transition support and training
    to ensure continuity of operations and customer relationships. Management is expected to remain
    in place post-close.
  </p>
</div>

<div class="disclaimer">
  This Confidential Information Memorandum has been prepared on behalf of the seller solely for use
  by prospective purchasers in evaluating a possible acquisition. The information contained herein
  is based on sources believed to be reliable but has not been independently verified. All
  projections are illustrative. Recipients agree to keep this document strictly confidential.
</div>

</body>
</html>"""
        yield sse({"step": 5, "label": "Complete", "message": "Confidential Information Memorandum successfully generated.", "status": "complete", "html": sample_html})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _run_pipeline(req: CIMRequest) -> dict:
    log.info("CIM pipeline started — listing=%r", req.listing_name)
    try:
        result = await cim_graph.ainvoke(_build_initial_state(req))
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
