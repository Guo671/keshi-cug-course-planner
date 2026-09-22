"""Small native Windows lifecycle window with no .NET or WebView2 dependency."""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes as w


def show_browser_control(url, message, reopen):
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

    class WindowClass(ctypes.Structure):
        _fields_ = [
            ("style", w.UINT),
            ("proc", callback_type),
            ("class_extra", ctypes.c_int),
            ("window_extra", ctypes.c_int),
            ("instance", w.HINSTANCE),
            ("icon", w.HICON),
            ("cursor", w.HANDLE),
            ("background", w.HBRUSH),
            ("menu", w.LPCWSTR),
            ("name", w.LPCWSTR),
        ]

    kernel.GetModuleHandleW.argtypes = [w.LPCWSTR]
    kernel.GetModuleHandleW.restype = w.HMODULE
    user.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user.DefWindowProcW.restype = ctypes.c_ssize_t
    user.CreateWindowExW.argtypes = [
        w.DWORD,
        w.LPCWSTR,
        w.LPCWSTR,
        w.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        w.HWND,
        w.HMENU,
        w.HINSTANCE,
        ctypes.c_void_p,
    ]
    user.CreateWindowExW.restype = w.HWND
    user.RegisterClassW.argtypes = [ctypes.POINTER(WindowClass)]
    user.RegisterClassW.restype = w.ATOM
    user.DestroyWindow.argtypes = [w.HWND]
    user.SetForegroundWindow.argtypes = [w.HWND]
    user.SetWindowTextW.argtypes = [w.HWND, w.LPCWSTR]
    user.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user.SendMessageW.restype = ctypes.c_ssize_t
    user.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
    user.EnableWindow.argtypes = [w.HWND, w.BOOL]
    user.MessageBoxW.argtypes = [w.HWND, w.LPCWSTR, w.LPCWSTR, w.UINT]
    user.MessageBoxW.restype = ctypes.c_int
    user.GetMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]
    user.GetMessageW.restype = ctypes.c_int
    user.TranslateMessage.argtypes = [ctypes.POINTER(w.MSG)]
    user.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
    user.DispatchMessageW.restype = ctypes.c_ssize_t
    user.UnregisterClassW.argtypes = [w.LPCWSTR, w.HINSTANCE]
    instance = kernel.GetModuleHandleW(None)
    status = None
    reopen_button = None

    def confirm_stop(hwnd):
        return (
            user.MessageBoxW(
                hwnd,
                "结束后浏览器页面将无法继续保存或排课。\n请先确认页面显示“草稿已保存”。\n\n现在结束课石吗？",
                "结束课石服务",
                0x00000004 | 0x00000030 | 0x00000100,
            )
            == 6
        )

    @callback_type
    def proc(hwnd, msg, wp, lp):
        if msg == 0x0111:  # WM_COMMAND
            if wp & 0xFFFF == 1001:
                user.EnableWindow(reopen_button, False)

                def retry():
                    try:
                        name = reopen()
                        user.SetWindowTextW(
                            status,
                            f"已重新打开{name}。"
                            if name
                            else "页面仍未确认加载，请把本机地址复制到浏览器，或查看启动日志。",
                        )
                    except Exception:
                        user.SetWindowTextW(status, "重新打开失败，请查看启动日志。")
                    finally:
                        user.EnableWindow(reopen_button, True)

                threading.Thread(target=retry, daemon=True).start()
                return 0
            if wp & 0xFFFF == 1002:
                if confirm_stop(hwnd):
                    user.DestroyWindow(hwnd)
                return 0
        if msg == 0x0010:  # WM_CLOSE
            if confirm_stop(hwnd):
                user.DestroyWindow(hwnd)
            return 0
        if msg == 0x0002:  # WM_DESTROY
            user.PostQuitMessage(0)
            return 0
        return user.DefWindowProcW(hwnd, msg, wp, lp)

    class_name = "KeshiBrowserControl"
    wc = WindowClass(0, proc, 0, 0, instance, None, None, 6, None, class_name)
    atom = user.RegisterClassW(ctypes.byref(wc))
    if not atom:
        raise ctypes.WinError(ctypes.get_last_error())
    hwnd = user.CreateWindowExW(
        0,
        class_name,
        "课石 · 本机浏览器模式",
        0x00CA0000,
        220,
        180,
        570,
        270,
        None,
        None,
        instance,
        None,
    )
    if not hwnd:
        user.UnregisterClassW(class_name, instance)
        raise ctypes.WinError(ctypes.get_last_error())
    gdi = ctypes.WinDLL("gdi32")
    gdi.GetStockObject.argtypes = [ctypes.c_int]
    gdi.GetStockObject.restype = w.HANDLE
    font = gdi.GetStockObject(17)

    def child(kind, title, x, y, width, height, identifier=0, extra=0):
        handle = user.CreateWindowExW(
            0,
            kind,
            title,
            0x50000000 | extra,
            x,
            y,
            width,
            height,
            hwnd,
            identifier,
            instance,
            None,
        )
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        user.SendMessageW(handle, 0x0030, font, 1)
        return handle

    try:
        status = child("STATIC", message, 20, 15, 510, 52)
        child("EDIT", url, 20, 72, 510, 25, extra=0x00800880)
        child(
            "STATIC",
            "账号与课表仍保存在本机。关闭浏览器不会结束课石。\n使用完毕请点击“结束课石”，或关闭这个控制窗口。",
            20,
            111,
            510,
            48,
        )
        reopen_button = child("BUTTON", "重新打开页面", 190, 176, 160, 34, 1001, 0x10000)
        child("BUTTON", "结束课石", 368, 176, 160, 34, 1002, 0x10000)
        user.ShowWindow(hwnd, 1)
        event = w.MSG()
        while True:
            code = user.GetMessageW(ctypes.byref(event), None, 0, 0)
            if code == 0:
                break
            if code == -1:
                raise ctypes.WinError(ctypes.get_last_error())
            user.TranslateMessage(ctypes.byref(event))
            user.DispatchMessageW(ctypes.byref(event))
    finally:
        user.DestroyWindow(hwnd)
        user.UnregisterClassW(class_name, instance)
