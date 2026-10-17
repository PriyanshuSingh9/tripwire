"""Case review persistence: creation from scored IDs, decisions, history."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from helpers import FakeScoreRepository, assert_no_labels

from api.app import create_app, get_score_repository


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "nested" / "cases.db"
    monkeypatch.setenv("TRACEAML_CASE_DB_PATH", str(path))
    return path


@pytest.fixture
def client(db_path: Path) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_score_repository] = lambda: FakeScoreRepository()
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def open_case(client: TestClient, transaction_id: str = "tx_1") -> dict[str, object]:
    response = client.post(
        "/api/cases",
        json={"transaction_id": transaction_id, "opened_by": "analyst-a", "notes": "queue"},
    )
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


def test_list_starts_empty_without_seed_data(client: TestClient, db_path: Path) -> None:
    response = client.get("/api/cases")
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}
    assert db_path.is_file()


def test_create_case_from_scored_transaction(client: TestClient) -> None:
    body = open_case(client)
    assert body["transaction_id"] == "tx_1"
    assert body["status"] == "suspected"
    assert body["risk_score"] == 0.9
    assert body["opened_by"] == "analyst-a"
    history = body["history"]
    assert isinstance(history, list) and len(history) == 1
    assert history[0]["action"] == "open"
    assert history[0]["reviewer_id"] == "analyst-a"
    assert_no_labels(body)


def test_create_case_requires_scored_transaction(client: TestClient) -> None:
    response = client.post(
        "/api/cases", json={"transaction_id": "tx_unknown", "opened_by": "analyst-a"}
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "transaction not scored"}
    assert client.get("/api/cases").json()["total"] == 0


def test_duplicate_active_case_is_conflict(client: TestClient) -> None:
    open_case(client)
    response = client.post("/api/cases", json={"transaction_id": "tx_1", "opened_by": "b"})
    assert response.status_code == 409


def test_confirm_records_provenance_and_is_terminal(client: TestClient) -> None:
    case_id = open_case(client)["case_id"]
    response = client.post(
        f"/api/cases/{case_id}/review",
        json={
            "action": "confirm",
            "reviewer_id": "analyst-b",
            "pattern_type": "fan_out",
            "notes": "rapid transfers",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "confirmed"
    assert [entry["action"] for entry in body["history"]] == ["open", "confirm"]
    confirm = body["history"][1]
    assert confirm["reviewer_id"] == "analyst-b"
    assert confirm["pattern_type"] == "fan_out"
    assert confirm["notes"] == "rapid transfers"
    again = client.post(
        f"/api/cases/{case_id}/review", json={"action": "dismiss", "reviewer_id": "c"}
    )
    assert again.status_code == 409
    assert client.get(f"/api/cases/{case_id}").json()["history"] == body["history"]


def test_dismissal_preserves_history_and_allows_new_case(client: TestClient) -> None:
    first_id = open_case(client)["case_id"]
    dismissed = client.post(
        f"/api/cases/{first_id}/review",
        json={"action": "dismiss", "reviewer_id": "analyst-b", "notes": "benign payroll"},
    )
    assert dismissed.status_code == 200
    assert dismissed.json()["status"] == "dismissed"
    second_id = open_case(client)["case_id"]
    assert second_id != first_id

    listing = client.get("/api/cases").json()
    assert listing["total"] == 2
    by_id = {item["case_id"]: item for item in listing["items"]}
    assert [entry["action"] for entry in by_id[first_id]["history"]] == ["open", "dismiss"]
    assert by_id[first_id]["history"][1]["notes"] == "benign payroll"
    dismissed_only = client.get("/api/cases?status=dismissed").json()
    assert [item["case_id"] for item in dismissed_only["items"]] == [first_id]


def test_history_is_append_only_in_storage(client: TestClient, db_path: Path) -> None:
    open_case(client)
    connection = sqlite3.connect(db_path)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute("UPDATE case_reviews SET notes = 'edited'")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute("DELETE FROM case_reviews")
        with pytest.raises(sqlite3.DatabaseError, match="never deleted"):
            connection.execute("DELETE FROM cases")
    finally:
        connection.close()


def test_unknown_case_and_bad_requests(client: TestClient) -> None:
    assert client.get("/api/cases/case-missing").status_code == 404
    missing = client.post(
        "/api/cases/case-missing/review", json={"action": "confirm", "reviewer_id": "a"}
    )
    assert missing.status_code == 404
    assert missing.json() == {"detail": "case not found"}
    case_id = open_case(client)["case_id"]
    invalid = [
        {"action": "promote", "reviewer_id": "a"},
        {"action": "confirm", "reviewer_id": ""},
        {"action": "confirm", "reviewer_id": "a", "is_laundering": 1},
    ]
    for payload in invalid:
        response = client.post(f"/api/cases/{case_id}/review", json=payload)
        assert response.status_code == 422
    assert client.get("/api/cases?limit=500").status_code == 422
    assert client.get("/api/cases?status=open").status_code == 422
