import asyncio
import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

from voice_harness.app import create_app
from voice_harness.native_voice import NativeVoice
from voice_harness.rpc import CodexRpc, RpcError, safe_error
from voice_harness.service import Harness
from voice_harness.state import StateStore


class FakeRpc:
    def __init__(self, cwd, on_event):
        self.on_event = on_event
        self.calls = []
        self.replies = {}
        self.auth = "chatgpt"
        self.next_turn = "turn-1"

    async def start(self):
        pass

    async def close(self):
        pass

    async def request(self, method, params, **kwargs):
        self.calls.append((method, params))
        if method in self.replies:
            reply = self.replies[method]
            if isinstance(reply, Exception):
                raise reply
            return reply
        return {
            "account/read": {"account": {"type": self.auth, "planType": "plus"}},
            "model/list": {"data": [{"model": "gpt-6.1-sol", "supportedReasoningEfforts": [{"reasoningEffort": "xhigh"}]}]},
            "thread/start": {"thread": {"id": "thread-1"}, "model": "gpt-6.1-sol", "reasoningEffort": "xhigh"},
            "thread/resume": {"thread": {"id": "thread-1"}, "model": "gpt-6.1-sol", "reasoningEffort": "xhigh"},
            "turn/start": {"turn": {"id": self.next_turn, "status": "inProgress"}},
        }.get(method, {})


@pytest.fixture
async def harness(tmp_path):
    h = Harness(tmp_path, tmp_path / "state", rpc_factory=FakeRpc, desktop_factory=None)
    await h.start()
    yield h
    await h.close()


def event(method, **params):
    return {"method": method, "params": {"threadId": "thread-1", **params}}


@pytest.mark.asyncio
async def test_exact_model_effort_and_no_fallback(harness):
    result = await harness.message("Primerjaj dve možnosti.", deep=True)
    assert result["effort"] == "xhigh"
    method, params = next(call for call in harness.rpc.calls if call[0] == "thread/start")
    assert params["allowProviderModelFallback"] is False
    assert params["modelProvider"] == "openai"
    assert params["sandbox"] == "read-only"
    turn = next(params for method, params in harness.rpc.calls if method == "turn/start")
    assert turn["model"] == "gpt-6.1-sol" and turn["effort"] == "xhigh"
    assert "Premisli temeljito" in turn["input"][0]["text"]
    assert harness.state["playback"] == "wait"


@pytest.mark.asyncio
async def test_turn_started_after_ack_preserves_request(harness):
    await harness.message("Moje vprašanje")
    original_id = harness.state["task"]["id"]
    await harness.on_event(event("turn/started", turn={"id": "turn-1"}))
    assert harness.state["task"]["id"] == original_id
    assert harness.state["task"]["request"] == "Moje vprašanje"


@pytest.mark.asyncio
async def test_interruption_stops_speech_but_preserves_reasoning(harness):
    await harness.message("Razmisli")
    harness.voice.active = True
    harness.voice.thread_id = "thread-1"
    harness.owner = "browser-1"
    harness.add_entry("assistant", "Dolga razlaga", "voice")
    await harness.browser_activity("interrupted", "browser-1")
    assert harness.state["task"]["status"] == "running"
    assert harness.state["transcript"][-1]["interrupted"]
    assert not any(method == "turn/interrupt" for method, _ in harness.rpc.calls)
    assert any(method == "thread/realtime/appendText" for method, _ in harness.rpc.calls)


@pytest.mark.asyncio
async def test_correction_steers_exact_turn_and_drops_old_draft(harness):
    await harness.message("Za petek")
    harness.state["task"]["result"] = "Petek ..."
    await harness.message("Popravek: za četrtek.")
    steering = next(params for method, params in harness.rpc.calls if method == "turn/steer")
    assert steering["expectedTurnId"] == "turn-1"
    assert harness.state["task"]["revision"] == 2
    assert harness.state["task"]["result"] == ""
    assert len([method for method, _ in harness.rpc.calls if method == "turn/start"]) == 1


@pytest.mark.asyncio
async def test_cancellation_waits_for_confirmation(harness):
    await harness.message("Dolga naloga")
    await harness.cancel_task()
    assert harness.state["task"]["status"] == "cancelling"
    await harness.on_event(event("turn/completed", turn={"id": "turn-1", "status": "interrupted"}))
    assert harness.state["task"]["status"] == "interrupted"


@pytest.mark.asyncio
async def test_cancellation_retries_only_the_steering_race(harness):
    await harness.message("Naloga")
    original = harness.rpc.request
    attempts = 0

    async def racing(method, params, **kwargs):
        nonlocal attempts
        if method == "turn/interrupt":
            attempts += 1
            if attempts == 1:
                raise RpcError("no active turn to interrupt")
        return await original(method, params, **kwargs)

    harness.rpc.request = racing
    await harness.cancel_task()
    assert attempts == 2
    assert harness.state["task"]["status"] == "cancelling"
    assert len([method for method, _ in harness.rpc.calls if method == "turn/start"]) == 1


