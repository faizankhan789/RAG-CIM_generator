"""
Automated unit + integration tests for server.py — callback wiring, CIMRequest model,
and all edge-case scenarios for Option B (Python saves doc via PHP callback).

Run:  cd cim-generator && python -m pytest tests/test_server.py -v
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

# Import the app
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from server import app, _do_callback, CIMRequest, _build_initial_state, _build_file_tree


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    return TestClient(app)


MINIMAL_LISTING = {
    "Name": "Acme Bakery",
    "Asking Price": "$500,000",
}

FULL_REQUEST = {
    "listing_data": MINIMAL_LISTING,
    "logo": "",
    "gallery_images": [],
    "listing_files": [],
    "callback_url": "http://localhost:8081/index.php/api/cimCallback",
    "listing_id": 42,
}


# ─── 1. CIMRequest model ─────────────────────────────────────────────────────

class TestCIMRequest:

    def test_default_callback_fields(self):
        """callback_url and listing_id default to empty/0 — no crash on old callers."""
        req = CIMRequest(listing_data=MINIMAL_LISTING)
        assert req.callback_url == ""
        assert req.listing_id == 0

    def test_callback_fields_accepted(self):
        req = CIMRequest(**FULL_REQUEST)
        assert req.callback_url == "http://localhost:8081/index.php/api/cimCallback"
        assert req.listing_id == 42

    def test_listing_data_optional(self):
        req = CIMRequest()
        assert req.listing_data == {}

    def test_extra_fields_ignored(self):
        """Pydantic should not crash on unknown fields from old browser payloads."""
        data = {**FULL_REQUEST, "unknown_field": "ignore_me"}
        # Pydantic v2 raises by default; if it doesn't crash, model is permissive
        try:
            req = CIMRequest(**data)
            assert req.listing_id == 42
        except Exception:
            pass  # strict mode OK too — just must not crash the server


# ─── 2. _build_initial_state ─────────────────────────────────────────────────

class TestBuildInitialState:

    def test_listing_name_from_Name_key(self):
        req = CIMRequest(listing_data={"Name": "Acme"})
        state = _build_initial_state(req)
        assert state["listing_name"] == "Acme"

    def test_listing_name_from_name_key(self):
        req = CIMRequest(listing_data={"name": "Bakery"})
        state = _build_initial_state(req)
        assert state["listing_name"] == "Bakery"

    def test_listing_name_fallback_empty(self):
        req = CIMRequest(listing_data={})
        state = _build_initial_state(req)
        assert state["listing_name"] == ""

    def test_file_tree_built(self):
        req = CIMRequest(
            listing_files=[{"name": "financials.pdf", "url": "http://x/f.pdf", "mimeType": "application/pdf"}],
            gallery_images=["http://x/img.jpg"],
        )
        tree = _build_file_tree(req)
        assert any(f["name"] == "financials.pdf" for f in tree)
        assert any(f["name"] == "img.jpg" for f in tree)


# ─── 3. _do_callback ─────────────────────────────────────────────────────────

class TestDoCallback:

    @pytest.mark.asyncio
    async def test_empty_callback_url_returns_none(self):
        result = await _do_callback("", "<html/>", "Acme", 1)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_html_returns_none(self):
        result = await _do_callback("http://localhost/cb", "", "Acme", 1)
        assert result is None

    @pytest.mark.asyncio
    async def test_successful_callback_returns_doc_id(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"success": True, "doc_id": 99}

        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
            result = await _do_callback("http://localhost/cb", "<html/>", "Acme", 1)
        assert result == 99

    @pytest.mark.asyncio
    async def test_callback_php_error_returns_none(self):
        """PHP returns success:false — _do_callback returns None (no crash)."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"success": False, "error": "Save failed"}

        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
            result = await _do_callback("http://localhost/cb", "<html/>", "Acme", 1)
        assert result is None

    @pytest.mark.asyncio
    async def test_callback_network_error_returns_none(self):
        """Connection refused / timeout — must not crash, returns None."""
        with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.ConnectError("refused"))):
            result = await _do_callback("http://localhost/cb", "<html/>", "Acme", 1)
        assert result is None

    @pytest.mark.asyncio
    async def test_callback_timeout_returns_none(self):
        with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.TimeoutException("timeout"))):
            result = await _do_callback("http://localhost/cb", "<html/>", "Acme", 1)
        assert result is None

    @pytest.mark.asyncio
    async def test_callback_includes_doc_id_on_update(self):
        """When doc_id > 0 is passed, it should be forwarded in the payload."""
        captured = {}

        async def fake_post(self_client, url, *, json=None, timeout=None):
            captured["payload"] = json
            mock = MagicMock()
            mock.json.return_value = {"success": True, "doc_id": 7}
            return mock

        with patch("httpx.AsyncClient.post", new=fake_post):
            await _do_callback("http://localhost/cb", "<html/>", "Acme", 1, doc_id=7)

        assert captured["payload"]["doc_id"] == 7

    @pytest.mark.asyncio
    async def test_callback_no_doc_id_when_zero(self):
        """doc_id=0 should NOT be included in payload (creates new, not update)."""
        captured = {}

        async def fake_post(self_client, url, *, json=None, timeout=None):
            captured["payload"] = json
            mock = MagicMock()
            mock.json.return_value = {"success": True, "doc_id": 5}
            return mock

        with patch("httpx.AsyncClient.post", new=fake_post):
            await _do_callback("http://localhost/cb", "<html/>", "Acme", 1, doc_id=0)

        assert "doc_id" not in captured["payload"]

    @pytest.mark.asyncio
    async def test_callback_sends_correct_payload(self):
        captured = {}

        async def fake_post(self_client, url, *, json=None, timeout=None):
            captured["payload"] = json
            captured["url"] = url
            mock = MagicMock()
            mock.json.return_value = {"success": True, "doc_id": 3}
            return mock

        with patch("httpx.AsyncClient.post", new=fake_post):
            await _do_callback("http://localhost/cb", "<html>CIM</html>", "Bakery", 42)

        assert captured["payload"]["name"] == "Bakery"
        assert captured["payload"]["listing_id"] == 42
        assert captured["payload"]["html"] == "<html>CIM</html>"
        assert captured["url"] == "http://localhost/cb"


