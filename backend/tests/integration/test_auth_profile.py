from fastapi.testclient import TestClient


def test_register_profile_and_login_round_trip(client: TestClient) -> None:
    registered = client.post(
        "/api/auth/register",
        json={"username": "Student072242", "password": "strong-local-password"},
    )
    assert registered.status_code == 201
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}

    me_before = client.get("/api/auth/me", headers=headers)
    assert me_before.json()["profile_complete"] is False

    profile = client.put(
        "/api/profile",
        headers=headers,
        json={
            "college": "机械与电子信息学院",
            "major": "机械设计制造及其自动化（卓越计划）",
            "cohort_year": 2024,
            "plan_variant": "卓越计划",
            "administrative_class": "072242",
        },
    )
    assert profile.status_code == 200, profile.text
    assert profile.json()["semester"] == 5
    assert profile.json()["semester_mapping_needs_confirmation"] is True
    assert profile.json()["administrative_class"] == "072242"
    loaded_profile = client.get("/api/profile", headers=headers)
    assert loaded_profile.json()["administrative_class"] == "072242"

    duplicate = client.post(
        "/api/auth/register",
        json={"username": "student072242", "password": "another-password-123"},
    )
    assert duplicate.status_code == 409

    login = client.post(
        "/api/auth/login",
        json={"username": "STUDENT072242", "password": "strong-local-password"},
    )
    assert login.status_code == 200


def test_profile_is_required_and_validated(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    assert client.get("/api/profile", headers=auth_headers).status_code == 404
    invalid = client.put(
        "/api/profile",
        headers=auth_headers,
        json={"college": "机械与电子信息学院", "major": "机械工程", "cohort_year": 2015},
    )
    assert invalid.status_code == 422
