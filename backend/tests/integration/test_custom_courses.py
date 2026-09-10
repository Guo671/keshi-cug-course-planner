from copy import deepcopy

import pytest


def custom_choice(cid, weeks, start=1, end=2, day=1):
    return {
        "course_id": f"custom:{cid}",
        "custom": {
            "name": f"课程{cid}",
            "sections": [
                {
                    "id": "one",
                    "instructors": ["测试教师"],
                    "meetings": [
                        {
                            "weeks": weeks,
                            "weekday": day,
                            "start_period": start,
                            "end_period": end,
                        }
                    ],
                }
            ],
        },
    }


def solve(client, headers, choices, preferences=None):
    client.put(
        "/api/profile",
        headers=headers,
        json={"college": "工程学院", "major": "土木工程", "cohort_year": 2024},
    )
    return client.post(
        "/api/plans/generate",
        headers=headers,
        json={
            "input_mode": "manual",
            "manual_courses": choices,
            "preferences": preferences or {},
        },
    )


def test_custom_courses_without_catalog_and_draft_edit(client, auth_headers):
    choice = custom_choice("a", [1, 3, 7])
    draft = {"input_mode": "manual", "manual_courses": [choice]}
    assert client.put("/api/plans/draft", headers=auth_headers, json=draft).status_code == 200
    restored = client.get("/api/plans/draft", headers=auth_headers).json()["draft"]
    assert restored["manual_courses"][0]["custom"]["sections"][0]["meetings"][0]["weeks"] == [
        1,
        3,
        7,
    ]
    response = solve(client, auth_headers, [choice])
    assert response.status_code == 200, response.text
    assert response.json()["plans"][0]["scheduled_course_count"] == 1
    assert response.json()["adjustment"] is None
    assert client.get("/api/catalog/status", headers=auth_headers).json()["course_count"] == 0


def test_five_conflicting_weeks_count_as_five_absences(client, auth_headers):
    result = solve(
        client,
        auth_headers,
        [custom_choice("a", [1, 2, 3, 4, 5]), custom_choice("b", [1, 2, 3, 4, 5])],
    ).json()
    assert result["adjustment"]["leave_count"] == 5
    assert result["adjustment"]["optimal"]
    assert result["adjustment"]["remove_courses"] == []


def test_same_absence_resolves_two_conflicts_once(client, auth_headers):
    result = solve(
        client,
        auth_headers,
        [
            custom_choice("a", [1], 1, 4),
            custom_choice("b", [1], 1, 2),
            custom_choice("c", [1], 3, 4),
        ],
    ).json()
    assert result["adjustment"]["leave_count"] == 1
    assert result["adjustment"]["leaves"][0]["course_id"] == "custom:a"


@pytest.mark.parametrize("weeks,expected_drop", [(list(range(1, 11)), 0), (list(range(1, 12)), 1)])
def test_ten_absence_boundary(client, auth_headers, weeks, expected_drop):
    result = solve(
        client, auth_headers, [custom_choice("a", weeks), custom_choice("b", weeks)]
    ).json()
    assert len(result["adjustment"]["remove_courses"]) == expected_drop
    assert result["adjustment"]["leave_count"] <= 10


def test_disjoint_weeks_are_not_conflicts(client, auth_headers):
    result = solve(
        client, auth_headers, [custom_choice("a", [1, 3, 5]), custom_choice("b", [2, 4, 6])]
    ).json()
    assert result["plans"][0]["scheduled_course_count"] == 2
    assert result["adjustment"] is None


def test_alternative_class_avoids_absences(client, auth_headers):
    first = custom_choice("a", [1, 2])
    alt = deepcopy(first["custom"]["sections"][0])
    alt["id"] = "two"
    alt["meetings"][0]["weekday"] = 2
    first["custom"]["sections"].append(alt)
    result = solve(client, auth_headers, [first, custom_choice("b", [1, 2])]).json()
    assert result["plans"][0]["scheduled_course_count"] == 2
    assert result["adjustment"] is None


