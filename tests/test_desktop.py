import asyncio
import json
import os
import struct

import pytest

from voice_harness.desktop import DesktopIpc, DesktopWorker, apply_patches, checked_socket, turn_summary
from voice_harness.rpc import RpcError
from voice_harness.service import Harness
from voice_harness.state import StateStore
from test_harness import FakeRpc


def conversation(turn_id="desktop-turn", status="inProgress", text="", requests=None):
    return {"id": "desktop-chat", "requests": requests or [], "turnHistory": {
        "kind": "canonical", "history": {"entitiesByKey": {"tail:1": {
            "turnId": turn_id, "status": status, "params": {"model": "gpt-6.1-sol", "effort": "xhigh"},
            "items": [{"id": "progress", "type": "agentMessage", "phase": "commentary", "text": "Preverjam."},
                      {"id": "tool", "type": "commandExecution", "command": "secret command"},
                      {"id": "final", "type": "agentMessage", "phase": "final_answer", "text": text}],
        }}, "islands": [{"entries": [{"value": "tail:1"}]}]}}}


class FakeWorker:
    def __init__(self, cwd, on_event):
        self.on_event, self.thread_id = on_event, "desktop-chat"
        self.calls = []

    async def event(self, method, **params):
        await self.on_event({"origin": "desktop", "method": method, "params": {"threadId": self.thread_id, **params}})

    async def start(self, thread_id=None, tracked_turn=None):
        self.calls.append(("start", thread_id, tracked_turn))
        await self.event("desktop/status", connection="ready")

    async def submit(self, text, caller_thread):
        self.calls.append(("submit", text, caller_thread))
        await self.event("desktop/turn", turn=turn_summary(conversation()))
        return {"turn": {"id": "desktop-turn"}}

    async def steer(self, text, turn_id):
        self.calls.append(("steer", text, turn_id))
        return {"turnId": turn_id}

    async def cancel(self, turn_id):
        self.calls.append(("cancel", turn_id))

    async def close(self):
        self.calls.append(("close",))


@pytest.fixture
async def harness(tmp_path):
    service = Harness(tmp_path, tmp_path / "state", rpc_factory=FakeRpc, desktop_factory=FakeWorker)
    await service.start()
    yield service
    await service.close()


@pytest.mark.asyncio
async def test_reasoning_goes_to_desktop_while_voice_keeps_own_thread(harness):
    await harness.message("Poišči rezultate na Amazon.de.")
    assert harness.worker.calls[-1] == ("submit", "Poišči rezultate na Amazon.de.", "thread-1")
    assert not any(m == "turn/start" for m, _ in harness.rpc.calls)
    assert harness.state["thread_id"] == "thread-1"
    assert harness.state["desktop_thread_id"] == "desktop-chat"
    assert harness.state["desktop"]["effort"] == "xhigh"
    assert harness.state["task"]["tool_count"] == 1


@pytest.mark.asyncio
async def test_desktop_steering_and_voice_interruption_preserve_task(harness):
    await harness.message("Za petek.")
    await harness.message("Popravek: za četrtek.")
    assert harness.worker.calls[-1] == ("steer", "Popravek: za četrtek.", "desktop-turn")
    await harness.browser_activity("interrupted", "browser")
    assert harness.state["task"]["status"] == "running"
    assert not any(c[0] == "cancel" for c in harness.worker.calls)
    assert harness.state["task"]["revision"] == 2


@pytest.mark.asyncio
async def test_desktop_cancellation_requires_terminal_confirmation(harness):
    await harness.message("Dolga naloga.")
    await harness.cancel_task()
    assert harness.worker.calls[-1] == ("cancel", "desktop-turn")
    assert harness.state["task"]["status"] == "cancelling"
    await harness.worker.event("desktop/turn", turn=turn_summary(conversation(status="interrupted")))
    assert harness.state["task"]["status"] == "interrupted"


