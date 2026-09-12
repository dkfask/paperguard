"""Generic document platform rollout flag tests."""

from fastapi.testclient import TestClient

from app.core.feature_flags import general_document_platform_enabled
from app.main import app


def test_generic_document_routes_can_be_disabled_without_disabling_thesis_routes(monkeypatch) -> None:
    monkeypatch.setenv("PAPERGUARD_FEATURE_GENERAL_DOCUMENT_PLATFORM", "false")
    assert general_document_platform_enabled() is False
    client = TestClient(app)

    assert client.get("/api/document-packages").status_code == 404
    assert client.get("/api/document-projects").status_code == 404
    assert client.get("/api/templates").status_code == 200

    monkeypatch.setenv("PAPERGUARD_FEATURE_GENERAL_DOCUMENT_PLATFORM", "true")
    assert general_document_platform_enabled() is True
    assert client.get("/api/document-packages").status_code == 401
