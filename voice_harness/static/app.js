const $ = (id) => document.getElementById(id);
const clientId = crypto.randomUUID();
let state = null;
let eventsConnected = false;
let submitting = false;
let localError = null;
let dismissedError = null;
let timerStart = null;

async function api(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? {} : {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail));
  return result;
}

function reportError(error) {
  localError = error.message || String(error);
  render();
}

export class NativeVoiceClient {
  constructor() {
    this.peer = null;
    this.mic = null;
    this.audio = $('remote-audio');
    this.muted = false;
    this.starting = false;
    this.speaking = false;
    this.listening = false;
    this.context = null;
    this.nodes = [];
    this.levelTimer = null;
    this.debug = {events: {}, peak: 0, audioSamples: 0};
    this.lastOutput = 0;
    this.lastInput = 0;
    this.ducked = false;
    this.manualHush = false;
    this.generation = 0;
    this.awaitingDecision = false;
  }

  async start() {
    if (this.peer || this.starting) return;
    this.starting = true;
    const generation = ++this.generation;
    localError = null;
    render();
    let requested = false;
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error('Brskalnik ne omogoča mikrofona. Odpri aplikacijo prek http://127.0.0.1:8765.');
      this.mic = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}, video: false});
      if (generation !== this.generation) {this.closeLocal(); return;}
      this.context = new AudioContext();
      await this.context.resume();
      this.inputAnalyser = this.context.createAnalyser();
      this.inputAnalyser.fftSize = 512;
      const input = this.context.createMediaStreamSource(this.mic);
      const silent = this.context.createGain();
      silent.gain.value = 0;
      input.connect(this.inputAnalyser).connect(silent).connect(this.context.destination);
      this.nodes.push(input, silent, this.inputAnalyser);
      this.peer = new RTCPeerConnection();
      this.syncPlayback();
      const peer = this.peer;
      this.mic.getAudioTracks().forEach((track) => peer.addTrack(track, this.mic));
      const channel = peer.createDataChannel('oai-events');
      channel.onmessage = (event) => this.channelEvent(event);
      peer.ontrack = (event) => {
        const stream = event.streams[0] || new MediaStream([event.track]);
        this.audio.srcObject = stream;
        this.audio.play().catch(() => reportError(new Error('Brskalnik je ustavil predvajanje zvoka. Končaj in ponovno začni pogovor.')));
        this.outputAnalyser = this.context.createAnalyser();
        this.outputAnalyser.fftSize = 512;
        const source = this.context.createMediaStreamSource(stream);
        const zero = this.context.createGain();
        zero.gain.value = 0;
        source.connect(this.outputAnalyser).connect(zero).connect(this.context.destination);
        this.nodes.push(source, zero, this.outputAnalyser);
      };
      peer.onconnectionstatechange = () => {
        if (peer !== this.peer) return;
        if (peer.connectionState === 'connected') {
          timerStart = Date.now();
          api('/api/voice/activity', {client_id: clientId, kind: 'connected'}).catch(reportError);
        } else if (['failed', 'disconnected'].includes(peer.connectionState)) {
          reportError(new Error('Glasovna povezava je prekinjena. Začni nov glasovni pogovor; naloga se ne pošlje znova.'));
          this.stop().catch(reportError);
        }
        render();
      };
      await peer.setLocalDescription(await peer.createOffer());
      if (peer.iceGatheringState !== 'complete') {
        await new Promise((resolve) => {
          const timeout = setTimeout(resolve, 2000);
          peer.addEventListener('icegatheringstatechange', () => {
            if (peer.iceGatheringState === 'complete') { clearTimeout(timeout); resolve(); }
          });
        });
      }
      requested = true;
      const answer = await api('/api/voice/start', {client_id: clientId, sdp: peer.localDescription.sdp});
      if (generation !== this.generation) return;
      await peer.setRemoteDescription(answer);
      this.levelTimer = setInterval(() => this.measure(), 50);
    } catch (error) {
      if (error.name === 'NotAllowedError') error = new Error('Dostop do mikrofona ni dovoljen. Omogoči mikrofon za to lokalno stran in poskusi znova.');
      if (error.name === 'NotFoundError') error = new Error('Mikrofon ni na voljo. Priklopi ga ali nadaljuj s tipkanjem.');
      this.closeLocal();
      if (requested) await api('/api/voice/stop', {client_id: clientId}).catch(() => {});
      reportError(error);
    } finally {
      this.starting = false;
      render();
    }
  }

  channelEvent(event) {
    try {
      const message = JSON.parse(event.data);
      const type = message.type || 'unknown';
      this.debug.events[type] = (this.debug.events[type] || 0) + 1;
      if (type === 'input_audio_buffer.speech_started') this.interrupted();
      if (type === 'error') reportError(new Error(message.error?.message || 'Glasovna storitev je vrnila napako.'));
    } catch { /* Some experimental channel payloads are not JSON. */ }
  }

  rms(analyser) {
    if (!analyser) return 0;
    const values = new Float32Array(analyser.fftSize);
    analyser.getFloatTimeDomainData(values);
    return Math.sqrt(values.reduce((sum, value) => sum + value * value, 0) / values.length);
  }

  measure() {
    const input = this.muted ? 0 : this.rms(this.inputAnalyser);
    const output = this.rms(this.outputAnalyser);
    const current = performance.now();
    this.debug.peak = Math.max(this.debug.peak, output);
    if (output > 0.003) {this.lastOutput = current; this.debug.audioSamples++;}
    const speaking = current - this.lastOutput < 450 && this.lastOutput > 0;
    this.inputFrames = input > 0.035 ? (this.inputFrames || 0) + 1 : 0;
    if (this.inputFrames >= 3) {
      this.lastInput = current;
      if (!this.inputWasActive) {
        api('/api/voice/activity', {client_id: clientId, kind: 'input_started'}).catch(reportError);
        this.awaitingDecision = true;
        this.inputRevision = state?.input_revision || 0;
        this.syncPlayback();
      }
      this.inputWasActive = true;
      if (speaking && !this.ducked) this.interrupted();
    }
    if (this.inputWasActive && current - this.lastInput > 400) {
      this.inputWasActive = false;
      api('/api/voice/activity', {client_id: clientId, kind: 'input_stopped'}).catch(reportError);
    }
    if (this.awaitingDecision && current - this.lastInput > 4000 && state?.playback !== 'wait') {
      this.awaitingDecision = false;
      this.syncPlayback();
    }
    if ((this.ducked || this.manualHush) && current - this.lastInput > 400 && current - this.lastOutput > 250) {
      this.ducked = false;
      this.manualHush = false;
      this.syncPlayback();
    }
    const listening = input > 0.025;
    if (this.speaking !== speaking || this.listening !== listening) {
      this.speaking = speaking;
      this.listening = listening;
      renderVoice();
    }
  }

  interrupted() {
    if (this.ducked || !this.peer) return;
    this.ducked = true;
    this.lastInput = performance.now();
    this.audio.muted = true;
    api('/api/voice/activity', {client_id: clientId, kind: 'interrupted'}).catch(reportError);
  }

  hush() {
    if (!this.peer || !this.speaking) return;
    this.manualHush = true;
    this.audio.muted = true;
  }

  setMuted(value) {
    this.muted = value;
    this.mic?.getAudioTracks().forEach((track) => {track.enabled = !value;});
    renderVoice();
  }

  syncPlayback() {
    if ((state?.input_revision || 0) > (this.inputRevision || 0)) this.awaitingDecision = false;
    this.audio.muted = this.ducked || this.manualHush || this.awaitingDecision || state?.playback === 'wait';
  }

  closeLocal() {
    this.generation++;
    clearInterval(this.levelTimer);
    this.peer?.close();
    this.peer = null;
    this.mic?.getTracks().forEach((track) => track.stop());
    this.mic = null;
    this.audio.srcObject = null;
    this.audio.muted = false;
    this.nodes.forEach((node) => node.disconnect());
    this.nodes = [];
    this.context?.close().catch(() => {});
    this.context = null;
    this.inputAnalyser = null;
    this.outputAnalyser = null;
    this.muted = false;
    this.speaking = false;
    this.listening = false;
    this.ducked = false;
    this.manualHush = false;
    this.awaitingDecision = false;
    this.inputWasActive = false;
    this.starting = false;
    this.lastInput = 0;
    this.lastOutput = 0;
    timerStart = null;
  }

  async stop() {
    this.closeLocal();
    render();
    await api('/api/voice/stop', {client_id: clientId});
  }
}

