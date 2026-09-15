def _register_other(client):
    r = client.post('/api/auth/register', json={'username': 'delete_other', 'password': 'isolated-delete-test'})
    assert r.status_code == 201
    return {'Authorization': 'Bearer ' + r.json()['access_token']}


def _save_profile(client, headers):
    r = client.put('/api/profile', headers=headers, json={'college': '测试学院', 'major': '测试专业', 'cohort_year': 2026})
    assert r.status_code == 200


def make_run(client, headers):
    _save_profile(client, headers)
    r = client.post('/api/plans/generate', headers=headers, json={
        'manual_courses': [{'course_id': 'custom:delete-test', 'custom': {
            'name': '删除测试', 'sections': [{'id': 'a', 'meetings': [
                {'weeks': [1], 'weekday': 1, 'start_period': 1, 'end_period': 2}
            ]}]
        }}]
    })
    assert r.status_code == 200, r.text
    return r.json()['run_id']


def test_delete_draft_is_idempotent_isolated_and_keeps_history(client, auth_headers):
    other = _register_other(client)
    run = make_run(client, auth_headers)
    for h in [auth_headers, other]:
        assert client.put('/api/plans/draft', headers=h, json={'manual_courses': []}).status_code == 200
    for _ in range(3):
        assert client.delete('/api/plans/draft', headers=auth_headers).status_code == 204
    assert client.get('/api/plans/draft', headers=auth_headers).status_code == 404
    assert client.get('/api/plans/draft', headers=other).status_code == 200
    assert client.get('/api/plans/history/' + run, headers=auth_headers).status_code == 200
    assert client.get('/api/auth/me', headers=auth_headers).status_code == 200


def test_delete_one_and_all_history_cannot_delete_other_users(client, auth_headers):
    other = _register_other(client)
    first, second = [make_run(client, auth_headers) for _ in range(2)]
    foreign = make_run(client, other)
    client.put('/api/plans/draft', headers=auth_headers, json={'manual_courses': []})
    assert client.delete('/api/plans/history/' + foreign, headers=auth_headers).status_code == 404
    assert client.delete('/api/plans/history/' + first, headers=auth_headers).status_code == 204
    assert client.delete('/api/plans/history/' + first, headers=auth_headers).status_code == 404
    assert client.get('/api/plans/history/' + second, headers=auth_headers).status_code == 200
    assert client.delete('/api/plans/history', headers=auth_headers).status_code == 204
    assert client.get('/api/plans/history', headers=auth_headers).json() == []
    assert client.get('/api/plans/history/' + foreign, headers=other).status_code == 200
    assert client.get('/api/plans/draft', headers=auth_headers).status_code == 200


def test_deletion_requires_authentication(client):
    for url in ['/api/plans/draft', '/api/plans/history', '/api/plans/history/unknown']:
        assert client.delete(url).status_code in (401, 403)
