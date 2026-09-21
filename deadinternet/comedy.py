"""The comedy layer: every part of being funny that is not a model call.

A small instruct model asked to "be funny" produces a panel discussion. It is
tuned to be agreeable, balanced and explanatory, and a menu of options ("a
take, a detail, a disagreement, a joke") gets the mildest one every time. What
it *can* do is carry out one concrete instruction. So the decisions are made
here, by code, and the model is only ever asked to execute:

  * a move deck -- one specific comedic move dealt per turn from a shuffled
    bag, the same way vocal stims are rolled;
  * a line filter -- the assistant tics that make a line sound generated,
    detected cheaply enough to re-roll the line instead of playing it;
  * numbers -- the same tics counted over a whole segment, so "is it funnier"
    can be answered with something other than a feeling.

Nothing in here touches the network, which is what makes it testable. The
calls that do -- premises, bits, the judge -- are in llm.py.
"""
import re
from dataclasses import dataclass
from statistics import mean, pstdev
from typing import List, Optional

# A `short:` move is held to this many words. Rhythm is half of what is funny
# out loud, and left alone every line comes back the same two sentences long.
SHORT_WORDS = 6
# What the filter actually enforces, and what the token budget is cut to. Slack
# over SHORT_WORDS because "six words or fewer" reliably comes back as eight,
# and eight is still a short line.
SHORT_MAX_WORDS = 9
SHORT_NUM_PREDICT = 24

# One move per line. Blank Settings.moves_text uses this; the box resets to it.
#
# Tags, before the text and in any order:
#   short:  hold the reply to a handful of words
#   duo:    needs somebody else to have spoken -- skipped for a lone speaker
# A move containing {bit} is a callback: dealt only once the show has a running
# bit to call back to, and {bit} is replaced with one.
#
# Every one of these is a thing to *do*, never a thing to *be*. "Be sarcastic"
# gets one sarcastic adjective; "agree, for a reason that should worry
# everyone" gets a joke.
DEFAULT_MOVES = """\
duo: Take the last thing said completely literally, and respond to that.
Make this about a personal grievance of yours that has nothing to do with the topic, as if it obviously does.
duo: Agree with them, but for a reason that should worry everyone.
Take what was just said to its most extreme conclusion, and state it as plain fact.
Back up your point with a made-up personal anecdote. Give it a named person, a place and an exact number.
short: Reply in six words or fewer. Flat and final. No explanation.
short: duo: One short sentence of pure disbelief or disgust at what was just said. Do not elaborate.
Quietly walk back something you said earlier, as if nobody will notice.
Call back to this from earlier in the show: "{bit}". Tie it to what was just said as if the two were obviously related. Do not explain the reference.
duo: Pick one word or phrase they just used and fixate on it. Ignore the rest of their point.
Confidently state a fact that is wrong in a specific, checkable way, and build your point on it.
Treat the most trivial detail of what was said as the real scandal here.
duo: Accuse the last speaker of something oddly specific, based only on what they just said.
Announce a plan of action that is wildly out of proportion to this, with a first step you have already taken.
Compare this to something from your own life that is not comparable at all, and insist the comparison is exact.
Say the quiet part out loud: state your selfish motive plainly, as if it were reasonable."""

_TAG = re.compile(r"^\s*(short|duo)\s*:\s*", re.I)


@dataclass(frozen=True)
class Move:
    text: str
    short: bool = False
    duo: bool = False

    @property
    def needs_bit(self) -> bool:
        return "{bit}" in self.text


def parse_moves(text: str) -> List[Move]:
    moves = []
    for raw in (text or "").splitlines():
        line, tags = raw.strip(), set()
        while True:
            m = _TAG.match(line)
            if not m:
                break
            tags.add(m.group(1).lower())
            line = line[m.end():]
        if line:
            moves.append(Move(line, "short" in tags, "duo" in tags))
    return moves


class MoveDeck:
    """Deals moves from a shuffled bag, refilled once it is empty.

    A bag rather than a fresh random pick for the same reason the pins use
    one: independent draws repeat, and the same move twice running is a
    character with one joke. Takes the director's seeded RNG, so a fixed seed
    reproduces the moves along with everything else.
    """

    def __init__(self, rng):
        self._rng = rng
        self._bag: List[Move] = []
        self._source = None

    def deal(self, text: str = "", bits=(), solo: bool = False) -> Optional[Move]:
        source = (text or "").strip() or DEFAULT_MOVES
        if source != self._source:
            # The deck was edited; what is left of the old bag is stale.
            self._source, self._bag = source, []
        if not self._bag:
            self._bag = parse_moves(source)
            self._rng.shuffle(self._bag)
        for i, move in enumerate(self._bag):
            if (move.needs_bit and not bits) or (move.duo and solo):
                continue
            del self._bag[i]
            if move.needs_bit:
                return Move(move.text.replace("{bit}", self._rng.choice(list(bits))),
                            move.short, move.duo)
            return move
        # Nothing left that fits this turn. Start over next time rather than
        # dealing the unplayable remainder forever.
        self._bag = []
        return None