const voice = new NativeVoiceClient();
// Local diagnostics and browser acceptance tests; no account credentials live here.
window.voiceClient = voice;

function renderVoice() {
  const active = !!voice.peer;
  const ready = eventsConnected && state?.connection === 'ready';
  const connecting = voice.starting || (active && voice.peer.connectionState !== 'connected');
  $('start-button').disabled = voice.starting || (!active && !ready);
  $('start-button').classList.toggle('ending', active && !connecting);
  $('start-button').querySelector('span').textContent = connecting ? 'Povezujem …' : active ? 'Končaj pogovor' : 'Začni pogovor';
  $('mute-button').disabled = !active;
  $('mute-button').classList.toggle('muted', voice.muted);
  $('mute-button').setAttribute('aria-label', voice.muted ? 'Vklopi mikrofon' : 'Izklopi mikrofon');
  $('mute-button').setAttribute('title', voice.muted ? 'Vklopi mikrofon' : 'Izklopi mikrofon');
  $('voice-stage').classList.toggle('active', active);
  $('voice-stage').classList.toggle('speaking', active && voice.speaking && !voice.audio.muted);
  $('voice-stage').classList.toggle('listening', active && voice.listening);
  $('voice-title').textContent = connecting ? 'Povezujem tvoj glas.' : !active ? 'Povej na glas.' : voice.muted ? 'Mikrofon je izklopljen.' : voice.listening ? 'Poslušam.' : state?.playback === 'wait' ? 'Razmišljam v ozadju.' : voice.speaking ? 'Misel dobiva glas.' : 'Tukaj sem.';
  $('voice-description').textContent = active ? 'Dodaj misel ali me prekini. Pogovor ostaja povezan.' : 'Začni pogovor. Lahko me prekineš kadar koli.';
  $('voice-state-label').textContent = connecting ? 'Vzpostavljam povezavo' : active ? 'Pogovor je aktiven' : 'Pripravljen, ko si ti';
}

