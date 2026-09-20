"""Tests for /template/save, /template/saved, /template/saved/{id} — persisting
an already-extracted custom template so it can be reused from the template
picker instead of re-uploading the file. Mocks core.template_store's DB
functions directly (no real MySQL connection made).
"""

from __future__ import annotations

import sys
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv

load_dotenv()  # core.llm reads CIM_MODEL at import time, same as server.py does

from server import app, _derive_crm_url

SAMPLE_TEMPLATE = {
    "id": "custom-upload",
    "name": "Your Uploaded Template",
    "palette": {"primary": "#000000", "accent": "#000000", "light": "#ffffff", "mid": "#000000"},
    "fonts": {"heading": "serif", "body": "sans-serif"},
    "file_b64": "AAAA",
    "file_ext": "pdf",
}


@pytest.fixture
def client():
    return TestClient(app)


def test_derive_crm_url_prefers_explicit_value():
    assert _derive_crm_url("https://explicit.example.com", "https://ignored.example.com/x") == "https://explicit.example.com"


def test_derive_crm_url_falls_back_to_callback_host():
    assert _derive_crm_url("", "https://crm.example.com/site/cimCallback?token=1") == "https://crm.example.com"


def test_derive_crm_url_empty_when_neither_given():
    assert _derive_crm_url("", "") == ""


class TestSaveTemplate:
    def test_saves_and_returns_id(self, client):
        with patch("server.template_store.save_template", new=AsyncMock(return_value=42)) as mock_save:
            response = client.post("/template/save", json={
                "name": "Acme Bakery Style",
                "template": SAMPLE_TEMPLATE,
                "callback_url": "https://crm.example.com/site/cimCallback",
            })
        assert response.status_code == 200
        assert response.json() == {"id": 42, "name": "Acme Bakery Style"}
        mock_save.assert_awaited_once()
        args = mock_save.call_args.args
        assert args[0] == "https://crm.example.com"   # crm_url derived from callback_url
        assert args[1] == "unknown"                    # username defaults when not sent
        assert args[2] == "Acme Bakery Style"
        assert args[3] == SAMPLE_TEMPLATE

    def test_500_when_save_fails(self, client):
        with patch("server.template_store.save_template", new=AsyncMock(return_value=None)):
            response = client.post("/template/save", json={
                "name": "X", "template": SAMPLE_TEMPLATE, "callback_url": "https://crm.example.com/cb",
            })
        assert response.status_code == 500


class TestListSavedTemplates:
    def test_returns_lightweight_list(self, client):
        with patch("server.template_store.list_templates", new=AsyncMock(
            return_value=[{"id": 1, "name": "Acme Style"}, {"id": 2, "name": "Beta Style"}]
        )) as mock_list:
            response = client.get("/template/saved", params={"callback_url": "https://crm.example.com/cb"})
        assert response.status_code == 200
        assert response.json()["templates"] == [{"id": 1, "name": "Acme Style"}, {"id": 2, "name": "Beta Style"}]
        mock_list.assert_awaited_once_with("https://crm.example.com")

    def test_empty_list_is_not_an_error(self, client):
        with patch("server.template_store.list_templates", new=AsyncMock(return_value=[])):
            response = client.get("/template/saved", params={"callback_url": "https://crm.example.com/cb"})
        assert response.status_code == 200
        assert response.json()["templates"] == []


class TestGetSavedTemplate:
    def test_returns_full_template_dict(self, client):
        with patch("server.template_store.get_template", new=AsyncMock(return_value=SAMPLE_TEMPLATE)) as mock_get:
            response = client.get("/template/saved/42", params={"callback_url": "https://crm.example.com/cb"})
        assert response.status_code == 200
        assert response.json()["template"] == SAMPLE_TEMPLATE
        mock_get.assert_awaited_once_with(42, "https://crm.example.com")

    def test_404_when_not_found_or_wrong_tenant(self, client):
        with patch("server.template_store.get_template", new=AsyncMock(return_value=None)):
            response = client.get("/template/saved/999", params={"callback_url": "https://crm.example.com/cb"})
        assert response.status_code == 404
