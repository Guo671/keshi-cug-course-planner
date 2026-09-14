from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

from desktop import launcher


def test_launcher_enables_downloads_before_window_creation(monkeypatch, tmp_path):
    paths = Mock(resource_root=tmp_path, data_root=tmp_path,
                 database_path=tmp_path/'test.db', webview_storage_path=tmp_path,
                 icon_path=tmp_path/'icon.ico')
    monkeypatch.setattr(launcher.RuntimePaths, 'discover', lambda _: paths)
    monkeypatch.setattr(launcher, '_configure_logging', lambda _: None)
    monkeypatch.setattr(launcher, 'SingleInstance', nullcontext)
    monkeypatch.setattr(launcher, 'webview2_runtime_available', lambda: True)
    monkeypatch.setattr(launcher, 'seed_user_database', lambda _: False)
    monkeypatch.setattr(launcher, 'reserve_backend_socket', lambda: Mock())
    server = Mock(url='http://127.0.0.1:12345')
    monkeypatch.setattr(launcher, 'BackendServer', lambda _: server)
    view = SimpleNamespace(settings={'ALLOW_DOWNLOADS': False}, start=Mock())
    def create_window(*args, **kwargs):
        assert view.settings['ALLOW_DOWNLOADS'] is True
    view.create_window = Mock(side_effect=create_window)
    monkeypatch.setitem(launcher.sys.modules, 'webview', view)
    monkeypatch.setattr(launcher.sys, 'platform', 'win32')
    assert launcher._run_gui(tmp_path) == 0
    view.create_window.assert_called_once()
    view.start.assert_called_once()
    server.stop.assert_called_once()
