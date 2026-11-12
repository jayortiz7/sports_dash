"""Tests for espn.py. They use a trimmed copy of a real ESPN event, so they
never touch the network.   Run with:  pytest -v
"""
from datetime import datetime, timezone

import pytest

from espn import parse_espn_date, parse_event, find_team

FAKE_TEAMS = (
    {"id": "9", "displayName": "Green Bay Packers", "shortDisplayName": "Packers",
     "nickname": "Packers", "abbreviation": "GB"},
    {"id": "17", "displayName": "New England Patriots", "shortDisplayName": "Patriots",
     "nickname": "Patriots", "abbreviation": "NE"},
)

def test_find_team_matches_any_name_style(monkeypatch):
    monkeypatch.setattr("espn._fetch_teams", lambda league: FAKE_TEAMS)
    for name in ("Green Bay Packers", "packers", " GB "):
        assert [t["id"] for t in find_team("football/nfl", name)] == ["9"]

def test_find_team_returns_empty_for_unknown_name(monkeypatch):
    monkeypatch.setattr("espn._fetch_teams", lambda league: FAKE_TEAMS)
    assert find_team("football/nfl", "Seattle Mariners") == []


PACKERS_ID = "9"
PATRIOTS_ID = "17"

SAMPLE_EVENT = {
    "id": "401873049",
    "date": "2026-11-08T21:25Z",
    "name": "Green Bay Packers at New England Patriots",
    "competitions": [
        {
            "competitors": [
                {
                    "homeAway": "home",
                    "team": {"id": "17", "displayName": "New England Patriots"},
                },
                {
                    "homeAway": "away",
                    "team": {"id": "9", "displayName": "Green Bay Packers"},
                },
            ]
        }
    ],
}


def test_parse_event_from_away_team_perspective():
    result = parse_event(SAMPLE_EVENT, PACKERS_ID)
    assert result == {
        "external_id": "401873049",
        "opponent": "New England Patriots",
        "is_home": False,
        "competition": "NFL",
        "kickoff_utc": datetime(2026, 11, 8, 21, 25, tzinfo=timezone.utc),
    }


def test_parse_event_from_home_team_perspective():
    result = parse_event(SAMPLE_EVENT, PATRIOTS_ID)
    assert result["opponent"] == "Green Bay Packers"
    assert result["is_home"] is True


def test_parse_event_returns_none_when_team_not_in_game():
    assert parse_event(SAMPLE_EVENT, "999") is None


def test_parse_event_returns_none_without_competitions():
    assert parse_event({"id": "1", "date": "2026-11-08T21:25Z"}, PACKERS_ID) is None


def test_parse_espn_date_without_seconds():
    assert parse_espn_date("2026-11-08T21:25Z") == datetime(2026, 11, 8, 21, 25, tzinfo=timezone.utc)


def test_parse_espn_date_with_seconds():
    assert parse_espn_date("2026-11-08T21:25:30Z") == datetime(2026, 11, 8, 21, 25, 30, tzinfo=timezone.utc)


def test_parse_espn_date_rejects_garbage():
    with pytest.raises(ValueError):
        parse_espn_date("next sunday")
