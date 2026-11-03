"""Tests for main.py. Run from the project folder with:  pytest -v

Deps: pip install pytest httpx   (plus fastapi and sqlmodel from main.py)
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from main import app, get_session


# ---------- Fixtures ----------
@pytest.fixture(name="session")
def session_fixture():
    # In-memory DB, fresh for every test. StaticPool keeps one connection alive
    # so the tables don't vanish between requests.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session):
    # Swap the real DB dependency for the test session.
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()  # don't leak overrides into other tests


# ---------- Helpers ----------
def make_team(client: TestClient, name="Club America", sport="soccer", league="Liga MX") -> dict:
    response = client.post("/teams", json={"name": name, "sport": sport, "league": league})
    assert response.status_code == 201
    return response.json()


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def match_payload(kickoff: datetime, opponent="Chivas") -> dict:
    return {
        "opponent": opponent,
        "is_home": True,
        "competition": "Liga MX",
        "kickoff_utc": iso(kickoff),
    }

def make_match(client: TestClient, team_id: int, kickoff: datetime, opponent="Chivas") -> dict:
    response = client.post(f"/teams/{team_id}/matches", json=match_payload(kickoff, opponent))
    assert response.status_code == 201
    return response.json()

# ---------- Teams ----------
def test_create_and_list_teams(client: TestClient):
    team = make_team(client)
    assert team["id"] is not None
    assert team["name"] == "Club America"

    response = client.get("/teams")
    assert response.status_code == 200
    assert [t["name"] for t in response.json()] == ["Club America"]


def test_team_league_is_optional(client: TestClient):
    response = client.post("/teams", json={"name": "Mexico (W)", "sport": "soccer"})
    assert response.status_code == 201
    assert response.json()["league"] is None


def test_create_team_missing_field_is_422(client: TestClient):
    response = client.post("/teams", json={"name": "No Sport FC"})
    assert response.status_code == 422


def test_delete_team_removes_its_matches(client: TestClient):
    team = make_team(client)
    future = datetime.now(timezone.utc) + timedelta(days=2)
    client.post(f"/teams/{team['id']}/matches", json=match_payload(future))

    assert client.delete(f"/teams/{team['id']}").status_code == 204
    assert client.get("/teams").json() == []
    assert client.get("/matches/upcoming").json() == []


def test_delete_missing_team_is_404(client: TestClient):
    assert client.delete("/teams/999").status_code == 404


# ---------- Matches ----------
def test_add_match_converts_to_requested_timezone(client: TestClient):
    team = make_team(client)
    kickoff = datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc)

    response = client.post(
        f"/teams/{team['id']}/matches",
        params={"tz": "America/Mexico_City"},
        json=match_payload(kickoff),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["team_id"] == team["id"]
    # Mexico City is UTC-6 in November, so 20:00 UTC -> 14:00 local.
    assert body["kickoff_local"].startswith("2026-11-02T14:00:00")


def test_add_match_without_timezone_offset_is_rejected(client: TestClient):
    team = make_team(client)
    payload = match_payload(datetime.now(timezone.utc) + timedelta(days=1))
    payload["kickoff_utc"] = "2026-11-02T20:00:00"  # no Z / offset

    response = client.post(f"/teams/{team['id']}/matches", json=payload)
    assert response.status_code == 422


def test_add_match_for_missing_team_is_404(client: TestClient):
    future = datetime.now(timezone.utc) + timedelta(days=1)
    response = client.post("/teams/999/matches", json=match_payload(future))
    assert response.status_code == 404


def test_unknown_timezone_is_400(client: TestClient):
    response = client.get("/matches/upcoming", params={"tz": "Mars/Olympus_Mons"})
    assert response.status_code == 400

def test_add_match_for_duplicate_match_is_rejected(client: TestClient):
    team = make_team(client)
    kickoff = datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc)
    make_match(client, team["id"], kickoff)

    response = client.post(
        f"/teams/{team['id']}/matches",
        params={"tz": "America/Mexico_City"},
        json=match_payload(kickoff),
    )

    assert response.status_code == 409

def test_update_match_for_missing_match_is_404(client: TestClient):
    team = make_team(client)
    kickoff = datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc)

    response = client.patch(
        f"/teams/{team['id']}/matches/999",
        params={"tz": "America/Mexico_City"},
        json={"kickoff_utc": iso(kickoff)},
    )
    
    assert response.status_code == 404

def test_update_match_for_missing_fields_is_422(client: TestClient):
    team = make_team(client)
    kickoff = datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc)
    match = make_match(client, team["id"], kickoff)

    response = client.patch(
        f"/teams/{match['team_id']}/matches/{match['id']}",
        json={"opponent": None},
    )

    assert response.status_code == 422

    response = client.patch(
        f"/teams/{match['team_id']}/matches/{match['id']}",
        json={"kickoff_utc": "2026-11-02T20:00:00"}
    )

    assert response.status_code == 422

def test_update_match_for_existing_match_is_409(client: TestClient):
    team = make_team(client)
    first_kickoff = datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc)
    second_kickoff = datetime(2026, 11, 9, 20, 0, tzinfo=timezone.utc)
    make_match(client, team["id"], first_kickoff)
    second = make_match(client, team["id"], second_kickoff)
    
    response = client.patch(
        f"/teams/{team['id']}/matches/{second['id']}",
        json={"kickoff_utc": iso(first_kickoff)},
    )

    assert response.status_code == 409

def test_update_match_updates(client: TestClient):
    team = make_team(client)
    first_kickoff = datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc)
    match = make_match(client, team["id"], first_kickoff)

    second_kickoff = datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc)
    response = client.patch(
        f"/teams/{team['id']}/matches/{match['id']}",
        params={"tz": "America/Mexico_City"},
        json={"kickoff_utc": iso(second_kickoff)},
    )

    assert response.status_code == 200

    update = response.json()
    assert update["id"] == match["id"]
    assert update["team_id"] == team["id"]
    assert update["kickoff_local"].startswith("2026-11-02T08:00:00")
    

# ---------- Upcoming views ----------
def test_team_upcoming_excludes_past_and_sorts_by_kickoff(client: TestClient):
    team = make_team(client)
    now = datetime.now(timezone.utc)
    for opponent, delta in [("Past FC", timedelta(days=-1)),
                            ("Later FC", timedelta(days=5)),
                            ("Sooner FC", timedelta(days=1))]:
        client.post(f"/teams/{team['id']}/matches", json=match_payload(now + delta, opponent))

    response = client.get(f"/teams/{team['id']}/matches/upcoming")
    assert response.status_code == 200
    assert [m["opponent"] for m in response.json()] == ["Sooner FC", "Later FC"]


def test_upcoming_respects_days_window(client: TestClient):
    team = make_team(client)
    now = datetime.now(timezone.utc)
    client.post(f"/teams/{team['id']}/matches", json=match_payload(now + timedelta(days=2), "Near FC"))
    client.post(f"/teams/{team['id']}/matches", json=match_payload(now + timedelta(days=20), "Far FC"))

    response = client.get("/matches/upcoming", params={"days": 7})
    assert [m["opponent"] for m in response.json()] == ["Near FC"]


def test_all_upcoming_merges_teams_in_time_order(client: TestClient):
    a = make_team(client, name="Team A")
    b = make_team(client, name="Team B")
    now = datetime.now(timezone.utc)
    client.post(f"/teams/{a['id']}/matches", json=match_payload(now + timedelta(days=3), "A-opp"))
    client.post(f"/teams/{b['id']}/matches", json=match_payload(now + timedelta(days=1), "B-opp"))

    response = client.get("/matches/upcoming")
    assert [m["opponent"] for m in response.json()] == ["B-opp", "A-opp"]


def test_team_upcoming_for_missing_team_is_404(client: TestClient):
    assert client.get("/teams/999/matches/upcoming").status_code == 404
