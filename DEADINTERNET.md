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

Optional, for the microphone on the Run tab:

```bash
./setup-whisper.sh            # small.en (466 MB); pass base.en for a faster, rougher one
```

Clones and builds whisper.cpp into a gitignored `whisper.cpp/`, CPU only. Not a
submodule on purpose: `.gitmodules` is upstream's file, so an entry there would
put a merge conflict in the path of every upstream pull. CPU on purpose too --
the card is the scarce resource here, not the CPU, and a 9950X does 11 seconds
of speech in about one second with the GPU untouched.

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
| `Run` | transport, mode, current topic and who's talking on the left; the queue, topic injection, transcript and "say a line as" (typed **or** spoken) on the right |
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

### Letting the model think

**Let the model think first** on the **Behaviour** tab sets Ollama's `think` on
every request. Off by default, and worth understanding before turning it on:

- **It costs seconds per line**, and in a live voice call that is dead air. The
  masthead shows `thinking` while it is on, so a conversation that has gone
  sluggish can be explained without hunting for the setting.
- **Every line gets 8x the token budget.** Reasoning has to fit inside
  `num_predict` alongside the answer, so at the normal per-line budget a
  reasoning model spends all of it thinking and returns nothing. The raise
  happens up front rather than after an empty first attempt.
- **The interrupt router never thinks**, whatever this is set to. It is a
  one-word classification on the path of answering a real person, and every
  second of it is silence in the channel.
- **Ollama only.** An OpenAI-compatible endpoint has no equivalent field —
  reasoning there is a property of the model you pick, not of the request. The
  checkbox is accepted and ignored on that provider.
- A model with no reasoning mode ignores `think` entirely, so turning this on
  against `gemma4` changes nothing but the budget.

### Discord commands

Two real slash commands, registered when the bot connects and synced **per
guild** — a global sync can take an hour to appear in clients, a guild sync is
immediate. Both reply privately, so the channel does not fill with
acknowledgements.

- **`/topics text:<...>`** — queue something for the hosts to talk about. This
  replaced the old text-prefix `/topic ` handler; there is one path now and it
  shows up in Discord's own picker.
- **`/sayas speaker:<...> text:<...>`** — put a line straight in a host's mouth,
  the same entry point the web UI's **Say** button uses. Refuses with a reason
  when the bot is not in a voice channel, rather than generating a line nobody
  would hear.

`speaker` uses **autocomplete, not a choice list**. Discord caps an option at 25
static choices and the roster is already 27 — a static enumeration cannot
represent it at all, and would go stale the moment a speaker is added.
Autocomplete is re-evaluated per keystroke and filters as you type.

### The message queue

Messages are **queued and answered in order**, up to *Messages held at once*
(default 8) on the **Behaviour** tab. This used to be a single slot: a second
message arriving before the turn loop came back round overwrote the first, and
nothing said so — which is most of a sentence, every sentence, so a room typing
at any speed lost most of what it said.

Every accepted message sounds a short two-note **cue**, mixed *over* whoever is
talking rather than interrupting them. That is the receipt: it tells someone
typing mid-line that they landed without the bot having to stop to say so.
**Silence means the message was refused**, not that it was missed — the only
thing that refuses is a full queue, and *Diagnostics → Chat input* counts them.

Interruption stays useful without becoming a stutter: *Interrupt the current
line when someone types* fires only for a message that arrives with **nothing
already waiting**. A rapid handful queues behind that one, because cutting off
every sentence in a row means nothing ever gets finished — and the cue has
already told them the later messages landed. The cue and the queue depth are
both switchable on **Behaviour**.

A voice client plays exactly one source, so mixing is what makes the cue
possible at all: `_Mixer` in `deadinternet/bot.py` sums the cue into the speech
frames on their way out, and pads past the end of a clip so a cue that outlasts
it is not cut off mid-note.

### Speaking instead of typing

Under **Say a line as** there is a microphone: pick an input, press Record, and
talk. Each transcript is spoken by the chosen speaker **immediately** — there is
no review step, so what Whisper heard is what goes out. Every transcript is
written to the event log on **Diagnostics**, so you can always see what it
actually heard, including on a failure. The **File** button sends an audio file
down the same path.

