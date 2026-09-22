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
cd ~/Documents/qwentts.cpp && ./run.sh
```

Then open <http://127.0.0.1:7860>. The web UI is serving within a second or
two; tts-server comes up behind it and the roster is re-registered as soon as
it answers (roughly 30s for 23 voices). Watch the log on **Diagnostics** —
`[tts] server up` then `[boot] restored n/n voices`. Until that lands, voice
dropdowns are empty; **Refresh voices** on the Speakers roster row repairs
them at any time.

`run.sh` is the whole lifecycle:

| | |
| --- | --- |
| `./run.sh` | run in this terminal |
| `./run.sh -d` | detached — survives closing the terminal |
| `./run.sh status` | app, tts-server (and its backend), whisper-server, VRAM |
| `./run.sh logs` | follow `logs/app.log` |
| `./run.sh restart` | stop and start detached |
| `./run.sh stop` | stop the app, **leave tts-server running** |
| `./run.sh stop --all` | stop tts-server and whisper-server too |
| `--fresh` | with start/restart: stop tts-server and unload Ollama first |
| `-- <args>` | passed to `app.py`, e.g. `./run.sh -d -- --port 7870` |

Two defaults are deliberate. **`stop` leaves tts-server alone**, because a
server that allocated on an empty card is the fast one and every app restart
would otherwise gamble it — see *VRAM* above. `--fresh` is the opposite move,
for when you want it reallocated properly. And **detached mode does not
redirect into `logs/app.log`**: the app writes that file itself *and* prints
each line, so the `>> app.log` recipe this section used to give duplicated
every entry. Console output goes to `logs/app-console.log`, which only matters
when something dies before logging is up.

It finds running processes through `/proc` (argv and working directory), not a
pidfile, so it sees an app however it was started — and cannot match itself,
which `pkill -f app.py` does.

**The app writes `logs/app.log` itself**, timestamped, rolling over at 8 MB.
Everything written by the app and the servers it starts lives in `logs/` —
`app.log`, `app-console.log`, `tts-server.log`, `whisper-server.log` and their
`.1` rollovers. The folder is gitignored whole.

Startup skips the launch if port 8080 already answers, so an app restart never
stacks a second server on the card — and never disturbs a healthy one. Pass
`--no-tts-autostart` to suppress the launch entirely (it still registers
voices against a server that is already up).

To run tts-server yourself instead:

```bash
cd ~/Documents/qwentts.cpp && nohup ./build/tts-server --model models/qwen-talker-1.7b-base-Q8_0.gguf --codec models/qwen-tokenizer-12hz-F32.gguf --host 127.0.0.1 --port 8080 --lang English >> logs/tts-server.log 2>&1 & disown
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
- **Refresh voices** on the Speakers roster row re-uploads the roster to a
  server that came back empty.

Both wait up to 120s and log to `logs/tts-server.log`. Restarting the app does both
at once, which is why `pkill`-both-then-start-the-app now works as a recipe.

### Speaking through a tts-server on another machine

**Outputs → Speech engine → Where speech is generated** takes *this machine* or
*remote server*; the box next to it is the remote address (`http://host:port`,
address only, no path), and **Use this server** switches to it. `--tts` does the
same thing at startup and is equally remote-capable. The URL is the whole
setting — there is no separate "remote" flag — so anything that reads as local
is local, and `localhost.example.com` is not.

Worth knowing:

- **A remote server is read-only.** Its voices belong to whoever else uses it,
  so the app speaks through them and never uploads over them: `register` and
  `forget` both refuse, which means deleting a speaker from this roster cannot
  delete a voice out from under anyone. Only a local tts-server gets the roster
  uploaded to it.
- **Nothing is started for you on the far end.** If it is not answering, the
  switch still happens and says so, because the alternative is quietly
  continuing to speak through the old server.
- **A speaker whose voice is not there is named at boot** (`no voice called
  'Bjork' on ...`) rather than failing later, mid-line.
- **Backend (GPU/CPU), Restart and Stop only ever act on tts-server here**, and
  refuse while speech is pointed elsewhere rather than restarting a server that
  nothing is speaking through. Autostart is skipped for the same reason. While
  speech is remote those three controls and the whole **Testing** tab are off
  the page, with one line under *Speech engine* saying so; **Use this server**
  pointed back at this machine brings them back.
- **Going remote stops tts-server here.** Switching with *Use this server*
  stops a tts-server running on this machine and says how much VRAM that
  freed, and the app does the same at boot when the saved setting is remote,
  so a server left up by an earlier session or by `run.sh stop` (which
  deliberately leaves it running) is not holding 6.5 GB of the card under a
  configuration that never speaks through it. Switching back to this machine
  starts it again.
- The clips leave this machine in the clear over HTTP unless the remote is
  HTTPS, so this belongs on a network you trust.

---

## VRAM: the thing that will bite you

The card has ~16 GB and **Ollama and tts-server compete for it**. What matters
is the state of the card **when tts-server allocates**, not the state it is in
while it runs. Measured on one process, in this order, seconds of compute per
second of audio produced (lower is better):

