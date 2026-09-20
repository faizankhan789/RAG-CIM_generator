"""Persistence for user-uploaded CIM design templates, so a template extracted
by core/template_extractor.py can be saved once and reused from the template
picker instead of re-uploading the file every time.

Stores the exact same dict shape /template/upload already returns (palette,
fonts, headings, layout_notes, cover_override, section_header_override,
allow_brand_override, file_b64, file_ext) as one JSON blob — a saved template
is fed back into generate_cim_html as `custom_template` completely unchanged,
so reuse goes through the identical code path (and identical vision-block
attachment) as a fresh upload.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Optional

import pymysql
import pymysql.cursors

from core.pricing import estimate_price

log = logging.getLogger(__name__)

_DB_CONFIG = {
    "host":     os.getenv("RAG_CIM_DB_HOST", ""),
    "port":     int(os.getenv("RAG_CIM_DB_PORT", "25060")),
    "user":     os.getenv("RAG_CIM_DB_USER", ""),
    "password": os.getenv("RAG_CIM_DB_PASS", ""),
    "database": os.getenv("RAG_CIM_DB_NAME", "rag_cim_db"),
    "autocommit": True,
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
    "connect_timeout": 3,
}

_CREATE_TEMPLATES_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS custom_templates (
    id INT AUTO_INCREMENT PRIMARY KEY,
    crm_url VARCHAR(255) NOT NULL,
    username VARCHAR(100) NOT NULL DEFAULT 'unknown',
    name VARCHAR(255) NOT NULL,
    template_data LONGTEXT NOT NULL,
    created_at DATETIME NOT NULL,
    INDEX idx_crm_url (crm_url)
)"""

