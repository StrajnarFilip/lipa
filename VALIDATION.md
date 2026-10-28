# Validation

Validated locally on 28 October 2026 with `codex-cli 0.164.0-alpha.11.1` and a managed
ChatGPT login, using the app's own `slovene_voice_harness` client identity.

- **41 automated tests passed.** Coverage includes authentication/billing route selection,
  exact Sol `xhigh` configuration, speech fragment assembly, duplicate prevention, steering,
  stale results, cancellation races, unavailable native voice, usage failures, and resumption
  without task replay. The installed Starlette test client emits an httpx deprecation warning.
- **Live browser audio passed.** Chrome established native V3 WebRTC audio. A synthetic
  Slovenian recording entered through its audio input track: “Premisli temeljito. Koliko je
  devetnajst krat sedemnajst?” Sol `xhigh` completed with “Devetnajst krat sedemnajst je tristo
  triindvajset.” Native audio output followed the completed answer.
- **Live task controls passed.** A speech-interruption control preserved a running task;
  a correction was accepted on the same turn; cancellation was confirmed as `interrupted`.
- **Browser UI checks passed.** Desktop and 390 px layouts rendered without horizontal
  overflow. Microphone denial produced a visible error before a metered voice session started.
- **Transport distinction verified.** WebSocket audio rejected ChatGPT authentication with
  “realtime conversation requires API key auth”; WebRTC succeeded. There is no API-key fallback.

Detailed local reports: `test-results/live-acceptance.json` and `test-results/live-controls.json`.
Screenshots and the synthetic speech fixture are also under the ignored `test-results/` folder.

Not established by these tests: recognition of the user's actual dialect, subjective Slovene
pronunciation quality, acoustic echo behavior in their room, or Live-equivalent conversational
smoothness. The initial recognizer/synthesizer is native voice and retains its language limitations.
For a personal check, interrupt an explanation, correct a detail, ask a side question, then say
“Nadaljuj”; also try numbers, names, and mixed English technical terms.

## Desktop delegation — 28 October 2026

The dedicated **Lipa desktop worker** was created through the installed app’s own
`codex-app-tools` bridge. Its actual turns confirmed **gpt-6.1-sol / xhigh**.

- Desktop shell execution printed `LIPA_DESKTOP_OK` with exit code 0.
- The desktop’s existing browser tool opened `https://example.com` and read
  “Example Domain”. The user approved the app’s normal origin-access prompt.
  No browser-control implementation or additional speech/inference provider was installed.
- A live correction was accepted through the desktop’s follower interface.
- Speech interruption preserved the running desktop task. Explicit cancellation was
  confirmed as `interrupted`. A separate 300-second sleeping Python process was then
  cancelled: its PID existed before cancellation and was absent afterward.
- Reconnection reused the saved worker and did not resubmit a task. Restarting Lipa
  while a desktop task was active recovered that task from the live desktop stream.
- Synthetic Slovene speech travelled through native WebRTC recognition, the desktop
  Sol xhigh worker, and native spoken output. Desktop/mobile layouts and microphone
  denial passed in the updated interface. Test voice state was isolated from user history.

Local reports: `test-results/desktop-lifecycle.json`,
`test-results/desktop-command-cancellation.json`, and
`test-results/desktop-voice-final/live-acceptance.json`.

The mocked suite additionally covers canonical desktop history/patches, exclusion of
other chats, stale-result suppression, command cleanup before cancellation confirmation,
and unloaded-worker admission without replaying an old result.

The desktop follower interface is private and versioned. This build uses stream version 11,
steering version 1, and exact-turn interruption version 4 (idle command cleanup version 3).
An app upgrade may require an adapter update. Protocol failures do not trigger another
executor or an automatic task retry.
