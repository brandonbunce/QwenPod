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
"""

MIC_HTML = """
<div id="qp-mic">
  <div class="qp-mic-row">
    <select id="qp-mic-dev" title="Which microphone to record from"></select>
    <button id="qp-mic-rec" type="button">Record</button>
    <label id="qp-mic-file-label" for="qp-mic-file" title="Send an audio file instead">File</label>
    <input id="qp-mic-file" type="file" accept="audio/*">
    <span id="qp-mic-meter"><i></i></span>
  </div>
  <div id="qp-mic-status">Pick a microphone and press Record.</div>
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
  const btn    = root.querySelector('#qp-mic-rec');
  const file   = root.querySelector('#qp-mic-file');
  const status = root.querySelector('#qp-mic-status');
  const meter  = root.querySelector('#qp-mic-meter i');
  const say = (m) => { status.textContent = m; };

  // The hidden textbox is the seam back into gradio: setting a Svelte-bound
  // input needs the native value setter plus a bubbling 'input' event, or the
  // framework never sees the change.
  const sink = () => document.querySelector('#qp-mic-path textarea');
  const handOff = (path) => {
    const el = sink();
    if (!el) { say('Internal error: mic path field missing.'); return; }
    const setter = Object.getOwnPropertyDescriptor(
        window.HTMLTextAreaElement.prototype, 'value').set;
    setter.call(el, path);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  };

  const send = async (blob, name) => {
    if (!blob || !blob.size) { say('Nothing recorded.'); return; }
    say('Sending ' + Math.round(blob.size / 1024) + ' kB...');
    try {
      const fd = new FormData();
      fd.append('files', blob, name);
      const r = await fetch('gradio_api/upload', { method: 'POST', body: fd });
      if (!r.ok) { say('Upload failed: HTTP ' + r.status); return; }
      const paths = await r.json();
      if (!paths || !paths[0]) { say('Upload returned nothing.'); return; }
      say('Transcribing...');
      handOff(paths[0]);
    } catch (e) { say('Upload failed: ' + e.message); }
  };

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

  let rec = null, chunks = [], ctx = null, raf = null;

  // A live level bar, so a dead input is obvious before you speak into it
  // rather than after the transcription comes back empty.
  const startMeter = (stream) => {
    try {
      ctx = new AudioContext();
      const src = ctx.createMediaStreamSource(stream);
      const an = ctx.createAnalyser();
      an.fftSize = 512;
      src.connect(an);
      const buf = new Float32Array(an.fftSize);
      let peak = 0;
      const tick = () => {
        an.getFloatTimeDomainData(buf);
        let m = 0;
        for (let i = 0; i < buf.length; i++) { const v = Math.abs(buf[i]); if (v > m) m = v; }
        peak = Math.max(m, peak * 0.92);
        meter.style.width = Math.min(100, Math.round(peak * 140)) + '%';
        meter.style.background = peak < 0.005 ? 'var(--color-accent)' : '#3fb950';
        raf = requestAnimationFrame(tick);
      };
      tick();
    } catch (e) { /* meter is a nicety; never let it break recording */ }
  };
  const stopMeter = () => {
    if (raf) cancelAnimationFrame(raf); raf = null;
    if (ctx) { ctx.close().catch(() => {}); ctx = null; }
    meter.style.width = '0%';
  };

  btn.onclick = async () => {
    if (rec && rec.state === 'recording') { rec.stop(); return; }
    try {
      const want = sel.value
        ? { audio: { deviceId: { exact: sel.value } } }
        : { audio: true };
      const stream = await navigator.mediaDevices.getUserMedia(want);
      await listDevices();          // labels only populate post-permission
      startMeter(stream);
      chunks = [];
      rec = new MediaRecorder(stream);
      rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
      rec.onstop = async () => {
        stopMeter();
        stream.getTracks().forEach(t => t.stop());
        btn.textContent = 'Record';
        btn.classList.remove('qp-recording');
        await send(new Blob(chunks, { type: rec.mimeType || 'audio/webm' }), 'take.webm');
      };
      rec.start();
      btn.textContent = 'Stop';
      btn.classList.add('qp-recording');
      const dev = sel.options[sel.selectedIndex];
      say('Recording from ' + ((dev && dev.textContent) || 'default') + '...');
    } catch (e) {
      say('Microphone error: ' + e.message);
    }
  };

  file.onchange = () => {
    const f = file.files && file.files[0];
    if (f) send(f, f.name);
    file.value = '';
  };

  navigator.mediaDevices.addEventListener?.('devicechange', listDevices);
  listDevices();
}
"""
