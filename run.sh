#!/usr/bin/env bash
# QwenPod launcher.
#
#   ./run.sh                 run in this terminal (Ctrl+C stops it)
#   ./run.sh -d              run detached, survives closing the terminal
#   ./run.sh stop            stop the app; tts-server keeps running (see below)
#   ./run.sh stop --all      stop the app, tts-server and whisper-server
#   ./run.sh restart [-d]    stop then start (detached by default)
#   ./run.sh status          what is running, which backend, how full the card is
#   ./run.sh logs            follow logs/app.log
#
#   --fresh                  with start/restart: stop tts-server and unload
#                            Ollama first, so tts-server allocates on an empty card
#   -- <args>                passed to app.py, e.g. ./run.sh -d -- --port 7870
#
# Fork-owned. Upstream has no run.sh (checked against upstream/master e46682f,
# 2026-09-13); if they ever add one, this is the file that will conflict.
#
# Two things this deliberately does NOT do by default:
#
#  * Stop tts-server on `stop`. It is started detached so it outlives the app,
#    and that is load-bearing: tts-server must allocate while the card is empty,
#    and a server that allocated on a full card runs ~3x slower until it is
#    restarted (0.16 vs 0.53 s of compute per second of audio, measured). Killing
#    it on every app restart would put that at risk every time. Use --all, or
#    --fresh on the way back up, when you mean it.
#
#  * Redirect stdout into logs/app.log. The app writes that file itself AND
#    prints every line, so that redirection -- which the docs used to recommend
#    -- duplicates every entry. Console output goes to logs/app-console.log
#    instead, which is only interesting when something dies before logging is up.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
HERE=$(pwd -P)

PY="$HERE/.venv-app/bin/python"
LOGS="$HERE/logs"
CONSOLE="$LOGS/app-console.log"
TTS_BIN="$HERE/build/tts-server"
WHISPER_BIN="$HERE/whisper.cpp/build/bin/whisper-server"
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"

say()  { printf '%s\n' "$*"; }
warn() { printf 'run.sh: %s\n' "$*" >&2; }

# ---- finding processes ----------------------------------------------------
# Through /proc rather than a pidfile, so an app started some other way -- by
# hand, by an editor, by a previous session -- is still found. Matching on the
# process's own cwd and argv also means this script can never match itself,
# which is the trap `pkill -f app.py` walks straight into.
app_pids() {
    local d cmd
    for d in /proc/[0-9]*; do
        # The braces matter: a process can exit between the glob listing it
        # and this read, and a failed `<` redirect is reported by the shell
        # itself -- a 2>/dev/null on tr alone never sees it.
        cmd=$({ tr '\0' ' ' < "$d/cmdline"; } 2>/dev/null) || continue
        case "$cmd" in *python*app.py*) ;; *) continue ;; esac
        [ "$({ readlink "$d/cwd"; } 2>/dev/null)" = "$HERE" ] || continue
        printf '%s\n' "${d#/proc/}"
    done
}

bin_pids() {    # pids whose argv[0] is exactly $1
    local d argv0
    for d in /proc/[0-9]*; do
        argv0=$({ tr '\0' '\n' < "$d/cmdline" | head -n1; } 2>/dev/null) || continue
        [ "$argv0" = "$1" ] && printf '%s\n' "${d#/proc/}"
    done
    return 0
}

stop_pids() {   # stop_pids <label> <pid>...
    local label=$1; shift
    [ $# -gt 0 ] || { say "$label: not running"; return 0; }
    kill -TERM "$@" 2>/dev/null || true
    local i
    for i in $(seq 1 30); do
        local alive=0 p
        for p in "$@"; do [ -d "/proc/$p" ] && alive=1; done
        [ $alive -eq 0 ] && { say "$label: stopped"; return 0; }
        sleep 0.5
    done
    kill -KILL "$@" 2>/dev/null || true
    say "$label: did not exit in 15s, killed"
}

vram() {        # "used/total GB" for the largest card, or nothing
    local best_t=0 best_u=0 dev u t
    for dev in /sys/class/drm/card*/device; do
        [ -r "$dev/mem_info_vram_total" ] || continue
        t=$(cat "$dev/mem_info_vram_total"); u=$(cat "$dev/mem_info_vram_used")
        [ "$t" -gt "$best_t" ] && { best_t=$t; best_u=$u; }
    done
    [ "$best_t" -gt 0 ] && awk -v u="$best_u" -v t="$best_t" \
        'BEGIN { printf "%.1f/%.1f GB", u/1e9, t/1e9 }'
    return 0
}

# ---- arguments ------------------------------------------------------------
CMD=start DETACH=0 FRESH=0 ALL=0 PASS=()
while [ $# -gt 0 ]; do
    case "$1" in
        start|stop|restart|status|logs) CMD=$1 ;;
        -d|--detach) DETACH=1 ;;
        --fresh)     FRESH=1 ;;
        --all)       ALL=1 ;;
        -h|--help)   sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        --)          shift; PASS=("$@"); break ;;
        *)           warn "unknown argument: $1 (see ./run.sh --help)"; exit 2 ;;
    esac
    shift