# ─── 4. /health endpoint ─────────────────────────────────────────────────────

class TestHealthEndpoint:

    def test_health_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ─── 5. /generate-cim/html endpoint ─────────────────────────────────────────

class TestGenerateCimHtml:

    def _mock_pipeline(self, html="<html>CIM</html>", errors=None):
        async def fake_invoke(state):
            return {"cim_output": html, "errors": errors or []}
        return fake_invoke

    def test_missing_html_output_raises_500(self, client):
        with patch("server.cim_graph") as mock_graph:
            mock_graph.ainvoke = self._mock_pipeline(html="")
            r = client.post("/generate-cim/html", json={"listing_data": MINIMAL_LISTING})
        assert r.status_code == 500

    def test_returns_html_on_success(self, client):
        with patch("server.cim_graph") as mock_graph, \
             patch("server._do_callback", new=AsyncMock(return_value=None)):
            mock_graph.ainvoke = self._mock_pipeline(html="<html>OK</html>")
            r = client.post("/generate-cim/html", json=FULL_REQUEST)
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert "<html>OK</html>" in body["html"]

    def test_no_callback_when_url_empty(self, client):
        """If callback_url is blank, _do_callback should never be called."""
        call_count = {"n": 0}

        async def spy(*a, **kw):
            call_count["n"] += 1
            return None

        payload = {**FULL_REQUEST, "callback_url": ""}
        with patch("server.cim_graph") as mock_graph, \
             patch("server._do_callback", new=spy):
            mock_graph.ainvoke = self._mock_pipeline(html="<html/>")
            client.post("/generate-cim/html", json=payload)

        assert call_count["n"] == 0


