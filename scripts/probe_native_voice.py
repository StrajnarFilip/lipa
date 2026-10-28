#!/usr/bin/env python3
"""Probe native Codex voice using ChatGPT authentication, without API fallback."""

import asyncio
import argparse
import collections
import contextlib
import json
import os
from pathlib import Path
import re
import signal
import sys


def redact(value):
    text = str(value)
    text = re.sub(r"Bearer\s+\S+", "Bearer [redacted]", text, flags=re.I)
    text = re.sub(r"\beyJ[A-Za-z0-9_.-]+", "[redacted token]", text)
    return text


class RpcError(RuntimeError):
    pass


class Probe:
    def __init__(self, sdp_file=None):
        self.pending = {}
        self.events = asyncio.Queue()
        self.counts = collections.Counter()
        self.sequence = 0
        self.tasks = []
        self.thread_id = None
        self.proc = None
        self.sdp_file = sdp_file
        self.report = {"client": "slovene_voice_harness", "model": "gpt-6.1-sol", "effort": "xhigh", "stages": {}}

    async def start(self):
        env = dict(os.environ)
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY"):
            env.pop(key, None)
        self.proc = await asyncio.create_subprocess_exec(
            "codex", "app-server", "--listen", "stdio://",
            "-c", 'forced_login_method="chatgpt"',
            "-c", 'model_provider="openai"',
            "-c", 'model="gpt-6.1-sol"',
            "-c", 'model_reasoning_effort="xhigh"',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024,
            start_new_session=True, env=env,
        )
        self.tasks = [asyncio.create_task(self.read()), asyncio.create_task(self.read_stderr())]

    async def read_stderr(self):
        while line := await self.proc.stderr.readline():
            # Avoid persisting global configuration, credentials, or unrelated logs.
            if b"ERROR" in line:
                print("app-server:", redact(line.decode(errors="replace").strip())[:2000], flush=True)

    async def read(self):
        try:
            while line := await self.proc.stdout.readline():
                msg = json.loads(line)
                if "id" in msg and "method" not in msg:
                    future = self.pending.pop(msg["id"], None)
                    if future and not future.done():
                        if "error" in msg:
                            future.set_exception(RpcError(redact(json.dumps(msg["error"]))))
                        else:
                            future.set_result(msg.get("result"))
                elif "id" in msg:
                    self.proc.stdin.write((json.dumps({"id": msg["id"], "error": {"code": -32601, "message": "Probe does not execute tools or approvals"}}) + "\n").encode())
                    await self.proc.stdin.drain()
                else:
                    method = msg.get("method", "unknown")
                    self.counts[method] += 1
                    await self.events.put(msg)
        except Exception as exc:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(exc)

    async def rpc(self, method, params, timeout=40):
        self.sequence += 1
        request_id = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        self.proc.stdin.write((json.dumps({"id": request_id, "method": method, "params": params}) + "\n").encode())
        await self.proc.stdin.drain()
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(request_id, None)

    async def wait_for(self, predicate, timeout):
        async with asyncio.timeout(timeout):
            while True:
                msg = await self.events.get()
                method = msg.get("method", "")
                params = msg.get("params", {})
                if method in ("thread/realtime/error", "error"):
                    raise RpcError(redact(json.dumps(params)))
                if predicate(msg):
                    return msg

    def stage(self, name, result):
        self.report["stages"][name] = result
        print(name + ":", json.dumps(result, ensure_ascii=False), flush=True)

    async def run(self):
        await self.start()
        await self.rpc("initialize", {"clientInfo": {"name": "slovene_voice_harness", "title": "Slovene Voice Harness", "version": "0.1.0"}, "capabilities": {"experimentalApi": True}})
        self.proc.stdin.write(b'{"method":"initialized","params":{}}\n')
        await self.proc.stdin.drain()
        account = await self.rpc("account/read", {"refreshToken": False})
        kind = (account.get("account") or {}).get("type")
        self.stage("authentication", {"type": kind})
        if kind != "chatgpt":
            raise RpcError("A managed ChatGPT login is required; no API-key fallback is permitted.")
        models = await self.rpc("model/list", {"includeHidden": False})
        model = next((m for m in models.get("data", []) if m.get("model") == "gpt-6.1-sol"), None)
        self.stage("model_catalog", {"found": bool(model), "efforts": model.get("supportedReasoningEfforts", []) if model else []})
        if not model:
            raise RpcError("GPT-6.1 Sol is absent from this account/client model catalog.")
        thread = await self.rpc("thread/start", {
            "model": "gpt-6.1-sol", "modelProvider": "openai", "allowProviderModelFallback": False,
            "cwd": str(Path.cwd()), "sandbox": "read-only", "approvalPolicy": "never", "ephemeral": True,
            "config": {"model_reasoning_effort": "xhigh"},
            "developerInstructions": "Odgovarjaj slovensko. To je preizkus glasovnega vmesnika. Ne uporabljaj orodij in ne spreminjaj datotek.",
        })
        self.thread_id = thread["thread"]["id"]
        self.stage("thread", {"created": True, "model": thread.get("model"), "effort": thread.get("reasoningEffort")})
        try:
            voices = await self.rpc("thread/realtime/listVoices", {})
            self.stage("voices", {"available": True, "response_keys": list(voices)})
        except RpcError as exc:
            self.stage("voices", {"available": False, "error": str(exc)})
        transport = {"type": "websocket"}
        if self.sdp_file:
            transport = {"type": "webrtc", "sdp": Path(self.sdp_file).read_text()}
        result = await self.rpc("thread/realtime/start", {
            "threadId": self.thread_id, "outputModality": "audio", "version": "v3",
            "transport": transport, "includeStartupContext": False,
            "prompt": "Govori izključno slovensko. To je kratek tehnični preizkus. Na poziv izgovori zahtevano besedilo.",
        }, timeout=50)
        self.stage("voice_start", {"accepted": True, "result": result})
        if self.sdp_file:
            answer = await self.wait_for(lambda m: m.get("method") == "thread/realtime/sdp", 30)
            self.stage("webrtc_answer", {"received": True})
            Path(str(self.sdp_file) + ".answer").write_text(answer["params"]["sdp"])
            return
        await self.wait_for(lambda m: m.get("method") == "thread/realtime/started", 20)
        await self.rpc("thread/realtime/appendSpeech", {"threadId": self.thread_id, "text": "Pozdravljeni. To je preizkus slovenskega glasovnega pomočnika."})
        audio = await self.wait_for(lambda m: m.get("method") == "thread/realtime/outputAudio/delta", 30)
        self.stage("audio_output", {"received": True, "notification": audio["method"]})

    async def close(self):
        if self.proc and self.proc.returncode is None:
            if self.thread_id:
                with contextlib.suppress(Exception):
                    await self.rpc("thread/realtime/stop", {"threadId": self.thread_id}, timeout=5)
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.proc.pid, signal.SIGTERM)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.proc.wait(), 5)
            if self.proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.proc.pid, signal.SIGKILL)
                await self.proc.wait()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdp-file", help="Use a browser-generated SDP offer to test the WebRTC transport")
    parser.add_argument("--report", default="native-voice-probe.json")
    args = parser.parse_args()
    probe = Probe(args.sdp_file)
    try:
        await probe.run()
        probe.report["status"] = "webrtc_answer_verified" if args.sdp_file else "voice_output_verified"
    except Exception as exc:
        probe.report.update(status="blocked", error=redact(exc), error_type=type(exc).__name__)
        print("PROBE BLOCKED:", redact(exc), flush=True)
    finally:
        await probe.close()
        probe.report["events"] = dict(probe.counts)
        report_path = Path(args.report)
        report_path.write_text(json.dumps(probe.report, ensure_ascii=False, indent=2) + "\n")
        print("Report:", report_path.resolve(), flush=True)
    return 0 if probe.report["status"] != "blocked" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
