from app.config import PROJECT_ROOT, settings
from app.main import create_app
from fastapi.testclient import TestClient


def test_project_paths_and_frontend_mount_point_to_the_subfolder() -> None:
    assert PROJECT_ROOT.name == "cug-course-planner-2026-fall"
    assert settings.static_dir == PROJECT_ROOT / "frontend"
    assert settings.static_dir.joinpath("index.html").is_file()
    assert settings.curriculum_registry_path.is_file()
    assert "cug-course-planner-2026-fall" in settings.database_url

    with TestClient(create_app()) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "课石" in response.text