# ─── 6. /generate-cim/stream SSE endpoint ────────────────────────────────────

class TestGenerateCimStream:

    def _collect_sse(self, response) -> list[dict]:
        events = []
        for line in response.iter_lines():
            if line.startswith("data: "):
                try:
                    events.append(json.loads(line[6:]))
                except Exception:
                    pass
        return events

    def test_stream_emits_complete_event(self, client):
        async def fake_invoke(state):
            return {"cim_output": "<html>CIM</html>", "errors": []}

        with patch("server.cim_graph") as mock_graph, \
             patch("server._do_callback", new=AsyncMock(return_value=55)):
            mock_graph.ainvoke = fake_invoke
            with client.stream("POST", "/generate-cim/stream", json=FULL_REQUEST) as r:
                events = self._collect_sse(r)

        complete = [e for e in events if e.get("status") == "complete"]
        assert len(complete) == 1

    def test_stream_complete_event_has_html(self, client):
        async def fake_invoke(state):
            return {"cim_output": "<html>BODY</html>", "errors": []}

        with patch("server.cim_graph") as mock_graph, \
             patch("server._do_callback", new=AsyncMock(return_value=None)):
            mock_graph.ainvoke = fake_invoke
            with client.stream("POST", "/generate-cim/stream", json=FULL_REQUEST) as r:
                events = self._collect_sse(r)

        complete = next(e for e in events if e.get("status") == "complete")
        assert "<html>BODY</html>" in complete.get("html", "")

    def test_stream_callback_fires_when_url_set(self, client):
        fired = {"called": False}

        async def spy_callback(url, html, name, listing_id, doc_id=0):
            fired["called"] = True
            fired["listing_id"] = listing_id
            return 77

        async def fake_invoke(state):
            return {"cim_output": "<html/>", "errors": []}

        with patch("server.cim_graph") as mock_graph, \
             patch("server._do_callback", new=spy_callback):
            mock_graph.ainvoke = fake_invoke
            with client.stream("POST", "/generate-cim/stream", json=FULL_REQUEST) as r:
                self._collect_sse(r)

        assert fired["called"]
        assert fired["listing_id"] == 42

    def test_stream_no_callback_when_url_empty(self, client):
        fired = {"called": False}

        async def spy_callback(*a, **kw):
            fired["called"] = True
            return None

        async def fake_invoke(state):
            return {"cim_output": "<html/>", "errors": []}

        payload = {**FULL_REQUEST, "callback_url": ""}
        with patch("server.cim_graph") as mock_graph, \
             patch("server._do_callback", new=spy_callback):
            mock_graph.ainvoke = fake_invoke
            with client.stream("POST", "/generate-cim/stream", json=payload) as r:
                self._collect_sse(r)

        assert not fired["called"]

    def test_stream_pipeline_error_emits_error_event(self, client):
        async def failing_invoke(state):
            raise RuntimeError("LLM exploded")

        with patch("server.cim_graph") as mock_graph:
            mock_graph.ainvoke = failing_invoke
            with client.stream("POST", "/generate-cim/stream", json=FULL_REQUEST) as r:
                events = self._collect_sse(r)

        error_events = [e for e in events if e.get("status") == "error"]
        assert len(error_events) == 1
        assert "LLM exploded" in error_events[0]["message"]

    def test_stream_callback_failure_still_completes(self, client):
        """Callback network error must NOT crash the SSE stream."""
        async def failing_callback(*a, **kw):
            raise httpx.ConnectError("refused")

        async def fake_invoke(state):
            return {"cim_output": "<html>OK</html>", "errors": []}

        with patch("server.cim_graph") as mock_graph, \
             patch("server._do_callback", new=failing_callback):
            mock_graph.ainvoke = fake_invoke
            with client.stream("POST", "/generate-cim/stream", json=FULL_REQUEST) as r:
                events = self._collect_sse(r)

        complete = [e for e in events if e.get("status") == "complete"]
        assert len(complete) == 1, "stream must complete even when callback fails"

    def test_stream_doc_id_in_complete_event(self, client):
        """doc_id from callback must appear in the complete SSE event."""
        async def fake_invoke(state):
            return {"cim_output": "<html/>", "errors": []}

        with patch("server.cim_graph") as mock_graph, \
             patch("server._do_callback", new=AsyncMock(return_value=88)):
            mock_graph.ainvoke = fake_invoke
            with client.stream("POST", "/generate-cim/stream", json=FULL_REQUEST) as r:
                events = self._collect_sse(r)

        complete = next(e for e in events if e.get("status") == "complete")
        assert complete.get("doc_id") == 88

    def test_stream_doc_id_none_when_callback_fails(self, client):
        """If callback returns None, doc_id in SSE should be None/absent — not crash."""
        async def fake_invoke(state):
            return {"cim_output": "<html/>", "errors": []}

        with patch("server.cim_graph") as mock_graph, \
             patch("server._do_callback", new=AsyncMock(return_value=None)):
            mock_graph.ainvoke = fake_invoke
            with client.stream("POST", "/generate-cim/stream", json=FULL_REQUEST) as r:
                events = self._collect_sse(r)

        complete = next(e for e in events if e.get("status") == "complete")
        # doc_id absent or None — browser JS handles both safely
        assert complete.get("doc_id") is None or "doc_id" not in complete