def test_hard_teacher_exclusion_not_relaxed_by_absence(client, auth_headers):
    result = solve(
        client,
        auth_headers,
        [custom_choice("a", [1])],
        {
            "instructor_rules": [{"id": "x", "instructor": "测试教师", "strength": "hard"}],
        },
    ).json()
    assert result["adjustment"]["remove_courses"][0]["course_id"] == "custom:a"
    assert not result["adjustment"]["leaves"]


@pytest.mark.parametrize("field,value", [("weeks", []), ("weekday", 0), ("end_period", 0)])
def test_custom_time_is_required_and_validated(client, auth_headers, field, value):
    choice = custom_choice("a", [1])
    choice["custom"]["sections"][0]["meetings"][0][field] = value
    assert solve(client, auth_headers, [choice]).status_code == 422


def test_delete_catalog_keeps_custom_draft_and_history(client, auth_headers):
    choice = custom_choice("a", [1])
    assert solve(client, auth_headers, [choice]).status_code == 200
    client.put(
        "/api/plans/draft",
        headers=auth_headers,
        json={"input_mode": "manual", "manual_courses": [choice]},
    )
    assert client.delete("/api/catalog", headers=auth_headers).status_code == 204
    assert client.get("/api/plans/draft", headers=auth_headers).status_code == 200
    assert len(client.get("/api/plans/history", headers=auth_headers).json()) == 1


def test_invalid_upload_does_not_replace_catalog(client, auth_headers):
    response = client.post(
        "/api/catalog/import",
        headers=auth_headers,
        files=[("files", ("bad.zip", b"not a zip", "application/zip"))],
    )
    assert response.status_code == 422


def test_upload_count_limit(client, auth_headers):
    response = client.post(
        "/api/catalog/import-courses",
        headers=auth_headers,
        files=[("files", (f"{i}.xlsx", b"x")) for i in range(21)],
    )
    assert response.status_code == 422


def test_retired_feature_removed_but_old_draft_courses_survive(client, auth_headers):
    choice = custom_choice("old", [1])
    saved = client.put(
        "/api/plans/draft",
        headers=auth_headers,
        json={
            "input_mode": "mixed",
            "manual_courses": [choice],
            "curriculum": {"source_id": "old-source", "semester": 5},
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["draft"]["input_mode"] == "manual"
    assert "curriculum" not in saved.json()["draft"]
    assert saved.json()["draft"]["manual_courses"][0]["custom"]["name"] == "课程old"
    assert client.get("/api/curricula/sources", headers=auth_headers).status_code == 404


def test_unconfirmed_component_is_never_reported_as_complete(client, auth_headers):
    choice = custom_choice("a", [1])
    choice["custom"]["component_relationship_confirmed"] = False
    response = solve(client, auth_headers, [choice])
    assert response.status_code == 422
    assert "组成关系" in response.json()["detail"]


def test_count_of_courses_takes_precedence_over_individual_priority(client, auth_headers):
    a = custom_choice("a", [1], 1, 4)
    a["priority"] = 10000
    b, c = custom_choice("b", [1], 1, 2), custom_choice("c", [1], 3, 4)
    response = solve(client, auth_headers, [a, b, c])
    assert response.status_code == 200
    assert response.json()["plans"][0]["scheduled_course_count"] == 2


def test_duplicate_course_code_cannot_count_as_two_courses(client, auth_headers):
    a, b = custom_choice("a", [1]), custom_choice("b", [2])
    for c in (a, b):
        c["custom"]["code"] = "20739000"
    assert solve(client, auth_headers, [a, b]).status_code == 422


def test_future_spring_semester_can_be_saved(client, auth_headers):
    response = client.put(
        "/api/profile",
        headers=auth_headers,
        json={
            "college": "工程学院",
            "major": "土木工程",
            "cohort_year": 2026,
            "target_year": 2027,
            "target_season": "spring",
        },
    )
    assert response.status_code == 200
    assert response.json()["semester"] == 2
