"""Codex JSONL transport. Authentication stays in the managed Codex client."""

import asyncio
from collections import deque
import contextlib
import json
import os
from pathlib import Path
import re
import signal

MODEL = "gpt-6.1-sol"
EFFORT = "xhigh"


def safe_error(value):
    text = str(value)
    text = re.sub(r"Bearer\s+\S+", "Bearer [redacted]", text, flags=re.I)
    text = re.sub(r"\beyJ[A-Za-z0-9_.-]+", "[redacted token]", text)
    return text[:4000]


class RpcError(RuntimeError):
    def __init__(self, message, code=None):
        super().__init__(safe_error(message))
        self.code = code


class CodexRpc:
    def __init__(self, cwd, on_event, executable="codex"):
        self.cwd = Path(cwd)
        self.on_event = on_event
        self.executable = executable
        self.process = None
        self.pending = {}
        self.sequence = 0
        self.events = asyncio.Queue()
        self.tasks = []
        self.stderr = deque(maxlen=10)
        self.write_lock = asyncio.Lock()
        self.closing = False

    async def start(self):
        env = dict(os.environ)
        for name in ("OPENAI_API_KEY", "CODEX_API_KEY"):
            env.pop(name, None)
        self.process = await asyncio.create_subprocess_exec(
            self.executable, "app-server", "--listen", "stdio://",
            "-c", 'forced_login_method="chatgpt"',
            "-c", 'model_provider="openai"',
            "-c", f'model="{MODEL}"',
            "-c", f'model_reasoning_effort="{EFFORT}"',
            cwd=self.cwd, env=env, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024, start_new_session=True,
        )
        self.tasks = [asyncio.create_task(self._read()), asyncio.create_task(self._dispatch()), asyncio.create_task(self._stderr())]
        await self.request("initialize", {
            "clientInfo": {"name": "slovene_voice_harness", "title": "Slovene Voice Harness", "version": "0.1.0"},
            "capabilities": {"experimentalApi": True},
        })
        await self.send({"method": "initialized", "params": {}})

    async def send(self, message):
        async with self.write_lock:
            if not self.process or self.process.returncode is not None:
                raise RpcError("Codex app-server is not running. Restart the application to reconnect.")
            self.process.stdin.write((json.dumps(message) + "\n").encode())
            await self.process.stdin.drain()

    async def request(self, method, params, timeout=45):
        self.sequence += 1
        request_id = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as exc:
            raise RpcError(f"Codex did not acknowledge {method} in time. The request was not automatically repeated; its outcome may be unknown.") from exc
        finally:
            self.pending.pop(request_id, None)

    async def _read(self):
        failure = "Codex app-server disconnected. Work was not automatically repeated."
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if "id" in message and "method" not in message:
                    future = self.pending.get(message["id"])
                    if future and not future.done():
                        if "error" in message:
                            error = message["error"]
                            future.set_exception(RpcError(error.get("message", str(error)), error.get("code")))
                        else:
                            future.set_result(message.get("result", {}))
                elif "id" in message:
                    # This first iteration does not execute client tools or approve actions.
                    await self.send({"id": message["id"], "error": {"code": -32601, "message": "This voice client does not execute tools or grant approvals."}})
                else:
                    await self.events.put(message)
        except Exception as exc:
            failure = safe_error(exc)
        finally:
            for future in list(self.pending.values()):
                if not future.done():
                    future.set_exception(RpcError(failure))
            if not self.closing:
                await self.events.put({"method": "transport/closed", "params": {"message": failure}})

    async def _dispatch(self):
        while True:
            message = await self.events.get()
            try:
                await self.on_event(message)
            except Exception as exc:
                self.stderr.append(safe_error(exc))

    async def _stderr(self):
        while line := await self.process.stderr.readline():
            if b"ERROR" in line:
                self.stderr.append(safe_error(line.decode(errors="replace")))

    async def close(self):
        self.closing = True
        if self.process and self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.process.pid, signal.SIGKILL)
                await self.process.wait()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
