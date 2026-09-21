"""The virtual microphone: make it, monitor it, take it down.

A null sink with a remapped source is what turns the local output into an
input device every game and voice client on the box can select. These are the
same pactl commands DEADINTERNET.md spells out, behind buttons, so nobody has
to paste them into a terminal after every reboot -- the modules are not
persistent, and PipeWire restarts more often than people expect.

Adapted from VoiceChanger's devices.py, with one deliberate difference: removal
unloads only *our* modules, by id. Unloading module-null-sink by name takes
every null sink on the machine with it, including ones that belong to
something else entirely.
"""
import os
import shutil
import subprocess
import time

SINK = "qwenpod"
SOURCE = "qwenpod_mic"
SINK_DESC = "QwenPod_Output"
SOURCE_DESC = "QwenPod_Microphone"

MONITOR_MAX_VOLUME = 150


def pactl():
    return shutil.which("pactl")


def _run(tool, *args, check=False, timeout=5):
    # LC_ALL=C: several of these listings are parsed as text, and a localised
    # "Owner Module:" would silently match nothing.
    return subprocess.run([tool, *args], capture_output=True, text=True,
                          timeout=timeout, check=check,
                          env={**os.environ, "LC_ALL": "C"})


def _modules(tool):
    """[(id, module name, arguments)] from `pactl list short modules`."""
    try:
        out = _run(tool, "list", "short", "modules").stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            rows.append((parts[0], parts[1], parts[2]))
    return rows


def _names(tool, kind):
    try:
        out = _run(tool, "list", "short", kind).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {p[1] for p in (l.split("\t") for l in out.splitlines()) if len(p) >= 2}


def _is_monitor(name, args):
    return name == "module-loopback" and f"source={SINK}.monitor" in args


def _ours(name, args):
    """Is this module one this file loaded? Matched on the arguments, which
    carry our names; the module id changes every time."""
    if name == "module-null-sink":
        return f"sink_name={SINK}" in args.split()
    if name == "module-remap-source":
        return f"source_name={SOURCE}" in args.split()
    return _is_monitor(name, args)


def state():
    """-> (sink exists, source exists, monitoring). All False without pactl."""
    tool = pactl()
    if not tool:
        return False, False, False
    monitoring = any(_is_monitor(n, a) for _, n, a in _modules(tool))
    return (SINK in _names(tool, "sinks"), SOURCE in _names(tool, "sources"),
            monitoring)


def report():
    """One line of markdown for the Outputs tab."""
    if not pactl():
        return "_pactl not found - install pulseaudio-utils (or pipewire-pulse)._"
    sink, source, monitoring = state()
    if sink and source:
        mic = f"**{SOURCE_DESC}** is up"
    elif sink or source:
        mic = "**half made** - press Create to finish it"
    else:
        mic = "not created"
    mon = "monitoring on" if monitoring else "monitoring off"
    return f"Virtual microphone: {mic} · {mon}"


def create_mic():
    """Make the null sink and the remapped source. -> (ok, message).

    Idempotent: whichever half already exists is left alone, so pressing it
    twice, or after a crash that made only the sink, does the right thing.
    """
    tool = pactl()
    if not tool:
        return False, "pactl not found - install pulseaudio-utils."
    have_sink, have_source, _ = state()
    made = []
    try:
        if not have_sink:
            _run(tool, "load-module", "module-null-sink", f"sink_name={SINK}",
                 f"sink_properties=device.description={SINK_DESC}",
                 check=True, timeout=10)
            made.append("sink")
        if not have_source:
            _run(tool, "load-module", "module-remap-source",
                 f"master={SINK}.monitor", f"source_name={SOURCE}",
                 f"source_properties=device.description={SOURCE_DESC}",
                 check=True, timeout=10)
            made.append("source")
    except subprocess.CalledProcessError as e:
        return False, f"pactl failed: {(e.stderr or '').strip()[:160]}"
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"pactl failed: {e}"
    if not made:
        return True, "The virtual microphone already exists."
    return True, (f"Created the virtual microphone. Pick **{SINK}** as the "
                  f"output device and **{SOURCE_DESC}** as the input in the "
                  "other app. It will not survive a reboot.")