**You do not press Stop between sentences.** Recording is continuous, and a
voice-activity detector cuts the take wherever you pause for longer than the
gap chosen next to the device list (0.8s by default). Each piece is transcribed
and spoken while you carry on with the next one. Stop ends the session and
sends whatever is left, if anything was said in it. A take with nothing in it is
never sent, so a pause in the wrong place costs nothing.

Clips are handled strictly one at a time, in the order they were said: the page
waits for the server to finish with one before releasing the next, and the
handler is pinned to `concurrency_limit=1`. Without both, four sentences would
transcribe in parallel and be spoken in whatever order Whisper happened to
finish them.

Three things about it are deliberate, and the first two come from the same
afternoon of debugging:

- **It is not a `gr.Audio`.** Gradio's recorder hands the finished clip to its
  own player, which decodes it — measured at roughly 3x realtime here, so a
  six-second line froze the page for twenty seconds. There is no option to skip
  that. This recorder never puts the audio in a component: it POSTs the blob to
  gradio's upload endpoint and passes the server path on, so nothing decodes it
  in the browser. See `deadinternet/ui/mic.py`.
- **The device is chosen explicitly, and there is a level meter.**
  `getUserMedia({audio: true})` resolves to *Chrome's* default input, not the
  system's. On a machine with several inputs that is easily an unplugged
  socket, which records perfectly formed silence — and then Whisper gets blamed
  for a bad transcription. The meter shows whether anything is arriving before
  you speak, and a silent take is reported as silent. It turns green when the
  detector counts what it is hearing as speech, so what you see is exactly what
  decides where the cuts go.
- **Levels come off the audio thread, not `requestAnimationFrame`.** rAF does
  not fire at all in a hidden tab, and the detector is the only thing that ends
  a take — on rAF, switching tabs mid-sentence froze it, so nothing was cut or
  sent and the segment grew past its own 25s cap, which was checked in the same
  dead loop. An `AudioWorklet` runs wherever the audio does. There is a
  `ScriptProcessorNode` fallback; if both fail the mic says so and degrades to
  one take per press rather than pretending to listen.

It needs `./setup-whisper.sh`; until then the mic reports that it is not set up
and **Diagnostics** shows `whisper: not set up`. The rest of the app does not
care whether it is there.

Two settings in `deadinternet.json` if you want a different model:
`whisper_binary`, `whisper_model`, and `whisper_lang` (a whisper.cpp language
code like `en`, not the TTS language name).

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

**The transcript fills itself in.** Add or record a clip and whisper.cpp reads
it back into the box — the same engine the Run-tab microphone uses, so it needs
`./setup-whisper.sh` and says so if it is missing. **Check what it wrote before
saving:** a wrong word in the reference transcript makes the clone worse and
nothing else reports it. **Transcribe clip** re-runs it by hand, which is how
you transcribe a clip loaded off the roster — the automatic pass is wired to the
clip's *upload* and *stop recording* events rather than its change event, so
selecting a speaker never overwrites a transcript that was already right.

### Characters that evolve

Each speaker has **two** system prompts:

- **Base** — yours. The app never rewrites it. It is the anchor every evolution
  starts from and what **Reset dynamic to base** returns to.
- **Dynamic** — read-only, rewritten between segments from what that character
  actually said. **While it has anything in it, it is what the model is told to
  be.** Empty means the base is in use.

Turn it on with *Evolve characters between segments* on **Behaviour**. After each
topic, the speakers who actually spoke get their lines fed back through the model
in **thinking mode**, with the base prompt included as an anchor — so drift
accumulates but a character twenty topics in is still recognisably the one you
wrote. Only speakers who spoke are candidates; the rest are ordered
longest-unevolved-first so a quiet character still comes round.

Saving a speaker **cannot** wipe an evolution. The dynamic box is display-only
and is deliberately not an input to Save — a rewrite can land between the page
rendering and you pressing the button, and reading it back from the form would
silently undo it.

Two things to watch:

- **It competes with tts-server for the card.** Reasoning while speech is
  synthesising is exactly the VRAM contention described above. Off by default.
