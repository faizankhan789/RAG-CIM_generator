"""
Manual test harness for services/processors/docx.py.

Usage:
    python scripts/test_docx.py path/to/file1.docx path/to/file2.docx ...
    python scripts/test_docx.py ~/Documents/*.docx

Shows per-file:
  - is_structured_docx result
  - segment count by type
  - full segment dump (truncated text)
  - peak memory consumed (tracemalloc)
  - wall-clock time for structure detection and splitting

Ends with a side-by-side comparison table.

Run from the cim-generator root so the `services` package is importable.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import tracemalloc
from collections import Counter
from pathlib import Path

# Make imports work when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.processors.docx import (  # noqa: E402
    DocSegment,
    SegmentType,
    is_structured_docx,
    split_docx,
)

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _truncate(text: str, limit: int = 100) -> str:
    """One-line preview of a segment body."""
    flat = text.replace("\n", " ⏎ ").replace("\t", " ⇥ ")
    if len(flat) <= limit:
        return flat
    return flat[:limit - 1] + "…"


# ---------------------------------------------------------------------------
# Per-file runner
# ---------------------------------------------------------------------------

def run_file(path: str, summary_only: bool, max_segments: int) -> dict:
    """Process one file and return its row of summary stats."""
    abs_path = os.path.abspath(path)
    print("=" * 88)
    print(f"FILE: {abs_path}")

    if not os.path.isfile(abs_path):
        print("  !! File not found — skipping")
        return {"path": abs_path, "ok": False}

    size = os.path.getsize(abs_path)
    print(f"  size: {_human_bytes(size)}")

    # ── is_structured_docx ──────────────────────────────────────────
    tracemalloc.start()
    t0 = time.perf_counter()
    try:
        structured = is_structured_docx(abs_path)
    except Exception as exc:
        print(f"  !! is_structured_docx raised: {exc}")
        tracemalloc.stop()
        return {"path": abs_path, "ok": False}
    detect_time = time.perf_counter() - t0
    _, detect_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print(f"  is_structured: {structured}")
    print(f"  detect took:   {detect_time * 1000:.1f} ms")
    print(f"  detect peak:   {_human_bytes(detect_peak)}")

    # ── split_docx ──────────────────────────────────────────────────
    segments: list[DocSegment] = []
    tracemalloc.start()
    t0 = time.perf_counter()
    try:
        for seg in split_docx(abs_path):
            segments.append(seg)
    except Exception as exc:
        print(f"  !! split_docx raised: {exc}")
        tracemalloc.stop()
        return {"path": abs_path, "ok": False}
    split_time = time.perf_counter() - t0
    _, split_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print(f"  split took:    {split_time * 1000:.1f} ms")
    print(f"  split peak:    {_human_bytes(split_peak)}")

    # ── Counts by segment type ──────────────────────────────────────
    counts: Counter[str] = Counter(seg.type.value for seg in segments)
    total = sum(counts.values())
    print(f"  segments:      {total}")
    for t in (
        SegmentType.HEADING,
        SegmentType.KEY_VALUE,
        SegmentType.LIST,
        SegmentType.TABLE,
        SegmentType.PARAGRAPH,
    ):
        print(f"    {t.value:10s} {counts.get(t.value, 0):5d}")

    # ── Segment dump ────────────────────────────────────────────────
    if not summary_only and segments:
        shown = segments if max_segments <= 0 else segments[:max_segments]
        print(f"  segments dump (showing {len(shown)} of {total}):")
        for i, seg in enumerate(shown):
            h = seg.heading_level if seg.heading_level is not None else "-"
            print(
                f"    [{i:4d}] {seg.type.value:10s}"
                f" h={h!s:3s} {_truncate(seg.text)}"
            )
        if len(shown) < total:
            print(f"    … ({total - len(shown)} more)")

    print()
    return {
        "path": abs_path,
        "ok": True,
        "size": size,
        "structured": structured,
        "segments": total,
        "headings": counts.get(SegmentType.HEADING.value, 0),
        "kv": counts.get(SegmentType.KEY_VALUE.value, 0),
        "lists": counts.get(SegmentType.LIST.value, 0),
        "tables": counts.get(SegmentType.TABLE.value, 0),
        "paragraphs": counts.get(SegmentType.PARAGRAPH.value, 0),
        "detect_ms": detect_time * 1000,
        "split_ms": split_time * 1000,
        "detect_peak": detect_peak,
        "split_peak": split_peak,
    }


# ---------------------------------------------------------------------------
# Side-by-side comparison table
# ---------------------------------------------------------------------------

def print_comparison(rows: list[dict]) -> None:
    ok_rows = [r for r in rows if r.get("ok")]
    if not ok_rows:
        print("No files processed successfully — nothing to compare.")
        return

    print("=" * 88)
    print("COMPARISON")
    print("=" * 88)

    header = (
        f"{'file':<30s}"
        f" {'size':>10s}"
        f" {'struct':>6s}"
        f" {'segs':>5s}"
        f" {'hdr':>4s}"
        f" {'kv':>4s}"
        f" {'lst':>4s}"
        f" {'tbl':>4s}"
        f" {'para':>5s}"
        f" {'split_ms':>9s}"
        f" {'peak':>8s}"
    )
    print(header)
    print("-" * len(header))

    for r in ok_rows:
        name = os.path.basename(r["path"])
        if len(name) > 30:
            name = name[:27] + "..."
        print(
            f"{name:<30s}"
            f" {_human_bytes(r['size']):>10s}"
            f" {'yes' if r['structured'] else 'no':>6s}"
            f" {r['segments']:>5d}"
            f" {r['headings']:>4d}"
            f" {r['kv']:>4d}"
            f" {r['lists']:>4d}"
            f" {r['tables']:>4d}"
            f" {r['paragraphs']:>5d}"
            f" {r['split_ms']:>9.1f}"
            f" {_human_bytes(r['split_peak']):>8s}"
        )
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Test docx.py against real files")
    ap.add_argument("files", nargs="+", help="DOCX files to test")
    ap.add_argument(
        "--summary-only",
        action="store_true",
        help="Skip the per-segment dump; show counts only",
    )
    ap.add_argument(
        "--max-segments",
        type=int,
        default=50,
        help="Max segments to dump per file (0 = all, default: 50)",
    )
    args = ap.parse_args()

    rows: list[dict] = []
    for path in args.files:
        rows.append(run_file(path, args.summary_only, args.max_segments))

    print_comparison(rows)
    return 0 if all(r.get("ok") for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
