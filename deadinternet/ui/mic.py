"""A microphone that does not go through gradio's Audio component.

gradio's recorder hands the finished clip to its own player, which decodes and
renders it -- and that decode measured ~3x realtime on this machine, so a
six-second line froze the page for twenty seconds. Profiles put the whole stall
inside getChannelData; disabling the waveform, the trim editor and matching the
context sample rate did not move it. The component has no option to skip
rendering audio it is holding.

So this does not hold any audio. It records with MediaRecorder, POSTs the blob
straight to gradio's own upload endpoint, and hands the returned server path to
a hidden textbox that a normal gradio event listens to. Nothing decodes the
clip in the browser, so there is nothing to stall on.

Doing it directly also fixes the problem that started this: gradio calls
getUserMedia({audio: true}), which resolves to Chrome's default input rather
than the system's. On a machine with several inputs that is easily an unplugged
socket, and it records perfectly formed silence. Here the device is chosen
explicitly and a live level meter shows whether anything is actually arriving,
before you say a word into a dead microphone.

Sentence segmentation
---------------------
Recording is continuous. A voice-activity detector watches the same analyser
that drives the meter, and when you stop talking for longer than the chosen
pause it cuts the take there and sends it -- so a sentence is transcribed and
spoken while you carry on with the next one, instead of after you press Stop.

The cut is a real MediaRecorder stop/start rather than a timeslice, because
webm chunks after the first have no header and are not standalone files. The
few milliseconds lost in the restart fall inside the pause, by construction.

Segments are sent strictly one at a time: upload, hand off, then wait for the
server to clear the hidden textbox before releasing the next one. Speech has to
come out in the order it went in, and gradio's queue is free to run four
handlers at once.
"""

MIC_HTML = """
<div id="qp-mic" class="qp-mic">
  <div class="qp-mic-row">
    <select id="qp-mic-dev" class="qp-control qp-select qp-grow"
            title="Which microphone to record from"></select>
    <select id="qp-mic-gap" class="qp-control qp-select"
            title="How long a pause ends a sentence">
      <option value="500" selected>pause 0.5s</option>
      <option value="800">pause 0.8s</option>
      <option value="1200">pause 1.2s</option>
      <option value="2000">pause 2.0s</option>
    </select>
    <button id="qp-mic-rec" class="qp-control qp-btn" type="button">Record</button>
    <label id="qp-mic-file-label" class="qp-control qp-btn" for="qp-mic-file"
           title="Send an audio file instead">File</label>
    <input id="qp-mic-file" class="qp-hidden" type="file" accept="audio/*">
    <span id="qp-mic-meter" class="qp-meter"><i></i></span>
  </div>
  <div id="qp-mic-status" class="qp-mic-status">Pick a microphone and press Record.</div>
</div>
"""