@pytest.mark.asyncio
async def test_cancellation_reconciles_idle_runtime_with_durable_interruption(harness):
    await harness.message("Naloga")
    harness.rpc.replies["turn/interrupt"] = RpcError("no active turn to interrupt")
    harness.rpc.replies["thread/read"] = {"thread": {"status": {"type": "idle"}}}
    harness.rpc.replies["thread/turns/list"] = {"data": [{"id": "turn-1", "status": "interrupted"}]}
    await harness.cancel_task()
    assert harness.state["task"]["status"] == "interrupted"
    assert harness.state["error"] is None
    await harness.on_event(event("turn/started", turn={"id": "turn-1"}))
    assert harness.state["task"]["status"] == "interrupted"


@pytest.mark.asyncio
async def test_spoken_cancel_is_explicit_and_negation_does_not_cancel(harness):
    await harness.message("Naloga")
    await harness._user_voice_text("user", "Ne prekliči naloge.")
    assert not any(method == "turn/interrupt" for method, _ in harness.rpc.calls)
    await harness._user_voice_text("user", "Prekliči nalogo.")
    assert harness.state["task"]["status"] == "cancelling"
    assert any(method == "turn/interrupt" for method, _ in harness.rpc.calls)


@pytest.mark.asyncio
async def test_stale_result_is_not_spoken(harness):
    await harness.message("Nova naloga")
    harness.voice.active = True
    harness.voice.thread_id = "thread-1"
    harness.results["obsolete-turn"] = "Napačen zastarel odgovor"
    await harness.on_event(event("turn/completed", turn={"id": "obsolete-turn", "status": "completed"}))
    assert not any(method == "thread/realtime/appendSpeech" for method, _ in harness.rpc.calls)
    assert harness.state["task"]["turn_id"] == "turn-1"
    assert harness.state["playback"] == "wait"


@pytest.mark.asyncio
async def test_verified_result_releases_audio(harness):
    await harness.message("Koliko je 19 krat 17?")
    harness.voice.active = True
    harness.voice.thread_id = "thread-1"
    harness.results["turn-1"] = "323"
    await harness.on_event(event("turn/completed", turn={"id": "turn-1", "status": "completed"}))
    assert harness.state["playback"] == "allow"
    assert ("thread/realtime/appendSpeech", {"threadId": "thread-1", "text": "323"}) in harness.rpc.calls


@pytest.mark.asyncio
async def test_cancel_race_never_speaks_completed_result(harness):
    await harness.message("Naloga")
    harness.voice.active = True
    harness.voice.thread_id = "thread-1"
    await harness.cancel_task()
    harness.results["turn-1"] = "Končano"
    await harness.on_event(event("turn/completed", turn={"id": "turn-1", "status": "completed"}))
    assert harness.state["task"]["status"] == "completed"
    assert not any(method == "thread/realtime/appendSpeech" for method, _ in harness.rpc.calls)


@pytest.mark.asyncio
async def test_usage_error_is_surfaced_without_replay(harness):
    harness.rpc.replies["turn/start"] = RpcError("Usage limit exceeded", 429)
    with pytest.raises(RpcError, match="Usage limit"):
        await harness.message("Vprašanje")
    assert "Usage limit" in harness.state["error"]
    assert harness.state["transcript"][-1]["send_failed"]
    assert len([m for m, _ in harness.rpc.calls if m == "turn/start"]) == 1


@pytest.mark.asyncio
async def test_disconnect_marks_outcome_unknown_without_restart(harness):
    await harness.message("Naloga")
    calls = len(harness.rpc.calls)
    await harness.on_event({"method": "transport/closed", "params": {"message": "disconnected"}})
    assert harness.state["task"]["status"] == "unknown"
    assert harness.state["connection"] == "error"
    assert len(harness.rpc.calls) == calls


@pytest.mark.asyncio
async def test_resume_history_without_reexecuting_task(tmp_path):
    store = StateStore(tmp_path / "state")
    store.save({"thread_id": "thread-1", "transcript": [], "task": {"status": "running", "turn_id": "old-turn"}})
    h = Harness(tmp_path, tmp_path / "state", rpc_factory=FakeRpc, desktop_factory=None)
    await h.start()
    assert h.state["task"]["status"] == "unknown"
    assert any(method == "thread/resume" for method, _ in h.rpc.calls)
    assert not any(method == "turn/start" for method, _ in h.rpc.calls)
    await h.close()


