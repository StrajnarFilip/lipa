"""Experimental native voice adapter. WebSocket audio needs different auth."""

import asyncio
import contextlib
from .rpc import RpcError

VOICE_PROMPT = """You are a calm voice assistant speaking Slovenian (slovenščina).
Speak Slovenian consistently, never substitute Croatian or Serbian. Preserve English technical
terms and exact names, numbers, and negations. If audio is unclear, ask instead of guessing.
Use concise, natural speech. Do not read markdown, URLs, code, or private reasoning aloud.

Backchannel policy: Listen without competing with the caller. Brief acknowledgments are enough.
Interruption policy: Stop speaking when interrupted. Listen, apply corrections, and keep the
original task unless the caller changes or cancels it. An interrupted reply was only partly heard.
When asked 'Nadaljuj', continue from the relevant unfinished point without restarting everything.

Delegation policy: The APPLICATION routes spoken requests to a backing Codex model,
GPT-6.1 Sol at xhigh. Do NOT create native backend delegations or Generate <|SpawnThinking|>.
For a new substantive question, briefly acknowledge that you will think and wait for the
application's verified answer instead of answering speculatively. When the caller says
'Premisli temeljito', acknowledge and wait: the application will explicitly start/steer Codex.
While backend work runs you can handle brief side conversation, greetings, and clarifications
without abandoning the original task. Corrections are routed by the application. Never claim
the backend ran or completed until it actually did. Relay verified backend results faithfully
in Slovenian. Speech interruption alone must not cancel backend work.
The application handles the exact spoken command 'Prekliči nalogo'; do not delegate a new task
just to cancel. Wait for application confirmation before announcing that the task stopped.
"""


class NativeVoice:
    def __init__(self, rpc):
        self.rpc = rpc
        self.thread_id = None
        self.active = False
        self.answer_future = None

    async def start(self, thread_id, sdp):
        if self.active:
            raise RpcError("A voice conversation is already active. End it before starting another.")
        self.thread_id = thread_id
        self.answer_future = asyncio.get_running_loop().create_future()
        self.active = True
        try:
            await self.rpc.request("thread/realtime/start", {
                "threadId": thread_id, "version": "v3", "outputModality": "audio",
                "transport": {"type": "webrtc", "sdp": sdp},
                "prompt": VOICE_PROMPT, "includeStartupContext": True,
                "clientManagedHandoffs": True, "backendReasoningStatus": True,
                "flushTranscriptTailOnSessionEnd": False,
            })
            return await asyncio.wait_for(self.answer_future, 35)
        except BaseException:
            if not self.answer_future.done():
                self.answer_future.cancel()
            elif not self.answer_future.cancelled():
                self.answer_future.exception()
            with contextlib.suppress(Exception):
                await self.stop()
            raise
        finally:
            self.answer_future = None

    def handle_event(self, message):
        params = message.get("params", {})
        if params.get("threadId") != self.thread_id:
            return
        method = message.get("method")
        future = self.answer_future
        if method == "thread/realtime/sdp" and future and not future.done():
            future.set_result(params["sdp"])
        elif method in ("thread/realtime/error", "thread/realtime/closed"):
            self.active = False
            if future and not future.done():
                future.set_exception(RpcError(params.get("message") or params.get("reason") or "Native voice closed before connecting."))

    async def context(self, text):
        if self.active:
            await self.rpc.request("thread/realtime/appendText", {"threadId": self.thread_id, "role": "developer", "text": text})

    async def speak(self, text):
        if self.active and text.strip():
            await self.rpc.request("thread/realtime/appendSpeech", {"threadId": self.thread_id, "text": text})

    async def stop(self):
        if self.active:
            try:
                await self.rpc.request("thread/realtime/stop", {"threadId": self.thread_id}, timeout=10)
            finally:
                self.active = False