def remove_mic():
    """Unload our sink, source and monitor -- and nothing else. -> (ok, msg)."""
    tool = pactl()
    if not tool:
        return False, "pactl not found."
    # Newest first: the remap source and the loopback both hang off the sink's
    # monitor, and unloading the sink under them moves them to the default.
    ours = [m for m in _modules(tool) if _ours(m[1], m[2])]
    ours.sort(key=lambda m: int(m[0]) if m[0].isdigit() else 0, reverse=True)
    for mod_id, _, _ in ours:
        try:
            _run(tool, "unload-module", mod_id, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
    if not ours:
        return True, "There was no virtual microphone to remove."
    return True, "Virtual microphone removed."


def _clamp_volume(percent):
    return max(0, min(MONITOR_MAX_VOLUME, int(round(float(percent)))))


def _owned_sink_input(listing, modules):
    """The sink-input id owned by one of `modules`, from `pactl list sink-inputs`.

    Matched by owning module, not by name: the loopback's stream is named
    something like "loopback-2532-14 output", which says nothing about whose
    loopback it is.
    """
    current = None
    for line in listing.splitlines():
        s = line.strip()
        if s.startswith("Sink Input #"):
            current = s.split("#", 1)[1].strip()
        elif s.startswith("Owner Module:") and current is not None:
            if s.split(":", 1)[1].strip() in modules:
                return current
    return None


def _monitor_stream(tool):
    ours = {i for i, n, a in _modules(tool) if _is_monitor(n, a)}
    if not ours:
        return None
    try:
        listing = _run(tool, "list", "sink-inputs").stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return _owned_sink_input(listing, ours)


def set_monitor_volume(percent):
    """How loud you hear the cast. Only the monitor's own stream changes; the
    microphone other apps record is not touched. -> (ok, message)."""
    tool = pactl()
    if not tool:
        return False, "pactl not found."
    pct = _clamp_volume(percent)
    stream = _monitor_stream(tool)
    if stream is None:
        return True, f"Monitor volume {pct}% saved; it applies when the monitor starts."
    try:
        _run(tool, "set-sink-input-volume", stream, f"{pct}%", check=True)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"Could not set the monitor volume: {e}"
    return True, f"Monitor volume {pct}%."


def start_monitor(latency_ms=60, volume=None):
    """Echo the virtual sink to whatever the default output is.

    A null sink has no speakers, so routing the cast into it goes silent on
    your end -- the first thing everybody hits, and it looks like the app has
    broken. No `sink=` on purpose: omitting it attaches to the *default*
    output and follows it when you change devices. 60 ms is a floor for
    Bluetooth; wired can go to 20.
    """
    tool = pactl()
    if not tool:
        return False, "pactl not found."
    have_sink, _, monitoring = state()
    if monitoring:
        # Still apply the volume: asking for a level and being told "already
        # monitoring" would leave it wherever it was.
        if volume is not None:
            set_monitor_volume(volume)
            return True, f"Already monitoring, now at {_clamp_volume(volume)}%."
        return True, "Already monitoring."
    if not have_sink:
        return False, "Create the virtual microphone first."
    try:
        _run(tool, "load-module", "module-loopback", f"source={SINK}.monitor",
             f"latency_msec={int(latency_ms)}", check=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"Could not start the monitor: {e}"
    if volume is not None:
        # The loopback's stream appears a moment after the module loads.
        for _ in range(20):
            if _monitor_stream(tool) is not None:
                break
            time.sleep(0.05)
        set_monitor_volume(volume)
        return True, (f"Monitoring on your default output at "
                      f"{_clamp_volume(volume)}%. Anything recording the "
                      "microphone still gets the full level.")
    return True, "Monitoring on your default output."


def stop_monitor():
    """Unload our loopback only; one the user made for something else stays."""
    tool = pactl()
    if not tool:
        return False, "pactl not found."
    ours = [i for i, n, a in _modules(tool) if _is_monitor(n, a)]
    for mod_id in ours:
        try:
            _run(tool, "unload-module", mod_id, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
    return True, ("Monitor stopped." if ours else "Not monitoring.")
