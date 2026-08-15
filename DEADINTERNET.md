# QwenPod — Dead Internet Mode

QwenPod: a web UI over qwentts.cpp with a Discord bot that fills a voice
channel with cloned personas driven by a local or remote LLM.

**Only clone people who agreed to it, and keep the bot to channels where
everyone present knows the voices are synthetic.**

---

## Building

The app needs **one** binary: it launches `build/tts-server` and then speaks
HTTP to it. `qwen-tts`, `qwen-codec` and `quantize` are upstream's CLI tools —
still built by the `build*.sh` scripts, still shipped in the Docker images,
just never on this app's path.

Configure once for your backend (this wipes `build/` and runs cmake):

```bash
./buildvulkan.sh
```

After that, rebuild just what the app uses:

```bash
./buildapp.sh
```

Python side, once:

```bash
python -m venv .venv-app && ./.venv-app/bin/pip install -r requirements-app.txt
```

`requirements-app.txt` pins gradio exactly. `deadinternet/ui/` is written
around four behaviours specific to 6.22, so treat a gradio upgrade as a change
that needs the UI re-checked, not a routine bump.

---

## Running the stack

Three processes. Ollama is a systemd service and starts on boot; **the app
launches tts-server itself**, so normally you start one thing.

**1. Ollama** — check it's up, nothing to do normally:

```bash
systemctl is-active ollama
```

**2. The app** — this also brings up tts-server:

```bash
cd ~/Documents/qwentts.cpp && ./.venv-app/bin/python ./app.py
```

Then open <http://127.0.0.1:7860>. The web UI is serving within a second or
two; tts-server comes up behind it and the roster is re-registered as soon as
it answers (roughly 30s for 23 voices). Watch the log on **Diagnostics** —
`[tts] server up` then `[boot] restored n/n voices`. Until that lands, voice
dropdowns are empty; **Refresh voices** on the Testing tab repairs them at
any time.

To run it detached instead of holding a terminal:

```bash
cd ~/Documents/qwentts.cpp && nohup ./.venv-app/bin/python ./app.py >> app.log 2>&1 & disown
```

Startup skips the launch if port 8080 already answers, so an app restart never
stacks a second server on the card — and never disturbs a healthy one. Pass
`--no-tts-autostart` to suppress the launch entirely (it still registers
voices against a server that is already up).

To run tts-server yourself instead:

```bash
cd ~/Documents/qwentts.cpp && nohup ./build/tts-server --model models/qwen-talker-1.7b-base-Q8_0.gguf --codec models/qwen-tokenizer-12hz-F32.gguf --host 127.0.0.1 --port 8080 --lang English >> tts-server.log 2>&1 & disown
```

Checking and stopping:

```bash
for p in 7860 8080 11434; do curl -s -o /dev/null -m 2 -w "port $p: %{http_code}\n" http://127.0.0.1:$p/; done
```

```bash
pkill -f build/tts-server; pkill -f "app\.py"
```

Port 8080 returning **404** is healthy — tts-server has no `/` route. Use
`/v1/models` if you want a 200. App flags: `--tts`, `--ollama`, `--model`,
`--host`, `--port`, `--no-tts-autostart`.

Kill tts-server by PID, or with a pattern that isn't also in the command you
are typing — `pkill -f build/tts-server` inside a longer command line matches
that command line too and kills your own shell.

You do **not** need conda. Both venvs are uv-managed CPython 3.12; miniconda
was removed from this machine. `tts-server` is a native binary with no Python
linkage at all.

### If tts-server dies while the app is up

`pkill -f build/tts-server` alone leaves the app running against nothing, and
the app cannot tell — registrations live only in the server's memory, so it
still believes every voice is live. Two ways back:

- **Start** on the Run tab launches it if port 8080 is dead, same as boot does.
- **Refresh voices** on the Testing tab re-uploads the roster to a server that
  came back empty.

Both wait up to 120s and log to `tts-server.log`. Restarting the app does both
at once, which is why `pkill`-both-then-start-the-app now works as a recipe.

---

## VRAM: the thing that will bite you

The card has ~16 GB and **Ollama and tts-server compete for it**. Measured on
this machine, same 1.7B model, same job:

| VRAM state | TTS speed |
| --- | --- |
| headroom free | 4.2 ms/frame, RTF 0.24 |
| 99% full (two Ollama models resident) | 34 ms/frame, RTF 1.07 |

A ~5x collapse, and **it does not recover on its own**. Once the driver evicts
tts-server's Vulkan buffers to host memory they stay there for the life of the
process; freeing VRAM afterwards does nothing. You must restart tts-server.

