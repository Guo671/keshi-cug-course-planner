from pathlib import Path
from unittest.mock import Mock

from desktop import browser_mode


def test_discover_edge_then_chrome_deduplicates_and_ignores_missing(tmp_path, monkeypatch):
    edge=tmp_path/'Microsoft/Edge/Application/msedge.exe'
    chrome=tmp_path/'Google/Chrome/Application/chrome.exe'
    for p in [edge,chrome]:
        p.parent.mkdir(parents=True);p.write_bytes(b'test')
    monkeypatch.setattr(browser_mode, '_registered_browsers', lambda: [])
    found=browser_mode.find_browsers({'ProgramFiles':str(tmp_path),'ProgramFiles(x86)':str(tmp_path)})
    assert [(name,path) for name,path in found]==[('Microsoft Edge',edge),('Google Chrome',chrome)]


def test_edge_failure_falls_back_to_chrome_without_shell_commands():
    session=browser_mode.BrowserSession()
    launch=Mock(side_effect=[OSError('blocked'),object()])
    session.ready.wait=Mock(return_value=True)
    name=browser_mode.open_browser('http://127.0.0.1:18765',session,
        candidates=[('Edge',Path('C:/edge.exe')),('Chrome',Path('C:/chrome.exe'))],spawn=launch)
    assert name=='Chrome'
    assert launch.call_count==2
    args=launch.call_args.args[0]
    assert Path(args[0])==Path('C:/chrome.exe')
    assert args[1]=='--new-window'
    assert args[2].startswith('http://127.0.0.1:18765/#keshi-launch=')
    assert launch.call_args.kwargs.get('shell',False) is False


def test_unresponsive_browser_is_not_claimed_ready():
    session=browser_mode.BrowserSession();session.ready.wait=Mock(return_value=False)
    name=browser_mode.open_browser('http://127.0.0.1:18765',session,
        candidates=[('Edge',Path('C:/edge.exe'))],spawn=Mock(),open_default=Mock(return_value=False),wait_seconds=.01)
    assert name is None


def test_invalid_urls_are_never_opened():
    import pytest
    for url in ['https://example.com','http://0.0.0.0:18765','file:///C:/test']:
        with pytest.raises(ValueError):browser_mode.open_browser(url,browser_mode.BrowserSession(),candidates=[])


def test_browser_ready_requires_matching_origin_and_secret():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    session=browser_mode.BrowserSession();app=FastAPI()
    session.attach(app,'http://127.0.0.1:18765')
    with TestClient(app) as client:
        assert client.post('/_keshi/browser-ready',json={'token':session.token},headers={'Origin':'https://evil.test'}).status_code==403
        assert client.post('/_keshi/browser-ready',json={'token':'wrong'},headers={'Origin':'http://127.0.0.1:18765'}).status_code==403
        assert client.post('/_keshi/browser-ready',json={'token':'错误令牌'},headers={'Origin':'http://127.0.0.1:18765'}).status_code==403
        assert not session.ready.is_set()
        assert client.post('/_keshi/browser-ready',json={'token':session.token},headers={'Origin':'http://127.0.0.1:18765'}).status_code==204
        assert session.ready.is_set()


def test_download_mark_is_detected_but_never_removed(tmp_path):
    file=tmp_path/'pythonnet/runtime/Python.Runtime.dll';file.parent.mkdir(parents=True);file.write_bytes(b'test')
    stream=Path(str(file)+':Zone.Identifier');stream.write_text('[ZoneTransfer]\nZoneId=3\n')
    assert browser_mode.window_dependency_blocked(tmp_path)
    assert stream.read_text().endswith('ZoneId=3\n')


def test_missing_new_frontend_resources_are_reported(tmp_path):
    import pytest
    from desktop.runtime import RuntimePaths,DesktopRuntimeError
    from dataclasses import replace
    paths=RuntimePaths.discover(tmp_path/'state')
    static=tmp_path/'static';static.mkdir()
    for name in ['index.html','app.js','course-editor.js','help.html','styles.css']:(static/name).write_text('x')
    with pytest.raises(DesktopRuntimeError,match='memory-actions.js|browser-compat.js'):
        replace(paths,static_dir=static).validate_resources()
