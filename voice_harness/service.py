import asyncio
import copy
from collections import deque
import contextlib
import json
from datetime import datetime, timezone
import time
import re
import uuid

from .native_voice import NativeVoice
from .desktop import DesktopWorker
from .rpc import CodexRpc, EFFORT, MODEL, RpcError, safe_error
from .state import StateStore

BACKEND_INSTRUCTIONS = """You are the reasoning backend of a personal Slovenian voice assistant.
Reply in clear Slovenian, preserving English technical terms when useful. Never substitute
Croatian or Serbian. Preserve numbers and negations. Ask when the request is ambiguous.
Work carefully and follow corrections without losing the original objective. You are selected
as GPT-6.1 Sol at xhigh effort. Give a useful answer suitable for speaking, using short paragraphs.
Do not expose private reasoning. This iteration is conversation and analysis only: do not use
shell commands, modify files, send messages, or invoke external tools. The host will not approve
actions. If a voice answer was interrupted, the caller may not have heard its remaining content.
"""

CANCEL_COMMAND = re.compile(r"^(?:prosim[, ]+)?(?:prekliči|preklici|ustavi)\s+nalogo[.!?\s]*$", re.IGNORECASE)
DEEP_COMMAND = re.compile(r"^(?:prosim[, ]+)?premisli\s+temeljito\b", re.IGNORECASE)
SOCIAL_TURN = re.compile(r"^(?:živjo|zdravo|pozdravljen[ai]?|hvala(?: lepa| ti)?|super|v redu|ok(?:ay)?|aha|mhm|ja|ne|nadaljuj(?: prosim)?|ponovi(?: prosim)?)[.!?\s]*$", re.IGNORECASE)
CORRECTION = re.compile(r"^(?:popravek\b|ne[, ]|mislil[ a]? sem\b|raje\b|upoštevaj\b|dodaj\b|pravzaprav\b)", re.IGNORECASE)
VOICE_DEBOUNCE_SECONDS = 0.9


def now():
    return datetime.now(timezone.utc).isoformat()