| # | Card state | TTS speed |
| --- | --- | --- |
| A | started with an empty card (1.8 GB used) | **0.16** |
| B | same process, a 12 GB model loaded alongside (16.6 GB) | 0.53 |
| C | same process, that model unloaded again (4.4 GB) | 0.53 |

A **3.3x collapse**, and **it does not recover on its own** — B and C are the
same number. Once the driver evicts tts-server's Vulkan buffers to host memory
they stay there for the life of the process; freeing VRAM afterwards does
nothing. You must restart tts-server.

This is worth stating plainly because the obvious experiment gives the wrong
answer. Unloading Ollama and re-measuring shows *no change* — which looks like
proof that VRAM does not matter, and is actually step C above. The only
experiment that shows the effect is starting tts-server on an empty card.

- Start tts-server while the card is empty — starting the app early does this
  for you, before Ollama has a model resident.
- Keep one Ollama model loaded: `OLLAMA_MAX_LOADED_MODELS=1`.
- Prefer a small model. `gemma4:e4b` is ~3.3 GB. A 13B+ will not fit alongside.
- `ollama stop <model>` unloads one you're done with.
- The status line shows live VRAM and warns past 85%. **If speech goes
  sluggish, check there first, then restart tts-server.**
- **Restart tts-server** on the **Outputs** tab does that without a terminal.
  It stops the running server, waits for the driver to actually release the
  card, relaunches, and re-registers the roster; the confirmation says what the
  VRAM was *at the moment it allocated*, and warns if that was still full
  enough to have evicted it again. Note it finds the running server through
  `/proc` rather than through a handle, because the server is deliberately
  detached and usually outlives the app that started it.
- The same control switches **backend**. One binary does both: an empty
  `GGML_VK_VISIBLE_DEVICES` hides the GPU from ggml and it falls back to CPU,
  so nothing is rebuilt to switch. `GGML_VULKAN_DISABLE` and `GGML_VK_DISABLE`
  are both ignored -- they were tried. CPU is roughly 2.5x slower than a
  healthy GPU but costs no VRAM at all, which is the trade when something else
  needs the whole card.

### The other half: cooperative matrix

Check `logs/tts-server.log` for the line ggml prints at startup:

```
ggml_vulkan: 0 = AMD Radeon Graphics (RADV GFX1201) ... matrix cores: KHR_coopmat
```

If that says **`matrix cores: none`**, the GPU's matrix units are not being
used at all and every matmul is running on plain vector math. On this machine
RADV in Mesa 25.0.7 exposed no cooperative-matrix extension for GFX1201 (RDNA4)
at all — `vulkaninfo | grep -c cooperative_matrix` returned 0. Mesa 26.1.2 from
`trixie-backports` exposes it, and the same binary went from 0.80 to 0.53:

```bash
sudo apt install -t trixie-backports mesa-vulkan-drivers
```

No rebuild needed — it is a driver change, not a compile-time one. The
tell-tale that something is wrong here is the CPU comparison: a CPU-only build
of the same model on a 9950X runs at 1.36. A 16 GB GPU beating a CPU by only
1.7x is not a GPU being used properly; with coopmat *and* an empty card at
allocation time it is 0.16, which is 8.5x the CPU and 5x where this started.

**ROCm is not an option here.** Debian 13 ships ROCm 5.5.1 and gfx1201 needs
6.4+; there is no `hipcc` and `libhipblas0` is 5.5.1-4. It would mean AMD's own
repository and a ~30 GB install, to beat a Vulkan path that is now working.

---

## The interface

A persistent masthead over seven tabs:

```
QwenPod              [Refresh]  mode • voice • director • vram • gpu
action line    the last thing you did, and why it did or didn't work
─────────────────────────────────────────────────────────────────────
Run · Speakers · Inputs · Outputs · Behaviour · Testing · Diagnostics
```

Two rows above the tabs, not four. Name hard left, Refresh and the live status
as a pair on the right, then the action line, then the tabs. (Gradio's `.block`
sets `width: 100%` and `flex-basis: auto` resolves to it, so the masthead's
children each claimed the whole row and wrapped onto three lines — `flex:` alone
could not fix that, the width override is what makes it one row.) On a narrow
window the status drops to its own line rather than being truncated: a VRAM
warning you cannot read is worse than a taller header.

The status strip streams and lives above the tabs, so mode and VRAM stay on
screen wherever you are — a stalled director or a full card is easiest to miss
exactly when you are looking at some other tab. The action line below it does
**not** stream: it holds your last action's result until you do something else.
It closes the header block, so it carries the bottom rule, and a long message
scrolls inside two lines rather than pushing the tabs down the page.

Scrollbars follow the theme — thin, thumb on gradio's own border colour. They
are styled from `body` down and **never on `:root`**: gradio defines its theme
variables on the container, so at `<html>` they still resolve to the light
fallbacks and a scrollbar styled there comes out pale grey on a dark page.
Leaving `<html>` alone also lets the spec propagate `body`'s scrollbar styling
to the viewport, so the page scrollbar matches too.

