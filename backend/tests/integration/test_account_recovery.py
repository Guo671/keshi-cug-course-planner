from sqlalchemy import select
from app.infrastructure.tables import User


OLD = "test-account-password"
NEW = "new-account-password"


def test_transaction_finishes_before_success_is_sent(client, session_factory):
    from app.api.dependencies import get_session

    events = []

    def transaction():
        with session_factory() as db:
            yield db
            db.commit()
            events.append("committed")

    client.app.dependency_overrides[get_session] = transaction
    original = client.app

    async def inspect_response(scope, receive, send):
        async def checked_send(message):
            if message["type"] == "http.response.start":
                assert events == ["committed"]
            await send(message)

        await original(scope, receive, checked_send)

    client._transport.app = inspect_response
    register(client)


def register(client, name="recover_student"):
    r = client.post("/api/auth/register", json={"username": name, "password": OLD})
    assert r.status_code == 201, r.text
    return r.json(), {"Authorization": "Bearer " + r.json()["access_token"]}


def reset(client, name, code, password=NEW):
    return client.post(
        "/api/auth/reset-password",
        json={"username": name, "recovery_code": code, "new_password": password},
    )


def test_registration_returns_one_time_code_without_persisting_plaintext(client, session_factory):
    data, h = register(client)
    code = data["recovery_code"]
    assert len(code) == 39
    assert client.get("/api/auth/me", headers=h).json()["recovery_enabled"] is True
    login = client.post("/api/auth/login", json={"username": "recover_student", "password": OLD})
    assert not login.json().get("recovery_code")
    from app.infrastructure.tables import AccountRecovery

    with session_factory() as db:
        row = db.scalar(select(AccountRecovery))
        assert row.recovery_hash != code and len(row.recovery_hash) == 64
    assert "recovery_code" not in client.get("/api/auth/me", headers=h).text


