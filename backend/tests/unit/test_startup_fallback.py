import sys
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from desktop import launcher
from desktop.runtime import RuntimePaths


def test_packaged_ui_check_arguments():
    args=launcher._argument_parser().parse_args(['--check-ui','chrome'])
    assert args.check_ui=='chrome'


def setup_runtime(monkeypatch,tmp_path):
    paths=RuntimePaths.discover(tmp_path/'state')
    monkeypatch.setattr(launcher.RuntimePaths,'discover',lambda _:paths)
    monkeypatch.setattr(launcher,'_configure_logging',lambda _:None)
    monkeypatch.setattr(launcher,'SingleInstance',nullcontext)
    monkeypatch.setattr(launcher,'seed_user_database',lambda _:False)
    monkeypatch.setattr(launcher.RuntimePaths,'validate_resources',lambda _:None)
    monkeypatch.setattr(launcher.RuntimePaths,'configure_backend_environment',lambda _:None)
    monkeypatch.setattr(launcher,'reserve_backend_socket',lambda:Mock())
    server=Mock(url='http://127.0.0.1:12345')
    monkeypatch.setattr(launcher,'BackendServer',lambda _:server)
    fallback=Mock(side_effect=lambda *args:server.stop.assert_not_called())
    monkeypatch.setattr(launcher,'run_browser_mode',fallback)
    monkeypatch.setattr(launcher.sys,'platform','win32')
    return server,fallback


def test_missing_webview2_uses_browser_and_keeps_backend_until_exit(monkeypatch,tmp_path):
    server,fallback=setup_runtime(monkeypatch,tmp_path)
    monkeypatch.setattr(launcher,'webview2_runtime_available',lambda:False)
    assert launcher._run_gui(tmp_path)==0
    fallback.assert_called_once();server.stop.assert_called_once()


def test_pythonnet_error_uses_browser_instead_of_aborting(monkeypatch,tmp_path):
    server,fallback=setup_runtime(monkeypatch,tmp_path)
    monkeypatch.setattr(launcher,'webview2_runtime_available',lambda:True)
    view=SimpleNamespace(settings={},create_window=Mock(),start=Mock(side_effect=RuntimeError('Failed to resolve Python.Runtime.Loader.Initialize')))
    monkeypatch.setitem(sys.modules,'webview',view)
    assert launcher._run_gui(tmp_path)==0
    fallback.assert_called_once();server.stop.assert_called_once()


def test_explicit_browser_never_initializes_dotnet(monkeypatch,tmp_path):
    server,fallback=setup_runtime(monkeypatch,tmp_path)
    monkeypatch.setattr(launcher,'webview2_runtime_available',Mock(side_effect=AssertionError('must not check')))
    assert launcher._run_gui(tmp_path,mode='browser')==0
    fallback.assert_called_once();server.stop.assert_called_once()


def test_backend_failure_is_not_masked_by_browser(monkeypatch,tmp_path):
    server,fallback=setup_runtime(monkeypatch,tmp_path)
    server.start.side_effect=RuntimeError('database broken')
    with pytest.raises(RuntimeError,match='database broken'):launcher._run_gui(tmp_path,mode='browser')
    fallback.assert_not_called();server.stop.assert_called_once()


def test_real_webview_registry_id_and_zero_version(monkeypatch):
    import winreg
    from pathlib import Path
    from desktop.runtime import webview2_runtime_available
    monkeypatch.setattr(Path,'is_dir',lambda _:False)
    def open_key(hive,key):
        if '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}' not in key:raise FileNotFoundError()
        return nullcontext('key')
    monkeypatch.setattr(winreg,'OpenKey',open_key)
    monkeypatch.setattr(winreg,'QueryValueEx',lambda *_:('123.0.0.1',1))
    assert webview2_runtime_available()
    monkeypatch.setattr(winreg,'QueryValueEx',lambda *_:('0.0.0.0',1))
    assert not webview2_runtime_available()


def test_empty_runtime_directory_does_not_count_as_an_install(monkeypatch,tmp_path):
    import winreg
    from desktop.runtime import webview2_runtime_available
    for key in ['ProgramFiles','ProgramFiles(x86)','LOCALAPPDATA']:monkeypatch.setenv(key,str(tmp_path))
    version=tmp_path/'Microsoft/EdgeWebView/Application/123.0.0.1';version.mkdir(parents=True)
    monkeypatch.setattr(winreg,'OpenKey',Mock(side_effect=FileNotFoundError()))
    assert not webview2_runtime_available()
    (version/'msedgewebview2.exe').write_bytes(b'fixture')
    assert webview2_runtime_available()


def test_cold_start_health_at_25_seconds_is_not_prematurely_aborted(monkeypatch):
    from desktop import runtime
    clock=[0]
    monkeypatch.setattr(runtime.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(runtime.time,'sleep',lambda _:clock.__setitem__(0,clock[0]+5))
    thread=Mock();thread.is_alive.return_value=True
    monkeypatch.setattr(runtime.threading,'Thread',lambda **_:thread)
    def request(*args,**kwargs):
        if clock[0]<25:raise OSError('cold startup')
        return nullcontext(SimpleNamespace(status=200,read=lambda:b'{"status":"ok"}'))
    monkeypatch.setattr(runtime.urllib.request,'urlopen',request)
    server=runtime.BackendServer(Mock(port=12345))
    assert server.start()=={'status':'ok'}