The roster strip on **Speakers** is **three rows deep and scrolls sideways**
with a plain mouse wheel — a vertical wheel has nowhere else to go there, and
the arrows alone made a long roster tedious. At either end the event is
released so the page scrolls normally, rather than the strip becoming something
you have to steer around.

Three rows comes from `grid-auto-flow: column` with `grid-template-rows:
repeat(3, auto)`: the grid fills *down* each column before starting the next,
so the strip stays horizontal and becomes a third as wide. `flex-wrap` cannot
do this — it fills across and wraps down, which needs a height cap and turns
the scroll vertical.

**Styling either of them: gradio puts `elem_classes` on both the outer `.block`
and the inner `.prose` div.** A bare `.action-line { padding; border }` is
applied twice — double padding, and a second bottom rule drawn above the real
one. Decorate `.action-line.block` and flatten `.action-line.prose`.

| Tab | What it holds |
| --- | --- |
| `Run` | driving it on the left — mode, Start/Stop, what's next, what's on now, and the four ways to change it; watching it on the right — who's talking, the raw model output, the transcript, and "say a line as" (typed **or** spoken) |
| `Speakers` | the roster as a strip across the top (arrows step through it; Refresh voices and Export all sit on the same row), editor in two columns below; stims and persona-building fold away |
| `Inputs` | where topics come from — the topic itself and rotation rules on the left, the weighted sources on the right |
| `Outputs` | where the audio goes — Discord or this machine's own sound card as two tabs, one at a time; loudness and the speech engine (local or remote tts-server) underneath |
| `Behaviour` | LLM backend open; conversation tuning, comedy, between-segment work, connection rules and spoken templates as closed sections |
| `Testing` | speak arbitrary text in a chosen voice, with the raw sampling knobs. Hidden while speech comes from a remote server |
| `Diagnostics` | service status and the event log on the left, subsystem reports on the right |

The rules the page is drawn by — one help mechanism (a short line under the
label, a `?` for the rest), sections as accordions with only the tab's job
open, no unbounded lists, controls hidden rather than disabled when the
configuration makes them meaningless — are in `docs/UI-GUIDELINES.md`.

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
- **A model with no reasoning mode does not ignore `think` — it refuses the
  request.** Ollama answers `400 "<model> does not support thinking"` and the
  whole call is lost. That mattered when `evolve_persona` *forced*
  `think=True` (it is now the *Think before rewriting* switch, off by default):
  on such a model every persona rewrite failed, silently and
  forever, while the show itself carried on working normally. The client now
  learns this from the first 400, drops `think`, retries, and remembers for the
  session — the same way `OpenAIClient` learns its token parameter. An
  unreasoned rewrite is enormously better than none.

### What "speak and hear it back" actually costs

Measured, for a two-second utterance:

| Stage | Cost |
| --- | --- |
| waiting to be sure you stopped | 500 ms (the pause setting) |
| gradio round trip | ~100 ms |
| whisper | ~250-700 ms, depending on load |
| synthesis | ~450 ms |
| playback starts | — |

Two of those are already overlapped and one cannot be. **Transcription of the
next clip runs while the current one plays** -- `say_from_mic` clears the
hidden textbox as soon as it has queued the line, not after it has been
spoken, so the recorder releases the following clip immediately.
**Synthesis of the next line also runs during playback** -- one line of
lookahead in `_speak_manual`, the same trick `_pre_task` has always done for
podcast turns. Before that, a run of dictated sentences paid the full
synthesis latency between every single one: talk, wait, hear it, talk, wait.

What cannot be overlapped is the sentence itself: **TTS cannot synthesise a
sentence before it knows where the sentence ends.** Every voice changer of this
shape is chunk-based, and the floor is roughly one pause plus one synthesis.

**Whisper runs resident, on the GPU.** This one is worth reading carefully,
because the obvious measurement gives the wrong answer twice.