def test_reset_preserves_draft_profile_and_history_revokes_sessions_and_rotates_code(
    client, session_factory
):
    data, h = register(client)
    code = data["recovery_code"]
    client.put(
        "/api/profile", headers=h, json={"college": "测试", "major": "测试", "cohort_year": 2026}
    )
    draft = {"manual_courses": []}
    client.put("/api/plans/draft", headers=h, json=draft)
    r = client.post(
        "/api/plans/generate",
        headers=h,
        json={
            "manual_courses": [
                {
                    "course_id": "custom:test",
                    "custom": {
                        "name": "测试课",
                        "sections": [
                            {
                                "id": "a",
                                "meetings": [
                                    {"weeks": [1], "weekday": 1, "start_period": 1, "end_period": 2}
                                ],
                            }
                        ],
                    },
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    token2 = client.post(
        "/api/auth/login", json={"username": "recover_student", "password": OLD}
    ).json()["access_token"]
    r = reset(client, "recover_student", code.lower().replace("-", " "))
    assert r.status_code == 200, r.text
    assert r.json()["recovery_code"] != code
    assert r.headers["cache-control"] == "no-store"
    assert client.get("/api/auth/me", headers=h).status_code == 401
    assert (
        client.get("/api/auth/me", headers={"Authorization": "Bearer " + token2}).status_code == 401
    )
    assert (
        client.post(
            "/api/auth/login", json={"username": "recover_student", "password": OLD}
        ).status_code
        == 401
    )
    new = client.post("/api/auth/login", json={"username": "recover_student", "password": NEW})
    assert new.status_code == 200
    nh = {"Authorization": "Bearer " + new.json()["access_token"]}
    assert client.get("/api/plans/draft", headers=nh).json()["draft"]["manual_courses"] == []
    assert client.get("/api/plans/history/" + run_id, headers=nh).status_code == 200
    assert client.get("/api/profile", headers=nh).json()["college"] == "测试"
    assert reset(client, "recover_student", code).status_code == 400


def test_wrong_or_foreign_code_does_not_change_accounts(client):
    a, ha = register(client, "student_a")
    b, hb = register(client, "student_b")
    for name, code in [
        ("student_b", a["recovery_code"]),
        ("unknown", a["recovery_code"]),
        ("student_a", "错误码"),
    ]:
        assert reset(client, name, code).status_code == 400
    for h in [ha, hb]:
        assert client.get("/api/auth/me", headers=h).status_code == 200
    assert reset(client, "student_a", a["recovery_code"]).status_code == 200


def test_generate_code_requires_current_password_and_replaces_old_code(client):
    a, h = register(client)
    assert (
        client.post(
            "/api/auth/recovery-code", headers=h, json={"current_password": "wrong"}
        ).status_code
        == 400
    )
    r = client.post("/api/auth/recovery-code", headers=h, json={"current_password": OLD})
    assert r.status_code == 200, r.text
    assert reset(client, "recover_student", a["recovery_code"]).status_code == 400
    assert reset(client, "recover_student", r.json()["recovery_code"]).status_code == 200


def test_change_password_checks_old_password_and_keeps_recovery_code(client):
    a, h = register(client)
    endpoint = "/api/auth/change-password"
    assert (
        client.post(
            endpoint, headers=h, json={"current_password": "wrong", "new_password": NEW}
        ).status_code
        == 400
    )
    assert (
        client.post(
            endpoint, headers=h, json={"current_password": OLD, "new_password": "short"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            endpoint, headers=h, json={"current_password": OLD, "new_password": NEW}
        ).status_code
        == 204
    )
    assert client.get("/api/auth/me", headers=h).status_code == 401
    assert reset(client, "recover_student", a["recovery_code"], password=OLD).status_code == 200


def test_old_account_gets_recovery_code_on_first_successful_login_only(client, session_factory):
    from app.infrastructure.security import hash_password

    with session_factory() as db:
        db.add(User(username="legacy_user", password_hash=hash_password(OLD)))
        db.commit()
    r = client.post("/api/auth/login", json={"username": "legacy_user", "password": OLD})
    assert r.status_code == 200 and r.json()["recovery_code"]
    assert (
        not client.post("/api/auth/login", json={"username": "legacy_user", "password": OLD})
        .json()
        .get("recovery_code")
    )


def test_bad_recovery_attempts_are_bounded(client):
    register(client)
    statuses = [reset(client, "recover_student", "WRONG").status_code for _ in range(7)]
    assert statuses[-1] == 429
    assert all(x in [400, 429] for x in statuses)


def test_account_actions_require_login(client):
    for endpoint in ["/api/auth/recovery-code", "/api/auth/change-password"]:
        assert (
            client.post(endpoint, json={"current_password": OLD, "new_password": NEW}).status_code
            == 401
        )


def test_old_password_login_cannot_issue_token_after_other_process_reset(tmp_path):
    import pytest
    from fastapi import HTTPException
    from sqlalchemy.orm import sessionmaker
    from app.api.routes_auth import _issue_token
    from app.infrastructure.database import create_database_engine, initialize_database
    from app.infrastructure.account_recovery import ensure_recovery_code, recover_password
    from app.infrastructure.security import hash_password, verify_password

    engine = create_database_engine("sqlite:///" + str(tmp_path / "race.db"))
    initialize_database(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as db:
        user = User(username="race_student", password_hash=hash_password(OLD))
        db.add(user)
        db.flush()
        uid = user.id
        code = ensure_recovery_code(db, user)
    with factory() as delayed_login:
        stale = delayed_login.get(User, uid)
        assert verify_password(stale.password_hash, OLD)
        with factory.begin() as reset_db:
            recover_password(reset_db, reset_db.get(User, uid), code, NEW)
        with pytest.raises(HTTPException) as failure:
            _issue_token(delayed_login, stale)
        assert failure.value.status_code == 401
    engine.dispose()