done
[ "$CMD" = restart ] && DETACH=1

PORT=7860
for ((i = 0; i < ${#PASS[@]}; i++)); do
    [ "${PASS[$i]}" = --port ] && PORT="${PASS[$((i + 1))]:-7860}"
done

# ---- commands -------------------------------------------------------------
do_stop() {
    # shellcheck disable=SC2046
    stop_pids "app" $(app_pids)
    if [ "$ALL" -eq 1 ]; then
        # shellcheck disable=SC2046
        stop_pids "tts-server" $(bin_pids "$TTS_BIN")
        # shellcheck disable=SC2046
        stop_pids "whisper-server" $(bin_pids "$WHISPER_BIN")
    elif [ -n "$(bin_pids "$TTS_BIN")" ]; then
        say "tts-server: left running (--all to stop it too)"
    fi
}

do_fresh() {
    say "fresh start: freeing the card before tts-server allocates"
    # shellcheck disable=SC2046
    stop_pids "tts-server" $(bin_pids "$TTS_BIN")
    local models
    models=$(curl -s -m 3 "$OLLAMA_URL/api/ps" 2>/dev/null | "$PY" -c \
        'import json,sys
try: print("\n".join(m["name"] for m in json.load(sys.stdin).get("models", [])))
except Exception: pass' 2>/dev/null) || true
    if [ -n "$models" ]; then
        while IFS= read -r m; do
            curl -s -m 30 "$OLLAMA_URL/api/generate" \
                -d "{\"model\":\"$m\",\"keep_alive\":0}" >/dev/null 2>&1 || true
            say "ollama: unloaded $m"
        done <<< "$models"
    fi
    sleep 2     # the driver releases VRAM asynchronously
    say "card now: $(vram)"
}

do_start() {
    if [ ! -x "$PY" ]; then
        warn "no virtualenv at .venv-app -- create it once with:"
        warn "    python3 -m venv .venv-app && ./.venv-app/bin/pip install -r requirements-app.txt"
        exit 1
    fi
    [ -x "$TTS_BIN" ] || warn "no $TTS_BIN -- build it with ./buildvulkan.sh (or buildcpu.sh);" \
                             "the app will still use a tts-server that is already running"
    local running; running=$(app_pids)
    if [ -n "$running" ]; then
        say "app: already running (pid $(printf '%s\n' "$running" | pids_line)) -- http://127.0.0.1:$PORT"
        say "     use ./run.sh restart to replace it"
        exit 0
    fi
    [ "$FRESH" -eq 1 ] && do_fresh

    if [ "$DETACH" -eq 0 ]; then
        say "app: starting in this terminal -- http://127.0.0.1:$PORT  (Ctrl+C to stop)"
        exec "$PY" ./app.py "${PASS[@]}"
    fi

    # setsid, not `& disown`: a new session is what actually detaches it from the
    # terminal. The disown form was tried and quietly died with its shell.
    mkdir -p "$LOGS"
    setsid nohup "$PY" ./app.py "${PASS[@]}" >> "$CONSOLE" 2>&1 < /dev/null &
    local i
    for i in $(seq 1 40); do
        curl -s -o /dev/null -m 1 "http://127.0.0.1:$PORT/" && {
            say "app: up -- http://127.0.0.1:$PORT  (./run.sh logs to follow, ./run.sh stop to end)"
            return 0
        }
        [ -z "$(app_pids)" ] && {
            warn "app exited during startup -- last lines of logs/app-console.log:"
            tail -n 15 "$CONSOLE" >&2
            exit 1
        }
        sleep 0.5
    done
    warn "app is running but not answering on :$PORT after 20s -- check ./run.sh logs"
}

pids_line() {    # newline-separated pids -> "1 2 3", no trailing space
    tr '\n' ' ' | sed 's/ *$//'
}

do_status() {
    local p
    # Explicit branches, not ${p:+..}${p:-..}: when p is set, ${p:-x} expands
    # to p itself, which glued the pid onto the end of the URL.
    p=$(app_pids | pids_line)
    if [ -n "$p" ]; then
        say "app            : running (pid $p) -- http://127.0.0.1:$PORT"
    else
        say "app            : not running"
    fi
    p=$(bin_pids "$TTS_BIN" | pids_line)
    if [ -n "$p" ]; then
        local backend
        backend=$(grep -a "Talker backend" "$LOGS/tts-server.log" 2>/dev/null | tail -n1 \
                  | sed 's/.*Talker backend: //') || true
        say "tts-server     : running (pid $p)${backend:+ -- $backend}"
    else
        say "tts-server     : not running"
    fi
    p=$(bin_pids "$WHISPER_BIN" | pids_line)
    if [ -n "$p" ]; then
        say "whisper-server : running (pid $p)"
    else
        say "whisper-server : not running"
    fi
    local v; v=$(vram)
    [ -n "$v" ] && say "vram           : $v"
    return 0
}

case "$CMD" in
    start)   do_start ;;
    stop)    do_stop ;;
    restart) do_stop; do_start ;;
    status)  do_status ;;
    logs)    exec tail -n 40 -F "$LOGS/app.log" ;;
esac