- Start tts-server while the card is empty — starting the app early does this
  for you, before Ollama has a model resident.
- Keep one Ollama model loaded: `OLLAMA_MAX_LOADED_MODELS=1`.
- Prefer a small model. `gemma4:e4b` is ~3.3 GB. A 13B+ will not fit alongside.
- `ollama stop <model>` unloads one you're done with.
- The status line shows live VRAM and warns past 85%. **If speech goes
  sluggish, check there first, then restart tts-server.**

---

## The interface

A persistent masthead over seven tabs:

```
QwenPod        mode • voice • listeners • director • vram • gpu     [Refresh]
action line    the last thing you did, and why it did or didn't work
─────────────────────────────────────────────────────────────────────
Run · Speakers · Inputs · Outputs · Behaviour · Testing · Diagnostics
```

The status strip streams and lives above the tabs, so mode and VRAM stay on
screen wherever you are — a stalled director or a full card is easiest to miss
exactly when you are looking at some other tab. The action line below it does
**not** stream: it holds your last action's result until you do something else.

| Tab | What it holds |
| --- | --- |
| `Run` | transport, mode, current topic and who's talking on the left; the queue, topic injection, transcript and "say a line as" on the right |
| `Speakers` | the roster as a strip across the top (arrows step through it), editor in two columns below |
| `Inputs` | where topics come from — the topic itself and rotation rules on the left, the weighted sources on the right |
| `Outputs` | where the audio goes. Connect the bot and join a voice channel here |
| `Behaviour` | LLM backend, conversation tuning, spoken templates |
| `Testing` | speak arbitrary text in a chosen voice, with the raw sampling knobs |
| `Diagnostics` | service status and the event log on the left, subsystem reports on the right |

Two conventions worth knowing:

- **There is no Apply button.** Settings persist the moment you change them —
  sliders on release, text boxes on blur or Enter, everything else on change.
  The action line confirms each save.
- **The status bar streams; the action line does not.** They used to be one
  Markdown, so every button's confirmation was overwritten by the 1.5s feed
  within a second or two. Keep them separate.

Actions also raise a toast (`gr.Info`/`gr.Warning`), so a result is visible
wherever you have scrolled to.

---

## Modes

- **podcast** — speakers take turns among themselves. Chat ignored.
- **interactive** — a real person **typing** in any text channel of the server
  the bot is in interrupts. A router picks who answers, based on who was
  addressed by name, whose persona fits, and who spoke last.
- **manual** — nothing automatic. Pick a speaker, type a line, they say it.
  Switching to manual cuts the current line and drops the queued one.

Typing is the only input path — there is no speech recognition. See
*No speech input* under Known limitations.

The manual **Say** box works in any mode, jumps the queue, and starts the
director itself — no need to press Start first. It still needs the bot
connected and in a channel, since the audio has to go somewhere.

---

## Speakers

Each is a cloned voice + a system prompt. Reference clips are copied into
`voices/` and the roster is saved to `deadinternet.json`, because **tts-server
holds registered voices in memory only** — they vanish on restart. The app
re-registers the whole roster at boot from those files.

Saving a speaker registers its clip with tts-server, so there is one path that
creates a voice rather than two. A speaker with no persona is parked
`enabled: false` and will not join conversations until you tick it on **Run**.

5–10s of clean audio makes a good reference. Supplying the transcript enables
ICL mode, which tracks the reference more closely than speaker-embedding-only.

### Building a persona from Discord history

**Build persona** on the **Speakers** tab takes a Discord handle, reads that person's
messages from the last two years, samples 40 at random, and has the LLM write
a system prompt from them. The sampled messages are quoted in the finished
prompt too — a description tells the model who someone is, but the raw lines
are what make it sound like them.

The handle is matched against username, global name, server nickname, the
`name#0` form, and the numeric user ID. Matching happens per message, so this
does **not** need the privileged members intent — but it does need
`message_content`, same as chat input.

Reading each channel newest-first would sample only the last few weeks, so
most requests are anchored at random points inside the two-year window
(`ANCHORS_PER_CHANNEL` probes per channel plus one recent slice, shuffled).
The scan stops at `PERSONA_SCAN_CAP` messages or `PERSONA_POOL_TARGET` hits,
whichever comes first; without that cap a busy server would take an hour.
Expect one to a few minutes — the button streams progress rather than
freezing.

Messages are cleaned before use: links, mentions and custom emoji are
stripped, and anything left under four characters is dropped. Duplicates are
removed, so a hundred "lol"s count once.