const transcriptNodes = new Map();
function renderTranscript(entries) {
  const container = $('transcript');
  const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 70;
  $('empty-state').hidden = entries.length > 0;
  const ids = new Set(entries.map((entry) => entry.id));
  for (const [id, node] of transcriptNodes) {
    if (!ids.has(id)) {node.remove(); transcriptNodes.delete(id);}
  }
  for (const entry of entries) {
    let node = transcriptNodes.get(entry.id);
    if (!node) {
      node = document.createElement('article');
      node.innerHTML = '<div class="transcript-meta"><strong></strong><span></span><time></time></div><div class="transcript-text"></div><div class="interrupted-label" hidden></div>';
      transcriptNodes.set(entry.id, node);
      container.append(node);
    }
    node.className = `transcript-entry ${entry.role} ${entry.partial ? 'partial' : ''}`;
    node.querySelector('strong').textContent = entry.role === 'user' ? 'Ti' : 'Lipa';
    node.querySelector('.transcript-meta span').textContent = entry.source === 'typed' ? 'tipkano' : entry.source === 'voice' ? 'glas' : 'razmislek';
    node.querySelector('time').textContent = new Intl.DateTimeFormat('sl-SI', {hour: '2-digit', minute: '2-digit'}).format(new Date(entry.time));
    node.querySelector('.transcript-text').textContent = entry.text || '…';
    const note = node.querySelector('.interrupted-label');
    note.hidden = !entry.interrupted && !entry.send_failed && !entry.held;
    note.textContent = entry.send_failed ? 'Pošiljanje ni potrjeno. Sporočilo ni bilo samodejno poslano znova.' : entry.held ? 'Sprotni govor je zadržan do zaključka razmisleka.' : 'Govor je bil prekinjen · odgovor je bil slišan le delno';
  }
  if (nearBottom) container.scrollTop = container.scrollHeight;
  $('transcript-count').textContent = entries.length ? `${entries.length} sporočil` : 'Vse ostane povezano';
}