Spawning `whisper-cli` per clip, a Vulkan build and a CPU build are *identical*
-- 0.32s each. That looks like proof the GPU is useless here, and it is not:
whisper's own timings for a two-second utterance are `load 96ms | mel 2ms |
encode 38ms | decode 8ms`, so process startup and model load swamp the
arithmetic and hide whatever the arithmetic is doing. Remove that overhead with
a resident `whisper-server` and the compute gap appears:

| | per clip |
| --- | --- |
| spawn `whisper-cli` per clip | 640 ms |
| resident server, CPU, 16 threads | 619 ms |
| **resident server, Vulkan** | **92 ms** |

So residency is what makes the GPU matter, and the GPU is what makes residency
worth much. Either alone is nearly nothing; together they are 7x.

It costs about **0.45 GB of VRAM** held for the process's life, so the app
starts it *after* tts-server -- tts-server must get the card while it is empty,
and evicted TTS buffers never recover. Measured after: TTS unchanged.

Turn it off with `whisper_server: false` and transcription falls straight back
to spawning `whisper-cli`, which is what it always did. The same fallback fires
automatically if the server is unreachable -- but a server that *answers* with
an error is reported rather than silently retried on the CPU, because "slow for
no visible reason" is the failure this whole section exists to avoid.

### Seeing what it is waiting on

A **stage strip** sits above the transcript on **Run** and says what each part
of the pipeline is doing and for how long:

```
holding on TTS for 6.2s
Router 232ms   LLM 1.8s   TTS 6.2s   Playback 2.5s   Gap 800ms   queued 3
```

The status bar says whether the director is *running*; this says what it is
*waiting on*, which is the question you actually have when the show has gone
quiet. A stall looks identical from outside whether the model is thinking,
tts-server is queued behind another request, whisper is chewing on a clip, or
the gap between turns is simply set to three seconds — and telling those apart
used to mean reading `logs/app.log` afterwards.

- **A working stage counts up live.** The number is elapsed time on the call in
  flight, not a stale reading; the strip is pushed on the feed's fast tick along
  with the raw box, because a chip that only moves every 1.5s reads as frozen.
  With several calls in flight the number follows the **oldest** — with three
  synths running, what is holding the turn up is the slowest one, and `×3` on
  the chip says how many there are.
- **A stage that has never run shows `–`, not `0ms`.** Zero reads as "fast" when
  it means "never happened", and "TTS has never been called" is exactly the
  answer when nothing is coming out.
- **Past four seconds in one stage it is named on its own line.** Counting up in
  place is easy to miss; a healthy turn does not spend four seconds anywhere.
- **`Router`, `LLM`, `TTS`, `Playback` and `Gap` are always shown.** `Whisper`,
  `Ad`, `Evolve`, `Persona` and `Topic` appear once they have actually run, so a
  show with no microphone does not carry permanently empty chips. Order is
  fixed — a chip that moves when another stage wakes up is one you have to
  re-find every time.
- **The dashed chips are the counters the stages cannot explain**: messages
  `queued`, `/sayas` lines `dropped`, and `voices` when more than one is talking.
  A pipeline idle across the board with eight messages waiting is a different
  fault from one idle with nothing waiting.
- **A failed stage goes red and keeps the reason**, on the chip's tooltip along
  with the call count and average.

Instrumentation is at choke points, not call sites. Every LLM call is timed in
`BaseLLM.chat()` and routed to a stage by the label that already existed for the
raw output box, so `router`, `ad:`, `evolve:` and `persona:` land in their own
chips and a new kind of call is instrumented by virtue of being labelled. TTS is
timed in the client rather than at the director's five call sites; the same for
whisper and for playback. **Timings are always collected** — it is a dict update
per call — so *Show the stage strip* on **Behaviour** only decides whether they
are drawn.

Nothing is persisted. It is a window on the current process and dies with it.

### Watching the model work

**Raw model output** sits above the transcript on **Run** and fills as each
generation arrives. The transcript shows what was *said* — cleaned by
`BaseLLM._clean`, trimmed back to the last complete sentence, and only the lines
that survived. This shows the other thing: the reasoning, the false starts, the
JSON the router answers with, the persona rewrite that never reaches the
channel, and the empty completion behind a turn that silently did not happen.
That last one is the reason it exists — an empty generation used to look
identical to a quiet moment.

Each generation is a labelled block (`── Alice ──`, `router`, `ad: Bob`,
`evolve: Bob`), with reasoning marked `·` and still live ones marked `(live)`.
Blocks are kept **apart rather than concatenated**: persona evolution runs in the
background while the ad break plays, and two generations interleaved token by
token would be unreadable — you would not be able to tell there were two.

- **It is what turns streaming on.** `client.tap` is the whole mechanism: with
  the null tap attached, Ollama still sends `"stream": false` and the OpenAI
  client still reads one buffered body, byte for byte the request they made
  before this existed. **Show raw model output** on **Behaviour** attaches or
  drops the tap, and switching it off empties the box rather than leaving a
  frozen generation that reads as a hung feed.
- **The box updates faster than everything else.** The live feed runs at 1.5s;
  1.5s in this box reads as stuttering rather than live, so `stream_status`
  ticks at 0.3s and does the expensive work — four reports, a transcript render
  — only every 1.5s. The cheap ticks push `gr.update()` into every other field,
  which the client applies as "leave this alone", and a tick with nothing new
  yields nothing at all. **One generator, not two:** gradio's queue has four
  concurrency slots for the whole app and a second permanent stream per open
  tab would spend them.
- **Nothing is persisted.** It is a bounded in-memory ring — eight calls, 3000
  characters each, tail-kept — that dies with the process. Model output about
  real people never reaches a file.
- **A textarea, not Markdown**, for the same reason the event log is: this is
  unfiltered output, and a stray backtick or hash in it must render as itself.
  Gradio sets a textarea's `.value` property, which mutates no nodes, so the
  autoscroll `MutationObserver` used for the transcript never fires — the raw
  box is pinned by a 250ms interval instead.

### Local output — speaking out of the sound card

**Outputs** offers a second, completely independent output: play out of a local
audio device instead of into a voice channel. No token, no bot, no server. The
obvious use is a **virtual microphone**, which makes the cast an input device
every game and voice client on the box can select.

The **Virtual microphone** accordion under *This machine* on Outputs makes one. *Create* runs these
two commands (only the half that is missing, so it is safe to press twice):

```bash
pactl load-module module-null-sink sink_name=qwenpod sink_properties=device.description=QwenPod_Output
```
```bash
pactl load-module module-remap-source master=qwenpod.monitor source_name=qwenpod_mic source_properties=device.description=QwenPod_Microphone
```

Pick **qwenpod** as the output device, press *Start local output*, and select
**QwenPod_Microphone** as the input in the other application. Games that only
use the system default need `pactl set-default-source qwenpod_mic`. Proton
exposes PulseAudio sources through winepulse, so Steam titles see it like any
native one.

A null sink has no speakers, so without more you go deaf to everything you are
sending. *Monitor on* loops it back to your default output:

```bash
pactl load-module module-loopback source=qwenpod.monitor latency_msec=60
```

Leaving out `sink=` is deliberate — it follows whatever your default output
is. The loopback is its own stream, so the *Monitor volume* slider (or
`pavucontrol`) turns down what you hear without touching what anyone else
does. None of these modules survive a reboot; press *Create* again.
*Remove* and *Monitor off* unload **only these modules, by id** — unloading
`module-null-sink` by name would take every null sink on the machine with it
(`deadinternet/audiodev.py`).

**One output at a time.** The director holds a single runtime, so each output
refuses to start while the other is live: *Start local output* is refused while
the bot is logged in (press *Disconnect*), and *Connect bot* is refused while
the local output is playing (press *Stop*). Switching is something you do on
purpose, never a side effect of another button.

Two things are markedly easier here than over Discord, and one is lost:

- **Overlapping voices are free.** A voice client plays exactly one source,
  which is the whole reason `bot.py` sums extra speakers into the outgoing
  frames by hand. PulseAudio mixes concurrent streams itself, so an overlaid
  `/sayas` line is just a second `paplay`.
- **Interrupting is killing a process.**
- **Everything that genuinely *is* Discord goes away**: pinned messages as a
  topic source, images, and people typing at the cast. Those return empty
  rather than raising, so the same director code runs against either output —
  but on local you are on web search, typed topics, and the Run tab.

`humans_present()` is always true locally. *Pause while empty* exists because
talking to an empty voice channel is pointless; a sound card has no such
notion, and a local output that paused itself would never speak at all.

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
static choices and the roster is already larger than that — a static enumeration
cannot represent it at all, and would go stale the moment a speaker is added.
Autocomplete is re-evaluated per keystroke and filters as you type. It offers
**everyone with a clip, not just the enabled cast**: `enabled` means eligible to
take turns on its own, while `/sayas` is an explicit override that works in any
mode, and the code that speaks the line never consults the flag. Enabled
speakers are listed first, because 25 of a longer roster is all Discord will
show against an empty query — the rest are one keystroke away.

Autocomplete suggests but does not constrain: Discord submits whatever was
typed. An unrecognised name is refused with a reason rather than accepted and
dropped, which is what used to happen — the reply said the line was queued and
then nothing was said.

**Spamming `/sayas` makes them talk over each other.** Lines from the command
are mixed on top of whatever is already playing instead of queueing behind it,
up to *Voices at once* (default 3, counting whoever was already speaking). Past
that the extra lines are dropped with a log line rather than cutting anyone off,
and the cap is checked *before* synthesis so spam costs no tts-server work.

This is scoped to the slash command deliberately. The Run tab's **Say** box and
the microphone stay strictly in order: the recorder cuts one clip per sentence,
and sentences spoken on top of each other are not a sentence. It also bypasses
the manual queue entirely — the turn loop pops that one item at a time and
awaits playback, so anything going through it is serialised by construction.

A voice client plays exactly one source, so this is the same mechanism as the
message cue: extra speech is summed into the outgoing 20ms frames rather than
played. Overlaid voices come in at 0.78 so the original line stays followable
and each addition reads as someone cutting in rather than as the mix getting
louder — three real voices measured a peak of 20388 of 32767 with nothing
clipped.

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
gap chosen next to the device list (0.5s by default). Each piece is transcribed
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
with the base prompt included as an anchor — so drift
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
- **Personas want to bloat.** Every rewrite has something new to account for and
  no reason to drop anything, so left alone each character climbs to the ceiling
  and sits there — a one-line base carrying a page of fixations that each lasted
  one segment. *Evolved prompt size* on **Behaviour** (`evolve_max_chars`, 600 by
  default) is the budget that stops it:
  - the rewrite is told to rebuild the sheet inside the budget — core identity
    first, at most four lasting tendencies, one-segment material out — rather
    than append to the current one;
  - a sheet that is already over budget is called out, so it shrinks on that
    character's next rewrite. That includes anything that grew before the
    setting existed; nothing needs resetting by hand;
  - a result that still comes back long gets a second, non-thinking *condense*
    call (`evolve: Name (condense)` in the raw feed) instead of being cut off.
    Cutting drops the tail, which is the newest material, and keeps the oldest
    clutter. A sentence-boundary cut remains as the last resort if that call
    fails.

- **Thinking is off for rewrites, and separately switchable** (*Think before
  rewriting*, `evolve_think`). It used to be forced on. Measured on a 12B
  reasoning model it was 60–70 seconds a character against 3–15 without, it spent
  its entire token allowance and returned nothing about two times in three, and
  the rewrites it did produce were no better. At six characters a break that is
  seven minutes against one, behind a 60-second hold. If you turn it on, a
  thinking pass that comes back empty is retried once without
  (`evolve: Name (no think)`) rather than lost.
- **Not everyone who spoke is rewritten.** One line is a remark, not a tendency,
  and rewriting from it only promotes whatever was said into a trait — so it
  takes two lines to be a candidate. A sheet that is over its size budget is the
  exception: it is rewritten whatever was said, and goes to the front of the
  queue, so a backlog of bloated sheets clears in a few breaks.

  The budget never goes below the length of the base prompt you wrote, and never
  above 1200 characters. The log line for each rewrite shows the size change
  (`[evolve] Alex (1133 -> 540 chars)`).

### The ad break

*Play an ad break* on **Behaviour** fills the gap when the topic rotates: a
random speaker reads an invented sponsor spot about something the segment
actually covered, over a music bed from `music/` (gitignored — upload tracks on
the same tab, one is picked at random per break).

**The bed is levelled against the read, not against the file.** *Music level
under the read* is a ratio: 0.22 means the bed sits at 22% of the read's RMS,
about 13 dB under it. It used to multiply the decoded track directly, which made
the slider mean nothing across a folder — uploaded tracks are mastered anywhere
from -20 to -6 dBFS, so at one setting one bed was inaudible and the next buried
the voice. Measured on two real tracks, the same 0.22 needed gains of 0.103 and
0.150 to land in the same place. The level is measured over the **window
actually used**, not the whole file, or a track that opens on a quiet intro and
lands on a chorus would be scaled by a level that appears nowhere in what plays.
A silent or unreadable track falls back to the literal multiplier rather than
dividing by nothing.

The ad and the rewriting are one feature, not two. Rewriting takes tens of
seconds and the gap is the only place it can happen — but a gap that long is dead
air. So the rewrite starts first and the ad plays **over** it. The break is the
loading screen.

**Someone hands over to it out loud.** *Hand over to the break out loud* has
the reader say a line — "Alright, we'll pick this up in a moment. First, a word
from our sponsor." — and that line is spoken **while the ad is still being
written**, not after. Writing the spot takes seconds and synthesising it takes
more, and before this the break opened with all of it as silence, which in a
live call sounds like the show has stopped rather than like a break. The lines
are editable on the same tab, one per line, picked at random; `{name}` is
whoever is reading and `{topic}` is the segment that just ended.

The hand-off is its own turn in the transcript, `kind="ad"` for the same reasons
the read is. If it cannot be synthesised the break still runs — and if the *ad*
fails after the hand-off has already played, the next topic simply follows it.
That is the one awkward case, and it is the right trade: the alternative is
waiting to find out, which is the silence this removes.

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

### When the server calls a voice something else

A remote tts-server has its own namespace. Ours has **Warden (Deadlock)**;
`voice.example.internal` has `deadlock_warden`, `tf2_heavy_weapons_guy`,
`persona_joe_rogan`, `irl_chris_dyer` — source prefix, then the name. The
roster does not follow that convention, because its names are what the UI, the
feed, the transcript and every persona use.

So the registration gives way instead: **Voice on the server** in the speaker
editor holds the name that server knows this voice by, and `Speaker.voice_name()`
— `server_voice` or, when empty, `name` — is what every synth and register call
asks for. Empty is the local case, where the two are the same.

The box is a dropdown of what the current server actually has, and accepts a
name it does not have yet. A speaker with a server voice needs no reference
clip here: the voice is already over there, and the clip is only ever used to
upload one.

### Downloading the voices

**Export all (.zip)** on the Speakers roster row writes one zip — a
`qwen-voices-export/` folder with a pair of files per speaker that has a
reference clip:

| | |
| --- | --- |
| `<name>.wav` | the clip as tts-server hears it: mono, 16-bit, 24 kHz |
| `<name>.txt` | that speaker's `ref_text`, byte for byte; empty file for a voice-only clone |
| `manifest.json` | `<name>` to the real speaker name and the clip length |

`<name>` is the speaker name lowercased down to `a-z 0-9 _ -`, with `_2` on a
collision. The audio is not a fresh conversion of the file in `voices/`: it is
built from the same bytes `TTSClient.register` posts and then put through what
tts-server does on receipt, so a clip already at 24 kHz comes back
sample-for-sample identical to what the cloner was given. Neither side trims,
so neither does the export.

Clips and transcripts only — no personas, transcripts of conversations,
settings or logs. The zip is built in a temp folder and never written into the
repo, but note that **anyone who can open the UI can download every voice**,
and gradio keeps its own copy of each served file under `/tmp/gradio` until it
is cleaned up.

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

> *"Okay, new topic by alice: check this out. We're looking at an image. A
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

### Web topics are read, not just found

A search result is not a topic. Its title and snippet were written for a results
page, and for anything anyone sells the first several are adverts: searching
`vr` used to hand the cast *"5 Best VR Headsets 2025 - Read our Expert
Reviews!"*, which nobody can have an opinion about. With **Read the article** on
(**Topic → Web search**, `web_read_articles`, the default), a web topic is built
in three steps in `websearch.py` and `llm.brief_article`:

1. **Find stories.** Bing's news feed is asked first — one URL per article,
   dated, no adverts. It is inconsistent about answering in RSS (broad queries
   like `politics` get its ordinary results page instead), so both shapes are
   parsed, and the page is fetched as well when the feed comes back thin.
   DuckDuckGo is the fallback, with sponsored results dropped and its redirect
   links unwrapped to the real address.
2. **Read one.** The page is fetched (public `http(s)` only, 1.5 MB cap) and the
   prose pulled out with the standard library: paragraph text, preferring
   `<article>`/`<main>`, skipping navigation, forms and footers. A page with
   under 600 characters of actual sentences is not an article — that is what
   rejects shops, video players and cookie walls, by content rather than by a
   list of domains. Blocks that are mostly digits and prices are not prose
   either, which is what a product grid otherwise passes as.
3. **Brief it.** The model turns up to 6000 characters of article into two or
   three spoken sentences carrying the specifics — names, numbers, the odd
   detail — because those are what a panel disagrees about. **That brief is the
   topic**: the announcer reads it out and every speaker sees it for the
   segment. The article text goes in fenced and the model is told it is material,
   not instruction; it can also answer `SKIP` for a page that turned out not to
   be a story.

Up to three results are tried per subject before moving to the next subject, and
stories already used this session are not used again. If nothing anywhere can be
opened, the old behaviour — the headline — is the last resort rather than the
source going silent. All of it happens in the background topic build, so it costs
no air time: about 3–6 seconds per topic on a local 12B model. Every step logs
under `[web]`, including what was read and the brief that came out.

Two things that will not be worked around: a site that refuses the request
(403, paywall) is skipped, and if DuckDuckGo answers with a CAPTCHA it is left
alone for thirty minutes. MSN links are tried last, because those pages are an
empty shell filled in by script and there is never anything to read.

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
out loud — *"Okay, new topic by alice: the best pizza topping"* — using the
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

discord.py's voice logging is forwarded into `logs/app.log` (`discord.voice_state`
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
`deadinternet.json`, and `logs/` are gitignored.

Edits to `.env` need an app restart.

---

## Making it funny

Left alone, a small local model does not do comedy. It holds a panel
discussion: every character is the same agreeable assistant under a different
name, each line is two balanced sentences about the topic, and nothing anyone
says changes what anyone else says next. Telling it to be funny does not help.
It is tuned to be helpful, and a prompt that offers a choice of "a take, a
detail, a disagreement, a joke" gets the mildest option every time.

What a 12B model *can* do is carry out one specific instruction. So the
decisions are made by code, and the model only executes them. All of it is on
the **Behaviour** tab under **Comedy**, all of it can be switched off
independently, and the code is `deadinternet/comedy.py` (everything that is not
a model call) plus four methods in `deadinternet/llm.py`.

| Piece | What it does | What it costs |
| --- | --- | --- |
| **Move deck** | Deals one concrete comedic move per turn - *take it literally*, *agree for a worrying reason*, *a made-up anecdote with a name and an exact number*, *six words or fewer* - from a shuffled bag, on the seeded RNG. | Nothing. One sentence in the prompt. |
| **Rhythm** | Comes with the deck. About half of ordinary turns are held to one sentence, and a `short:` move to a few words. A short move the model ignored is cut to its first sentence, so it always lands. | Nothing - lines get *cheaper*. |
| **Wear** | Comes with the deck. Phrases a speaker has used in two or more recent lines are named in the prompt as off-limits for one line. Evolved personas are mostly fixations, and without this every line visits all of them. Stims and cast names are spared. | Nothing. |
| **Premises** | Once per topic, one call gives each character (the first 8) something petty to *want* out of it, chosen to collide with the others. Prefetched with the topic; for a typed topic it is written behind the first line. | One call per topic, off-air. |
| **Line filter** | A take that opens by agreeing, explains itself, echoes a recent line, leans on a worn phrase or asks yet another question is written again, up to **Takes per line** times. The least-flagged one plays. | Nothing for a clean first take; one more LLM call per flagged one. |
| **Judge** | Off by default. Always writes every take and asks the model which is most *surprising and specific* - never "funniest", which picks the take with the most visible joke in it. | Takes + 1 calls for every line. Watch the stage strip. |
| **Callback memory** | Every 8 lines, one small call notes the oddest specifics said ("exactly forty-two in Vegas"). The callback move draws on them, so something from two topics ago can come back. Kept across topic switches, cleared with the context. | One small call per 8 lines, queued behind the next line. |
| **Script framing** | Off by default. Shows the model the conversation as a page of dialogue to continue instead of chat turns to answer - chat turns are what an assistant is trained to be helpful inside. | Nothing. Try it per model. |
| **min-p / repeat penalty** | Ollama only. min-p is what lets temperature go past 1.0 without lines falling apart. Try min-p 0.05-0.1 with temperature 1.1-1.3. | Nothing. |

Moves are skipped when answering a real person. They typed something to get an
answer to it, and half the deck is ways of not giving one.

### The deck is yours

**The deck** box takes one move per line; empty uses the default. Write things
to *do*, never things to *be* - "be sarcastic" gets one sarcastic adjective,
"treat the most trivial detail as the real scandal" gets a joke. Prefix a line
with `short:` to hold the reply to a few words, `duo:` to skip it when only one
speaker is on, and put `{bit}` in a line to make it a callback.

### Characters are most of it

No amount of machinery rescues a persona that is a list of adjectives. "You
are witty" gives the model nothing to do. A character is funny because of what
they **want**, what is **wrong with them**, and what they are **wrong about**,
written as behaviour: not *lazy* but *has an excuse ready that involves his
knee*.

On the **Speakers** tab:

- **Sharpen base for comedy** has the model restate the base prompt in those
  terms, with an attitude toward a couple of the other cast members. It only
  fills the box - read it, fix it, then save.
- **Lines in their voice** takes things the character would actually say, one
  per line. Four are shown to the model each turn, rotated so it does not start
  quoting them. At this model size a handful of real lines does more than any
  description. They live outside the persona on purpose: evolution rewrites
  the persona, and a rewrite summarises examples into adjectives. Personas
  built from Discord history get this for free - the quoted messages are read
  as samples once the character has evolved past the prompt that contained
  them.

Evolution is also told never to make a character more reasonable, balanced or
self-aware than the base you wrote, which is the one direction they all drift.

### Is it actually funnier?

`deadinternet/evalrun.py` writes a conversation through the director's real
line-writing path - same prompts, deck, filter and seeded RNG - with no TTS, no
Discord and no playback. It reads the real roster and settings and writes
neither. Change one thing, keep the seed and topic, compare:

```bash
.venv-app/bin/python -m deadinternet.evalrun --seed 7 --turns 30 \
    --topic "airport security" --out /tmp/a.json
