"""Bounded request spooling and per-process admission for the local desktop server."""

from __future__ import annotations

import asyncio
import re
from tempfile import SpooledTemporaryFile
from threading import BoundedSemaphore
from time import monotonic

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

JSON_LIMIT = 4 * 1024 * 1024
UPLOAD_LIMIT = 101 * 1024 * 1024


class RequestLimitsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.general = BoundedSemaphore(16)
        self.heavy = BoundedSemaphore(1)
        self.auth = BoundedSemaphore(2)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        if path == "/api/health":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        limit = (
            UPLOAD_LIMIT
            if b"multipart/form-data" in headers.get(b"content-type", b"")
            else JSON_LIMIT
        )

        async def reject(status: int, message: str) -> None:
            await JSONResponse(
                {"detail": message},
                status_code=status,
                headers={"Retry-After": "1"} if status == 429 else None,
            )(scope, receive, send)

        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            await reject(400, "请求长度格式不正确")
            return
        if length < 0 or length > limit:
            await reject(413, "请求内容过大，请减少文件或课程数量")
            return
        gates = [self.general]
        if path in {
            "/api/plans/generate",
            "/api/catalog/import",
            "/api/catalog/import-courses",
            "/api/catalog/restore-builtin",
        } or (path == "/api/catalog" and scope["method"] == "DELETE"):
            gates.append(self.heavy)
        if path in {"/api/auth/register", "/api/auth/login"}:
            gates.append(self.auth)
        acquired = []
        try:
            for gate in gates:
                if not gate.acquire(blocking=False):
                    await reject(429, "正在处理其他任务，请稍后重试")
                    return
                acquired.append(gate)
            # Validate the actual streamed length BEFORE the multipart parser runs.
            with SpooledTemporaryFile(max_size=64 * 1024) as spool:
                total = 0
                deadline = monotonic() + 60
                while True:
                    try:
                        message = await asyncio.wait_for(
                            receive(), timeout=max(0.01, min(15, deadline - monotonic()))
                        )
                    except TimeoutError:
                        await reject(408, "接收文件超时，请重新选择文件后重试")
                        return
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    total += len(chunk)
                    if total > limit:
                        await reject(413, "请求内容过大，请减少文件或课程数量")
                        return
                    spool.write(chunk)
                    if not message.get("more_body", False):
                        break
                spool.seek(0)
                if b"multipart/form-data" not in headers.get(b"content-type", b"") and total:
                    raw = spool.read()
                    depth = 0
                    containers = 0
                    for token in re.finditer(rb'"(?:[^"\\]|\\.)*"|[\[\]{}]', raw):
                        first = token.group()[0]
                        if first in (ord("["), ord("{")):
                            depth += 1
                            containers += 1
                            if depth > 64 or containers > 30000:
                                await reject(422, "请求嵌套过深或内容过于复杂，请减少课程和时段")
                                return
                        elif first in (ord("]"), ord("}")):
                            depth -= 1
                    del raw
                    spool.seek(0)
                sent = 0

                async def replay() -> Message:
                    nonlocal sent
                    if sent > total:
                        return await receive()
                    data = spool.read(64 * 1024)
                    sent += len(data)
                    more = sent < total
                    if not more:
                        sent = total + 1
                    return {"type": "http.request", "body": data, "more_body": more}

                await self.app(scope, replay, send)
        finally:
            for gate in reversed(acquired):
                gate.release()