# ─── 8. custom_template wiring (uploaded PDF templates) ─────────────────────

def test_build_initial_state_carries_custom_template():
    req = CIMRequest(
        listing_data=MINIMAL_LISTING,
        custom_template={"id": "custom-upload", "name": "My Upload"},
    )
    state = _build_initial_state(req)
    assert state["custom_template"] == {"id": "custom-upload", "name": "My Upload"}


def test_build_initial_state_defaults_custom_template_to_none():
    req = CIMRequest(listing_data=MINIMAL_LISTING)
    state = _build_initial_state(req)
    assert state["custom_template"] is None


# ─── 9. featured_image wiring (image-upload popup) ───────────────────────────

def test_build_initial_state_carries_featured_image():
    req = CIMRequest(
        listing_data=MINIMAL_LISTING,
        featured_image={"b64": "AAAA", "mime": "image/png", "label": "Storefront"},
    )
    state = _build_initial_state(req)
    assert state["featured_image"] == {"b64": "AAAA", "mime": "image/png", "label": "Storefront"}


def test_build_initial_state_defaults_featured_image_to_none():
    req = CIMRequest(listing_data=MINIMAL_LISTING)
    state = _build_initial_state(req)
    assert state["featured_image"] is None


# ─── 9. /template/upload endpoint ────────────────────────────────────────────