# ---- the line filter ---------------------------------------------------------
# Each of these is a way a line announces that a language model wrote it. None
# is banned outright -- a flagged line is re-rolled, and when every take is
# flagged the least bad one plays -- so a character who really does say
# "honestly" still gets to.

# The agreeable wind-up. The single most reliable tell, and the one that turns
# an argument into a panel discussion: nobody funny opens by validating the
# last speaker.
_OPENER = re.compile(
    r"^\W*(?:"
    r"(?:oh[, ]+)?(?:that'?s|what) an? (?:great|good|fair|interesting|excellent|"
    r"valid|fascinating|solid) (?:point|question|take|observation|way)"
    r"|i (?:totally |completely |absolutely |definitely |really )?(?:agree|hear you|get (?:that|it))"
    r"|you(?:'re| are) (?:absolutely |totally |so |completely )?right"
    r"|(?:absolutely|exactly|totally|definitely|precisely|indeed|true|right)[,.!]"
    r"|(?:honestly|you know|i mean|i think|to be fair|to be honest|frankly)[, ]"
    r"|(?:it'?s|that'?s) (?:funny|interesting|fascinating|wild|crazy) (?:you|how|that|because)"
    r"|speaking of"
    r"|ah[,.!]"
    r")", re.I)

# Explaining the joke, summarising the discussion, or lapsing into the
# assistant's own vocabulary.
_FILLER = re.compile(
    r"\b(?:no pun intended|pun intended|see what i did|get it\?|the irony (?:is|here)"
    r"|it'?s almost (?:as if|like)|in other words|at the end of the day"
    r"|it'?s important to|it'?s worth (?:noting|remembering)|let'?s unpack"
    r"|delve|nuanced?|valid point|food for thought|a testament to"
    r"|when you think about it|if you think about it|on (?:the )?one hand"
    r"|both sides|who knows,? maybe|only time will tell)\b", re.I)

_WORD = re.compile(r"[a-z0-9']+")
_STOP = frozenset(
    "a an the and or but so if of to in on at for with from by as is are was "
    "were be been it its it's that this these those i you he she we they me "
    "my your our their not no do does did just like about what which who how "
    "have has had will would can could than then there here".split())

FLAG_OPENER = "agreeable opener"
FLAG_FILLER = "explains itself"
FLAG_QUESTION = "another question"
FLAG_ECHO = "echoes a recent line"
FLAG_LONG = "too long for a short move"
FLAG_WORN = "leans on a worn-out phrase"

# Share of ordinary turns held to a single sentence. Left to itself every line
# is two full sentences -- the second usually a lap of the character's
# favourite subjects -- and a conversation where everyone speaks for fifteen
# seconds at a time has no rhythm to be funny in.
BRIEF_CHANCE = 45.0
LENGTH_BRIEF = "One sentence, under fifteen words."
LENGTH_FULL = "One or two sentences."
LENGTH_SHORT = "Six words at most. Count them."


def _words(text: str) -> List[str]:
    return _WORD.findall((text or "").lower())


def _content(text: str) -> set:
    return {w for w in _words(text) if w not in _STOP and len(w) > 2}


def _trigrams(text: str) -> set:
    w = _words(text)
    return {tuple(w[i:i + 3]) for i in range(len(w) - 2)}


def worn_phrases(own_lines, limit: int = 5) -> List[str]:
    """Phrases this speaker has used in two or more of their recent lines.

    A character with a fixation is funny the first time and a tape loop by the
    fourth -- and an evolved persona is mostly fixations, so left alone every
    line visits all of them. This finds the loop; the caller tells the model
    what to stay off for one line, and the filter re-rolls a take that did not
    listen. Longest first, and nothing that is just part of a longer one.
    """
    seen = {}
    for line in own_lines:
        w = _words(line)
        grams = set()
        for n in (4, 3, 2):
            for i in range(len(w) - n + 1):
                g = w[i:i + n]
                # Both ends have to be real words: "of the taco" is wear on
                # "taco", not a phrase anyone would notice.
                if g[0] in _STOP or g[-1] in _STOP:
                    continue
                grams.add(" ".join(g))
        for g in grams:
            seen[g] = seen.get(g, 0) + 1
    worn = sorted((g for g, n in seen.items() if n >= 2),
                  key=lambda g: (-len(g.split()), g))
    out = []
    for g in worn:
        if not any(g in kept for kept in out):
            out.append(g)
    return out[:limit]