@pytest.mark.asyncio
async def test_desktop_completion_speaks_only_current_verified_result(harness):
    await harness.message("Naloga")
    harness.voice.active, harness.voice.thread_id = True, "thread-1"
    await harness.worker.event("desktop/turn", turn=turn_summary(conversation(turn_id="obsolete", status="completed", text="Zastarelo")))
    assert not any(m == "thread/realtime/appendSpeech" for m, _ in harness.rpc.calls)
    await harness.worker.event("desktop/turn", turn=turn_summary(conversation(status="completed", text="Preverjen odgovor.")))
    assert ("thread/realtime/appendSpeech", {"threadId": "thread-1", "text": "Preverjen odgovor."}) in harness.rpc.calls
    assert harness.state["task"]["status"] == "completed"


@pytest.mark.asyncio
async def test_reconnect_recovers_same_turn_without_resubmission(harness):
    await harness.message("Naloga")
    await harness.worker.event("desktop/error", message="Lost desktop")
    assert harness.state["task"]["status"] == "unknown"
    await harness.reconnect_desktop()
    assert harness.worker.calls == [("start", "desktop-chat", "desktop-turn")]
    await harness.worker.event("desktop/turn", turn=turn_summary(conversation()))
    assert harness.state["task"]["status"] == "running"
    assert not any(m == "turn/start" for m, _ in harness.rpc.calls)


@pytest.mark.asyncio
async def test_voice_backing_turn_cannot_replace_desktop_task(harness):
    await harness.message("Naloga")
    await harness.on_event({"method": "turn/started", "params": {"threadId": "thread-1", "turn": {"id": "native-delegation"}}})
    assert harness.state["task"]["turn_id"] == "desktop-turn"


@pytest.mark.asyncio
async def test_resume_remembers_worker_without_forking_or_replaying_it(tmp_path):
    StateStore(tmp_path).save({"thread_id": "thread-1", "desktop_thread_id": "desktop-chat", "task": {"backend": "desktop", "turn_id": "old-turn", "status": "running"}})
    service = Harness(tmp_path, tmp_path, rpc_factory=FakeRpc, desktop_factory=FakeWorker)
    await service.start()
    assert service.worker.calls == [("start", "desktop-chat", "old-turn")]
    assert service.state["task"]["status"] == "unknown"
    await service.close()


def test_canonical_history_patches_keep_progress_and_final_separate():
    doc = conversation()
    path = ["turnHistory", "history", "entitiesByKey", "tail:1"]
    doc = apply_patches(doc, [{"op": "replace", "path": path + ["items", 2, "text"], "value": "Končano"}, {"op": "replace", "path": path + ["status"], "value": "completed"}])
    summary = turn_summary(doc)
    assert summary["result"] == "Končano" and summary["progress"] == "Preverjam."
    assert "secret" not in summary["activity"]


def test_submission_matches_desktop_delegation_not_old_result(tmp_path):
    worker = DesktopWorker(tmp_path, None)
    worker.expected_text = "New human request"
    worker.conversation = conversation(text="New human request")
    assert not worker._matches_submission()
    turn = worker.conversation["turnHistory"]["history"]["entitiesByKey"]["tail:1"]
    turn["items"].insert(0, {"type": "functionCallOutput", "name": "send_message_to_thread", "namespace": "codex_app", "output": "<input>New human request</input>"})
    assert worker._matches_submission()


@pytest.mark.asyncio
async def test_cancellation_cleans_commands_before_emitting_terminal_state(tmp_path):
    worker = DesktopWorker(tmp_path, None)
    worker.thread_id, worker.owner, worker.tracked_turn = "desktop-chat", "owner", "desktop-turn"
    worker.connected, worker.conversation = True, conversation()
    calls = []
    class Ipc:
        async def request(self, method, params, owner):
            if method == "thread-follower-interrupt-turn":
                assert worker.events.empty()
                calls.append(params)
                worker.conversation = conversation(status="interrupted")
            return {"result": {}}
    worker.ipc = Ipc()
    await worker.cancel("desktop-turn")
    assert calls == [{"conversationId": "desktop-chat", "mode": "user-stop", "expectedTurnId": "desktop-turn"}, {"conversationId": "desktop-chat", "mode": "user-stop"}]
    assert (await worker.events.get())["params"]["turn"]["status"] == "interrupted"