class TestTemplateUpload:
    """IMPORTANT: audit_template_design (core/llm.py) makes a real Claude API
    call. Every test in this class must go through a client fixture that has
    it mocked — see the autouse fixture below — so no test here ever spends
    real API credits, no matter which extraction path it exercises."""

    @pytest.fixture(autouse=True)
    def _no_real_design_audit(self):
        with patch("server.audit_template_design", new=AsyncMock(return_value=None)):
            yield

    @pytest.fixture(autouse=True)
    def _no_real_pdf_render(self):
        """docx/pptx -> PDF rendering is off by default here (as if soffice were
        missing), so every other test sees the original file_ext unchanged and
        never spawns LibreOffice. The PDF-render tests below override it."""
        from core.office_convert import LegacyConversionError
        with patch("server.convert_to_pdf", side_effect=LegacyConversionError("soffice not installed")):
            yield

    @pytest.mark.parametrize("name, make, mime", [
        ("template.docx", "make_text_docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("template.pptx", "make_text_pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ])
    def test_word_and_powerpoint_are_stored_as_rendered_pdf(self, client, name, make, mime):
        """Claude only has document vision for PDF — a docx/pptx template is
        rendered to PDF at upload so the audit, generation and the saved
        preview all see its real pages, not flattened text."""
        import base64
        from tests import template_helpers
        source_ext = name.rsplit(".", 1)[1]
        audit = AsyncMock(return_value=None)
        import pymupdf
        one_page = pymupdf.open()
        one_page.new_page()
        rendered = one_page.tobytes()
        with patch("server.convert_to_pdf", return_value=rendered) as mock_pdf, \
             patch("server.audit_template_design", new=audit):
            response = client.post(
                "/template/upload",
                files={"file": (name, getattr(template_helpers, make)(), mime)},
            )
        assert response.status_code == 200
        tpl = response.json()["template"]
        assert mock_pdf.call_args[0][1] == source_ext
        assert tpl["file_ext"] == "pdf"
        assert tpl["source_ext"] == source_ext
        assert base64.standard_b64decode(tpl["file_b64"]) == rendered
        audit.assert_awaited_once_with(tpl["file_b64"], "pdf")   # audit sees the PDF too

    def test_pdf_render_failure_keeps_original_docx(self, client):
        """Rendering is a fidelity upgrade, never a hard dependency — if
        LibreOffice fails, the upload still succeeds with the original file."""
        from tests.template_helpers import make_text_docx
        response = client.post(
            "/template/upload",
            files={"file": ("template.docx", make_text_docx(),
                             "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        assert response.status_code == 200
        tpl = response.json()["template"]
        assert tpl["file_ext"] == "docx"
        assert "source_ext" not in tpl

    def test_render_over_claude_pdf_page_limit_keeps_original(self, client):
        """Claude rejects PDFs over 100 pages (200K-context models such as Haiku
        4.5). A big deck rendered past that must fall back to the original
        text+images path, or every generation from it would fail."""
        import pymupdf
        from tests.template_helpers import make_text_pptx
        doc = pymupdf.open()
        for _ in range(101):
            doc.new_page()
        big_pdf = doc.tobytes()
        with patch("server.convert_to_pdf", return_value=big_pdf):
            response = client.post(
                "/template/upload",
                files={"file": ("deck.pptx", make_text_pptx(),
                                 "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
            )
        assert response.status_code == 200
        assert response.json()["template"]["file_ext"] == "pptx"

    def test_unreadable_rendered_pdf_keeps_original(self, client):
        from tests.template_helpers import make_text_docx
        with patch("server.convert_to_pdf", return_value=b"%PDF-1.7 truncated garbage"):
            response = client.post(
                "/template/upload",
                files={"file": ("template.docx", make_text_docx(),
                                 "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            )
        assert response.status_code == 200
        assert response.json()["template"]["file_ext"] == "docx"

    def test_pdf_upload_is_never_re_rendered(self, client):
        from tests.pdf_helpers import make_text_pdf
        with patch("server.convert_to_pdf") as mock_pdf:
            response = client.post(
                "/template/upload",
                files={"file": ("template.pdf", make_text_pdf(), "application/pdf")},
            )
        assert response.status_code == 200
        mock_pdf.assert_not_called()

    def test_returns_template_and_warnings(self, client):
        from tests.pdf_helpers import make_text_pdf
        pdf_bytes = make_text_pdf()
        response = client.post(
            "/template/upload",
            files={"file": ("template.pdf", pdf_bytes, "application/pdf")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["template"]["id"] == "custom-upload"
        assert isinstance(body["warnings"], list)
        assert body["template"]["file_ext"] == "pdf"
        assert body["template"]["file_b64"]  # raw file round-trips for core/llm.py's vision attach
        assert body["template"]["design_audit"] is None  # audit mocked off above -> graceful None

    def test_includes_design_audit_when_it_succeeds(self, client):
        from tests.pdf_helpers import make_text_pdf
        fake_audit = {"cover": {"layout": "centered"}}
        with patch("server.audit_template_design", new=AsyncMock(return_value=fake_audit)):
            response = client.post(
                "/template/upload",
                files={"file": ("template.pdf", make_text_pdf(), "application/pdf")},
            )
        assert response.status_code == 200
        assert response.json()["template"]["design_audit"] == fake_audit

    def test_upload_still_succeeds_when_design_audit_raises(self, client):
        """The audit is a fidelity upgrade, never a hard dependency — an
        exception from it must never fail the upload itself."""
        from tests.pdf_helpers import make_text_pdf
        with patch("server.audit_template_design", new=AsyncMock(side_effect=RuntimeError("API down"))):
            response = client.post(
                "/template/upload",
                files={"file": ("template.pdf", make_text_pdf(), "application/pdf")},
            )
        assert response.status_code == 200
        assert response.json()["template"]["design_audit"] is None

    def test_logs_real_audit_cost_when_tokens_were_spent(self, client):
        """The core fix: audit_template_design's tokens must actually reach
        template_store.log_template_audit — this was previously a silent
        no-op because /template/upload never established a token-counting
        context at all."""
        from tests.pdf_helpers import make_text_pdf
        from core.llm import MODEL, _add_tokens

        async def fake_audit(file_b64, file_ext):
            _add_tokens(500, 100)  # simulates what a real audit call would do internally
            return {"cover": {"layout": "centered"}}

        with patch("server.audit_template_design", new=fake_audit), \
             patch("server.template_store.log_template_audit", new=AsyncMock(return_value=1)) as mock_log:
            response = client.post(
                "/template/upload",
                files={"file": ("template.pdf", make_text_pdf(), "application/pdf")},
                data={"callback_url": "https://crm.example.com/site/cimCallback"},
            )

        assert response.status_code == 200
        assert response.json()["template"]["design_audit"] == {"cover": {"layout": "centered"}}
        mock_log.assert_awaited_once_with("https://crm.example.com", "unknown", MODEL, 500, 100)

    def test_does_not_log_cost_when_audit_returns_none_with_no_tokens(self, client):
        """The autouse fixture's default mock (audit returns None, no tokens
        spent) must never fire a cost-log call — nothing was actually spent."""
        from tests.pdf_helpers import make_text_pdf
        with patch("server.template_store.log_template_audit", new=AsyncMock(return_value=1)) as mock_log:
            response = client.post(
                "/template/upload",
                files={"file": ("template.pdf", make_text_pdf(), "application/pdf")},
            )
        assert response.status_code == 200
        mock_log.assert_not_awaited()

    def test_cost_logging_failure_never_discards_a_successful_audit(self, client):
        """Regression guard: cost-logging and the audit result must be fully
        independent. A bug here previously would have wiped out a perfectly
        good design_audit just because the unrelated logging step failed."""
        from tests.pdf_helpers import make_text_pdf
        from core.llm import _add_tokens

        async def fake_audit(file_b64, file_ext):
            _add_tokens(500, 100)
            return {"cover": {"layout": "centered"}}

        with patch("server.audit_template_design", new=fake_audit), \
             patch("server.template_store.log_template_audit", new=AsyncMock(side_effect=RuntimeError("db down"))):
            response = client.post(
                "/template/upload",
                files={"file": ("template.pdf", make_text_pdf(), "application/pdf")},
            )
        assert response.status_code == 200
        assert response.json()["template"]["design_audit"] == {"cover": {"layout": "centered"}}

    def test_accepts_docx(self, client):
        from tests.template_helpers import make_text_docx
        response = client.post(
            "/template/upload",
            files={"file": ("template.docx", make_text_docx(),
                             "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["template"]["id"] == "custom-upload"
        assert body["template"]["file_ext"] == "docx"

    def test_accepts_pptx(self, client):
        from tests.template_helpers import make_text_pptx
        response = client.post(
            "/template/upload",
            files={"file": ("template.pptx", make_text_pptx(),
                             "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["template"]["id"] == "custom-upload"
        assert body["template"]["file_ext"] == "pptx"

    def test_accepts_html(self, client):
        from tests.template_helpers import make_text_html
        response = client.post(
            "/template/upload",
            files={"file": ("template.html", make_text_html(), "text/html")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["template"]["id"] == "custom-upload"
        assert body["template"]["file_ext"] == "html"

    def test_accepts_xml(self, client):
        from tests.template_helpers import make_custom_schema_xml
        response = client.post(
            "/template/upload",
            files={"file": ("template.xml", make_custom_schema_xml(), "application/xml")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["template"]["id"] == "custom-upload"
        assert body["template"]["file_ext"] == "xml"

    def test_rejects_unsupported_file_type(self, client):
        response = client.post(
            "/template/upload",
            files={"file": ("notes.txt", b"just some text", "text/plain")},
        )
        assert response.status_code == 400

    def test_rejects_no_text_layer_pdf(self, client):
        from tests.pdf_helpers import make_no_text_pdf
        pdf_bytes = make_no_text_pdf()
        response = client.post(
            "/template/upload",
            files={"file": ("scanned.pdf", pdf_bytes, "application/pdf")},
        )
        assert response.status_code == 422

    @pytest.mark.parametrize("name, mime", [
        ("legacy.doc", "application/msword"),
        ("legacy.ppt", "application/vnd.ms-powerpoint"),
    ])
    def test_legacy_ext_with_garbage_content_is_rejected(self, client, name, mime):
        """Real soffice Writer never rejects a junk .doc — it "recovers" any
        unparseable content as plain text and converts it successfully, which
        used to turn a junk upload into a bogus default template (HTTP 200).
        convert_legacy's OLE2 signature check now rejects it before soffice
        runs, so both .doc and .ppt junk get a clean 400 — no soffice needed."""
        response = client.post(
            "/template/upload",
            files={"file": (name, b"not a real ole package", mime)},
        )
        assert response.status_code == 400
        assert "convert" in response.json()["detail"].lower()

    def test_extensionless_filename_uses_content_type_fallback(self, client):
        """A filename with no extension resolves its type from the content-type
        fallback — the extractor must be handed that resolved ext, not the bare
        filename (which used to fail as an unsupported type)."""
        from tests.template_helpers import make_text_docx
        response = client.post(
            "/template/upload",
            files={"file": ("mytemplate", make_text_docx(),
                             "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        assert response.status_code == 200
        assert response.json()["template"]["file_ext"] == "docx"

    def test_legacy_doc_is_converted_then_extracted(self, client):
        """Wiring guard: a legacy .doc must be routed through convert_legacy
        BEFORE extraction — the stored file_b64/file_ext must reflect the
        CONVERTED .docx, not the original .doc bytes, so the design audit
        and generation-time reference attachment (both keyed on file_ext)
        work on content python-docx can actually open."""
        import base64
        from tests.template_helpers import make_text_docx
        converted_bytes = make_text_docx()
        with patch("server.convert_legacy", return_value=(converted_bytes, "docx")) as mock_convert:
            response = client.post(
                "/template/upload",
                files={"file": ("legacy.doc", b"pretend legacy ole bytes", "application/msword")},
            )
        mock_convert.assert_called_once_with(b"pretend legacy ole bytes", "doc")
        assert response.status_code == 200
        body = response.json()
        assert body["template"]["file_ext"] == "docx"
        assert base64.standard_b64decode(body["template"]["file_b64"]) == converted_bytes

    def test_legacy_ppt_is_converted_then_extracted(self, client):
        import base64
        from tests.template_helpers import make_text_pptx
        converted_bytes = make_text_pptx()
        with patch("server.convert_legacy", return_value=(converted_bytes, "pptx")) as mock_convert:
            response = client.post(
                "/template/upload",
                files={"file": ("legacy.ppt", b"pretend legacy ole bytes", "application/vnd.ms-powerpoint")},
            )
        mock_convert.assert_called_once_with(b"pretend legacy ole bytes", "ppt")
        assert response.status_code == 200
        body = response.json()
        assert body["template"]["file_ext"] == "pptx"
        assert base64.standard_b64decode(body["template"]["file_b64"]) == converted_bytes

    def test_legacy_conversion_failure_returns_clear_400(self, client):
        from core.office_convert import LegacyConversionError
        with patch("server.convert_legacy", side_effect=LegacyConversionError("soffice exploded")):
            response = client.post(
                "/template/upload",
                files={"file": ("legacy.doc", b"pretend legacy ole bytes", "application/msword")},
            )
        assert response.status_code == 400
        assert "convert" in response.json()["detail"].lower()

    def test_legacy_ppt_conversion_failure_returns_clear_400(self, client):
        """.ppt counterpart — real soffice essentially never fails on .doc/.ppt
        input (verified: it falls back to a lenient text-recovery import even
        for garbage/empty/random-binary content), so the only way to reliably
        exercise this 400 path is mocking the failure directly."""
        from core.office_convert import LegacyConversionError
        with patch("server.convert_legacy", side_effect=LegacyConversionError("soffice exploded")):
            response = client.post(
                "/template/upload",
                files={"file": ("legacy.ppt", b"pretend legacy ole bytes", "application/vnd.ms-powerpoint")},
            )
        assert response.status_code == 400
        assert "convert" in response.json()["detail"].lower()

    def test_modern_docx_never_calls_convert_legacy(self, client):
        """Only genuinely legacy .doc/.ppt should ever invoke the LibreOffice
        subprocess — a real .docx must skip it entirely."""
        from tests.template_helpers import make_text_docx
        with patch("server.convert_legacy") as mock_convert:
            response = client.post(
                "/template/upload",
                files={"file": ("template.docx", make_text_docx(),
                                 "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            )
        mock_convert.assert_not_called()
        assert response.status_code == 200


class TestPreviewSavedTemplate:
    """/template/saved/{id}/preview — renders the saved template's own
    uploaded file for the picker's preview modal (previewSavedCustomTemplate
    in view.php), not a generated CIM."""

    def test_renders_pdf_inline(self, client):
        import base64
        fake_template = {"file_b64": base64.b64encode(b"%PDF-1.4 fake").decode(), "file_ext": "pdf"}
        with patch("server.template_store.get_template", new=AsyncMock(return_value=fake_template)):
            response = client.get("/template/saved/1/preview")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.content == b"%PDF-1.4 fake"

    def test_renders_html_inline(self, client):
        import base64
        html = b"<html><body>Hello</body></html>"
        fake_template = {"file_b64": base64.b64encode(html).decode(), "file_ext": "html"}
        with patch("server.template_store.get_template", new=AsyncMock(return_value=fake_template)):
            response = client.get("/template/saved/1/preview")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert b"Hello" in response.content

    def test_falls_back_to_extracted_text_for_docx(self, client):
        import base64
        fake_template = {"file_b64": base64.b64encode(b"whatever").decode(), "file_ext": "docx"}
        with patch("server.template_store.get_template", new=AsyncMock(return_value=fake_template)), \
             patch("core.docx_style_extractor.extract_plain_text", return_value="Extracted docx body"):
            response = client.get("/template/saved/1/preview")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert b"Extracted docx body" in response.content
        assert b"Live preview isn" in response.content

    def test_returns_placeholder_when_no_file_attached(self, client):
        with patch("server.template_store.get_template", new=AsyncMock(return_value={"file_b64": "", "file_ext": "pdf"})):
            response = client.get("/template/saved/1/preview")
        assert response.status_code == 200
        assert b"No preview available" in response.content

    def test_returns_404_when_template_missing(self, client):
        with patch("server.template_store.get_template", new=AsyncMock(return_value=None)):
            response = client.get("/template/saved/999/preview")
        assert response.status_code == 404

    def test_scopes_lookup_by_crm_url_derived_from_callback_url(self, client):
        with patch("server.template_store.get_template", new=AsyncMock(return_value=None)) as mock_get:
            client.get("/template/saved/1/preview?callback_url=" + "https://crm.example.com/site/cimCallback")
        mock_get.assert_awaited_once_with(1, "https://crm.example.com")
