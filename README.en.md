# Lipa

[Slovenščina](README.md) · English · [Apache 2.0](LICENSE) · [Validation](VALIDATION.md)

A local browser voice client that delegates work to a dedicated **Codex desktop chat**,
using GPT-6.1 Sol at `xhigh`. Native Codex Voice handles Slovenian speech.
It uses the existing **ChatGPT login and plan allowance**, without an API-key fallback.

## Requirements

Lipa currently targets Linux and has been tested on Fedora. You need:

- The Codex/ChatGPT desktop app, open and signed in. The CLI alone does not provide its desktop tools.
- A ChatGPT account with access to native Codex Voice and `gpt-6.1-sol` at `xhigh`.
- Codex CLI available as `codex` in your `PATH`, signed in using `codex login`.
- Python 3.11+ with `venv` and `ensurepip`.
- A WebRTC-capable browser and microphone. Chrome was used for validation.

The integration uses experimental voice and private desktop protocols. It may need
adjustment after Codex updates. The verified CLI build is `0.164.0-alpha.11.1`.

## Quick start

```bash
git clone https://github.com/StrajnarFilip/lipa.git
cd lipa
codex --version
codex login
./setup.sh
./run.sh
```

Keep the Codex/ChatGPT desktop app open and signed in.
Open **http://127.0.0.1:8765** in Chrome or another browser with WebRTC microphone support.
Select **Začni pogovor** and allow microphone access. Use headphones initially to avoid
acoustic echo during interruption testing. An optional port can be passed as `./run.sh --port 8766`.

For subsequent runs, use `./run.sh`. Set `VOICE_PYTHON` when running `./setup.sh` to choose
another Python interpreter. No separate Node installation or frontend build is needed to run Lipa.

## Using it

- Speak Slovenian naturally. **Premisli temeljito** explicitly requests delegated reasoning.
- Type a request or correction in the message field. Enter sends; Shift+Enter adds a line.
- The **Premisli temeljito** button submits the message field directly to Sol `xhigh`.
- The microphone button mutes input. It does not cancel a reasoning task.
- Interrupt speech to add a detail or ask a side question; say **Nadaljuj** to continue.
- **Prekliči nalogo** or the task cancellation button requests cancellation. The UI waits
  for the backend's interrupted status before claiming cancellation succeeded.
- **Končaj pogovor** stops metered voice but leaves any active reasoning task running.

Task status, tool activity, progress, and the actual desktop model/effort are shown beside
the transcript. Ordinary dialogue is handled by native voice; substantive work is delegated
to a dedicated desktop chat. The first request creates that chat; subsequent requests reuse it.
Use **Odpri nalogo v Codex** to view its tools, results, and permission prompts. Approvals
and sign-ins remain in Codex. A waiting task is labelled **Čakam tvojo potrditev v Codex**.
The desktop chat uses its normal workspace and approval settings; Lipa does not grant
blanket access to the entire machine or bypass site permissions.
The harness routes the spoken deep command directly, without relying on the voice model to
request delegation. New substantive spoken requests also go to Sol; while a task runs, explicit
deep requests and corrections steer it and native voice handles brief side conversation.
There is no additional fast text model in this first iteration.

Native V3 can split a spoken question across transcript items and keep them open until later.
The harness groups user fragments while the microphone detects speech, then waits for 900 ms
without a transcript update. If more of the same utterance arrives, it steers the existing task;
a later canonical commit does not submit it again. Long pauses mid-sentence can start reasoning
early, so continue speaking to supply the rest as a correction.

## Billing and native voice compatibility

The voice adapter starts an isolated app-server process over stdio with ChatGPT authentication forced,
removes API keys from that child process's environment, and refuses API-key-only accounts.
The desktop worker is requested as `gpt-6.1-sol` and `xhigh`, and its actual turn settings
are checked before results are accepted. No separately billed executor fallback is used. The current
installation is `codex-cli 0.164.0-alpha.11.1`.

Native voice uses the **experimental V3 WebRTC transport**. In the installed build,
WebSocket audio rejects the same ChatGPT login with `realtime conversation requires API key auth`.
WebRTC negotiation succeeds using this app's own `slovene_voice_harness` client identity.
The app does not impersonate the desktop client, copy tokens into the browser, or call
separately billed speech APIs. If native voice access fails, it surfaces that error.

Voice and reasoning share the account's plan limits; this app provides no extra allowance.
Purchased ChatGPT credits, if configured on the account, remain governed by that account's
billing settings. The app never purchases credits or switches to API-key billing.