def first_sentence(text: str) -> str:
    """The line cut back to its first sentence, for a short move that was
    ignored. Same promise the stims make: a successful roll always lands."""
    m = re.search(r"[.!?](?=\s|$)", text or "")
    return (text[: m.end()] if m else text or "").strip()


def line_flags(text: str, recent=(), max_words: int = 0, worn=()) -> List[str]:
    """Why this line sounds generated. Empty means play it.

    `recent` is what has been said lately, oldest first, as plain strings;
    `worn` is what worn_phrases() found for whoever is speaking.
    """
    flags = []
    text = (text or "").strip()
    low = " ".join(_words(text))
    if any(w in low for w in worn):
        flags.append(FLAG_WORN)
    if _OPENER.search(text):
        flags.append(FLAG_OPENER)
    if _FILLER.search(text):
        flags.append(FLAG_FILLER)
    # One question is conversation. Every line ending in one is the model
    # handing the work back, which it does constantly -- so a question is only
    # let through when nobody else has just asked one.
    if text.endswith("?") and any(
            (r or "").strip().endswith("?") for r in list(recent)[-3:]):
        flags.append(FLAG_QUESTION)
    if recent:
        mine, tri = _content(text), _trigrams(text)
        for r in list(recent)[-6:]:
            theirs = _content(r)
            overlap = (len(mine & theirs) / len(mine | theirs)
                       if mine and theirs else 0.0)
            if overlap > 0.5 or len(tri & _trigrams(r)) >= 3:
                flags.append(FLAG_ECHO)
                break
    if max_words and len(_words(text)) > max_words:
        flags.append(FLAG_LONG)
    return flags


def specifics(text: str) -> int:
    """Numbers, and capitalised words that are not starting a sentence.

    A crude stand-in for "said something concrete": a named person, a place, a
    figure. Vague lines score nothing, and vague is the thing being fixed.
    """
    n = len(re.findall(r"\b\d[\d,.]*\b", text or ""))
    for m in re.finditer(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z]{2,}\b", text or ""):
        if m.group(0) != "I":
            n += 1
    return n


def pick_best(takes, recent=(), max_words: int = 0, worn=()) -> int:
    """Index of the least-flagged take; specifics break a tie, then brevity."""
    def key(i):
        t = takes[i]
        return (len(line_flags(t, recent, max_words, worn)), -specifics(t), len(t))
    return min(range(len(takes)), key=key)


# ---- numbers -------------------------------------------------------------------
def metrics(lines) -> dict:
    """The line filter's tics, counted over a run of lines.

    These are proxies. None of them measures funny -- a transcript can score
    well on every one and still be dull -- but each is something a dull
    transcript reliably gets wrong, and they move when a change works.
    """
    lines = [ln.strip() for ln in lines if (ln or "").strip()]
    if not lines:
        return {"lines": 0}
    lengths = [len(_words(ln)) for ln in lines]
    tri_all = [t for ln in lines for t in _trigrams(ln)]
    return {
        "lines": len(lines),
        "mean_words": round(mean(lengths), 1),
        # The one that matters most to the ear. Near zero means every line is
        # the same shape.
        "sd_words": round(pstdev(lengths), 1),
        "pct_short": round(100 * sum(n <= SHORT_MAX_WORDS for n in lengths) / len(lines)),
        "pct_question": round(100 * sum(ln.endswith("?") for ln in lines) / len(lines)),
        "pct_opener": round(100 * sum(bool(_OPENER.search(ln)) for ln in lines) / len(lines)),
        "pct_filler": round(100 * sum(bool(_FILLER.search(ln)) for ln in lines) / len(lines)),
        "specifics_per_line": round(sum(specifics(ln) for ln in lines) / len(lines), 2),
        # Share of three-word runs that are said only once. Falls as the cast
        # starts repeating itself and each other.
        "distinct3": round(len(set(tri_all)) / len(tri_all), 2) if tri_all else 1.0,
    }


def format_metrics(m: dict) -> str:
    if not m.get("lines"):
        return "no lines"
    return (f"{m['lines']} lines, {m['mean_words']} words each (sd {m['sd_words']}), "
            f"{m['pct_short']}% short, {m['pct_question']}% questions, "
            f"{m['pct_opener']}% agreeable openers, {m['pct_filler']}% filler, "
            f"{m['specifics_per_line']} specifics/line, distinct-3 {m['distinct3']}")
