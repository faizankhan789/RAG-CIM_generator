"""Tests for core.llm's template-selection logic (no Claude API calls needed)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()  # core.llm reads CIM_MODEL at import time, same as server.py does

from core.llm import _select_template
from core.templates import TEMPLATES


def test_custom_template_wins_over_template_id():
    custom = {"id": "custom-upload", "name": "My Upload"}
    assert _select_template("classic", custom) == custom


def test_falls_back_to_template_id_when_no_custom():
    assert _select_template("minimalist", None) == TEMPLATES["minimalist"]


def test_falls_back_to_classic_on_unknown_template_id():
    assert _select_template("nonsense", None) == TEMPLATES["classic"]
