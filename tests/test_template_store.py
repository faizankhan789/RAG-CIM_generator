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


@pytest.fixture(autouse=True)
def _reset_shared_connection():
    """template_store now reuses one shared connection across calls (see
    _locked_conn) instead of opening a fresh one every time. Without this
    reset, whichever test runs first would leave its mock connection sitting
    in template_store._conn, and every later test's patched _connect would
    never actually get called — _locked_conn would just .ping() the stale
    mock from a previous test and reuse it."""
    template_store._conn = None
    yield
    template_store._conn = None


def _make_conn(cursor_mock):
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor_mock
    conn.cursor.return_value.__exit__.return_value = False
    return conn


@pytest.mark.asyncio
async def test_save_template_creates_table_on_first_use_then_retries_insert():
    """save_template's UPSERT does a SELECT (does a row with this name already
    exist?) before deciding INSERT vs UPDATE — so on a table that doesn't
    exist yet, it's that SELECT which hits 'table doesn't exist' (1146), not
    the INSERT. Code CREATEs the table on that failure, treats it as 'no
    existing row', and proceeds straight to a normal INSERT."""
    cursor = MagicMock()
    cursor.fetchone.return_value = None  # save_template's existing-row check: none found -> INSERT path
    table_missing = pymysql.err.ProgrammingError(1146, "Table 'rag_cim_db.custom_templates' doesn't exist")

    call_log = []

    def _execute(sql, params=None):
        stmt = sql.strip().split()[0].upper()
        call_log.append(stmt)
        if stmt == "SELECT" and call_log.count("SELECT") == 1:
            raise table_missing
        if stmt == "INSERT":
            cursor.lastrowid = 7

    cursor.execute.side_effect = _execute
    conn = _make_conn(cursor)

    with patch("core.template_store._connect", return_value=conn):
        row_id = await template_store.save_template("https://crm.example.com", "vadmin", "My Template", {"id": "custom-upload"})

    assert row_id == 7
    assert "CREATE" in call_log  # table got provisioned
    assert call_log.count("INSERT") == 1  # the SELECT failed, not the INSERT — no retry needed on it


@pytest.mark.asyncio
async def test_save_template_returns_none_on_persistent_failure():
    with patch("core.template_store._connect", side_effect=RuntimeError("connection refused")):
        row_id = await template_store.save_template("https://crm.example.com", "vadmin", "X", {})
    assert row_id is None


class TestConnectionReuse:
    """The perf fix: template_store must reuse one connection across calls
    instead of paying a fresh TCP+TLS handshake (measured ~1.6s against the
    real remote DB) on every single request — this was the actual cause of
    the saved-templates picker/badge taking ~5s to update. No preemptive
    ping() either — measured at ~0.3s per call against the same remote DB,
    i.e. the same cost as the query it would be "protecting", so _run_db
    tries the shared connection directly and only reconnects if a call
    actually fails with a connection-level error."""

    @pytest.mark.asyncio
    async def test_second_call_reuses_connection_with_no_ping_overhead(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        conn = _make_conn(cursor)

        with patch("core.template_store._connect", return_value=conn) as mock_connect:
            await template_store.list_templates("https://crm.example.com")
            await template_store.list_templates("https://crm.example.com")

        mock_connect.assert_called_once()
        conn.ping.assert_not_called()
        assert cursor.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_reconnects_and_retries_once_when_shared_connection_is_dead(self):
        """The shared connection's first use this call raises a connection-level
        error (simulating a dropped idle connection) — _run_db must reconnect
        and retry the SAME operation once, transparently, rather than
        surfacing the failure to the caller."""
        dead_cursor = MagicMock()
        dead_cursor.execute.side_effect = pymysql.err.OperationalError(2006, "MySQL server has gone away")
        dead_conn = _make_conn(dead_cursor)

        fresh_cursor = MagicMock()
        fresh_cursor.fetchall.return_value = [{"id": 1, "name": "Recovered"}]
        fresh_conn = _make_conn(fresh_cursor)

        with patch("core.template_store._connect", side_effect=[dead_conn, fresh_conn]) as mock_connect:
            rows = await template_store.list_templates("https://crm.example.com")

        assert mock_connect.call_count == 2
        assert rows == [{"id": 1, "name": "Recovered"}]

    @pytest.mark.asyncio
    async def test_unhandled_exception_drops_shared_connection_for_next_call(self):
        """A real (unhandled) failure mid-query must not leave a possibly
        broken connection sitting in _conn for the next unrelated call to
        inherit — drop it and let the next call reconnect fresh."""
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("connection reset by peer")
        conn = _make_conn(cursor)

        with patch("core.template_store._connect", return_value=conn):
            rows = await template_store.list_templates("https://crm.example.com")

        assert rows == []
        assert template_store._conn is None


class TestWarmConnection:
    """server.py calls this on FastAPI startup so the first real
    /template/saved(-related) request doesn't pay the connect cost."""

    def test_establishes_connection_when_none_open(self):
        conn = MagicMock()
        with patch("core.template_store._connect", return_value=conn) as mock_connect:
            template_store.warm_connection()

        mock_connect.assert_called_once()
        assert template_store._conn is conn

    def test_does_nothing_if_already_warm(self):
        existing = MagicMock()
        template_store._conn = existing
        with patch("core.template_store._connect") as mock_connect:
            template_store.warm_connection()

        mock_connect.assert_not_called()
        assert template_store._conn is existing

    def test_never_raises_on_failure(self):
        with patch("core.template_store._connect", side_effect=RuntimeError("down")):
            template_store.warm_connection()  # must not raise
        assert template_store._conn is None


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
