"""Write a conversation without speaking it, and measure it.

"Is it funnier now" is not answerable by ear across two sessions an hour
apart with different topics. This runs the director's real line-writing path
-- same prompts, same move deck, same filter, same seeded RNG -- with no TTS,
no Discord and no playback, so one setting can be changed and the two
transcripts compared on the same seed and topic.

    # a baseline, then the same run with the deck off
    .venv-app/bin/python -m deadinternet.evalrun --seed 7 --turns 30 \\
        --topic "airport security" --out /tmp/a.json
    .venv-app/bin/python -m deadinternet.evalrun --seed 7 --turns 30 \\
        --topic "airport security" --set moves_enabled=false --out /tmp/b.json

    # the numbers side by side, and the model's opinion
    .venv-app/bin/python -m deadinternet.evalrun --compare /tmp/a.json /tmp/b.json --judge

Reads the real roster and settings; writes neither. --set changes a setting
for this run only.

The judge is asked for a preference between the two, never for a score. A
model rating funniness out of ten says seven. It is asked twice with the order
swapped, because it also prefers whichever it read first.
"""
import argparse
import asyncio
import json
import sys
from dataclasses import fields

from . import comedy
from .config import State, Turn
from .director import Director
from .llm import make_llm


class _NoRuntime:
    """The director asks its runtime for very little on the writing path."""
    loop = None

    def humans_present(self):
        return True

    def connected(self):
        return True


