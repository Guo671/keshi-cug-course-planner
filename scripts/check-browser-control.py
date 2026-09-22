"""Exercise native fallback controls with an isolated child process, not user windows."""
import ctypes
import os
import subprocess
import sys
import time
import tempfile
import urllib.request
from ctypes import wintypes as w
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if '--child' in sys.argv:
    from desktop.browser_control import show_browser_control
    print(os.getpid(), flush=True)
    if '--integration' in sys.argv:
        import logging
        from desktop import launcher,browser_mode,browser_control
        from desktop.runtime import reserve_backend_socket,SingleInstance
        children=[]
        with tempfile.TemporaryDirectory(prefix='keshi-control-test-',ignore_cleanup_errors=True) as directory:
            root=Path(directory)
            original_open=browser_mode.open_browser
            def spawn(args,**kwargs):
                child=subprocess.Popen([args[0],'--headless=new','--disable-gpu','--no-first-run',
                    '--user-data-dir='+str(root/('browser-'+str(len(children)))),args[-1]],
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**kwargs)
                children.append(child);return child
            browser_mode.open_browser=lambda url,session:original_open(url,session,spawn=spawn)
            def control(url,message,reopen):
                print(url,flush=True)
                return show_browser_control(url,message,reopen)
            browser_control.show_browser_control=control
            def reserve_test_socket():
                reserved=reserve_backend_socket(0)
                reserved.port=reserved.socket.getsockname()[1]
                return reserved
            launcher.reserve_backend_socket=reserve_test_socket
            launcher.SingleInstance=lambda:SingleInstance('Local\\Keshi-control-test-'+str(os.getpid()))
            try:
                assert launcher._run_gui(root,mode='browser')==0
                assert (root/'startup.json').is_file()
            finally:
                for child in children:
                    if child.poll() is None:child.terminate();child.wait(timeout=8)
                logger=logging.getLogger('keshi.desktop')
                for handler in logger.handlers[:]:handler.close();logger.removeHandler(handler)
        raise SystemExit(0)
    show_browser_control('http://127.0.0.1:12345', '隔离控制窗口验证', lambda: '测试浏览器')
    raise SystemExit(0)

env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT),str(ROOT/'backend'),str(ROOT/'.venv/Lib/site-packages')]))
extra=['--integration'] if '--integration' in sys.argv else []
process = subprocess.Popen([getattr(sys,'_base_executable',sys.executable), str(Path(__file__).resolve()), '--child',*extra],
                           cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           creationflags=subprocess.CREATE_NO_WINDOW)
user = ctypes.WinDLL('user32')
callback = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
user.EnumWindows.argtypes = [callback, w.LPARAM]
user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
user.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user.IsWindow.argtypes = [w.HWND]
try:
    pid = int(process.stdout.readline())
    def find(title, seconds=75):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            handles=[]
            @callback
            def inspect(hwnd, _):
                value=w.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(value))
                buffer=ctypes.create_unicode_buffer(512);user.GetWindowTextW(hwnd,buffer,512)
                if value.value==pid and buffer.value==title:handles.append(hwnd)
                return True
            user.EnumWindows(inspect,0)
            if handles:return handles[0]
            if process.poll() is not None:raise RuntimeError(process.stderr.read().decode(errors='replace'))
            time.sleep(.05)
        raise AssertionError('Missing test window: '+title)
    hwnd=find('课石 · 本机浏览器模式')
    service_url=process.stdout.readline().decode().strip() if extra else None
    if service_url:
        assert urllib.request.urlopen(service_url+'/api/health',timeout=2).status==200
    user.PostMessageW(hwnd,0x0111,1001,0)
    user.PostMessageW(hwnd,0x0111,1002,0)
    dialog=find('结束课石服务');user.PostMessageW(dialog,0x0111,7,0) # No preserves service window.
    time.sleep(.15);assert user.IsWindow(hwnd)
    if service_url:
        assert urllib.request.urlopen(service_url+'/api/health',timeout=2).status==200
    user.PostMessageW(hwnd,0x0010,0,0)
    dialog=find('结束课石服务');user.PostMessageW(dialog,0x0111,6,0)
    assert process.wait(timeout=20)==0,process.stderr.read().decode(errors='replace')
    assert not user.IsWindow(hwnd)
    if service_url:
        try:urllib.request.urlopen(service_url+'/api/health',timeout=2)
        except OSError:pass
        else:raise AssertionError('Service still alive after confirmed exit')
    print('PASS: native control opens, reopens, cancellation retains it, confirmation exits')
finally:
    if process.poll() is None:process.kill();process.wait(timeout=5)