# Wired once on page load. Everything is scoped to #qp-mic so it cannot collide
# with gradio's own handlers.
MIC_JS = r"""
() => {
  const root = document.querySelector('#qp-mic');
  if (!root || root.dataset.wired) return;
  root.dataset.wired = '1';

  const sel    = root.querySelector('#qp-mic-dev');
  const gapSel = root.querySelector('#qp-mic-gap');
  const btn    = root.querySelector('#qp-mic-rec');
  const file   = root.querySelector('#qp-mic-file');
  const status = root.querySelector('#qp-mic-status');
  const meter  = root.querySelector('#qp-mic-meter i');

  // Never cut a take shorter than this -- a cough would otherwise become a
  // segment. Never let one run longer than the cap either: without it, a
  // detector fooled by steady background noise records until the tab dies.
  const MIN_SEG_MS = 600;
  const MAX_SEG_MS = 25000;
  // Whisper is slower than speech. Backing up is normal; unbounded is not.
  const MAX_QUEUE  = 8;

  /* ---- status line ---------------------------------------------------- */
  // Composed rather than assigned, because the recorder and the send queue
  // both have something to say and each would otherwise wipe the other.
  let recState = 'idle';       // idle | rec
  let speaking = false;
  let lastMsg  = 'Pick a microphone and press Record.';
  const render = () => {
    const inFlight = queue.length + (pumping ? 1 : 0);
    const bits = [];
    if (recState === 'rec') bits.push(speaking ? 'Listening (speech)' : 'Listening...');
    if (inFlight) bits.push(inFlight + (inFlight > 1 ? ' clips' : ' clip') + ' in flight');
    if (lastMsg) bits.push(lastMsg);
    status.textContent = bits.join('  -  ');
  };
  const say = (m) => { lastMsg = m; render(); };

  /* ---- the seam back into gradio -------------------------------------- */
  // Setting a Svelte-bound input needs the native value setter plus a bubbling
  // 'input' event, or the framework never sees the change.
  const sink = () => document.querySelector('#qp-mic-path textarea');
  const handOff = (path) => {
    const el = sink();
    if (!el) { say('Internal error: mic path field missing.'); return false; }
    const setter = Object.getOwnPropertyDescriptor(
        window.HTMLTextAreaElement.prototype, 'value').set;
    setter.call(el, path);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  };

  // The handler clears the textbox on every exit path, so an empty field means
  // the server is done with that clip. Waiting for it is what keeps the spoken
  // lines in the order they were said.
  const waitForClear = (timeoutMs) => new Promise((resolve) => {
    const started = Date.now();
    const check = () => {
      const el = sink();
      if (!el || el.value === '') return resolve(true);
      if (Date.now() - started > timeoutMs) return resolve(false);
      setTimeout(check, 150);
    };
    setTimeout(check, 150);
  });

  /* ---- the send queue -------------------------------------------------- */
  let queue = [], pumping = false;

  const sendOne = async (item) => {
    if (!item.blob || !item.blob.size) { say('Nothing recorded.'); return; }
    say('Sending ' + Math.round(item.blob.size / 1024) + ' kB...');
    let paths;
    try {
      const fd = new FormData();
      fd.append('files', item.blob, item.name);
      const r = await fetch('gradio_api/upload', { method: 'POST', body: fd });
      if (!r.ok) { say('Upload failed: HTTP ' + r.status); return; }
      paths = await r.json();
    } catch (e) { say('Upload failed: ' + e.message); return; }
    if (!paths || !paths[0]) { say('Upload returned nothing.'); return; }

    say('Transcribing...');
    if (!handOff(paths[0])) return;
    // Long enough to cover a cold whisper model on a busy CPU; short enough
    // that a dropped websocket does not wedge the queue for the session.
    say(await waitForClear(180000) ? 'Sent.'
                                   : 'No response for that clip -- carrying on.');
  };

  const pump = async () => {
    if (pumping) return;
    pumping = true;
    try {
      while (queue.length) { await sendOne(queue.shift()); render(); }
    } finally {
      pumping = false;
      // Otherwise the last clip's 'Transcribing...' sits there for good, which
      // reads as a clip that never came back.
      say(recState === 'rec' ? '' : 'Ready.');
    }
  };

  const enqueue = (blob, name) => {
    if (queue.length >= MAX_QUEUE) {
      say('Too far behind -- dropped a clip. Say less at once, or use a smaller model.');
      return;
    }
    queue.push({ blob: blob, name: name });
    render();
    pump();
  };

  /* ---- devices --------------------------------------------------------- */
  const listDevices = async () => {
    try {
      const devs = (await navigator.mediaDevices.enumerateDevices())
                     .filter(d => d.kind === 'audioinput');
      const keep = sel.value;
      sel.innerHTML = '';
      if (!devs.length) {
        const o = document.createElement('option');
        o.textContent = 'no microphone found'; o.value = '';
        sel.appendChild(o);
        return;
      }
      for (const d of devs) {
        const o = document.createElement('option');
        o.value = d.deviceId;
        // Labels are blank until permission has been granted at least once.
        o.textContent = d.label || ('input ' + d.deviceId.slice(0, 8));
        sel.appendChild(o);
      }
      if (keep && [...sel.options].some(o => o.value === keep)) sel.value = keep;
    } catch (e) { say('Could not list microphones: ' + e.message); }
  };

  /* ---- capture --------------------------------------------------------- */
  let rec = null, stream = null, chunks = [];
  let ctx = null, levelNode = null, muteNode = null;
  let heardSpeech = false, segStart = 0, lastVoiceAt = 0;
  let cutting = false, stopping = false;

  // Voice activity, off the same analyser that draws the meter. The threshold
  // rides an estimated noise floor -- fast to fall, very slow to rise -- so it
  // calibrates itself to the room instead of to a number someone guessed on a
  // different microphone. The absolute minimum stops a silent input (which has
  // a noise floor of nearly zero) from hearing speech in its own dither.
  let floorEst = null;
  const isSpeech = (rms) => {
    floorEst = floorEst === null ? rms
             : rms < floorEst ? floorEst * 0.85 + rms * 0.15
                              : floorEst * 0.9995 + rms * 0.0005;
    const on = Math.max(floorEst * 3.5, 0.008);
    return speaking ? rms > on * 0.55 : rms > on;   // hysteresis
  };

  const startSegment = () => {
    chunks = [];
    heardSpeech = false;
    segStart = Date.now();
    lastVoiceAt = segStart;
    cutting = false;
    rec = new MediaRecorder(stream);
    rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    rec.onstop = () => {
      const blob = new Blob(chunks, { type: rec.mimeType || 'audio/webm' });
      const keep = heardSpeech;
      if (keep) enqueue(blob, 'take.webm');
      if (stopping) {
        teardown();
        if (!keep) say('Stopped. Nothing was said.');
      } else {
        startSegment();     // straight back to listening
        render();
      }
    };
    rec.start();
  };

  const teardown = () => {
    recState = 'idle'; speaking = false;
    if (levelNode) { try { levelNode.disconnect(); } catch (e) {} levelNode = null; }
    if (muteNode) { try { muteNode.disconnect(); } catch (e) {} muteNode = null; }
    if (ctx) { ctx.close().catch(() => {}); ctx = null; }
    if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
    meter.style.width = '0%';
    meter.classList.remove('qp-voiced');
    btn.textContent = 'Record';
    btn.classList.remove('qp-recording');
    rec = null;
    render();
  };

  // One callback drives both the meter and the segmenter, so what the bar
  // shows is exactly what the detector is acting on. A dead input is a bar
  // that never moves -- visible before you speak, rather than after a
  // transcription comes back empty.
  let peakDecay = 0;
  const onLevel = (rms, peak) => {
    peakDecay = Math.max(peak, peakDecay * 0.85);
    meter.style.width = Math.min(100, Math.round(peakDecay * 140)) + '%';

    const now = Date.now();
    const voiced = isSpeech(rms);
    if (voiced !== speaking) {
      // Only on a transition: this runs ~50 times a second, and writing a
      // style every time is how you invent a performance problem.
      speaking = voiced;
      meter.classList.toggle('qp-voiced', voiced);
      render();
    }
    if (voiced) { lastVoiceAt = now; heardSpeech = true; }

    if (!rec || rec.state !== 'recording' || cutting || stopping) return;
    const age = now - segStart;
    const quiet = now - lastVoiceAt;
    const gap = parseInt(gapSel.value, 10) || 800;
    // Cut on a pause once something has actually been said, or when the cap
    // runs out and the pause never came.
    if ((heardSpeech && age > MIN_SEG_MS && quiet > gap) || age > MAX_SEG_MS) {
      cutting = true;
      rec.stop();
    }
  };

  // Levels come off the audio thread, not requestAnimationFrame.
  //
  // rAF does not fire at all in a hidden tab, and the segmenter is the only
  // thing that ends a take: on rAF, switching tabs mid-sentence froze the
  // detector, so nothing was ever cut or sent and the segment grew until the
  // 25s cap -- which is checked in the same dead loop, so not even that. An
  // AudioWorklet runs wherever the audio does, which is everywhere.
  const WORKLET = `
    class QpVad extends AudioWorkletProcessor {
      constructor() { super(); this.sum = 0; this.peak = 0; this.n = 0; }
      process(inputs) {
        const ch = inputs[0] && inputs[0][0];
        if (ch) {
          for (let i = 0; i < ch.length; i++) {
            const v = ch[i];
            this.sum += v * v;
            const a = v < 0 ? -v : v;
            if (a > this.peak) this.peak = a;
          }
          this.n += ch.length;
        }
        if (this.n >= 1024) {          // ~21ms at 48k
          this.port.postMessage([Math.sqrt(this.sum / this.n), this.peak]);
          this.sum = 0; this.peak = 0; this.n = 0;
        }
        return true;
      }
    }
    registerProcessor('qp-vad', QpVad);
  `;

  const startLevels = async () => {
    ctx = new AudioContext();
    const src = ctx.createMediaStreamSource(stream);
    floorEst = null; peakDecay = 0;
    let node;
    try {
      const url = URL.createObjectURL(
          new Blob([WORKLET], { type: 'application/javascript' }));
      await ctx.audioWorklet.addModule(url);
      URL.revokeObjectURL(url);
      node = new AudioWorkletNode(ctx, 'qp-vad');
      node.port.onmessage = (e) => onLevel(e.data[0], e.data[1]);
    } catch (e) {
      // Deprecated, and it puts the callback back on the main thread -- but
      // audio callbacks are not visibility-gated the way rAF is, so this is
      // still a working fallback rather than a silently broken one.
      node = ctx.createScriptProcessor(1024, 1, 1);
      node.onaudioprocess = (ev) => {
        const ch = ev.inputBuffer.getChannelData(0);
        let sum = 0, m = 0;
        for (let i = 0; i < ch.length; i++) {
          const v = ch[i];
          sum += v * v;
          const a = v < 0 ? -v : v;
          if (a > m) m = a;
        }
        onLevel(Math.sqrt(sum / ch.length), m);
      };
    }
    // Chrome renders the graph on demand from the destination, so both node
    // types need a path to it. The gain is zero -- this is about being pulled,
    // not about hearing yourself.
    src.connect(node);
    muteNode = ctx.createGain();
    muteNode.gain.value = 0;
    node.connect(muteNode);
    muteNode.connect(ctx.destination);
    levelNode = node;
  };

  btn.onclick = async () => {
    if (recState === 'rec') {
      stopping = true;
      say('Finishing...');
      if (rec && rec.state === 'recording') rec.stop(); else teardown();
      return;
    }
    try {
      const want = sel.value
        ? { audio: { deviceId: { exact: sel.value } } }
        : { audio: true };
      stream = await navigator.mediaDevices.getUserMedia(want);
      await listDevices();          // labels only populate post-permission
      stopping = false;
      recState = 'rec';
      btn.textContent = 'Stop';
      btn.classList.add('qp-recording');
      const dev = sel.options[sel.selectedIndex];
      say('Recording from ' + ((dev && dev.textContent) || 'default')
          + '. Pause between sentences and each one is sent on its own.');
      // Segment first: the worklet module takes a few milliseconds to load and
      // there is no reason for those to be missing from the take.
      startSegment();
      try {
        await startLevels();
      } catch (e2) {
        // No levels means no detector, and no detector means nothing ever ends
        // a take. Recording still works; it is just back to one per press. Said
        // after the line above, so it is the message that survives.
        say('No input levels (' + e2.message + ') -- sentences will not be split, '
            + 'press Stop to send.');
      }
    } catch (e) {
      recState = 'idle';
      say('Microphone error: ' + e.message);
    }
  };

  file.onchange = () => {
    const f = file.files && file.files[0];
    if (f) enqueue(f, f.name);
    file.value = '';
  };

  navigator.mediaDevices.addEventListener?.('devicechange', listDevices);
  listDevices();
  render();
}
"""