@pytest.mark.asyncio
async def test_unloaded_worker_reads_baseline_before_submitting(tmp_path):
    worker = DesktopWorker(tmp_path, None)
    worker.thread_id, worker.connected = "desktop-chat", True
    calls, attaches = [], 0
    async def attach():
        nonlocal attaches
        attaches += 1
        if attaches == 1:
            raise RpcError("Codex desktop: no-client-found")
        worker.conversation = conversation(turn_id="new-turn")
        turn = worker.conversation["turnHistory"]["history"]["entitiesByKey"]["tail:1"]
        turn["items"].append({"type": "functionCallOutput", "name": "send_message_to_thread", "namespace": "codex_app", "output": "<input>New request</input>"})
    class Mcp:
        async def call(self, method, params, caller):
            calls.append(method)
            return {"turns": [{"id": "old-turn"}]} if method == "read_thread" else {"status": "sent"}
    worker.attach, worker.mcp = attach, Mcp()
    result = await worker.submit("New request", "voice-chat")
    assert calls == ["read_thread", "send_message_to_thread"]
    assert result["turn"]["id"] == "new-turn"
    assert worker.baseline == "old-turn"


@pytest.mark.asyncio
async def test_worker_filters_other_chats_and_recovers_revision_gap(tmp_path):
    worker = DesktopWorker(tmp_path, None)
    worker.thread_id, worker.owner, worker.tracked_turn = "desktop-chat", "owner", "desktop-turn"
    worker._change({"type": "broadcast", "method": "thread-stream-state-changed", "version": 11, "sourceClientId": "owner", "params": {"conversationId": "someone-else", "hostId": "local", "change": {"type": "snapshot", "revision": 1, "conversationState": conversation()}}})
    assert worker.conversation == {} and worker.events.empty()
    refreshed = asyncio.Event()
    async def refresh():
        refreshed.set()
    worker._refresh = refresh
    worker._change({"type": "broadcast", "method": "thread-stream-state-changed", "version": 11, "sourceClientId": "owner", "params": {"conversationId": "desktop-chat", "hostId": "local", "change": {"type": "patches", "baseRevision": 10, "revision": 11, "patches": []}}})
    await asyncio.wait_for(refreshed.wait(), 1)


@pytest.mark.asyncio
async def test_ipc_handles_fragmented_frames_and_does_not_offer_task_ownership(tmp_path):
    path = tmp_path / "desktop.sock"
    seen = []
    async def peer(reader, writer):
        try:
            while True:
                size = struct.unpack("<I", await reader.readexactly(4))[0]
                msg = json.loads(await reader.readexactly(size)); seen.append(msg)
                result = {"type": "response", "requestId": msg["requestId"], "resultType": "success", "result": {"clientId": "lipa-client"}}
                data = json.dumps(result).encode(); frame = struct.pack("<I", len(data)) + data
                writer.write(frame[:2]); await writer.drain()
                await asyncio.sleep(0)
                writer.write(frame[2:]); await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
    server = await asyncio.start_unix_server(peer, str(path))
    os.chmod(path, 0o600)
    client = DesktopIpc(str(path), lambda m: None, lambda m: None)
    try:
        await client.start()
        assert client.client_id == "lipa-client"
        assert seen[0]["params"]["clientType"] == "lipa"
        with pytest.raises(RpcError, match="Unsupported"):
            await client.request("arbitrary-command", {})
    finally:
        await client.close(); server.close(); await server.wait_closed()


@pytest.mark.asyncio
async def test_unsafe_socket_rejected(tmp_path):
    path = tmp_path / "unsafe.sock"
    server = await asyncio.start_unix_server(lambda r, w: w.close(), str(path))
    try:
        os.chmod(path, 0o666)
        with pytest.raises(RpcError, match="private"):
            checked_socket(path)
    finally:
        server.close(); await server.wait_closed()