@pytest.mark.asyncio
async def test_locked_saved_thread_continues_from_history_without_replaying_work(tmp_path):
    store = StateStore(tmp_path / "state")
    store.save({"thread_id": "thread-1", "transcript": [{"id": "user-1", "role": "user", "text": "Prejšnji pogovor"}], "task": {"status": "running", "turn_id": "old-turn"}})
    h = Harness(tmp_path, tmp_path / "state", rpc_factory=FakeRpc, desktop_factory=None)
    h.rpc.replies["thread/resume"] = RpcError("thread thread-1 already has an active writer")
    h.rpc.replies["thread/fork"] = {"thread": {"id": "thread-2"}, "model": "gpt-6.1-sol", "reasoningEffort": "xhigh"}
    await h.start()
    assert h.state["connection"] == "ready"
    assert h.state["thread_id"] == "thread-2"
    assert h.state["transcript"][0]["text"] == "Prejšnji pogovor"
    assert h.state["task"]["status"] == "unknown"
    fork = next(params for method, params in h.rpc.calls if method == "thread/fork")
    assert fork["threadId"] == "thread-1" and fork["deferGoalContinuation"] is True
    assert not any(method in ("turn/start", "turn/interrupt", "thread/delete") for method, _ in h.rpc.calls)
    assert store.load()["thread_id"] == "thread-2"
    await h.close()


@pytest.mark.asyncio
async def test_resume_authentication_error_does_not_fork_or_reset_history(tmp_path):
    store = StateStore(tmp_path / "state")
    store.save({"thread_id": "thread-1", "transcript": [], "task": None})
    h = Harness(tmp_path, tmp_path / "state", rpc_factory=FakeRpc, desktop_factory=None)
    h.rpc.replies["thread/resume"] = RpcError("authentication failed")
    await h.start()
    assert h.state["connection"] == "error"
    assert h.state["thread_id"] == "thread-1"
    assert not any(method == "thread/fork" for method, _ in h.rpc.calls)
    await h.close()


@pytest.mark.asyncio
async def test_api_key_authentication_rejected(tmp_path):
    h = Harness(tmp_path, tmp_path / "state", rpc_factory=FakeRpc, desktop_factory=None)
    h.rpc.auth = "apiKey"
    await h.start()
    assert h.state["connection"] == "error"
    assert "API-key billing is disabled" in h.state["error"]
    assert not any(method == "thread/start" for method, _ in h.rpc.calls)
    await h.close()


@pytest.mark.asyncio
async def test_native_voice_webrtc_and_async_rejection(harness):
    async def reject():
        await asyncio.sleep(0)
        await harness.on_event(event("thread/realtime/error", message="This client is not eligible for native voice"))

    original = harness.rpc.request

    async def request(method, params, **kwargs):
        if method == "thread/realtime/start":
            assert params["transport"]["type"] == "webrtc"
            asyncio.create_task(reject())
        return await original(method, params, **kwargs)

    harness.rpc.request = request
    with pytest.raises(RpcError, match="not eligible"):
        await harness.start_voice("test valid sdp", "browser-1")
    assert harness.state["voice"] == "error"
    assert harness.owner is None


@pytest.mark.asyncio
async def test_voice_stop_preserves_task_and_normal_close_not_error(harness):
    await harness.message("Naloga")
    harness.voice.active = True
    harness.voice.thread_id = "thread-1"
    harness.owner = "browser-1"
    await harness.stop_voice("browser-1")
    await harness.on_event(event("thread/realtime/closed", reason="requested"))
    assert harness.state["task"]["status"] == "running"
    assert harness.state["error"] is None


@pytest.mark.asyncio
async def test_canonical_transcript_and_spoken_deep_command_directly_routes(harness, monkeypatch):
    monkeypatch.setattr("voice_harness.service.VOICE_DEBOUNCE_SECONDS", 0.01)
    harness.state["thread_id"] = "thread-1"
    item = {"id": "speech-1", "type": "transcriptSegment", "role": "user", "text": ""}
    await harness.on_event(event("thread/realtime/item/started", item=item))
    await harness.on_event(event("thread/realtime/item/transcript/delta", itemId="speech-1", delta="Premisli temeljito."))
    await harness.on_event(event("thread/realtime/item/completed", item={**item, "text": "Premisli temeljito."}))
    await asyncio.sleep(0.03)
    assert len(harness.state["transcript"]) == 1
    assert not harness.state["transcript"][0]["partial"]
    assert harness.state["task"]["status"] == "running"
    calls = [params for method, params in harness.rpc.calls if method == "turn/start"]
    assert len(calls) == 1 and calls[0]["effort"] == "xhigh"
    await harness.on_event(event("thread/realtime/item/completed", item={**item, "text": "Premisli temeljito."}))
    await asyncio.sleep(0.03)
    assert len([method for method, _ in harness.rpc.calls if method == "turn/start"]) == 1
    assert not any(method == "turn/steer" for method, _ in harness.rpc.calls)


