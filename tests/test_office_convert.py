"""Tests for core.office_convert — legacy .doc/.ppt -> .docx/.pptx conversion
via headless LibreOffice. Mocks subprocess.run entirely; no real soffice
invocation (this suite must pass even on a machine without LibreOffice
installed). A real end-to-end round-trip is exercised manually/in CI with
soffice actually present, not here.
"""

from __future__ import annotations

import shutil
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from core.office_convert import LegacyConversionError, convert_legacy

# Real legacy .doc/.ppt bytes start with the OLE2 signature — convert_legacy
# rejects anything else before ever invoking soffice.
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _mock_subprocess_writes_output(output_bytes: bytes, returncode: int = 0):
    """Return a subprocess.run replacement that, instead of actually
    invoking soffice, writes output_bytes to the expected --outdir path so
    convert_legacy's file-read afterward finds real content."""
    def _run(cmd, capture_output, timeout):
        # cmd = [..., "--convert-to", target_ext, "--outdir", tmpdir, src_path]
        outdir = cmd[cmd.index("--outdir") + 1]
        src_path = cmd[-1]
        target_ext = cmd[cmd.index("--convert-to") + 1]
        import os
        stem = os.path.splitext(os.path.basename(src_path))[0]
        out_path = os.path.join(outdir, f"{stem}.{target_ext}")
        if returncode == 0:
            with open(out_path, "wb") as f:
                f.write(output_bytes)
        result = MagicMock()
        result.returncode = returncode
        result.stderr = b"" if returncode == 0 else b"conversion error detail"
        return result
    return _run


def test_converts_doc_to_docx():
    with patch("subprocess.run", side_effect=_mock_subprocess_writes_output(b"fake docx bytes")):
        converted, new_ext = convert_legacy(OLE + b"fake legacy doc bytes", "doc")
    assert converted == b"fake docx bytes"
    assert new_ext == "docx"


def test_converts_ppt_to_pptx():
    with patch("subprocess.run", side_effect=_mock_subprocess_writes_output(b"fake pptx bytes")):
        converted, new_ext = convert_legacy(OLE + b"fake legacy ppt bytes", "ppt")
    assert converted == b"fake pptx bytes"
    assert new_ext == "pptx"


def test_rejects_unconfigured_extension():
    with pytest.raises(LegacyConversionError):
        convert_legacy(b"whatever", "xlsx")


def test_raises_when_soffice_not_installed():
    with patch("subprocess.run", side_effect=FileNotFoundError("no such file: soffice")):
        with pytest.raises(LegacyConversionError, match="not installed"):
            convert_legacy(OLE + b"fake doc bytes", "doc")


def test_raises_on_timeout():
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="soffice", timeout=60)):
        with pytest.raises(LegacyConversionError, match="timed out"):
            convert_legacy(OLE + b"fake doc bytes", "doc")


def test_raises_on_nonzero_exit():
    with patch("subprocess.run", side_effect=_mock_subprocess_writes_output(b"", returncode=1)):
        with pytest.raises(LegacyConversionError, match="exit 1"):
            convert_legacy(OLE + b"fake doc bytes", "doc")


def test_raises_when_output_file_missing_despite_success_exit():
    """A defensive case: soffice reports success (exit 0) but the expected
    output file isn't where it should be — must still raise, not silently
    return empty/garbage bytes."""
    def _run(cmd, capture_output, timeout):
        result = MagicMock()
        result.returncode = 0
        result.stderr = b""
        return result  # never writes the output file

    with patch("subprocess.run", side_effect=_run):
        with pytest.raises(LegacyConversionError):
            convert_legacy(OLE + b"fake doc bytes", "doc")


def test_uses_a_fresh_profile_dir_per_call():
    """Concurrency safety: each call must pass its own -env:UserInstallation
    profile path so two concurrent conversions never share/lock the same
    LibreOffice profile directory."""
    captured_cmds = []

    def _run(cmd, capture_output, timeout):
        captured_cmds.append(cmd)
        return _mock_subprocess_writes_output(b"x")(cmd, capture_output, timeout)

    with patch("subprocess.run", side_effect=_run):
        convert_legacy(OLE + b"fake doc bytes 1", "doc")
        convert_legacy(OLE + b"fake doc bytes 2", "doc")

    profile_args = [next(a for a in cmd if a.startswith("-env:UserInstallation=")) for cmd in captured_cmds]
    assert profile_args[0] != profile_args[1]


@pytest.mark.parametrize("junk", [b"not a real ole package", b"", b"\x00\x01random binary"])
@pytest.mark.parametrize("ext", ["doc", "ppt"])
def test_rejects_non_office_bytes_without_calling_soffice(junk, ext):
    """Real soffice never rejects a junk .doc (Writer recovers it as plain
    text), so the signature check must stop it before soffice runs at all."""
    with patch("subprocess.run") as mock_run:
        with pytest.raises(LegacyConversionError, match="Not a real legacy"):
            convert_legacy(junk, ext)
    mock_run.assert_not_called()


@pytest.mark.parametrize("ext, target", [("doc", "docx"), ("ppt", "pptx")])
def test_modern_file_renamed_to_legacy_ext_passes_through_unconverted(ext, target):
    zip_bytes = b"PK\x03\x04 rest of a real ooxml zip package"
    with patch("subprocess.run") as mock_run:
        converted, new_ext = convert_legacy(zip_bytes, ext)
    mock_run.assert_not_called()
    assert converted == zip_bytes
    assert new_ext == target


@pytest.mark.skipif(shutil.which("soffice") is None, reason="requires a real soffice install")
@pytest.mark.parametrize("ext, make", [("doc", "make_text_docx"), ("ppt", "make_text_pptx")])
def test_real_soffice_round_trip(tmp_path, ext, make):
    """Real end-to-end: build a genuine OLE2 .doc/.ppt with soffice itself,
    then convert it back with convert_legacy and open it with python-docx/pptx."""
    import io
    from tests import template_helpers
    modern_ext = {"doc": "docx", "ppt": "pptx"}[ext]
    src = tmp_path / f"src.{modern_ext}"
    src.write_bytes(getattr(template_helpers, make)())
    subprocess.run(
        ["soffice", "--headless", "--norestore",
         f"-env:UserInstallation=file://{tmp_path / 'profile'}",
         "--convert-to", ext, "--outdir", str(tmp_path), str(src)],
        capture_output=True, timeout=120, check=True,
    )
    legacy = (tmp_path / f"src.{ext}").read_bytes()
    assert legacy.startswith(OLE)

    converted, new_ext = convert_legacy(legacy, ext)
    assert new_ext == modern_ext
    if ext == "doc":
        from docx import Document
        assert Document(io.BytesIO(converted)).paragraphs
    else:
        from pptx import Presentation
        assert len(Presentation(io.BytesIO(converted)).slides) >= 1
