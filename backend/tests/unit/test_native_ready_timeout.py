from types import SimpleNamespace
from unittest.mock import Mock
import sys
import threading
import pytest
from desktop import launcher
from desktop.runtime import DesktopRuntimeError,RuntimePaths


class Hook:
    def __init__(self):self.handlers=[]
    def __iadd__(self,fn):self.handlers.append(fn);return self
    def fire(self,*args,**kwargs):
        for fn in self.handlers:fn()


def fake_window(monkeypatch):
    window=SimpleNamespace(events=SimpleNamespace(loaded=Hook(),closing=Hook()),evaluate_js=Mock(return_value={'ready':True,'failed':False}))
    window.destroy=window.events.closing.fire
    view=SimpleNamespace(settings={},create_window=Mock(return_value=window),start=Mock())
    monkeypatch.setitem(sys.modules,'webview',view)
    return view,window


def test_normal_window_timeout_is_a_fallback_error(monkeypatch,tmp_path):
    view,window=fake_window(monkeypatch)
    class Timer:
        def __init__(self,seconds,fn):self.fn=fn
        def start(self):self.fn()
        def cancel(self):pass
    monkeypatch.setattr(threading,'Timer',Timer)
    with pytest.raises(DesktopRuntimeError,match='加载'):
        launcher._open_desktop_window(RuntimePaths.discover(tmp_path),'http://127.0.0.1:12345')


def test_user_closing_during_start_does_not_open_a_browser_again(monkeypatch,tmp_path):
    view,window=fake_window(monkeypatch)
    view.start.side_effect=window.events.closing.fire
    launcher._open_desktop_window(RuntimePaths.discover(tmp_path),'http://127.0.0.1:12345')


def test_loaded_html_with_failed_application_is_rejected(monkeypatch,tmp_path):
    view,window=fake_window(monkeypatch)
    window.evaluate_js.return_value={'ready':False,'failed':True}
    view.start.side_effect=window.events.loaded.fire
    with pytest.raises(DesktopRuntimeError):
        launcher._open_desktop_window(RuntimePaths.discover(tmp_path),'http://127.0.0.1:12345')