@pytest.mark.asyncio
async def test_streaming_speech_routes_before_canonical_commit(harness, monkeypatch):
    monkeypatch.setattr("voice_harness.service.VOICE_DEBOUNCE_SECONDS", 0.01)
    harness.state["thread_id"] = "thread-1"
    item = {"id": "speech-2", "type": "transcriptSegment", "role": "user", "text": ""}
    await harness.on_event(event("thread/realtime/item/started", item=item))
    await harness.on_event(event("thread/realtime/item/transcript/delta", itemId="speech-2", delta="Premisli temeljito. Koliko je 19 krat 17?"))
    await asyncio.sleep(0.03)
    assert harness.state["task"]["status"] == "running"
    assert harness.state["transcript"][0]["partial"]
    # The native timeline may commit much later; that is not a new user request.
    await harness.on_event(event("thread/realtime/item/completed", item={**item, "text": "Premisli temeljito. Koliko je 19 krat 17?"}))
    assert len([m for m, _ in harness.rpc.calls if m == "turn/start"]) == 1
    assert not any(m == "turn/steer" for m, _ in harness.rpc.calls)


@pytest.mark.asyncio
async def test_fragments_are_combined_until_microphone_stops(harness, monkeypatch):
    monkeypatch.setattr("voice_harness.service.VOICE_DEBOUNCE_SECONDS", 0.01)
    harness.owner = "browser-1"
    harness.state["thread_id"] = "thread-1"
    await harness.browser_activity("input_started", "browser-1")
    for index, text in enumerate(["Premisli temeljito, koliko je", "devetnajst krat", "sedemnajst?"]):
        item = {"id": f"fragment-{index}", "type": "transcriptSegment", "role": "user", "text": text}
        await harness.on_event(event("thread/realtime/item/completed", item=item))
        await asyncio.sleep(0.02)
    assert not any(method == "turn/start" for method, _ in harness.rpc.calls)
    await harness.browser_activity("input_stopped", "browser-1")
    await asyncio.sleep(0.03)
    requests = [params for method, params in harness.rpc.calls if method == "turn/start"]
    assert len(requests) == 1
    assert "devetnajst krat sedemnajst?" in requests[0]["input"][0]["text"]


@pytest.mark.asyncio
async def test_side_conversation_preserves_work_but_spoken_correction_steers(harness):
    await harness.message("Analiziraj načrt")
    calls = len(harness.rpc.calls)
    await harness._user_voice_text("user", "Mimogrede, kako se temu reče?")
    assert len(harness.rpc.calls) == calls
    assert harness.state["task"]["status"] == "running"
    await harness._user_voice_text("user", "Popravek: upoštevaj še stroške.")
    assert any(method == "turn/steer" for method, _ in harness.rpc.calls)


def test_http_and_websocket_origin_and_validation(tmp_path):
    h = Harness(tmp_path, tmp_path / "state", rpc_factory=FakeRpc, desktop_factory=None)
    with TestClient(create_app(h)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        assert client.post("/api/message", json={"text": "   "}).status_code == 422
        assert client.post("/api/message", json={"text": "Test"}, headers={"Origin": "https://unrelated.example"}).status_code == 403
        assert client.get("/api/status").json()["auth"]["type"] == "chatgpt"


def test_state_store_atomic_private_and_no_credentials(tmp_path):
    store = StateStore(tmp_path)
    store.save({"thread_id": "t", "transcript": [{"text": "Četrtek"}]})
    assert store.load()["transcript"][0]["text"] == "Četrtek"
    assert os.stat(store.path).st_mode & 0o777 == 0o600
    assert not (tmp_path / "session.json.tmp").exists()


def test_error_redaction():
    result = safe_error("Bearer secret-token eyJabcd.efgh.ijkl")
    assert "secret-token" not in result and "eyJabcd" not in result


@pytest.mark.asyncio
async def test_rpc_child_has_no_api_key_and_chatgpt_is_forced(tmp_path, monkeypatch):
    captured = {}

    async def spawn(*args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        raise FileNotFoundError("test stops before child creation")

    monkeypatch.setenv("OPENAI_API_KEY", "not-for-this-app")
    monkeypatch.setenv("CODEX_API_KEY", "not-for-this-app-either")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    rpc = CodexRpc(tmp_path, lambda event: None)
    with pytest.raises(FileNotFoundError):
        await rpc.start()
    assert 'forced_login_method="chatgpt"' in captured["args"]
    assert "OPENAI_API_KEY" not in captured["kwargs"]["env"]
    assert "CODEX_API_KEY" not in captured["kwargs"]["env"]