- **Personas tend to bloat.** Every rewrite has something new to account for and
  no reason to drop anything. There is a hard 1200-character ceiling in the
  prompt *and* in code, but over a long session expect to reach for Reset. This
  is managed, not solved.

### The ad break

*Play an ad break* on **Behaviour** fills the gap when the topic rotates: a
random speaker reads an invented sponsor spot about something the segment
actually covered, over a music bed from `music/` (gitignored — upload tracks on
the same tab, one is picked at random per break).

The ad and the rewriting are one feature, not two. Rewriting takes tens of
seconds and the gap is the only place it can happen — but a gap that long is dead
air. So the rewrite starts first and the ad plays **over** it. The break is the
loading screen.

If the rewrites overrun *Seconds to hold the next topic*, the show carries on
anyway and they land whenever they finish — `system_prompt()` is read fresh every
turn, so a late result still applies, just a segment later. Dead air is worse.

The spot is read **in character**: the reader's own system prompt goes in ahead
of the brief, so an evolved host reads it as who they have become rather than as
an anonymous voice wearing their clone. The brief goes last, because it carries
the hard format rules and the last instruction is the one a model follows when a
chatty persona disagrees with it.

**The brief is editable.** *Ad brief* on **Behaviour** is the system prompt the
read is written from — leave it empty and the default shows greyed out in the
box as a placeholder, which is also what it falls back to. The segment's topic
and transcript are always appended underneath whatever you write, so a rewritten
brief cannot leave the model with nothing to go on. It does not have to be an
advert: ask for a jingle, a public information film, a threat.

The ad read is a spoken line like any other: it goes into the Run tab's
transcript and the event log's `speech` lines, with a separate `run` entry
recording that it was an ad and what it played over. It is added *between* the
two segments, so it belongs to neither and never shows up in what a character
learns from. It carries `kind="ad"` rather than `"bot"`: it is a real spoken
line and belongs in the transcript, but it is not the show, so it never counts
as the hosts having had a conversation and a character never learns from reading
an advert. The transcript labels it `(ad)`.

**Nothing runs while the channel is empty.** Topic rotation sits earlier in the
turn loop than the pause-when-empty guard, so it used to carry on into an empty
room — announcing topics, and once the ad break lived there, reading a full
sponsor spot every few minutes to nobody. The ad's own line in the transcript
then satisfied the "did a segment happen" check for the next one, so it
sustained itself indefinitely. Left overnight, that is all it did. Rotation is
now refused while paused; **Switch topic now** still overrides, since whoever
pressed it can see the channel is empty.

**Pressing "Switch topic now" runs the break too** — it is the same code path as
the timer. Two exceptions, both so a manual switch is not punished: nothing runs
if the segment has no host lines in it yet, and nothing runs while the director
is stopped. In that stopped case the switch happens inline on the web request,
which is capped at 60s — and the evolution wait alone defaults to 60.

Everything about the break degrades quietly: no music uploaded plays it dry, and
a failed ad — no LLM, no tts-server, an empty generation — skips the break
entirely. None of it can stop the topic rotating.

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

### No speech input *from Discord*

You can speak into the app: the microphone under **Say a line as** transcribes
locally with whisper.cpp and says the result as a chosen speaker. What follows
is about the other direction — the bot hearing people **in the voice channel**,
which remains impossible for a reason unrelated to transcription.

The bot is **output only** on the Discord side. Speech recognition was removed
along with the whole receive path; `faster-whisper` and `discord-ext-voice-recv` are
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
| `deadinternet/transcribe.py` | whisper.cpp wrapper for the Run-tab microphone |
| `deadinternet/adbreak.py` | the inter-segment break: sponsor read + character rewriting |
| `music/` | ad-break background beds (gitignored) |
| `setup-whisper.sh` | clone + build whisper.cpp, fetch a model |
| `deadinternet/tests/` | smoke test: the Gradio page constructs, with every handler wired |
| `buildapp.sh` | rebuild only `tts-server`, the one binary the app uses |
| `requirements-app.txt` | Python dependencies, gradio pinned exactly |
| `voices/` | reference clips (`voices/recovered/` = salvaged originals) |
| `deadinternet.json` | roster + settings |
