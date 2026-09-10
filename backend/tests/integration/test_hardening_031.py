from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing

from app.domain import (
    Course,
    CourseRequest,
    Meeting,
    SchedulingProblem,
    SectionOption,
    TeachingSection,
    WeekMask,
)
from app.infrastructure.database import create_database_engine, initialize_database
from app.infrastructure.request_limits import RequestLimitsMiddleware
from app.infrastructure.security import create_login_session, get_user_for_token
from app.infrastructure.tables import User
from app.scheduling.solver import ScheduleSolver, SolverConfig
from sqlalchemy.orm import Session


def test_authenticated_read_does_not_lock_out_another_writer(tmp_path):
    path = tmp_path / "auth.db"
    engine = create_database_engine(f"sqlite:///{path.as_posix()}")
    initialize_database(engine)
    try:
        with Session(engine) as db:
            user = User(username="read_only_check", password_hash="unused")
            db.add(user)
            db.flush()
            token, _ = create_login_session(db, user, lifetime_hours=1)
            db.commit()
        with Session(engine) as reader:
            assert get_user_for_token(reader, token)
            with closing(sqlite3.connect(path, timeout=0.1)) as writer:
                writer.execute("UPDATE users SET username='read_only_check' WHERE id=1")
                writer.commit()
    finally:
        engine.dispose()


def test_internal_room_conflict_is_rejected_and_identical_duplicate_is_not():
    weeks = WeekMask.from_weeks([1, 3])
    a = Meeting(weeks, 1, 1, 2, room="A101")
    b = Meeting(weeks, 1, 1, 2, room="B202")
    for meetings, expected in [((a, b), False), ((a, a), True)]:
        option = SectionOption("s", "c", (TeachingSection("s", "c", "0001", (), meetings),))
        result = ScheduleSolver(SolverConfig(max_solutions=1)).solve(
            SchedulingProblem((Course("c", "c", "测试课程"),), (CourseRequest("c"),), (option,))
        )
        assert any("s" in p.selected_option_ids for p in result.plans) == expected


def test_chunked_body_limit_is_enforced_before_request_parser(monkeypatch):
    monkeypatch.setattr("app.infrastructure.request_limits.JSON_LIMIT", 16)
    invoked = []
    messages = []

    async def downstream(scope, receive, send):
        invoked.append(True)

    middleware = RequestLimitsMiddleware(downstream)
    chunks = iter(
        [
            {"type": "http.request", "body": b"x" * 8, "more_body": True},
            {"type": "http.request", "body": b"x" * 12, "more_body": False},
        ]
    )

    async def receive():
        return next(chunks)

    async def send(message):
        messages.append(message)

    asyncio.run(
        middleware(
            {"type": "http", "path": "/api/plans/draft", "method": "PUT", "headers": []},
            receive,
            send,
        )
    )
    assert not invoked
    assert messages[0]["status"] == 413


def test_heavy_admission_rejects_overlap_and_releases_after_error():
    messages = []
    started = asyncio.Event()
    finish = asyncio.Event()

    async def downstream(scope, receive, send):
        started.set()
        await finish.wait()

    middleware = RequestLimitsMiddleware(downstream)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {"type": "http", "path": "/api/plans/generate", "method": "POST", "headers": []}

    async def check():
        first = asyncio.create_task(middleware(scope, receive, send))
        await started.wait()
        await middleware(scope, receive, send)
        assert messages[0]["status"] == 429
        finish.set()
        await first
        assert middleware.heavy.acquire(blocking=False)
        middleware.heavy.release()

    asyncio.run(check())


def test_duplicate_standard_headers_are_rejected(client, auth_headers):
    from io import BytesIO
    from openpyxl import Workbook
    from app.importers.standard_excel import STANDARD_HEADERS

    book = Workbook()
    book.active.append(STANDARD_HEADERS + ["周次"])
    book.active.append(["x", "测试", "0001", "", "1", "周一", 1, 2, "", "", "2"])
    buf = BytesIO()
    book.save(buf)
    r = client.post(
        "/api/catalog/import-courses",
        headers=auth_headers,
        files=[("files", ("bad.xlsx", buf.getvalue()))],
    )
    assert r.status_code == 422 and "重复列名" in r.json()["detail"]


def test_invalid_custom_section_id_collision_is_422_not_500(client, auth_headers):
    client.put(
        "/api/profile",
        headers=auth_headers,
        json={"college": "测试", "major": "测试", "cohort_year": 2024},
    )
    choices = []
    for cid, sid in [("custom:a", "b:c"), ("custom:a:b", "c")]:
        choices.append(
            {
                "course_id": cid,
                "custom": {
                    "name": "测试",
                    "sections": [
                        {
                            "id": sid,
                            "meetings": [
                                {"weeks": [1], "weekday": 1, "start_period": 1, "end_period": 2}
                            ],
                        }
                    ],
                },
            }
        )
    r = client.post("/api/plans/generate", headers=auth_headers, json={"manual_courses": choices})
    assert r.status_code == 422


def test_deep_json_rejected_with_or_without_content_type(client):
    body=b'['*2000+b']'*2000
    for headers in ({},{'Content-Type':'application/json'}):
        response=client.put('/api/plans/draft',content=body,headers=headers)
        assert response.status_code==422


def test_boolean_time_value_is_not_silently_converted_to_period_one():
    import pytest
    from pydantic import ValidationError
    from app.api.schemas import CustomMeeting
    with pytest.raises(ValidationError):
        CustomMeeting(weeks=[1],weekday=True,start_period=1,end_period=2)
