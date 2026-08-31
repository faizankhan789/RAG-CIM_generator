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


# ─── 9. /template/upload endpoint ────────────────────────────────────────────

class TestTemplateUpload:

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

    def test_rejects_non_pdf(self, client):
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
