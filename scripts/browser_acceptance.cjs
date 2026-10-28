/* Live acceptance: fake browser microphone, native voice output, and a spoken deep request.
 * Uses ChatGPT allowance. Does not open the physical microphone or use an API key.
 * Set PLAYWRIGHT_MODULE when Playwright is outside normal Node module resolution.
 */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const target = process.env.VOICE_URL || 'http://127.0.0.1:8765';
const results = process.env.VOICE_TEST_RESULTS || 'test-results';
fs.mkdirSync(results, {recursive: true});

async function waitForState(page, predicate, timeout = 60000) {
  const deadline = Date.now() + timeout;
  let state;
  while (Date.now() < deadline) {
    state = await page.evaluate(async () => fetch('/api/status').then((response) => response.json()));
    if (predicate(state)) return state;
    if (state.error) throw new Error(state.error);
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error('Timed out waiting for application state: ' + JSON.stringify({task: state?.task, voice: state?.voice, error: state?.error}));
}

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROME_BIN || undefined,
    headless: true,
    args: ['--no-sandbox', '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', '--autoplay-policy=no-user-gesture-required'],
  });
  const report = {target, checks: {}, errors: []};
  let page;
  try {
    const context = await browser.newContext({viewport: {width: 1440, height: 1080}, permissions: ['microphone'], locale: 'sl-SI'});
    page = await context.newPage();
    page.on('pageerror', (error) => report.errors.push(error.message));
    await page.goto(target);
    await page.getByText('Codex je povezan', {exact: true}).waitFor();
    await page.screenshot({path: `${results}/desktop-idle.png`, fullPage: true});
    await page.evaluate(() => {
      const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia = async (...args) => {
        const stream = await original(...args);
        stream.getAudioTracks().forEach((track) => {track.enabled = false;});
        return stream;
      };
    });
    await page.getByRole('button', {name: 'Začni pogovor', exact: true}).click();
    await page.waitForFunction(() => window.voiceClient?.peer?.connectionState === 'connected', null, {timeout: 60000});
    await page.evaluate(() => window.voiceClient.setMuted(true));
    report.checks.webrtc = 'connected';
    console.log('PASS native WebRTC connected with managed ChatGPT authentication');

    let submitted = {turn_id: (await page.evaluate(async () => (await fetch('/api/status').then((r) => r.json())).task))?.turn_id || 'none'};
    const savedFixture = process.env.VOICE_SPEECH_FIXTURE ? JSON.parse(fs.readFileSync(process.env.VOICE_SPEECH_FIXTURE)) : null;
    // Capture native spoken output once; subsequent debugging can reuse that fixture.
    if (!savedFixture) {
    await page.evaluate(() => {
      const client = window.voiceClient;
      window.__capture = [];
      const source = client.context.createMediaStreamSource(client.audio.srcObject);
      const processor = client.context.createScriptProcessor(4096, 1, 1);
      const zero = client.context.createGain();
      zero.gain.value = 0;
      processor.onaudioprocess = (event) => window.__capture.push(new Float32Array(event.inputBuffer.getChannelData(0)));
      source.connect(processor).connect(zero).connect(client.context.destination);
      window.__captureNodes = [source, processor, zero];
    });
    const replyPromise = page.waitForResponse((response) => response.url().endsWith('/api/message') && response.request().method() === 'POST');
    await page.getByRole('textbox', {name: 'Sporočilo ali popravek'}).fill('Izpiši samo ta stavek, brez uvoda ali dodatka: Premisli temeljito. Koliko je devetnajst krat sedemnajst?');
    await page.getByRole('button', {name: 'Premisli temeljito', exact: false}).click();
    submitted = await (await replyPromise).json();
    assert.equal(submitted.model, 'gpt-6.1-sol');
    assert.equal(submitted.effort, 'xhigh');
    report.submitted_turn = submitted.turn_id;
    const completed = await waitForState(page, (state) => state.task?.turn_id === submitted.turn_id && ['completed', 'failed'].includes(state.task.status));
    const task = completed.task;
    assert.equal(task.status, 'completed');
    report.checks.reasoning = {status: task.status, result: task.result, model: submitted.model, effort: submitted.effort};
    await page.waitForFunction(() => {
      const client = window.voiceClient;
      return client.debug.peak > 0.005 && client.lastOutput > 0 && performance.now() - client.lastOutput > 1100;
    }, null, {timeout: 45000});
    report.checks.output_audio = await page.evaluate(() => window.voiceClient.debug);
    console.log('PASS completed Sol xhigh turn and audible native speech output');
    }

    const previousVoiceIds = new Set((await page.evaluate(async () => (await fetch('/api/status').then((r) => r.json())).transcript)).map((entry) => entry.id));
    const fixture = await page.evaluate(async (savedFixture) => {
      window.__captureNodes?.forEach((node) => node.disconnect());
      const client = window.voiceClient;
      const chunks = savedFixture ? [new Float32Array(savedFixture.samples)] : window.__capture;
      const samples = new Float32Array(chunks.reduce((total, chunk) => total + chunk.length, 0));
      let offset = 0;
      chunks.forEach((chunk) => {samples.set(chunk, offset); offset += chunk.length;});
      let first = 0;
      let last = samples.length - 1;
      while (first < samples.length && Math.abs(samples[first]) < 0.003) first++;
      while (last > first && Math.abs(samples[last]) < 0.003) last--;
      first = Math.max(0, first - client.context.sampleRate * 0.25);
      last = Math.min(samples.length, last + client.context.sampleRate * 0.5);
      const trimmed = samples.slice(first, last);
      window.__speechFixture = {samples: Array.from(trimmed), sample_rate: client.context.sampleRate};
      const buffer = client.context.createBuffer(1, trimmed.length, client.context.sampleRate);
      buffer.copyToChannel(trimmed, 0);
      const source = client.context.createBufferSource();
      source.buffer = buffer;
      const destination = client.context.createMediaStreamDestination();
      source.connect(destination);
      await client.peer.getSenders().find((sender) => sender.track?.kind === 'audio').replaceTrack(destination.stream.getAudioTracks()[0]);
      client.mic.getTracks().forEach((track) => track.stop());
      client.mic = destination.stream;
      client.inputAnalyser = client.context.createAnalyser();
      client.inputAnalyser.fftSize = 512;
      const analysisSource = client.context.createMediaStreamSource(destination.stream);
      const silent = client.context.createGain();
      silent.gain.value = 0;
      analysisSource.connect(client.inputAnalyser).connect(silent).connect(client.context.destination);
      client.nodes.push(analysisSource, client.inputAnalyser, silent);
      client.setMuted(false);
      window.__fixtureNodes = [source, destination];
      source.start();
      return {samples: trimmed.length, sample_rate: client.context.sampleRate, duration: buffer.duration};
    }, savedFixture);
    report.checks.input_fixture = fixture;
    fs.writeFileSync(`${results}/slovene-speech-fixture.json`, JSON.stringify(await page.evaluate(() => window.__speechFixture)));
    console.log('Sent synthetic Slovene microphone fixture', JSON.stringify(fixture));
    await waitForState(page, (state) => state.transcript.some((entry) => !previousVoiceIds.has(entry.id) && entry.role === 'user' && entry.source === 'voice' && /devetnajst|sedemnajst/i.test(entry.text)), 45000);
    report.checks.input_transcript = await page.evaluate(async () => (await fetch('/api/status').then((r) => r.json())).transcript.filter((entry) => entry.role === 'user' && entry.source === 'voice'));
    console.log('PASS native Slovene recognition through the browser input track');
    await waitForState(page, (state) => state.task?.turn_id !== submitted.turn_id && state.task?.status === 'completed' && /323|tristo/i.test(state.task.result));
    report.checks.spoken_delegation = await page.evaluate(async () => {
      const state = await fetch('/api/status').then((r) => r.json());
      return {task: state.task, model: state.model, effort: state.effort, voice: state.voice, error: state.error};
    });
    assert.equal(report.checks.spoken_delegation.model, 'gpt-6.1-sol');
    assert.equal(report.checks.spoken_delegation.effort, 'xhigh');
    console.log('PASS spoken "Premisli temeljito" delegated to Sol xhigh');
    const outputBefore = await page.evaluate(() => window.voiceClient.debug.audioSamples);
    await page.waitForFunction((before) => window.voiceClient.debug.audioSamples > before + 2 && !window.voiceClient.audio.muted, outputBefore, {timeout: 30000});
    report.checks.verified_answer_audio = await page.evaluate(() => window.voiceClient.debug);
    await page.screenshot({path: `${results}/desktop-live.png`, fullPage: true});
    await page.getByRole('button', {name: 'Končaj pogovor', exact: true}).click();
    report.checks.voice_stopped = true;
    await page.setViewportSize({width: 390, height: 844});
    await page.screenshot({path: `${results}/mobile.png`, fullPage: true});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    report.checks.mobile_layout = 'no horizontal overflow';
    await page.evaluate(() => {navigator.mediaDevices.getUserMedia = async () => {throw new DOMException('denied', 'NotAllowedError');};});
    let nativeStarts = 0;
    page.on('request', (request) => {if (request.url().endsWith('/api/voice/start')) nativeStarts++;});
    await page.getByRole('button', {name: 'Začni pogovor', exact: true}).click();
    await page.getByText('Dostop do mikrofona ni dovoljen.', {exact: false}).waitFor();
    assert.equal(nativeStarts, 0);
    report.checks.microphone_denial = 'shown without starting a metered voice session';
    report.status = 'passed';
  } catch (error) {
    report.status = 'failed';
    report.errors.push(error.message);
    if (page) await page.screenshot({path: `${results}/failure.png`, fullPage: true}).catch(() => {});
    process.exitCode = 1;
    console.error(error.message);
  } finally {
    if (page) await page.evaluate(() => window.voiceClient?.stop()).catch(() => {});
    await browser.close();
    fs.writeFileSync(`${results}/live-acceptance.json`, JSON.stringify(report, null, 2));
    console.log(`Report: ${results}/live-acceptance.json`);
  }
})();