**Written text is not spoken text.** Discord shorthand read aloud sounds
nothing like the person, so the generated prompt explicitly separates what to
imitate (vocabulary, opinions, attitude) from what to drop (formatting,
abbreviations, emoji). If output still sounds like someone reading chat logs,
that instruction is the thing to strengthen.

Nothing is saved until you press **Save speaker**. The name field is filled in
only when it was empty, so it cannot silently retarget an existing speaker.

### Vocal stims

Per-speaker catchphrases. **Vocal stims** takes one phrase per line (commas
work too); **Stim chance (%)** is how often a turn carries one.

The phrase goes into that turn's system prompt as a verbal tic, which sounds
far better than concatenating it onto a finished sentence. If the model ignores
it the phrase is appended, so a successful roll always lands. **Diagnostics** shows
`(in-line)` vs `(appended)` for the last one — lots of `appended` means the
phrase is awkward to work into a sentence naturally.

---

## Inputs

The **Inputs** tab holds everything about what they talk about. The
**Topic** box is the live topic: rotation overwrites it, and the status feed
pushes the new value back into the box, so it always shows what is actually
being discussed. The feed only pushes when the value actually changed —
otherwise a 1.5s refresh would fight you typing in there.

Optional rotation. Every N minutes the director pulls pinned messages and
makes one the topic. Time-based, not token-based: turn lengths vary too much
for a token count to give predictable segments.

**Pins pool across every ticked channel** in the Pin channels checklist, so a
short pin list in one channel does not force the same few topics round again.
Duplicates are removed by message id. Pins with neither text nor an image are
dropped — there would be nothing to talk about.

Pins are chosen from a **shuffled bag** — random order, every pin used once
before any repeat, reshuffled per cycle. Driven by the director's own seeded
RNG (`random.Random`), not the global `random`.

**Pinned by** shows who posted the pin the topic came from, and where. It
blanks the moment you edit the topic by hand — there is no sender to credit
for something you typed.

### Images in pins

When the chosen pin has an image and **Send pinned images to the model** is
on, **the announcer describes it out loud**:

> *"Okay, new topic by brandon: check this out. We're looking at an image. A
> dog wearing sunglasses on a skateboard."*

That spoken line lands in the transcript, and the description is folded into
the topic string, so **every other speaker works from the text**. There is
exactly **one vision call per topic**, not one per turn. Beyond being far
cheaper, it keeps everyone's idea of the picture consistent — separate vision
calls would have each speaker seeing slightly different things.

The announcement matters for a second reason: the listeners can see the pin in
the channel, but nothing in the spoken line would otherwise explain why the
subject changed to something wordless.

Only the **chosen** pin's image is downloaded; fetching every attachment on
every rotation would be wasted bandwidth. Non-raster types (including SVG) and
anything over `MAX_IMAGE_BYTES` (8 MB) are skipped at pin-collection time.

The two providers disagree about the wire format, so each builds its own:
Ollama takes bare base64 in a sibling `images` list, OpenAI wants a `data:`
URI inside a content-part list.

**This needs a multimodal model.** If describing fails, the app falls back to
the old behaviour — the image is attached to each turn directly, and a model
that rejects it outright gets one text-only retry before images are switched
off for the session. A text-only Ollama model does not error at all: it
silently ignores the `images` field, so you get a description of nothing.
Check **image description** under **Diagnostics** if the speakers seem to be talking
about a picture they cannot see.

### Pins are read aloud, so they get cleaned

Pin text and chat messages both go through `clean_content` and then
`_readable()`. Without it a pin that is just `<@!000000000000000000>` gets
handed to the TTS to voice as digits. Mentions resolve to names, the `@` and
`#` sigils come off (otherwise they are read as "at" and "hash"), and custom
emoji are dropped since there is no way to say them.

### Switching is prefetched

Everything expensive about a switch — reading pins from every channel,
downloading the image, the vision call, **and synthesising the announcement**
— happens in the background while the current conversation carries on
(`_build_topic`, held in `_prepared`). The switch itself is then little more
than a `play_wav`: measured at ~1 ms against ~1 s of work done cold.

**Diagnostics** shows **next topic ready** and whether the last switch used a
prepared one. A forced switch that arrives before the prefetch finishes waits
up to 20s for it, then builds inline. **Reshuffle now** discards whatever was
queued, since it came out of the old bag.

- **RNG seed** — `0` = fresh entropy each start; any other number reproduces a
  sequence exactly (pin order, speaker choice, and stim rolls).
- **Reshuffle now** — new seed and empties the bag mid-session.
- With only 2–3 pins the order is inherently constrained; no amount of seeding
  fixes that.

