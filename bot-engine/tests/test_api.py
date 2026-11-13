"""Tests de la couche HTTP : transports, erreurs, configuration, sondes."""

from __future__ import annotations

import base64
import io
import json

import pytest
from fastapi.testclient import TestClient

from engine.api import main as api
from synthetic import build_level


@pytest.fixture()
def client() -> TestClient:
    return TestClient(api.app)


@pytest.fixture()
def level():
    return build_level(rows=8, cols=8, colors=5, seed=77)


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["version"]
    assert isinstance(payload["templates"], list)


def test_root_is_a_health_probe(client: TestClient) -> None:
    assert client.get("/").status_code == 200


def test_config_is_readable(client: TestClient) -> None:
    response = client.get("/config")
    assert response.status_code == 200
    payload = response.json()
    assert payload["vision"]["analysis_width"] > 0
    assert payload["solver"]["weights"]["block"] > 0


def test_config_write_is_refused_without_a_key(client: TestClient) -> None:
    response = client.post("/config", json={"solver": {"mode": "fast"}})
    assert response.status_code == 403
    assert response.json()["ok"] is False


def test_config_write_is_refused_with_a_wrong_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "ADMIN_KEY", "secret")
    response = client.post(
        "/config", json={"solver": {"mode": "fast"}}, headers={"X-Engine-Key": "autre"}
    )
    assert response.status_code == 403


def test_config_write_succeeds_with_the_right_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "ADMIN_KEY", "secret")
    original = client.get("/config").json()
    response = client.post(
        "/config",
        json={"solver": {"mode": "fast", "goal_color": 2}},
        headers={"X-Engine-Key": "secret"},
    )
    assert response.status_code == 200
    assert response.json()["solver"]["goal_color"] == 2

    # Remise en état pour ne pas polluer les tests suivants.
    client.post("/config", json=original, headers={"X-Engine-Key": "secret"})


def test_analyze_multipart(client: TestClient, level) -> None:
    payload = level.encode(quality=85)
    response = client.post(
        "/analyze",
        files={"image": ("capture.jpg", io.BytesIO(payload), "image/jpeg")},
        data={"session_id": "test", "display_width": "1080", "display_height": "2400"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["grid"]["rows"] == 8
    assert body["grid"]["cols"] == 8
    assert "from" in body["move"], "le geste doit exposer la clé 'from'"
    assert "to" in body["move"]
    assert 0.0 <= body["move"]["from"]["x"] <= 1.0
    assert body["timing"]["total_ms"] >= 0
    assert body["engine_version"]


def test_analyze_multipart_accepts_config_overrides(client: TestClient, level) -> None:
    payload = level.encode(quality=85)
    response = client.post(
        "/analyze",
        files={"image": ("capture.jpg", io.BytesIO(payload), "image/jpeg")},
        data={"config": json.dumps({"solver": {"mode": "fast"}})},
    )
    assert response.status_code == 200
    assert response.json()["solver_mode"] == "fast"


def test_analyze_json_base64(client: TestClient, level) -> None:
    payload = level.encode(quality=85)
    response = client.post(
        "/analyze",
        json={"image_base64": base64.b64encode(payload).decode("ascii"), "session_id": "j"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["move"] is not None


def test_analyze_rejects_a_missing_image(client: TestClient) -> None:
    response = client.post("/analyze", data={"session_id": "sans-image"})
    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_analyze_rejects_a_corrupted_image(client: TestClient) -> None:
    response = client.post(
        "/analyze",
        files={"image": ("capture.jpg", io.BytesIO(b"0" * 64), "image/jpeg")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["move"] is None


def test_analyze_rejects_invalid_base64_payload(client: TestClient) -> None:
    response = client.post("/analyze", json={"pas_image": 1})
    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_analyze_debug_flag_returns_an_image(client: TestClient, level) -> None:
    payload = level.encode(quality=85)
    response = client.post(
        "/analyze",
        files={"image": ("capture.jpg", io.BytesIO(payload), "image/jpeg")},
        data={"debug": "true"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["debug_image"]
    assert base64.b64decode(body["debug_image"])[:4] == b"\x89PNG"


def test_analyze_reports_no_board(client: TestClient) -> None:
    import cv2
    import numpy as np

    plain = np.full((1600, 720, 3), 40, dtype=np.uint8)
    ok, buffer = cv2.imencode(".jpg", plain)
    assert ok
    response = client.post(
        "/analyze",
        files={"image": ("plain.jpg", io.BytesIO(buffer.tobytes()), "image/jpeg")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["move"] is None


def test_cors_headers_are_present(client: TestClient, level) -> None:
    payload = level.encode(quality=85)
    response = client.post(
        "/analyze",
        files={"image": ("capture.jpg", io.BytesIO(payload), "image/jpeg")},
        headers={"Origin": "http://exemple.local"},
    )
    assert response.headers.get("access-control-allow-origin") == "*"


def test_openapi_schema_is_served(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert "/analyze" in response.json()["paths"]
