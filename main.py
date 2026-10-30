"""Stage 1: sports tracker API with manually entered data.

Run:  fastapi dev main.py   (then open http://127.0.0.1:8000/docs)
Deps: pip install "fastapi[standard]" sqlmodel
"""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlmodel import Field, Session, SQLModel, create_engine, select

engine = create_engine("sqlite:///tracker.db", connect_args={"check_same_thread": False})


# ---------- Models ----------
class TeamBase(SQLModel):
    name: str
    sport: str
    league: Optional[str] = None  # national teams play many competitions, so optional


class Team(TeamBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)


class TeamCreate(TeamBase):
    pass


class MatchBase(SQLModel):
    opponent: str
    is_home: bool = True
    competition: str
    kickoff_utc: datetime  # must include a timezone offset on input


class Match(MatchBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    team_id: int = Field(foreign_key="team.id", index=True)


class MatchCreate(MatchBase):
    pass


class MatchRead(MatchBase):
    id: int
    team_id: int
    kickoff_local: datetime


# ---------- Helpers ----------
def get_session():
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def get_tz(tz: str = Query("UTC", description="IANA name, e.g. America/Mexico_City")) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except ZoneInfoNotFoundError:
        raise HTTPException(status_code=400, detail=f"Unknown timezone: {tz}")


TzDep = Annotated[ZoneInfo, Depends(get_tz)]


def to_read(match: Match, tz: ZoneInfo) -> MatchRead:
    # SQLite drops tzinfo, so we store naive UTC and re-attach it on the way out.
    utc = match.kickoff_utc.replace(tzinfo=timezone.utc)
    data = match.model_dump()
    data["kickoff_utc"] = utc
    data["kickoff_local"] = utc.astimezone(tz)
    return MatchRead(**data)


def now_naive_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------- App ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    yield


app = FastAPI(title="Match Tracker", lifespan=lifespan)


# ---------- Teams ----------
@app.post("/teams", response_model=Team, status_code=201)
def create_team(team: TeamCreate, session: SessionDep):
    db_team = Team.model_validate(team)
    session.add(db_team)
    session.commit()
    session.refresh(db_team)
    return db_team


@app.get("/teams", response_model=list[Team])
def list_teams(session: SessionDep):
    return session.exec(select(Team)).all()


@app.delete("/teams/{team_id}", status_code=204)
def delete_team(team_id: int, session: SessionDep):
    team = session.get(Team, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    for m in session.exec(select(Match).where(Match.team_id == team_id)).all():
        session.delete(m)
    session.delete(team)
    session.commit()


# ---------- Matches ----------
@app.post("/teams/{team_id}/matches", response_model=MatchRead, status_code=201)
def add_match(team_id: int, match: MatchCreate, session: SessionDep, tz: TzDep):
    if not session.get(Team, team_id):
        raise HTTPException(status_code=404, detail="Team not found")
    if match.kickoff_utc.tzinfo is None:
        raise HTTPException(status_code=422, detail="kickoff_utc needs a timezone offset, e.g. 2026-11-02T20:00:00Z")
    stored = match.kickoff_utc.astimezone(timezone.utc).replace(tzinfo=None)
    db_match = Match(**match.model_dump(exclude={"kickoff_utc"}), kickoff_utc=stored, team_id=team_id)
    session.add(db_match)
    session.commit()
    session.refresh(db_match)
    return to_read(db_match, tz)


@app.get("/teams/{team_id}/matches/upcoming", response_model=list[MatchRead])
def team_upcoming(team_id: int, session: SessionDep, tz: TzDep, days: int = Query(30, ge=1, le=365)):
    if not session.get(Team, team_id):
        raise HTTPException(status_code=404, detail="Team not found")
    now = now_naive_utc()
    stmt = (
        select(Match)
        .where(Match.team_id == team_id, Match.kickoff_utc >= now, Match.kickoff_utc <= now + timedelta(days=days))
        .order_by(Match.kickoff_utc)
    )
    return [to_read(m, tz) for m in session.exec(stmt).all()]


@app.get("/matches/upcoming", response_model=list[MatchRead])
def all_upcoming(session: SessionDep, tz: TzDep, days: int = Query(7, ge=1, le=365)):
    now = now_naive_utc()
    stmt = (
        select(Match)
        .where(Match.kickoff_utc >= now, Match.kickoff_utc <= now + timedelta(days=days))
        .order_by(Match.kickoff_utc)
    )
    return [to_read(m, tz) for m in session.exec(stmt).all()]