Switches happen **between turns**, never mid-sentence. The **Switch topic now**
button queues rather than firing immediately, for the same reason — so the
longest you wait is the rest of the current line, capped by the speech limit.

By default the LLM context is wiped at each switch (a new topic on the old
transcript drags the conversation backwards), and a speaker reads the handover
out loud — *"Okay, new topic by brandon: the best pizza topping"* — using the
display name of whoever pinned it.

---

## Opening announcement

On **Start**, one speaker opens with the topic and how to join in:

> *"Alright, we're live. Today's topic: X. If you want to jump in, type a
> message in the voice channel chat and one of us will answer you."*

Editable template on the **Behaviour** tab (`{topic}` is substituted), with a
toggle. Fires
once per Start, skipped in manual mode.

It points people at **text chat** deliberately — that's the interruption path
that actually works.

---

## Known limitations

### Discord ends the call when the channel empties

**This is the thing that made the bot go quiet for good.** When the last person
leaves a voice channel, Discord terminates the call with close code **4014** or
**4022**. discord.py makes exactly one `_potential_reconnect()` attempt, and
when no new voice server arrives it disconnects permanently and breaks out of
its runner loop ([`voice_state.py`][vs], the `if exc.code in (4014, 4022)`
branch). **It never comes back on its own.** The voice client is still there,
`is_connected()` is just permanently `False`, so nothing notices when somebody
rejoins later.

[vs]: .venv-app/lib/python3.12/site-packages/discord/voice_state.py

The fix is a watchdog on the Discord loop, ticking every 3s:

- It remembers the channel the UI asked for (`target_channel_id`) and rejoins
  it when the connection is down.
- **It will not rejoin an empty channel.** The call would just be terminated
  again, and the retries would earn a 4021 rate limit — which discord.py
  treats as fatal. It waits for somebody to show up first.
- Rejoin failures back off 2 → 5 → 10 → 20 → 30 → 60s.
- **Leave** clears the target, so a deliberate departure is not treated as a
  drop and dragged back in.

Occupancy is counted from the guild's **voice states**, not the member list,
so it works without the privileged members intent. Members are only looked up
to skip other bots and to get a display name — a music bot parked in the
channel does not count as an audience.

Two related guards:

- **Pause while empty** stops generating speech nobody can hear. After five
  minutes (`RESUME_RESET_AFTER`) the conversation restarts rather than
  resuming, since whoever walks in would otherwise hear the tail of a
  discussion that stopped an hour ago.
- `play_wav` takes a **wedge-guard timeout**. If the connection dies mid-clip,
  discord.py's player blocks in `wait_until_connected` for up to
  `VoiceClient.timeout` (60s) before aborting, and the turn loop would sit
  inside that await the whole time. The bound is `2 × speech cap + 30s` — a
  guard, not a length limit.

discord.py's voice logging is forwarded into `app.log` (`discord.voice_state`
and `discord.gateway` at INFO). The close code is the only explanation of why
a call ended and it exists nowhere else; without the bridge a drop is silent.

### No speech input

The bot is **output only**. Speech recognition was removed along with the
whole receive path; `faster-whisper` and `discord-ext-voice-recv` are
uninstalled (they and their exclusive dependencies — `ctranslate2`,
`onnxruntime`, `av`, `tokenizers` — were 300 MB of the venv).

The reason it went, rather than being fixed: **Discord's DAVE end-to-end
encryption.** Since discord.py 2.6, voice connections negotiate DAVE via the
`davey` library, which E2E-encrypts the Opus payload. `discord-ext-voice-recv`
only undoes the *transport* layer — grep the package for "dave" and you get
zero matches. Inbound frames arrived undecodable and the sink saw **0
packets**, on every release including 0.5.2a179. A workaround exists
(declaring `max_dave_protocol_version = 0` to force the call to downgrade) but
it disables E2EE for everyone in the channel, so it was rejected.

**Type instead** — any text channel in the server the bot is in, including a
voice channel's built-in chat. The router responds normally. **Diagnostics** traces
a message from arriving to being answered.

Do not reinstall `davey` away: discord.py needs it for voice **playback** as
well, so it stays.

### Gradio 6.22 quirks

- **`gr.Timer` never fires.** The live status/transcript feed is a streaming
  generator on `demo.load` instead. It expires after an hour; reload or press
  Refresh.
- **`gr.Dataframe` ignores value updates** pushed from an event handler. The
  roster is a `gr.Radio` of `(label, name)` choices for this reason — `choices=`
  updates do work, which every selector in `ui.py` relies on. Don't reintroduce
  a Dataframe.