.venv-app/bin/python -m deadinternet.evalrun --seed 7 --turns 30 \
    --topic "airport security" --set moves_enabled=false --out /tmp/b.json
.venv-app/bin/python -m deadinternet.evalrun --compare /tmp/a.json /tmp/b.json --judge
```

`--compare` prints the numbers side by side; `--judge` asks the model which
recording it would air, twice with the order swapped, because a model judging
two things prefers whichever it read first. Add `--set provider=openai` to the
compare to judge with a bigger model than the one that wrote them. It is asked
for a preference and never a score - a model rating funniness out of ten says
seven.

The numbers (also live on **Diagnostics**, and logged as `[comedy] segment:`
at every topic switch) are proxies. None of them measures funny, but each is
something a dull transcript reliably gets wrong:

| Number | Dull looks like |
| --- | --- |
| words per line, and their **sd** | every line the same two sentences - sd near zero |
| % short | 0 |
| % questions | high: the model handing the work back |
| % agreeable openers, % filler | anything above a few percent |
| specifics per line | near 0 - no names, no figures |
| distinct-3 | falling, as the cast repeats itself and each other |

Measured on the default cast with `huihui_ai/gemma-4-abliterated:12b`, 14
lines, same seed and topic, comedy layer off against on: 44.5 -> 21.4 words per
line, specifics 0.36 -> 0.93 per line, distinct-3 0.92 -> 0.99, and the judge
preferred the second in both orders. Same model judging its own work, so
treat that as a smoke test and trust your ears. Writing stayed at about 2.3s a
line: the premises and bits calls are paid for by the lines being half as long.

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
| `deadinternet/ui/rosterscroll.py` | wheel-to-horizontal for the Speakers roster strip |
| `music/` | ad-break background beds (gitignored) |
| `setup-whisper.sh` | clone + build whisper.cpp, fetch a model |
| `deadinternet/tests/` | smoke test: the Gradio page constructs, with every handler wired |
| `buildapp.sh` | rebuild only `tts-server`, the one binary the app uses |
| `requirements-app.txt` | Python dependencies, gradio pinned exactly |
| `voices/` | reference clips (`voices/recovered/` = salvaged originals) |
| `deadinternet.json` | roster + settings |
