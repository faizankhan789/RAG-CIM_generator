"""Tests for core.template_store's DB layer — specifically the self-provisioning
retry (CREATE TABLE IF NOT EXISTS on first use, since there's no migration
step against the shared prod DB) and the crm_url-scoped read isolation.
Mocks pymysql.connect entirely; no real MySQL connection is made.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pymysql
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import template_store


def _make_conn(cursor_mock):
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor_mock
    conn.cursor.return_value.__exit__.return_value = False
    return conn


@pytest.mark.asyncio
async def test_save_template_creates_table_on_first_use_then_retries_insert():
    """First INSERT hits 'table doesn't exist' (1146) -> code CREATEs the
    table -> retries the same INSERT once, successfully."""
    cursor = MagicMock()
    table_missing = pymysql.err.ProgrammingError(1146, "Table 'rag_cim_db.custom_templates' doesn't exist")

    call_log = []

    def _execute(sql, params=None):
        call_log.append(sql.strip().split()[0])
        if sql.strip().upper().startswith("INSERT") and call_log.count("INSERT") == 1:
            raise table_missing
        cursor.lastrowid = 7

    cursor.execute.side_effect = _execute
    conn = _make_conn(cursor)

    with patch("core.template_store._connect", return_value=conn):
        row_id = await template_store.save_template("https://crm.example.com", "vadmin", "My Template", {"id": "custom-upload"})

    assert row_id == 7
    assert "CREATE" in call_log  # table got provisioned
    assert call_log.count("INSERT") == 2  # first failed attempt + the retry


@pytest.mark.asyncio
async def test_save_template_returns_none_on_persistent_failure():
    with patch("core.template_store._connect", side_effect=RuntimeError("connection refused")):
        row_id = await template_store.save_template("https://crm.example.com", "vadmin", "X", {})
    assert row_id is None


@pytest.mark.asyncio
async def test_list_templates_scopes_query_by_crm_url():
    cursor = MagicMock()
    cursor.fetchall.return_value = [{"id": 1, "name": "Saved One"}]
    conn = _make_conn(cursor)

    with patch("core.template_store._connect", return_value=conn):
        rows = await template_store.list_templates("https://crm.example.com")

    assert rows == [{"id": 1, "name": "Saved One"}]
    sql, params = cursor.execute.call_args.args
    assert "WHERE crm_url=%s" in sql
    assert params == ("https://crm.example.com",)


@pytest.mark.asyncio
async def test_list_templates_returns_empty_list_on_failure():
    with patch("core.template_store._connect", side_effect=RuntimeError("down")):
        rows = await template_store.list_templates("https://crm.example.com")
    assert rows == []


@pytest.mark.asyncio
async def test_get_template_decodes_json_and_scopes_by_crm_url():
    import json
    cursor = MagicMock()
    cursor.fetchone.return_value = {"template_data": json.dumps({"id": "custom-upload", "file_b64": "AAAA"})}
    conn = _make_conn(cursor)

    with patch("core.template_store._connect", return_value=conn):
        template = await template_store.get_template(42, "https://crm.example.com")

    assert template == {"id": "custom-upload", "file_b64": "AAAA"}
    sql, params = cursor.execute.call_args.args
    assert "WHERE id=%s AND crm_url=%s" in sql
    assert params == (42, "https://crm.example.com")


@pytest.mark.asyncio
async def test_get_template_returns_none_when_row_missing():
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    conn = _make_conn(cursor)

    with patch("core.template_store._connect", return_value=conn):
        template = await template_store.get_template(999, "https://crm.example.com")

    assert template is None


# ─── log_template_audit — tracks the real cost of audit_template_design ─────

@pytest.mark.asyncio
async def test_log_template_audit_inserts_correct_price_and_totals():
    cursor = MagicMock()
    cursor.lastrowid = 3
    conn = _make_conn(cursor)

    with patch("core.template_store._connect", return_value=conn):
        row_id = await template_store.log_template_audit(
            "https://crm.example.com", "vadmin", "claude-haiku-4-5-20251001", 2000, 400,
        )

    assert row_id == 3
    sql, params = cursor.execute.call_args.args
    assert "INSERT INTO template_audit_log" in sql
    crm_url, username, model, in_tok, out_tok, total_tok, price, _created_at = params
    assert crm_url == "https://crm.example.com"
    assert username == "vadmin"
    assert model == "claude-haiku-4-5-20251001"
    assert in_tok == 2000
    assert out_tok == 400
    assert total_tok == 2400
    from core.pricing import estimate_price
    assert price == estimate_price("claude-haiku-4-5-20251001", 2000, 400)


@pytest.mark.asyncio
async def test_log_template_audit_creates_table_on_first_use_then_retries():
    """Same self-provisioning contract as save_template, via the shared
    _insert_with_auto_create helper — verified independently here since
    template_audit_log is a separate table from custom_templates."""
    cursor = MagicMock()
    table_missing = pymysql.err.ProgrammingError(1146, "Table 'rag_cim_db.template_audit_log' doesn't exist")
    call_log = []

    def _execute(sql, params=None):
        call_log.append(sql.strip().split()[0])
        if sql.strip().upper().startswith("INSERT") and call_log.count("INSERT") == 1:
            raise table_missing
        cursor.lastrowid = 1

    cursor.execute.side_effect = _execute
    conn = _make_conn(cursor)

    with patch("core.template_store._connect", return_value=conn):
        row_id = await template_store.log_template_audit(
            "https://crm.example.com", "vadmin", "claude-haiku-4-5-20251001", 100, 50,
        )

    assert row_id == 1
    assert "CREATE" in call_log
    assert call_log.count("INSERT") == 2


@pytest.mark.asyncio
async def test_log_template_audit_returns_none_on_persistent_failure():
    """Never raises up to the caller — server.py's /template/upload must
    still return the (already-successful) design_audit even if cost-logging
    itself is broken."""
    with patch("core.template_store._connect", side_effect=RuntimeError("db down")):
        row_id = await template_store.log_template_audit(
            "https://crm.example.com", "vadmin", "claude-haiku-4-5-20251001", 100, 50,
        )
    assert row_id is None