- **`css=` and `theme=` moved from `Blocks()` to `launch()`** in Gradio 6.
  Passing them to `Blocks()` warns and is ignored.
- Components in **inactive sub-tabs are still mounted**, so the streaming feed
  keeps them current — switching tabs costs nothing and needs no rebinding.

### tts-server API

- **`response_format: "wav"` is mandatory.** Without it you get headerless raw
  PCM that no audio tool will open.
- **No per-request language field.** Language comes from `--lang` at startup.
- `--instruct` is rejected by Base models — tone comes from the reference clip.
  Only CustomVoice/VoiceDesign accept style instructions.

### LLM

- **Reasoning models return empty text.** They spend the whole `num_predict`
  budget in `thinking` and hand back empty content. The client always sends
  `think: false`; non-thinking models ignore it. Thinking would wreck the
  pacing of a live call anyway.
- Truncated lines are trimmed back to the last complete sentence, so the TTS
  never reads a dangling fragment.

### Discord

- One bot plays **one** audio stream, hence strict turn-taking. Simultaneous
  voices would need one bot application per persona.
- `move_to()` cannot change a voice client's class. This mattered when a
  receive-capable client class existed; with playback only, `move_to()` is
  now safe and the reconnect dance is gone.
- A voice client left over from a terminated call makes `connect()` raise
  "already connected". The join path force-disconnects a stale one first.

---

## Secrets

`.env` at the repo root (copy `.env.example`):

```
DISCORD_TOKEN=
OPENAI_API_KEY=      # only when provider is "openai"
```

Real environment variables override the file. The UI shows presence only,
never values, and has no field to type a key into. `.env`, `voices/`,
`deadinternet.json`, and `tts-server.log` are gitignored.

Edits to `.env` need an app restart.

---

## Tuning

Four controls with a **Reset to recommended** button (gap 0.4s, temp 0.9, 80
tokens, 12 turns). Note the small ↺ icons on each slider reset to the value the
slider was *built* with — i.e. your last saved setting — not to the
recommended default. The button is the one that goes to known-good.

Speech is hard-capped per utterance (default 25s), enforced twice: generation
is capped in frames (12.5 frames/sec) so the GPU never renders audio that gets
discarded, and anything still over is truncated with an 80 ms fade.

Output is loudness-normalised to a target dBFS RMS. Measured spread across
voices was 8.6 dB before, zero after.

---

## Architecture

```
Gradio (main thread)
   │  submit() / submit_async()
   ▼
Discord client ── own asyncio loop on its own thread
   │
   ├── Director task ── the turn loop
   │      ├── Ollama / OpenAI  (executor)   ← text, and pin images
   │      ├── TTS              (executor)
   │      └── play_wav ── FFmpegPCMAudio → 48 kHz stereo Opus
   │
   ├── on_message → push_user_text → router → reply   (the only input path)
   │
   └── mine_messages ── history scan for the persona builder
```

Everything blocking runs in an executor; the loop also drives audio playback
and must never stall. Cross-thread coroutine scheduling uses
`asyncio.run_coroutine_threadsafe`, **not** `loop.create_task` — the latter is
only safe from the loop thread and otherwise queues work that never runs.

The turn loop pre-generates: while speaker N talks, N+1's line is already going
through the LLM and TTS, so the only audible gap is the configured one. The
current line is appended to the transcript *before* pre-generation starts, so
the next speaker sees it.

| Path | Role |
| --- | --- |
| `app.py` | entrypoint, wiring, tts-server launch, VRAM reporting |
| `deadinternet/config.py` | shared state, persistence, `.env`, VRAM probe |
| `deadinternet/tts.py` | tts-server client, voice registration cache |
| `deadinternet/llm.py` | Ollama + OpenAI backends, images, router, persona authoring |
| `deadinternet/bot.py` | Discord runtime, playback, chat input, pins, history mining |
| `deadinternet/director.py` | turn loop, modes, pre-generation, topics, stims |
| `deadinternet/audio.py` | loudness normalisation, duration cap |
| `deadinternet/ui/` | Gradio interface: panels, per-tab handlers, styling |
| `deadinternet/events.py` | in-memory event log shown on Diagnostics |
| `deadinternet/tests/` | smoke test: the Gradio page constructs, with every handler wired |
| `buildapp.sh` | rebuild only `tts-server`, the one binary the app uses |
| `requirements-app.txt` | Python dependencies, gradio pinned exactly |
| `voices/` | reference clips (`voices/recovered/` = salvaged originals) |
| `deadinternet.json` | roster + settings |
