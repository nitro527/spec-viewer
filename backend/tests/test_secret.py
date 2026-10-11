import os

import pytest

from specview import secret
from specview.store import Library


@pytest.fixture
def fake_dpapi(monkeypatch):
    """DPAPI stand-in off Windows: reversible, and the plain key never appears in the stored value."""
    monkeypatch.setattr(secret, "available", lambda: True)
    monkeypatch.setattr(secret, "_crypt", lambda data, protect: bytes(b ^ 0x5A for b in data))


def _raw(lib, key):
    return lib.db.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()[0]


def test_api_keys_are_stored_encrypted(tmp_path, fake_dpapi):
    lib = Library(tmp_path / "library.sqlite")
    lib.set_setting("llm_gemini_api_key", "AIza-secret-1234")
    lib.set_setting("llm_gemini_model", "gemini-x")
    assert _raw(lib, "llm_gemini_api_key").startswith("dpapi:") and "secret" not in _raw(lib, "llm_gemini_api_key")
    assert lib.get_setting("llm_gemini_api_key") == "AIza-secret-1234"
    assert _raw(lib, "llm_gemini_model") == "gemini-x"  # only keys are encrypted


def test_plain_keys_of_earlier_versions_are_encrypted_at_startup(tmp_path, fake_dpapi):
    from fastapi.testclient import TestClient

    from specview.api import create_app

    lib = Library(tmp_path / "library.sqlite")
    with lib.db:
        lib.db.execute("INSERT INTO setting VALUES ('llm_openai_api_key', 'sk-old-5678')")
    lib.close()
    c = TestClient(create_app(tmp_path))
    lib = Library(tmp_path / "library.sqlite")
    assert _raw(lib, "llm_openai_api_key").startswith("dpapi:")
    assert c.get("/api/llm").json()["providers"]["openai"]["key_hint"] == "…5678"


def test_a_key_from_another_user_reads_as_missing(tmp_path, fake_dpapi, monkeypatch):
    lib = Library(tmp_path / "library.sqlite")
    lib.set_setting("llm_gemini_api_key", "AIza-secret-1234")

    def fail(data, protect):
        raise OSError(13, "DPAPI failed")

    monkeypatch.setattr(secret, "_crypt", fail)
    assert lib.get_setting("llm_gemini_api_key") == ""


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_real_dpapi_roundtrip():
    stored = secret.protect("AIza-secret-1234")
    assert stored.startswith("dpapi:") and "secret" not in stored
    assert secret.unprotect(stored) == "AIza-secret-1234"