_CREATE_AUDIT_LOG_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS template_audit_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    crm_url VARCHAR(255) NOT NULL,
    username VARCHAR(100) NOT NULL DEFAULT 'unknown',
    model VARCHAR(100) NOT NULL,
    input_tokens INT NOT NULL,
    output_tokens INT NOT NULL,
    total_tokens INT NOT NULL,
    estimated_price DECIMAL(10,8) NOT NULL,
    created_at DATETIME NOT NULL,
    INDEX idx_crm_url (crm_url)
)"""

_TABLE_MISSING_ERRNO = 1146  # MySQL "table doesn't exist"


def _connect() -> pymysql.connections.Connection:
    cfg = dict(_DB_CONFIG)
    return pymysql.connect(**cfg, ssl_disabled=False)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# One long-lived connection, reused across calls, instead of paying a fresh TCP+TLS
# handshake to the remote managed MySQL on every single request. Measured at ~1.6s per
# _connect() call from a normal dev network — this is exactly why the saved-templates
# picker/badge felt slow: showCimVerticaModeModal's badge fetch and showCimTemplateModal's
# grid fetch each independently call /template/saved, so a fresh-connection-per-call design
# compounded into a multi-second wait before either one visibly updated. Guarded by a lock
# since core/*.py's DB calls all run in a thread-pool executor (asyncio.get_event_loop().
# run_in_executor) and a pymysql connection/cursor is not safe for concurrent use from
# multiple threads at once.
_conn: Optional[pymysql.connections.Connection] = None
_conn_lock = threading.Lock()


# Connection-level failures only — NOT pymysql.err.ProgrammingError (e.g. 1146 table
# missing), which every write already handles itself as an expected self-provisioning
# case, not a dead connection.
_CONNECTION_ERRORS = (pymysql.err.OperationalError, pymysql.err.InterfaceError)


def _run_db(fn):
    """Run fn(conn) against the shared connection, reconnecting and retrying ONCE if the
    connection turned out to be dead. Deliberately does NOT ping() before every call to
    check liveness first — ping() is itself a full network round trip, measured at ~0.3s
    to the real remote DB here, i.e. the same cost as the query it would be "protecting",
    so pinging before every single call would just double the latency of every call to
    guard against a failure (an idle connection getting dropped) that's rare in practice.
    Optimistic-and-retry is cheaper on average: try the shared connection directly, and
    only pay a reconnect if it actually turns out to be dead.

    Any OTHER exception (including a ProgrammingError the caller doesn't itself recognize
    as the table-missing case) drops the shared connection before re-raising, so a call
    that failed mid-operation never leaves a possibly broken/half-transaction connection
    for the next unrelated caller to inherit."""
    global _conn
    with _conn_lock:
        if _conn is None:
            _conn = _connect()
        try:
            return fn(_conn)
        except _CONNECTION_ERRORS:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = _connect()
            return fn(_conn)
        except Exception:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None
            raise


def warm_connection() -> None:
    """Best-effort: establish the shared connection eagerly (e.g. at server startup, see
    server.py) so the first real request doesn't pay the ~1.5-2s TCP+TLS handshake cost
    that a cold _conn would otherwise incur. Never raises — a warm-up failure just means
    the first real call pays the normal (still self-healing) connect cost instead."""
    global _conn
    with _conn_lock:
        if _conn is None:
            try:
                _conn = _connect()
            except Exception as exc:
                log.warning("template_store warm_connection failed (non-fatal): %s", exc)


def _insert_with_auto_create(create_sql: str, insert_sql: str, params: tuple) -> Optional[int]:
    """INSERT with self-provisioning retry: on MySQL error 1146 ("table
    doesn't exist"), CREATE TABLE IF NOT EXISTS and retry the same INSERT
    once. Shared by every write in this module — including
    core/db_log.py's own cim_generation_log, this file has two tables that
    all follow the same "no manual migration against the shared prod DB"
    pattern, so the retry logic lives in exactly one place rather than being
    copy-pasted per table.

    Runs synchronously — callers wrap this in run_in_executor (see
    save_template / log_template_audit below).
    """
    def _op(conn: pymysql.connections.Connection) -> Optional[int]:
        try:
            with conn.cursor() as cur:
                cur.execute(insert_sql, params)
                return cur.lastrowid
        except pymysql.err.ProgrammingError as exc:
            if exc.args and exc.args[0] == _TABLE_MISSING_ERRNO:
                with conn.cursor() as cur:
                    cur.execute(create_sql)
                    cur.execute(insert_sql, params)
                    return cur.lastrowid
            raise

    return _run_db(_op)


async def save_template(crm_url: str, username: str, name: str, template: dict[str, Any]) -> Optional[int]:
    """UPSERT a saved template keyed on (crm_url, name): if a template with
    the same name already exists for this CRM, its content is refreshed in
    place (UPDATE) instead of creating a duplicate row — re-uploading /
    re-saving the same template no longer piles up copies in the picker
    grid. Returns the row id (new or refreshed), or None on failure.

    Self-provisioning: creates custom_templates on first use rather than
    requiring a manual migration against the shared prod DB — see
    core/db_log.py's cim_generation_log for the sibling table this lives
    next to.
    """
    def _upsert() -> Optional[int]:
        payload = json.dumps(template)

        def _op(conn: pymysql.connections.Connection) -> Optional[int]:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM custom_templates WHERE crm_url=%s AND name=%s",
                        (crm_url, name),
                    )
                    existing = cur.fetchone()
            except pymysql.err.ProgrammingError as exc:
                if exc.args and exc.args[0] == _TABLE_MISSING_ERRNO:
                    with conn.cursor() as cur:
                        cur.execute(_CREATE_TEMPLATES_TABLE_SQL)
                    existing = None
                else:
                    raise

            if existing:
                row_id = existing["id"]
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE custom_templates SET username=%s, template_data=%s, created_at=%s WHERE id=%s",
                        (username, payload, _now(), row_id),
                    )
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO custom_templates (crm_url, username, name, template_data, created_at)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (crm_url, username, name, payload, _now()),
                    )
                    row_id = cur.lastrowid
            return row_id

        try:
            return _run_db(_op)
        except Exception as exc:
            log.warning("template_store save failed: %s", exc)
            return None

    return await asyncio.get_event_loop().run_in_executor(None, _upsert)


async def list_templates(crm_url: str) -> list[dict[str, Any]]:
    """Lightweight list — id + name only, for the picker grid. Never returns
    template_data (can be several MB per row with the attached file)."""
    def _op(conn: pymysql.connections.Connection) -> list[dict[str, Any]]:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name FROM custom_templates WHERE crm_url=%s ORDER BY created_at DESC",
                (crm_url,),
            )
            return cur.fetchall()

    def _query() -> list[dict[str, Any]]:
        try:
            return _run_db(_op)
        except Exception as exc:
            log.warning("template_store list failed: %s", exc)
            return []

    return await asyncio.get_event_loop().run_in_executor(None, _query)


async def get_template(template_id: int, crm_url: str) -> Optional[dict[str, Any]]:
    """Fetch the full saved template dict for reuse. Scoped to crm_url so one
    tenant can never pull another tenant's saved template by guessing an id."""
    def _op(conn: pymysql.connections.Connection) -> Optional[dict[str, Any]]:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT template_data FROM custom_templates WHERE id=%s AND crm_url=%s",
                (template_id, crm_url),
            )
            row = cur.fetchone()
        if not row:
            return None
        return json.loads(row["template_data"])

    def _query() -> Optional[dict[str, Any]]:
        try:
            return _run_db(_op)
        except Exception as exc:
            log.warning("template_store get failed: %s", exc)
            return None

    return await asyncio.get_event_loop().run_in_executor(None, _query)


async def log_template_audit(
    crm_url: str, username: str, model: str, input_tokens: int, output_tokens: int,
) -> Optional[int]:
    """Record the real cost of one audit_template_design() call (core/llm.py).

    Without this, that cost was invisible everywhere: /template/upload runs
    outside any CIM-generation pipeline, so core/llm.py's _add_tokens() is a
    no-op there (its ContextVar is only set inside server.py's
    _run_job_pipeline) — the audit's tokens went uncounted in
    cim_generation_log and unseen in the Grafana cost dashboard. Server.py's
    /template/upload calls reset_token_counters()/get_token_counts() around
    just the audit call to capture its real usage, then logs it here — a
    dedicated table rather than cim_generation_log because an audit has no
    listing_id/listing_name to attach to; it's a template-upload event, not
    a CIM generation.

    Self-provisioning like save_template above. Best-effort — a logging
    failure must never surface to the user or block the upload response.
    """
    def _insert() -> Optional[int]:
        try:
            price = estimate_price(model, input_tokens, output_tokens)
            return _insert_with_auto_create(
                _CREATE_AUDIT_LOG_TABLE_SQL,
                """
                INSERT INTO template_audit_log
                    (crm_url, username, model, input_tokens, output_tokens, total_tokens, estimated_price, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (crm_url, username, model, input_tokens, output_tokens,
                 input_tokens + output_tokens, price, _now()),
            )
        except Exception as exc:
            log.warning("template_store log_template_audit failed: %s", exc)
            return None

    return await asyncio.get_event_loop().run_in_executor(None, _insert)