def _coerce(current, raw: str):
    if isinstance(current, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    return raw


def _apply(settings, pairs):
    known = {f.name for f in fields(settings)}
    for pair in pairs or []:
        key, _, raw = pair.partition("=")
        if key not in known:
            sys.exit(f"unknown setting '{key}'")
        setattr(settings, key, _coerce(getattr(settings, key), raw))


async def _write(args):
    state = State()
    # Nothing below calls state.save(), and it must stay that way: this is the
    # real roster.
    _apply(state.settings, args.set)
    s = state.settings
    s.rng_seed = args.seed
    if args.topic:
        s.topic = args.topic
    if args.cast:
        want = {n.strip().lower() for n in args.cast.split(",")}
        for sp in state.speakers:
            sp.enabled = sp.name.lower() in want
    cast = state.active()
    if not cast:
        sys.exit("no active speakers - enable some, or name them with --cast")

    llm = make_llm(s)
    llm.think = s.thinking
    d = Director(state, tts=None, llm=llm, runtime=_NoRuntime(),
                 log=lambda m: print(f"  {m}", file=sys.stderr))
    print(f"topic: {s.topic}\ncast:  {', '.join(sp.name for sp in cast)}\n"
          f"model: {llm.model}  seed: {args.seed}\n", file=sys.stderr)

    if s.premises_enabled:
        # Written up front here, where the live show writes them behind the
        # first line: a run this short would otherwise be half over before
        # they landed, and the comparison would mostly measure that.
        d._start_premises(s.topic)
        await d._premise_task

    for _ in range(args.turns):
        speaker = d._pick_next()
        text = await d._write_line(speaker)
        if not text:
            continue
        state.add_turn(Turn(speaker=speaker.name, text=text))
        d._last_speaker = speaker.name
        d._collect_bits()
        if d._bits_task:
            await d._bits_task
        move = d.comedy_debug["move"].partition(": ")[2]
        print(f"{speaker.name}: {text}")
        if move != "-":
            print(f"    [{move}]", file=sys.stderr)

    lines = [t.text for t in state.transcript]
    result = {
        "topic": s.topic, "model": llm.model, "seed": args.seed,
        "overrides": args.set or [],
        "premises": d.premises(), "bits": d._bits,
        "rerolled": d.comedy_debug["rerolled"],
        "metrics": comedy.metrics(lines),
        "transcript": [{"speaker": t.speaker, "text": t.text}
                       for t in state.transcript],
    }
    print("\n" + comedy.format_metrics(result["metrics"])
          + f", {result['rerolled']} re-rolled", file=sys.stderr)
    if args.out:
        with open(args.out, "w") as f:
            json.dump(result, f, indent=2)
        print(f"wrote {args.out}", file=sys.stderr)


def _page(run) -> str:
    return "\n".join(f"{t['speaker']}: {t['text']}" for t in run["transcript"])


def _prefer(llm, first, second) -> str:
    """'A', 'B' or '' -- which of two transcripts is funnier."""
    system = (
        "You are a comedy producer deciding which of two recordings of the "
        "same show goes out. Same cast, same topic. Pick the one a listener "
        "would actually laugh at: characters who want things, specific and "
        "surprising lines, friction, rhythm. Mark down polite agreement, "
        "summarising, lines that explain themselves, and everyone sounding "
        "alike.\n\n"
        'Answer with JSON only: {"pick": "A" or "B", "why": "<one sentence>"}'
    )
    user = f"RECORDING A:\n{_page(first)}\n\nRECORDING B:\n{_page(second)}"
    try:
        raw = llm.chat([{"role": "system", "content": system},
                        {"role": "user", "content": user}],
                       num_predict=120, temperature=0.1, fmt="json",
                       think=False, label="judge")
        obj = json.loads(raw)
    except Exception as e:
        print(f"  judge failed: {e}", file=sys.stderr)
        return ""
    print(f"  {obj.get('pick')}: {obj.get('why', '')}", file=sys.stderr)
    return str(obj.get("pick", "")).strip().upper()[:1]


def _compare(args):
    runs = []
    for path in args.compare:
        with open(path) as f:
            runs.append(json.load(f))
    a, b = runs
    keys = [k for k in a["metrics"] if k in b["metrics"]]
    width = max(len(k) for k in keys)
    print(f"{'':{width}}  {'A':>8}  {'B':>8}")
    for k in keys:
        print(f"{k:{width}}  {a['metrics'][k]:>8}  {b['metrics'][k]:>8}")
    print(f"\nA: {args.compare[0]}  {' '.join(a.get('overrides', [])) or '(as configured)'}")
    print(f"B: {args.compare[1]}  {' '.join(b.get('overrides', [])) or '(as configured)'}")
    if not args.judge:
        return
    state = State()
    _apply(state.settings, args.set)
    llm = make_llm(state.settings)
    print(f"\njudging with {llm.model}, both orders:", file=sys.stderr)
    votes = {"A": 0, "B": 0}
    first = _prefer(llm, a, b)
    if first in votes:
        votes[first] += 1
    # Swapped, so the letters swap back.
    second = {"A": "B", "B": "A"}.get(_prefer(llm, b, a), "")
    if second in votes:
        votes[second] += 1
    if votes["A"] == votes["B"]:
        print("\nno preference - it picked whichever it was shown first, "
              "or the judge failed")
    else:
        print(f"\npreferred: {'A' if votes['A'] > votes['B'] else 'B'} "
              f"({max(votes.values())} of 2)")


def main():
    ap = argparse.ArgumentParser(
        description="Write a conversation without speaking it, and measure it.")
    ap.add_argument("--turns", type=int, default=24)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--topic", default="")
    ap.add_argument("--cast", default="",
                    help="comma-separated speaker names; default is whoever is enabled")
    ap.add_argument("--set", action="append", metavar="KEY=VALUE",
                    help="override a setting for this run only; repeatable")
    ap.add_argument("--out", default="", help="write the run as JSON")
    ap.add_argument("--compare", nargs=2, metavar=("A.json", "B.json"))
    ap.add_argument("--judge", action="store_true",
                    help="with --compare: ask the model which is funnier")
    args = ap.parse_args()
    if args.compare:
        _compare(args)
    else:
        asyncio.run(_write(args))


if __name__ == "__main__":
    main()