class Harness:
    def __init__(self, cwd, state_dir, rpc_factory=CodexRpc, desktop_factory=DesktopWorker):
        self.cwd = str(cwd)
        self.store = StateStore(state_dir)
        self.state = {
            "connection": "starting", "voice": "off", "thread_id": None,
            "model": MODEL, "effort": EFFORT, "auth": None, "error": None,
            "task": None, "transcript": [], "usage": None, "deep_requested": False,
            "playback": "allow",
            "input_revision": 0,
            "backend": "desktop" if desktop_factory else "app-server",
            "desktop_thread_id": None,
            "desktop": {"connection": "starting" if desktop_factory else "disabled", "thread_id": None},
        }
        self.rpc = rpc_factory(cwd, self.on_event)
        self.worker = desktop_factory(cwd, self.on_event) if desktop_factory else None
        self.desktop_factory = desktop_factory
        self.voice = NativeVoice(self.rpc)
        self.listeners = set()
        self.submit_lock = asyncio.Lock()
        self.voice_lock = asyncio.Lock()
        self.phases = {}
        self.results = {}
        self.canonical_transcripts = False
        self.flat_items = {}
        self.last_interruption = 0.0
        self.last_user_text = ""
        self.routed_voice = deque(maxlen=30)
        self.routed_voice_items = {}
        self.voice_input_timer = None
        self.voice_pending_parts = {}
        self.consumed_voice_text = {}
        self.voice_speaking = False
        self.voice_episode = 0
        self.last_routed_episode = None
        self.last_routed_at = 0
        self.voice_inputs_enabled = True
        self.owner = None

    def snapshot(self):
        return copy.deepcopy(self.state)

    def publish(self, persist=False):
        self.state["transcript"] = self.state["transcript"][-300:]
        snapshot = self.snapshot()
        for queue in self.listeners:
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(snapshot)
        if persist:
            self.store.save({key: self.state[key] for key in ("thread_id", "desktop_thread_id", "model", "effort", "transcript", "task")})

    def subscribe(self):
        queue = asyncio.Queue(maxsize=8)
        self.listeners.add(queue)
        queue.put_nowait(self.snapshot())
        return queue

    def fail(self, error):
        self.state["error"] = safe_error(error)
        self.publish(persist=True)

    async def start(self):
        try:
            saved = self.store.load()
            for key in ("thread_id", "desktop_thread_id", "transcript", "task"):
                if key in saved:
                    self.state[key] = saved[key]
            task = self.state["task"]
            if task and task.get("status") in ("running", "starting", "cancelling"):
                task["status"] = "unknown"
                task["error"] = "Application restarted during this task. It was not automatically repeated."
            await self.rpc.start()
            account = await self.rpc.request("account/read", {"refreshToken": False})
            auth = account.get("account") or {}
            if auth.get("type") != "chatgpt":
                raise RpcError("Sign in with ChatGPT using `codex login` first. API-key billing is disabled.")
            self.state["auth"] = {"type": "chatgpt", "plan": auth.get("planType")}
            catalog = await self.rpc.request("model/list", {"includeHidden": False})
            model = next((m for m in catalog.get("data", []) if m.get("model") == MODEL), None)
            efforts = [e.get("reasoningEffort") for e in (model or {}).get("supportedReasoningEfforts", [])]
            if not model or EFFORT not in efforts:
                raise RpcError(f"This account/client does not list {MODEL} with {EFFORT}. No model fallback was selected.")
            if self.state["thread_id"]:
                await self._thread(resume=True)
            if self.worker:
                try:
                    await self.worker.start(self.state["desktop_thread_id"], task.get("turn_id") if task and task.get("backend") == "desktop" else None)
                    self.state["desktop"]["connection"] = "ready"
                except Exception as exc:
                    self.state["desktop"]["connection"] = "error"
                    self.state["error"] = safe_error(exc)
            self.state["connection"] = "ready"
            self.publish(persist=True)
        except Exception as exc:
            self.state["connection"] = "error"
            self.fail(exc)

    def _require_ready(self):
        if self.state["connection"] != "ready":
            raise RpcError(self.state["error"] or "Codex is not connected yet.")

    async def _thread(self, resume=False):
        if self.state["thread_id"] and not resume:
            return self.state["thread_id"]
        params = {
            "model": MODEL, "modelProvider": "openai", "cwd": self.cwd,
            "sandbox": "read-only", "approvalPolicy": "never",
            "developerInstructions": BACKEND_INSTRUCTIONS,
            "config": {"model_reasoning_effort": EFFORT},
            "serviceTier": "default",
        }
        if resume:
            params.update(threadId=self.state["thread_id"], excludeTurns=True)
            try:
                result = await self.rpc.request("thread/resume", params)
            except RpcError as exc:
                if "already has an active writer" not in str(exc):
                    raise
                # Another Codex client can own the saved thread after desktop startup.
                # Preserve its history in a writable continuation without interrupting
                # that client, discarding context, or restarting an unfinished task.
                result = await self.rpc.request("thread/fork", {**params, "deferGoalContinuation": True})
        else:
            params.update(allowProviderModelFallback=False, environments=[])
            result = await self.rpc.request("thread/start", params)
        if result.get("model") != MODEL or result.get("reasoningEffort") != EFFORT:
            raise RpcError("Codex returned a different model or reasoning effort; refusing to continue.")
        self.state.update(thread_id=result["thread"]["id"], model=result["model"], effort=result["reasoningEffort"])
        self.publish(persist=True)
        return self.state["thread_id"]

    async def start_voice(self, sdp, owner):
        self._require_ready()
        async with self.voice_lock:
            if self.owner and self.owner != owner:
                raise RpcError("Another browser tab owns the active voice session.")
            if self.state["voice"] in ("connecting", "connected"):
                raise RpcError("End the active conversation before starting another.")
            async with self.submit_lock:
                thread_id = await self._thread()
            self.owner = owner
            self.voice_inputs_enabled = True
            task = self.state["task"]
            self.state.update(voice="connecting", error=None, playback="wait" if task and task.get("status") in ("running", "starting", "cancelling") else "allow")
            self.publish()
            try:
                answer = await self.voice.start(thread_id, sdp)
                return {"sdp": answer, "type": "answer"}
            except Exception as exc:
                self.owner = None
                self.state["voice"] = "error"
                self.fail(exc)
                raise

    def require_owner(self, owner):
        if self.owner and owner != self.owner:
            raise RpcError("This voice session belongs to another browser tab.")

    async def stop_voice(self, owner=None):
        self.require_owner(owner)
        async with self.voice_lock:
            self.voice_inputs_enabled = False
            if self.voice_input_timer:
                self.voice_input_timer.cancel()
                self.voice_input_timer = None
            self.voice_pending_parts.clear()
            self.voice_speaking = False
            try:
                await self.voice.stop()
            finally:
                self.owner = None
                self.state["voice"] = "off"
                self.publish(persist=True)

    async def browser_activity(self, kind, owner):
        self.require_owner(owner)
        if kind == "input_started":
            self.voice_speaking = True
            self.voice_episode += 1
            if self.voice_input_timer:
                self.voice_input_timer.cancel()
                self.voice_input_timer = None
        elif kind == "input_stopped":
            self.voice_speaking = False
            self._schedule_voice_input()
        elif kind == "connected":
            if self.voice.active:
                self.state["voice"] = "connected"
                self.publish()
        elif kind == "interrupted":
            if time.monotonic() - self.last_interruption < 0.8:
                return
            self.last_interruption = time.monotonic()
            for entry in reversed(self.state["transcript"]):
                if entry["role"] == "assistant" and entry["source"] == "voice":
                    entry["interrupted"] = True
                    break
            self.publish(persist=True)
            # A voice interruption does not call turn/interrupt.
            await self.voice.context("The caller interrupted the spoken answer and may not have heard its remaining content. Stop speaking, listen, and preserve any ongoing backend task unless they cancel or change it.")

    def add_entry(self, role, text, source, entry_id=None, **extra):
        entry_id = entry_id or str(uuid.uuid4())
        entry = next((e for e in self.state["transcript"] if e["id"] == entry_id), None)
        if entry is None:
            entry = {"id": entry_id, "role": role, "text": text, "source": source, "time": now(), "partial": False, "interrupted": False}
            self.state["transcript"].append(entry)
        else:
            entry["text"] = text
        entry.update(extra)
        return entry

    async def message(self, text, deep=False, record_user=True):
        self._require_ready()
        if CANCEL_COMMAND.fullmatch(text.strip()):
            if record_user:
                self.add_entry("user", text, "typed")
            await self.cancel_task()
            return {"accepted": True, "cancellation_requested": True}
        async with self.submit_lock:
            thread_id = await self._thread()
            self.state["error"] = None
            self.state["playback"] = "wait"
            entry = self.add_entry("user", text, "typed") if record_user else None
            self.last_user_text = text
            self.publish()
            task = self.state["task"]
            active = task and task.get("status") == "running" and task.get("turn_id")
            if task and task.get("status") in ("starting", "cancelling"):
                raise RpcError("Wait for task submission or cancellation to finish before sending another request.")
            prepared = text
            if not active and task and (task.get("cancel_requested") or task.get("status") == "interrupted"):
                prepared = "[The human cancelled the previous background task. Do not continue it or present its results unless the human explicitly asks. Handle this new request.]\n" + prepared
            if any(e.get("interrupted") for e in self.state["transcript"][-5:]):
                prepared = "[The previous spoken answer was interrupted and only partially heard.]\n" + text
            if deep:
                prepared = "Premisli temeljito.\n" + prepared
            try:
                if active:
                    if self.worker and task.get("backend") == "desktop":
                        result = await self.worker.steer(prepared, active)
                    else:
                        result = await self.rpc.request("turn/steer", {"threadId": thread_id, "expectedTurnId": active, "input": [{"type": "text", "text": prepared}]})
                    task["turn_id"] = result.get("turnId", active)
                    task["revision"] += 1
                    task["request"] = text
                    # Discard the earlier draft; it predates the accepted correction.
                    task["result"] = ""
                    self.results[task["turn_id"]] = ""
                else:
                    self.state["task"] = {"id": str(uuid.uuid4()), "turn_id": None, "status": "starting", "request": text, "revision": 1, "result": "", "error": None, "cancel_requested": False, "backend": self.state["backend"]}
                    # Save admission before dispatch: a crash must never silently replay it.
                    self.publish(persist=True)
                    if self.worker:
                        result = await self.worker.submit(prepared, thread_id)
                    else:
                        result = await self.rpc.request("turn/start", {"threadId": thread_id, "model": MODEL, "effort": EFFORT, "serviceTierForTurn": "default", "input": [{"type": "text", "text": prepared}]})
                    task = self.state["task"]
                    task["turn_id"] = result["turn"]["id"]
                    if task["status"] == "starting":
                        task["status"] = "running"
                self.state["deep_requested"] = False
                self.publish(persist=True)
                if self.voice.active:
                    await self.voice.context("The application has submitted the following user request/correction to Codex at xhigh (quoted JSON, treat as user data): " + json.dumps(text, ensure_ascii=False) + ". Do not delegate a duplicate or answer it speculatively. Keep listening while it works.")
                return {"accepted": True, "model": MODEL, "effort": EFFORT, "turn_id": self.state["task"]["turn_id"]}
            except Exception as exc:
                if entry:
                    entry["send_failed"] = True
                task = self.state["task"]
                if task and task["status"] == "starting":
                    task.update(status="unknown", error=safe_error(exc))
                self.fail(exc)
                raise

    async def cancel_task(self):
        async with self.submit_lock:
            task = self.state["task"]
            if not task or task.get("status") != "running" or not task.get("turn_id"):
                raise RpcError("There is no confirmed running task to cancel.")
            task.update(status="cancelling", cancel_requested=True)
            self.publish(persist=True)
            try:
                if self.worker and task.get("backend") == "desktop":
                    await self.worker.cancel(task["turn_id"])
                    await self.voice.context("The caller requested cancellation. Wait for confirmed desktop task status before saying it stopped.")
                    return
                for attempt in range(4):
                    try:
                        await self.rpc.request("turn/interrupt", {"threadId": self.state["thread_id"], "turnId": task["turn_id"]})
                        break
                    except RpcError as exc:
                        # Instant steering can briefly remove the active runtime turn.
                        # Retrying cancellation is idempotent; never replay task execution.
                        if task.get("status") in ("completed", "failed", "interrupted"):
                            return
                        if "no active turn" not in str(exc).lower() or attempt == 3:
                            raise
                        await asyncio.sleep(0.1 * (attempt + 1))
                        # Some alpha builds persist the interruption before delivering
                        # its notification. Confirm an idle runtime plus terminal history.
                        runtime = await self.rpc.request("thread/read", {"threadId": self.state["thread_id"]})
                        if runtime.get("thread", {}).get("status", {}).get("type") == "idle":
                            history = await self.rpc.request("thread/turns/list", {"threadId": self.state["thread_id"], "limit": 10})
                            finished = next((turn for turn in history.get("data", []) if turn.get("id") == task["turn_id"] and turn.get("status") in ("completed", "failed", "interrupted")), None)
                            if finished:
                                await self._turn_completed(finished)
                                return
                # Success is confirmed by turn/completed with interrupted status.
                await self.voice.context("The caller requested cancellation. Do not announce it as cancelled until the application confirms the backend stopped.")
            except Exception as exc:
                task.update(status="unknown", error=safe_error(exc))
                self.fail(exc)
                raise

    async def reconnect_desktop(self):
        if not self.worker:
            raise RpcError("Desktop delegation is not enabled.")
        async with self.submit_lock:
            await self.worker.close()
            self.worker = self.desktop_factory(self.cwd, self.on_event)
            task = self.state["task"]
            tracked = task.get("turn_id") if task and task.get("backend") == "desktop" else None
            self.state["desktop"]["connection"] = "starting"
            self.publish()
            try:
                await self.worker.start(self.state["desktop_thread_id"], tracked)
                self.state["desktop"]["connection"] = "ready"
                self.state["error"] = None
                self.publish(persist=True)
            except Exception as exc:
                self.state["desktop"]["connection"] = "error"
                self.fail(exc)
                raise

    async def on_event(self, message):
        if message.get("origin") == "desktop":
            await self._desktop_event(message)
            return
        self.voice.handle_event(message)
        method = message.get("method", "")
        params = message.get("params", {})
        if method == "transport/closed":
            self.state.update(connection="error", voice="off")
            if self.state["task"] and self.state["task"]["status"] in ("starting", "running", "cancelling"):
                self.state["task"]["status"] = "unknown"
            self.fail(params.get("message", "Codex disconnected."))
            return
        if params.get("threadId") not in (None, self.state["thread_id"]):
            return
        if self.worker and (method.startswith("turn/") or method.startswith("item/") or method == "error"):
            return  # Native voice's backing thread must not compete with the desktop worker.
        if method == "thread/realtime/error":
            self.state["voice"] = "error"
            self.fail(params.get("message", "Native voice failed."))
        elif method == "thread/realtime/closed":
            self.owner = None
            self.state["voice"] = "off"
            if params.get("reason") and params["reason"] not in ("requested", "stopped", "ended"):
                self.state["error"] = safe_error(params["reason"])
            self.publish(persist=True)
        elif method.startswith("thread/realtime/item/"):
            await self._voice_item(method, params)
        elif method.startswith("thread/realtime/transcript/") and not self.canonical_transcripts:
            role = params.get("role", "assistant")
            item_id = self.flat_items.setdefault(role, "flat-" + str(uuid.uuid4()))
            previous = next((e["text"] for e in self.state["transcript"] if e["id"] == item_id), "")
            done = method.endswith("/done")
            text = params.get("text", "") if done else previous + params.get("delta", "")
            self.add_entry(role, text, "voice", item_id, partial=not done)
            if done:
                self.flat_items.pop(role, None)
                if role == "user":
                    entry = next(e for e in self.state["transcript"] if e["id"] == item_id)
                    self._queue_voice_entry(entry)
            self.publish(persist=done)
        elif method == "turn/started":
            turn_id = params["turn"]["id"]
            task = self.state["task"]
            if task and task.get("turn_id") == turn_id and task.get("status") in ("completed", "failed", "interrupted"):
                return
            if not task or (task.get("status") != "starting" and task.get("turn_id") != turn_id):
                task = {"id": str(uuid.uuid4()), "request": self.last_user_text or "Voice delegation", "revision": 1, "result": "", "error": None, "cancel_requested": False}
                self.state["task"] = task
            task.update(turn_id=turn_id, status="cancelling" if task.get("cancel_requested") else "running")
            self.results[turn_id] = ""
            self.state["deep_requested"] = False
            self.publish(persist=True)
        elif method in ("item/started", "item/completed"):
            item = params.get("item", {})
            if item.get("type") == "agentMessage":
                self.phases[item["id"]] = item.get("phase")
                if method == "item/completed" and item.get("phase") != "commentary":
                    turn_id = params.get("turnId")
                    self.results[turn_id] = item.get("text", "")
                    task = self.state["task"]
                    if task and task.get("turn_id") == turn_id:
                        task["result"] = item.get("text", "")
                        self.publish()
        elif method == "item/agentMessage/delta":
            task = self.state["task"]
            if task and task.get("turn_id") == params.get("turnId") and self.phases.get(params.get("itemId")) != "commentary":
                task["result"] += params.get("delta", "")
                self.publish()
        elif method == "turn/completed":
            await self._turn_completed(params["turn"])
        elif method == "thread/tokenUsage/updated":
            self.state["usage"] = params.get("tokenUsage")
            self.publish()
        elif method == "error":
            self.fail(params.get("error", {}).get("message", "Codex reported an error."))

    async def _desktop_event(self, message):
        method, params = message["method"], message.get("params", {})
        if params.get("threadId"):
            self.state["desktop_thread_id"] = params["threadId"]
            self.state["desktop"]["thread_id"] = params["threadId"]
        if method == "desktop/status":
            self.state["desktop"].update({k: v for k, v in params.items() if k != "threadId"})
            self.publish(persist=True)
            return
        task = self.state["task"]
        if method == "desktop/error":
            self.state["desktop"]["connection"] = "error"
            if task and task.get("backend") == "desktop" and task.get("status") in ("starting", "running", "cancelling"):
                task.update(status="unknown", error=params.get("message"))
            self.fail(params.get("message", "Codex desktop disconnected."))
            return
        if method != "desktop/turn" or not task or task.get("backend") != "desktop":
            return
        turn = params["turn"]
        if task.get("status") == "starting" and not task.get("turn_id"):
            task["turn_id"] = turn["id"]
        if task.get("turn_id") != turn["id"]:
            return
        self.state["desktop"].update(connection="ready", model=turn.get("model"), effort=turn.get("effort"))
        if turn.get("model") != MODEL or turn.get("effort") != EFFORT:
            task.update(status="unknown", error="Desktop model/effort differs from the requested Sol xhigh.")
            self.fail(task["error"])
            return
        was_terminal = task.get("status") in ("completed", "failed", "interrupted")
        task.update(result=turn.get("result", ""), progress=turn.get("progress", ""), activity=turn.get("activity", ""), tool_count=turn.get("tool_count", 0), waiting_for_user=turn.get("waiting_for_user", False))
        self.results[turn["id"]] = task["result"]
        if turn["status"] == "inProgress":
            task["status"] = "cancelling" if task.get("cancel_requested") else "running"
            self.publish(persist=True)
        elif turn["status"] in ("completed", "interrupted", "failed") and not was_terminal:
            await self._turn_completed(turn)

    async def _voice_item(self, method, params):
        if not self.canonical_transcripts:
            self.voice_pending_parts = {key: value for key, value in self.voice_pending_parts.items() if not key.startswith("flat-")}
        self.canonical_transcripts = True
        # Canonical timeline supersedes any provisional flat transcript.
        self.state["transcript"] = [e for e in self.state["transcript"] if not e["id"].startswith("flat-")]
        if method == "thread/realtime/item/transcript/delta":
            entry = next((e for e in self.state["transcript"] if e["id"] == params.get("itemId")), None)
            if entry:
                entry["text"] += params.get("delta", "")
                if entry["role"] == "user":
                    self._queue_voice_entry(entry)
                self.publish()
            return
        item = params.get("item", {})
        if item.get("type") != "transcriptSegment":
            return
        done = method == "thread/realtime/item/completed"
        held = item["role"] == "assistant" and self.state["playback"] == "wait"
        self.add_entry(item["role"], item.get("text", ""), "voice", item["id"], partial=not done, held=held)
        if item["role"] == "user" and item.get("text"):
            self._queue_voice_entry(next(e for e in self.state["transcript"] if e["id"] == item["id"]))
        self.publish(persist=done)

    def _queue_voice_entry(self, entry):
        if not self.voice_inputs_enabled:
            return
        consumed = self.consumed_voice_text.get(entry["id"], "")
        text = entry["text"].strip()
        consumed = consumed.strip()
        if re.sub(r"[.!?\s]+", " ", text).strip().casefold() == re.sub(r"[.!?\s]+", " ", consumed).strip().casefold():
            return
        tail = text[len(consumed):] if text.startswith(consumed) else text
        if not tail.strip():
            return
        self.voice_pending_parts[entry["id"]] = (text, tail)
        self._schedule_voice_input()

    def _schedule_voice_input(self):
        if self.voice_input_timer:
            self.voice_input_timer.cancel()
            self.voice_input_timer = None
        if not self.voice_inputs_enabled or self.voice_speaking or not self.voice_pending_parts:
            return

        async def route():
            try:
                await asyncio.sleep(VOICE_DEBOUNCE_SECONDS)
                self.voice_input_timer = None
                parts = self.voice_pending_parts
                self.voice_pending_parts = {}
                text = " ".join(tail.strip() for _, tail in parts.values())
                self.consumed_voice_text.update({item_id: full for item_id, (full, _) in parts.items()})
                while len(self.consumed_voice_text) > 300:
                    del self.consumed_voice_text[next(iter(self.consumed_voice_text))]
                continuation = self.voice_episode == self.last_routed_episode and time.monotonic() - self.last_routed_at < 5
                self.last_routed_episode = self.voice_episode
                self.last_routed_at = time.monotonic()
                await self._user_voice_text("user", text, "utterance-" + str(uuid.uuid4()), continuation=continuation)
            finally:
                if self.voice_input_timer is asyncio.current_task():
                    self.voice_input_timer = None

        self.voice_input_timer = asyncio.create_task(route())

    async def _user_voice_text(self, role, text, item_id=None, continuation=False):
        if role == "user" and self.voice_inputs_enabled:
            key = re.sub(r"[.!?\s]+", " ", text).strip().casefold()
            current = time.monotonic()
            previous_item = self.routed_voice_items.get(item_id) if item_id else None
            if previous_item == key:
                return
            if any(previous == key and current - timestamp < 3 for previous, timestamp in self.routed_voice):
                return
            if item_id:
                self.routed_voice_items[item_id] = key
                if len(self.routed_voice_items) > 300:
                    del self.routed_voice_items[next(iter(self.routed_voice_items))]
            self.routed_voice.append((key, current))
            self.state["input_revision"] += 1
            self.last_user_text = text
            if CANCEL_COMMAND.fullmatch(text.strip()):
                task = self.state["task"]
                if task and task.get("status") == "running":
                    try:
                        await self.cancel_task()
                    except RpcError as exc:
                        self.fail(exc)
                return
            text = text.strip()
            deep = bool(DEEP_COMMAND.match(text))
            task = self.state["task"]
            active = task and task.get("status") in ("starting", "running", "cancelling")
            if not text or SOCIAL_TURN.fullmatch(text):
                self.state["playback"] = "allow"
                self.publish()
                return
            # While work runs, native voice can handle side conversation. Only explicit
            # deep requests and corrections steer the existing task.
            if active and not deep and not CORRECTION.match(text) and previous_item is None and not continuation:
                self.state["playback"] = "allow"
                self.publish()
                return
            self.state["deep_requested"] = deep
            try:
                await self.message(text, deep=deep, record_user=False)
            except Exception as exc:
                self.fail(exc)

    async def _turn_completed(self, turn):
        task = self.state["task"]
        if not task or task.get("turn_id") != turn["id"]:
            self.results.pop(turn["id"], None)
            return  # An obsolete turn must never be spoken over a newer request.
        task["status"] = turn["status"]
        task["error"] = turn.get("error")
        task["result"] = self.results.pop(turn["id"], task.get("result", ""))
        task["finished"] = now()
        if task["status"] == "failed":
            error = turn.get("error") or {}
            self.state["error"] = safe_error(error.get("message", "The reasoning task failed."))
        self.publish(persist=True)
        if task["status"] == "completed" and task["result"] and not task.get("cancel_requested") and not self.voice.active:
            self.add_entry("assistant", task["result"], "reasoning", entry_id=turn["id"])
            self.publish(persist=True)
        if task["status"] == "interrupted":
            await self.voice.context("The application confirms that the backend task was interrupted and stopped.")
        elif task["status"] == "completed" and task.get("cancel_requested"):
            await self.voice.context("The task completed before cancellation took effect. Do not claim it was cancelled. Do not read the result unless asked.")
        elif task["status"] == "completed" and task["result"] and self.voice.active:
            try:
                await self.voice.speak(task["result"])
                # Release native audio only once a verified backend answer has been
                # handed back. Speculative frontend answers during reasoning stay muted.
                self.state["playback"] = "allow"
                self.publish()
            except Exception as exc:
                self.fail("The answer is available in text, but voice delivery failed: " + safe_error(exc))

    async def close(self):
        with contextlib.suppress(Exception):
            await self.stop_voice(self.owner)
        task = self.state["task"]
        if task and task.get("status") in ("running", "starting", "cancelling"):
            task["status"] = "unknown"
        self.publish(persist=True)
        if self.worker:
            await self.worker.close()
        await self.rpc.close()
