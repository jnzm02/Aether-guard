"""
Tests for the listener webhook parsing and alert enrichment schema.
"""
import sys
import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import listener  # noqa: E402
from listener import app  # noqa: E402

# ── Stub out external I/O at import time ──────────────────────────────────────
# fetch_prometheus_snapshot calls http://prometheus:9090 — unavailable in tests.
# fetch_container_logs calls the Docker socket — unavailable in tests.
_EMPTY_SNAPSHOT = {k: None for k, _ in listener._KEY_METRICS}
_STUB_LOGS = ["[unit-test stub: no Docker socket in CI]"]


@pytest.fixture(autouse=True)
def mock_external_io(monkeypatch):
    monkeypatch.setattr(
        listener, "fetch_prometheus_snapshot",
        AsyncMock(return_value=_EMPTY_SNAPSHOT),
    )
    monkeypatch.setattr(
        listener, "fetch_container_logs",
        MagicMock(return_value=_STUB_LOGS),
    )


@pytest.fixture(scope="function")
def client():
    # Clear the queue before every test to avoid inter-test contamination.
    listener.alert_queue.clear()
    return TestClient(app)


def alertmanager_payload(alerts: list | None = None) -> dict:
    """Minimal Alertmanager v4 webhook payload."""
    default_alerts = [
        {
            "status": "firing",
            "labels": {
                "alertname": "SLOErrorBudgetBurnCritical",
                "severity": "critical",
                "service": "target-service",
            },
            "annotations": {"summary": "High error rate"},
            "startsAt": "2024-01-01T00:00:00Z",
            "endsAt":   "0001-01-01T00:00:00Z",
            "fingerprint": "abc123",
        }
    ]
    return {
        "version": "4",
        "groupKey": "{}:{alertname='TestAlert'}",
        "status": "firing",
        "receiver": "listener",
        "groupLabels": {"alertname": "TestAlert"},
        "commonLabels": {"alertname": "TestAlert", "severity": "critical", "service": "target-service"},
        "commonAnnotations": {"summary": "Test alert for unit tests"},
        "externalURL": "http://alertmanager:9093",
        "alerts": alerts if alerts is not None else default_alerts,
    }


# ─────────────────────────────────────────────────────────────────────────────
# /health
# ─────────────────────────────────────────────────────────────────────────────

class TestHealthEndpoint:
    def test_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_status_is_ok(self, client):
        resp = client.get("/health")
        assert resp.json()["status"] == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# POST /webhook  (returns 202 Accepted by design)
# ─────────────────────────────────────────────────────────────────────────────

class TestWebhookEndpoint:
    def test_accepts_valid_payload(self, client):
        resp = client.post("/webhook", json=alertmanager_payload())
        assert resp.status_code == 202

    def test_returns_enqueued_count(self, client):
        payload = alertmanager_payload()
        resp = client.post("/webhook", json=payload)
        data = resp.json()
        assert "enqueued" in data
        assert data["enqueued"] == len(payload["alerts"])

    def test_empty_alerts_list_accepted(self, client):
        resp = client.post("/webhook", json=alertmanager_payload(alerts=[]))
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "accepted"
        assert data.get("enqueued", 0) == 0

    def test_multiple_alerts_in_one_payload(self, client):
        alerts = [
            {
                "status": "firing",
                "labels": {"alertname": f"Alert{i}", "severity": "warning"},
                "annotations": {},
                "startsAt": "2024-01-01T00:00:00Z",
                "endsAt":   "0001-01-01T00:00:00Z",
                "fingerprint": f"fp{i}",
            }
            for i in range(3)
        ]
        resp = client.post("/webhook", json=alertmanager_payload(alerts=alerts))
        assert resp.status_code == 202
        assert resp.json()["enqueued"] == 3

    def test_enriched_alert_has_metrics_snapshot(self, client):
        client.post("/webhook", json=alertmanager_payload())
        alerts = client.get("/alerts").json()["alerts"]
        assert len(alerts) == 1
        assert "metrics_snapshot" in alerts[0]

    def test_enriched_alert_has_log_tail(self, client):
        client.post("/webhook", json=alertmanager_payload())
        alerts = client.get("/alerts").json()["alerts"]
        assert "log_tail" in alerts[0]
        assert isinstance(alerts[0]["log_tail"], list)


# ─────────────────────────────────────────────────────────────────────────────
# GET /alerts
# ─────────────────────────────────────────────────────────────────────────────

class TestAlertsEndpoint:
    def test_returns_200(self, client):
        resp = client.get("/alerts")
        assert resp.status_code == 200

    def test_response_has_alerts_list(self, client):
        resp = client.get("/alerts")
        data = resp.json()
        assert "alerts" in data
        assert isinstance(data["alerts"], list)

    def test_alert_has_required_fields(self, client):
        client.post("/webhook", json=alertmanager_payload())
        alerts = client.get("/alerts").json()["alerts"]
        assert len(alerts) == 1
        for field in ("id", "labels", "status", "received_at"):
            assert field in alerts[0], f"missing field {field!r} in alert"

    def test_unprocessed_only_filter(self, client):
        client.post("/webhook", json=alertmanager_payload())
        resp = client.get("/alerts?unprocessed_only=true")
        assert resp.status_code == 200
        for alert in resp.json()["alerts"]:
            assert alert.get("processed_by_ai") is not True


# ─────────────────────────────────────────────────────────────────────────────
# POST /alerts/{id}/ack
# ─────────────────────────────────────────────────────────────────────────────

class TestAckEndpoint:
    def test_ack_nonexistent_id_returns_404(self, client):
        resp = client.post("/alerts/nonexistent-id/ack", json={"result": "ok"})
        assert resp.status_code == 404

    def test_ack_marks_alert_as_processed(self, client):
        client.post("/webhook", json=alertmanager_payload())
        alerts = client.get("/alerts?unprocessed_only=true").json()["alerts"]
        assert len(alerts) == 1

        alert_id = alerts[0]["id"]
        ack_resp = client.post(f"/alerts/{alert_id}/ack", json={"result": "acknowledged"})
        assert ack_resp.status_code == 200

        after = client.get("/alerts?unprocessed_only=true").json()["alerts"]
        assert all(a["id"] != alert_id for a in after)