function render() {
  const ready = eventsConnected && state?.connection === 'ready';
  const connection = $('connection-pill');
  connection.className = `connection-pill ${ready ? 'ready' : state?.connection === 'error' ? 'error' : ''}`;
  $('connection-label').textContent = ready ? 'Codex je povezan' : !eventsConnected ? 'Povezovanje' : state?.connection === 'error' ? 'Povezava ni uspela' : 'Preverjam Codex';
  $('account-status').textContent = state?.auth ? `${state.auth.plan || 'ChatGPT'} · prijavljen` : 'Preverjam povezavo …';
  $('account-check').textContent = state?.auth ? '✓' : '○';
  const error = localError || (state?.error !== dismissedError ? state?.error : null);
  $('error-banner').hidden = !error;
  $('error-text').textContent = error || '';
  const workerReady = state?.backend !== 'desktop' || state.desktop?.connection === 'ready';
  const taskTransition = ['starting', 'cancelling'].includes(state?.task?.status);
  $('send-button').disabled = !ready || !workerReady || submitting || taskTransition;
  $('deep-button').disabled = !ready || !workerReady || submitting || taskTransition;
  if (state) {
    renderTranscript(state.transcript || []);
    const task = state.task;
    const statuses = {starting: 'Pripravljam razmislek', running: 'Razmišljam v ozadju', completed: 'Razmislek je pripravljen', interrupted: 'Naloga je preklicana', cancelling: 'Čakam potrditev preklica', failed: 'Razmislek ni uspel', unknown: 'Izid še ni potrjen'};
    $('task-state').textContent = task?.waiting_for_user && task.status === 'running' ? 'Čakam tvojo potrditev v Codex' : task ? statuses[task.status] || task.status : state.deep_requested ? 'Čakam temeljit razmislek' : 'Brez aktivne naloge';
    $('task-card').classList.toggle('running', !!task && ['starting', 'running'].includes(task.status));
    $('task-description').textContent = task?.request || 'Ko vprašanje potrebuje več časa, razmislek poteka v ozadju. Pogovor lahko teče naprej.';
    $('task-progress').hidden = !task?.progress;
    $('task-progress').textContent = task?.progress || '';
    $('task-activity').hidden = !task?.activity;
    $('task-activity').textContent = `${task?.activity || ''} · ${task?.tool_count || 0} uporabljenih orodij`;
    $('task-result').hidden = !task?.result;
    $('task-result').textContent = task?.result || '';
    $('cancel-button').hidden = !task || !['running', 'cancelling'].includes(task.status);
    $('cancel-button').disabled = task?.status === 'cancelling';
    $('cancel-button').textContent = task?.status === 'cancelling' ? 'Preklicujem …' : 'Prekliči nalogo';
    $('model-name').textContent = state.model === 'gpt-6.1-sol' ? 'GPT-6.1 Sol' : state.model;
    $('model-effort').textContent = `Extra high · ${state.desktop?.effort || state.effort}`;
    $('model-badge').textContent = state.desktop?.model ? 'POTRJEN' : 'IZBRAN';
    $('desktop-card').hidden = state.backend !== 'desktop';
    const desktop = state.desktop || {};
    $('desktop-status').textContent = {ready: 'Povezan', starting: 'Povezujem …', error: 'Povezava je prekinjena'}[desktop.connection] || desktop.connection || '';
    $('desktop-link').hidden = !desktop.thread_id;
    if (desktop.thread_id) $('desktop-link').href = `codex://threads/${encodeURIComponent(desktop.thread_id)}`;
    $('desktop-note').textContent = task?.waiting_for_user ? 'Odpri nalogo v Codex in preglej zahtevo za dovoljenje ali odgovor.' : 'Brskalnik, ukaze, datoteke in dovoljenja upravlja Codex.';
    $('desktop-reconnect').disabled = desktop.connection === 'starting' || submitting;
  }
  renderVoice();
}

function connectEvents() {
  const socket = new WebSocket(`ws://${location.host}/api/events?client_id=${encodeURIComponent(clientId)}`);
  socket.onopen = () => {eventsConnected = true; render();};
  socket.onmessage = (event) => {
    state = JSON.parse(event.data);
    voice.syncPlayback();
    if (voice.peer && !voice.starting && ['off', 'error'].includes(state.voice)) voice.closeLocal();
    render();
  };
  socket.onclose = () => {
    eventsConnected = false;
    voice.closeLocal();
    render();
    setTimeout(connectEvents, 2000); // Only reconnect event delivery, never replay a task.
  };
  socket.onerror = () => socket.close();
}

async function sendMessage(deep = false) {
  const text = $('message-input').value.trim();
  if (!text || submitting) return;
  submitting = true;
  localError = null;
  voice.hush();
  render();
  try {
    await api('/api/message', {text, deep: deep || $('deep-checkbox').checked});
    $('message-input').value = '';
    $('deep-checkbox').checked = false;
  } catch (error) {reportError(error);}
  finally {submitting = false; render();}
}

$('start-button').addEventListener('click', () => (voice.peer ? voice.stop() : voice.start()).catch(reportError));
$('desktop-reconnect').addEventListener('click', () => api('/api/desktop/reconnect', {}).catch(reportError));
$('mute-button').addEventListener('click', () => voice.setMuted(!voice.muted));
$('message-form').addEventListener('submit', (event) => {event.preventDefault(); sendMessage();});
$('message-input').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {event.preventDefault(); sendMessage();}
});
$('deep-button').addEventListener('click', () => {
  if ($('message-input').value.trim()) sendMessage(true);
  else {$('deep-checkbox').checked = true; $('message-input').placeholder = 'Kaj naj temeljito premislim?'; $('message-input').focus();}
});
$('cancel-button').addEventListener('click', () => api('/api/task/cancel', {}).catch(reportError));
$('dismiss-error').addEventListener('click', () => {localError = null; dismissedError = state?.error; render();});
document.querySelectorAll('[data-prompt]').forEach((button) => button.addEventListener('click', () => {$('message-input').value = button.dataset.prompt; $('message-input').focus();}));
window.addEventListener('pagehide', () => {
  if (voice.peer) {
    navigator.sendBeacon('/api/voice/stop', new Blob([JSON.stringify({client_id: clientId})], {type: 'application/json'}));
    voice.closeLocal();
  }
});
setInterval(() => {
  const seconds = timerStart ? Math.floor((Date.now() - timerStart) / 1000) : 0;
  $('session-timer').textContent = `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}, 1000);
connectEvents();
render();