Official references: [app-server](https://learn.chatgpt.com/docs/app-server),
[Voice](https://learn.chatgpt.com/docs/features/voice),
[plan pricing](https://learn.chatgpt.com/docs/pricing).

## State and limitations

The HTTP server is local, but speech and reasoning use OpenAI services. Lipa is not an offline assistant.

- `.state/session.json` saves the Codex thread identifier, transcript, and latest task state,
  with private file permissions. It contains no credentials or audio. Codex also maintains
  its own thread history. Set `VOICE_STATE_DIR` for a separate local conversation.
- Restarting Lipa resumes its voice thread and reconnects to its saved desktop worker.
  Closing Lipa stops voice, while an existing desktop task can continue in Codex.
  Lipa reconciles the saved task with live desktop state when available. A disconnected
  task has an unknown outcome until confirmed and is **not automatically submitted again**.
  **Ponovno poveži** reattaches to desktop state without resubmitting work.
- If another Codex client owns that thread's writer lock, restart preserves its history
  in a writable continuation and saves the new thread ID. It leaves the original thread
  intact and does not automatically continue any unfinished task or goal.
- One browser tab owns the voice session. Closing that tab stops its voice session after
  a short grace period. Losing the event connection closes browser audio immediately.
- Interrupted speech is marked as only partly heard. Generated text is not proof of audio
  delivery; word-exact playback alignment is not exposed by this adapter.
- Slovenian is requested through native voice instructions. This is not a hard language
  setting on an independent recognizer. Native voice can still misrecognize, switch language,
  paraphrase, or mispronounce words. Long replies can exceed native speech limits; the full
  backend answer remains visible if speech delivery fails.
- Native audio is held while a newly submitted reasoning task is pending and released when
  the completed backend answer is handed back. A side conversation can reopen native audio
  while that task continues. This limits premature frontend answers, but does not make native
  speech a verbatim or perfectly synchronized renderer of the backend text.
- The desktop worker reuses Codex command execution, file tools, browser controls, search,
  and installed integrations. Tools stay in the desktop app, with its existing permissions.
  Amazon and other sites may still require site approval, sign-in or CAPTCHA handling.
- Native GUI Computer Use is currently unsupported in the Linux desktop preview. Browser
  control and shell/file access work separately; this integration does not add Linux GUI control.
- Desktop delegation uses the installed bundled `codex-app-tools` MCP and the app’s private,
  versioned local follower protocol. It is experimental and may need updating after app upgrades.
  Protocol errors are surfaced; Lipa does not silently use another executor. The client
  identifies itself as `lipa`, never as the desktop app, and never takes the worker’s writer lock.

## Tests

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

The mocked tests cover authentication, exact model/effort, interruption versus cancellation,
steering, stale results, cancellation races, unavailable voice, usage errors, and persistence.

`scripts/browser_acceptance.cjs` runs a **live test consuming ChatGPT allowance**. It requires
Node and Playwright plus an installed Chromium/Chrome. Start the app with an isolated
`VOICE_STATE_DIR` before running it:

```bash
VOICE_STATE_DIR=test-results/acceptance-state ./run.sh --port 8766
# In another terminal, with Playwright installed:
VOICE_URL=http://127.0.0.1:8766 CHROME_BIN=/path/to/chrome node scripts/browser_acceptance.cjs
```

The test uses a fake microphone, captures native Slovene output, and sends that recording
back through the browser's input track to verify recognition and spoken delegation. It
does not access the physical microphone. Reports and screenshots go to `test-results/`.
Set `PLAYWRIGHT_MODULE` to an absolute module path if Playwright is outside Node's default
search path. A real microphone, your dialect, pronunciation preference, and acoustic echo
still need a personal listening check.

## Layout

`voice_harness/desktop.py` connects to desktop task tools and live state, forwarding
corrections and cancellation to the existing owner. `rpc.py` owns the separate voice-only
Codex process and JSONL protocol. `native_voice.py` isolates
the voice adapter. `service.py` owns conversation/task state. `app.py` exposes localhost
HTTP controls and a WebSocket event feed; the frontend is static JavaScript and CSS.

## Desktop connection discovery

On this Fedora installation, the adapter uses `/usr/lib/chatgpt/resources`,
`~/.codex/ipc/ipc.sock`, and the current private `/tmp/codex-app-tools/*.sock`.
It accepts only sockets owned by the current user with private permissions. The current
app-provided socket environment variable takes priority; after reboot, a single private
app-tools socket is discovered automatically. Run the desktop app first. If discovery is
ambiguous, set `VOICE_DESKTOP_PIPE` explicitly; `VOICE_DESKTOP_IPC` and
`VOICE_CODEX_RESOURCES` override the IPC socket and installed resources directory.
No tokens are copied or stored by this bridge.

The voice thread identifier and the separate `desktop_thread_id` are saved in the same
private state file. Do not run two independent Lipa instances against the same worker
concurrently. For testing, use isolated state and a dedicated test worker, or stop submitting
work from the other instance.

## Configuration

Set environment variables before starting Lipa; `.env` files are not loaded automatically.

| Variable | Default | Purpose |
| --- | --- | --- |
| `VOICE_STATE_DIR` | Project `.state` directory | Transcript, thread identifiers and task state. |
| `VOICE_CODEX_RESOURCES` | `/usr/lib/chatgpt/resources` | Installed desktop resources, including bundled Node and app-tools MCP. |
| `VOICE_DESKTOP_IPC` | `$CODEX_HOME/ipc/ipc.sock`, otherwise `~/.codex/ipc/ipc.sock` | Desktop coordination socket. |
| `VOICE_DESKTOP_PIPE` | App-provided socket or the single private `/tmp/codex-app-tools/*.sock` | Explicit app-tools socket selection. |
| `VOICE_PYTHON` | `python3` | Interpreter used by `./setup.sh` to create the environment. |

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Desktop unavailable | Open and sign in to the desktop app, then select **Ponovno poveži**. Check the resource and socket paths above. |
| `codex` missing or authentication fails | Check `codex --version`, CLI installation and `codex login`. API-key-only authentication is unsupported. |
| Sol or `xhigh` unavailable | Check account access in Codex. Lipa deliberately does not select another model. |
| Microphone denied or no audio | Allow the microphone for localhost, check the selected device and restart voice. Typed input remains available. |
| Task awaiting approval | Select **Odpri nalogo v Codex** and respond to its permission or sign-in prompt. |
| Allowance exhausted | Check account limits in Codex. There is no separately billed inference fallback. |
| Protocol mismatch after an update | Check desktop compatibility; the adapter may need an update. |
| Python environment creation fails | Install `venv`/`ensurepip` support or run `VOICE_PYTHON=/path/to/python3 ./setup.sh`. |

After an uncertain submission, check the desktop chat before sending the request again.

## License

Copyright 2026 Filip Strajnar. Licensed under the [Apache License, Version 2.0](LICENSE).
OpenAI services and installed desktop components retain their own terms and licenses;
they are not included in this repository.
