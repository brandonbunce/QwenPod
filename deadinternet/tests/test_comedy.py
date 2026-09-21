"""The comedy layer does what it says, with no model in the room.

What is under test is the part that is code: that the deck deals every move
before repeating one, that the filter catches the lines it exists to catch
and leaves ordinary ones alone, that a flagged take is re-rolled and a clean
one is not, and that a model which answers the JSON calls with rubbish costs
a feature rather than a turn.

    .venv-app/bin/python deadinternet/tests/test_comedy.py
    .venv-app/bin/python -m pytest deadinternet/tests/
"""
import asyncio
import os
import random
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

# Before deadinternet.config is imported: see test_ui_build.py.
os.environ["DEADINTERNET_CONFIG"] = os.path.join(
    tempfile.mkdtemp(prefix="qwentts-test-"), "roster.json")

from deadinternet import comedy  # noqa: E402
from deadinternet.config import Speaker, State, Turn  # noqa: E402
from deadinternet.director import Director  # noqa: E402
from deadinternet.llm import BaseLLM, sampling_options  # noqa: E402


class FakeLLM(BaseLLM):
    """Answers from a queue and remembers what it was asked."""

    def __init__(self, *answers):
        super().__init__("fake")
        self.answers = list(answers)
        self.calls = []

    def chat(self, messages, num_predict=80, temperature=0.9, fmt=None,
             think=None, label=""):
        self.calls.append({"messages": messages, "label": label,
                           "num_predict": num_predict})
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


# ---- the deck ------------------------------------------------------------------
def test_default_deck_parses_with_its_tags():
    moves = comedy.parse_moves(comedy.DEFAULT_MOVES)
    assert len(moves) >= 12
    assert any(m.short for m in moves) and any(m.duo for m in moves)
    assert any(m.short and m.duo for m in moves), "stacked tags"
    assert sum(m.needs_bit for m in moves) == 1
    assert not any(m.text.lower().startswith(("short:", "duo:")) for m in moves)


def test_every_move_is_dealt_before_any_repeats():
    deck = comedy.MoveDeck(random.Random(3))
    n = len(comedy.parse_moves(comedy.DEFAULT_MOVES))
    dealt = [deck.deal(bits=["Gary and the mail"]).text for _ in range(n)]
    assert len(set(dealt)) == n


def test_callback_waits_for_a_bit_and_duo_waits_for_company():
    deck = comedy.MoveDeck(random.Random(3))
    for _ in range(40):
        m = deck.deal(bits=[], solo=True)
        assert m is None or (not m.needs_bit and not m.duo)
    deck = comedy.MoveDeck(random.Random(3))
    texts = [deck.deal(bits=["the four hundred dollar sandwich"]).text
             for _ in range(16)]
    assert any("four hundred dollar sandwich" in t for t in texts)
    assert not any("{bit}" in t for t in texts)


def test_editing_the_deck_takes_effect_on_the_next_deal():
    deck = comedy.MoveDeck(random.Random(1))
    deck.deal()
    assert deck.deal("short: Say only no.").text == "Say only no."


# ---- the filter ------------------------------------------------------------------
def test_filter_catches_what_it_exists_to_catch():
    bad = {
        "That's a great point, Dave. Airports really are stressful.": comedy.FLAG_OPENER,
        "I totally agree, the lines are the worst part.": comedy.FLAG_OPENER,
        "Absolutely! And the shoes thing too.": comedy.FLAG_OPENER,
        "Honestly, I think security is mostly theatre.": comedy.FLAG_OPENER,
        "It's almost as if they want us to be late.": comedy.FLAG_FILLER,
        "He brought a sword. No pun intended.": comedy.FLAG_FILLER,
    }
    for line, flag in bad.items():
        assert flag in comedy.line_flags(line), line


def test_filter_leaves_ordinary_lines_alone():
    for line in ("I have been banned from three airports and I would do it again.",
                 "No.",
                 "Gary took my belt in 2019 and I have not seen it since.",
                 "Right now there is a man in Tulsa wearing my shoes."):
        assert comedy.line_flags(line, ["Something else entirely was said."]) == [], line


def test_a_question_passes_until_everyone_is_asking_them():
    q = "What did you do with the belt?"
    assert comedy.line_flags(q, ["He took my belt."]) == []
    assert comedy.FLAG_QUESTION in comedy.line_flags(q, ["Why would he do that?"])


def test_echo_and_length():
    said = "The TSA took my grandmother's knitting needles at the Denver airport."
    echo = "They took my grandmother's knitting needles at the Denver airport too."
    assert comedy.FLAG_ECHO in comedy.line_flags(echo, [said])
    assert comedy.FLAG_LONG in comedy.line_flags(
        "This is very clearly a great many more words than were asked for.",
        max_words=comedy.SHORT_MAX_WORDS)


def test_worn_phrases_find_the_tape_loop():
    own = ["The taco integrity is failing and people with hollow heads miss it.",
           "We need massive doors, and the taco integrity matters.",
           "Only someone with a hollow head would touch taco integrity."]
    worn = comedy.worn_phrases(own)
    assert "taco integrity" in worn
    # Said once is not worn, and neither is glue.
    assert not any("massive doors" in w or w.startswith(("the ", "and ")) for w in worn)
    assert comedy.FLAG_WORN in comedy.line_flags("I love taco integrity.", worn=worn)
    assert comedy.line_flags("I love a good door.", worn=worn) == []


def test_a_short_move_that_was_ignored_is_cut_to_a_sentence():
    assert comedy.first_sentence("No way. And another thing, the taco.") == "No way."
    assert comedy.first_sentence("No full stop here") == "No full stop here"


def test_pick_best_prefers_clean_then_specific():
    takes = ["That's a great point about airports.",
             "It is bad there.",
             "Gary lost 40 dollars to a vending machine in Tulsa."]
    assert comedy.pick_best(takes) == 2


def test_metrics_move_the_right_way():
    dull = ["That's a great point, I agree with what you said there?"] * 6
    alive = ["No.", "Gary owes me 40 dollars and a belt.",
             "I have been to Tulsa exactly once and it was to fight a man.",
             "Fine.", "The needles were a gift from a woman named Doreen.",
             "You were never in Tulsa."]
    d, a = comedy.metrics(dull), comedy.metrics(alive)
    assert d["pct_opener"] == 100 and a["pct_opener"] == 0
    assert d["sd_words"] == 0 and a["sd_words"] > 3
    assert a["specifics_per_line"] > d["specifics_per_line"]
    assert a["distinct3"] > d["distinct3"]
    assert comedy.metrics([]) == {"lines": 0}


# ---- the prompt ------------------------------------------------------------------
def _speaker(name="Alex", **kw):
    return Speaker(name=name, ref_wav="x.wav", persona=f"You are {name}.", **kw)


def test_prompt_carries_move_premise_and_samples():
    llm = FakeLLM("No.")
    move = comedy.Move("Reply in six words or fewer.", short=True)
    llm.speak_as(_speaker(), [Turn("Sam", "Airports, eh.")], "airports",
                 move=move, premise="You are sure Gary has your belt.",
                 samples=["Don't talk to me about Gary."],
                 length=comedy.LENGTH_SHORT, avoid=["taco integrity"])
    system = llm.calls[0]["messages"][0]["content"]
    assert "Reply in six words or fewer." in system
    assert "Gary has your belt" in system
    assert "Don't talk to me about Gary." in system
    assert comedy.LENGTH_SHORT in system
    assert '"taco integrity"' in system
    # The move goes last: the last instruction is the one that is followed.
    assert system.rindex("Your move") > system.rindex("Gary has your belt")


def test_prompt_without_the_comedy_layer_keeps_the_old_menu():
    llm = FakeLLM("Fine.")
    llm.speak_as(_speaker(), [Turn("Sam", "Airports, eh.")], "airports")
    system = llm.calls[0]["messages"][0]["content"]
    assert "a joke that goes somewhere new" in system
    assert "Your move" not in system


def test_script_framing_is_one_page_not_chat_turns():
    llm = FakeLLM("Alex: No.")
    out = llm.speak_as(_speaker(), [Turn("Sam", "Airports, eh."),
                                    Turn("Alex", "Hm.")],
                       "airports", script=True)
    msgs = llm.calls[0]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert "SAM: Airports, eh." in msgs[1]["content"]
    assert out == "No."


def test_samples_survive_evolution():
    mined = ('You are Alex.\n\nHere are real messages Alex wrote:\n'
             '- "gary owes me money"\n- "never been to tulsa"')
    sp = Speaker(name="Alex", persona=mined)
    # The base prompt is in use and already quotes them.
    assert sp.voice_samples() == []
    sp.dynamic_persona = "You are Alex, and you hold grudges."
    assert sp.voice_samples() == ["gary owes me money", "never been to tulsa"]
    sp.sample_lines = "Typed in by hand."
    assert sp.voice_samples() == ["Typed in by hand."]


# ---- the model calls, with a model that misbehaves -----------------------------------
def test_premises_keep_only_real_cast_members():
    cast = [_speaker("Alex"), _speaker("Sam")]
    llm = FakeLLM('{"premises": [{"name": "alex", "premise": "You want the belt."},'
                  ' {"name": "Nobody", "premise": "x"}, "junk"]}')
    assert llm.write_premises("airports", cast) == {"Alex": "You want the belt."}


def test_premises_find_a_name_the_model_shortened():
    cast = [_speaker("Donald Trump (Rally)"), _speaker("Sam"), _speaker("Sam Two")]
    llm = FakeLLM('{"premises": [{"name": "Donald Trump", "premise": "Doors."},'
                  ' {"name": "sa", "premise": "ambiguous, so dropped"}]}')
    assert llm.write_premises("airports", cast) == {"Donald Trump (Rally)": "Doors."}


def test_rubbish_from_the_json_calls_costs_the_feature_not_the_turn():
    cast = [_speaker("Alex"), _speaker("Sam")]
    for bad in ("not json", "[]", '{"premises": 4}', RuntimeError("gone")):
        assert FakeLLM(bad).write_premises("airports", cast) == {}
    for bad in ("not json", '{"bits": "no"}', RuntimeError("gone")):
        assert FakeLLM(bad).extract_bits(["Alex: a line"]) == []
    for bad in ("not json", '{"pick": 9}', '{"pick": "two"}', RuntimeError("gone")):
        assert FakeLLM(bad).judge_takes("Alex", [], ["a", "b"]) == 0


def test_bits_are_deduplicated_and_tidied():
    llm = FakeLLM('{"bits": ["Gary and the mail", "  the belt. ", "x", "THE BELT"]}')
    assert llm.extract_bits(["Alex: a line"], have=["gary and the mail"]) == ["the belt"]


def test_judge_answers_are_one_based():
    assert FakeLLM('{"pick": 2}').judge_takes("Alex", [], ["a", "b"]) == 1


def test_sampling_options():
    s = State().settings
    s.min_p, s.repeat_penalty = 0.08, 1.1
    assert sampling_options(s) == {"min_p": 0.08, "top_p": 1.0, "top_k": 0,
                                   "repeat_penalty": 1.1}
    s.min_p, s.repeat_penalty = 0.0, 1.0
    assert sampling_options(s) == {}


# ---- the director ------------------------------------------------------------------
def _director(llm, **settings):
    state = State()
    state.speakers = [_speaker("Alex"), _speaker("Sam")]
    state.settings.rng_seed = 5
    # Off unless a test asks: each would otherwise take an answer off the queue.
    state.settings.premises_enabled = False
    state.settings.bits_enabled = False
    state.settings.moves_enabled = False
    for k, v in settings.items():
        setattr(state.settings, k, v)
    state.add_turn(Turn("Sam", "Airports are fine."))
    return Director(state, tts=None, llm=llm, runtime=None, log=lambda m: None)


def _line(d, name="Alex"):
    return asyncio.run(d._write_line(d.state.get(name)))


def test_a_clean_first_take_is_the_only_take():
    llm = FakeLLM("Gary has my belt.")
    assert _line(_director(llm)) == "Gary has my belt."
    assert len(llm.calls) == 1


def test_a_flagged_take_is_rerolled():
    llm = FakeLLM("That's a great point, Sam.", "Gary has my belt.")
    d = _director(llm)
    assert _line(d) == "Gary has my belt."
    assert [c["label"] for c in llm.calls] == ["Alex", "Alex (take 2)"]
    assert d.comedy_debug["rerolled"] == 1


def test_when_every_take_is_flagged_the_least_bad_one_plays():
    llm = FakeLLM("That's a great point, honestly. It's almost as if they know.",
                  "I totally agree.",
                  "Absolutely, no pun intended, at the end of the day.")
    assert _line(_director(llm)) == "I totally agree."


def test_one_take_means_no_filter():
    llm = FakeLLM("That's a great point, Sam.")
    assert _line(_director(llm, line_takes=1)) == "That's a great point, Sam."


def test_the_judge_sees_every_take_and_picks():
    llm = FakeLLM("Gary has my belt.", "Doreen has my shoes.", "Tulsa has my coat.",
                  '{"pick": 3}')
    d = _director(llm, line_judge=True)
    assert _line(d) in ("Gary has my belt.", "Doreen has my shoes.",
                        "Tulsa has my coat.")
    assert llm.calls[-1]["label"] == "judge"
    assert len(llm.calls) == 4


def test_a_model_that_fails_mid_reroll_still_gets_a_line_out():
    llm = FakeLLM("That's a great point, Sam.", RuntimeError("server went away"))
    assert _line(_director(llm)) == "That's a great point, Sam."


def test_a_short_move_cuts_the_token_budget():
    llm = FakeLLM("No.")
    d = _director(llm, moves_enabled=True, move_chance=100.0,
                  moves_text="short: Say only no.")
    assert _line(d) == "No."
    assert llm.calls[0]["num_predict"] == comedy.SHORT_NUM_PREDICT
    assert "Say only no." in llm.calls[0]["messages"][0]["content"]


def test_a_short_move_lands_even_when_every_take_ignores_it():
    long = "Absolutely not a chance. And furthermore the doors are magnificent things."
    d = _director(FakeLLM(long, long, long), moves_enabled=True,
                  move_chance=100.0, moves_text="short: Say only no.")
    assert _line(d) == "Absolutely not a chance."


def test_worn_phrases_spare_stims_and_names():
    d = _director(FakeLLM())
    alex = d.state.get("Alex")
    alex.stims = "sweet jellyfishing"
    d.state.speakers.append(_speaker("Sam Smith"))
    d.state.add_turn(Turn("Alex", "Sweet jellyfishing! Sam Smith hates taco integrity."))
    d.state.add_turn(Turn("Alex", "Ask Sam Smith about taco integrity. Sweet jellyfishing."))
    worn = d._worn_for(alex, d.state.recent())
    assert worn == ["taco integrity"]


def test_answering_a_real_person_is_never_a_bit():
    llm = FakeLLM("It is at gate four.")
    d = _director(llm, moves_enabled=True, move_chance=100.0)
    asyncio.run(d._write_line(d.state.get("Alex"), addressed_to="brandon"))
    assert "Your move" not in llm.calls[0]["messages"][0]["content"]


def test_premises_are_written_after_the_first_line_and_used_from_the_second():
    async def go():
        llm = FakeLLM("First.",
                      '{"premises": [{"name": "Alex", "premise": "You want the belt."}]}',
                      "Second.")
        d = _director(llm, premises_enabled=True)
        await d._write_line(d.state.get("Alex"))
        await d._premise_task
        await d._write_line(d.state.get("Alex"))
        return llm
    llm = asyncio.run(go())
    assert [c["label"] for c in llm.calls] == ["Alex", "premises", "Alex"]
    assert "You want the belt." not in llm.calls[0]["messages"][0]["content"]
    assert "You want the belt." in llm.calls[2]["messages"][0]["content"]


# ---- switching topic ---------------------------------------------------------------
def test_switch_progress_ticks_through_its_stages():
    from deadinternet.director import (STEP_AD, STEP_ANNOUNCE, STEP_BUILD,
                                       STEP_CUT, SwitchProgress)
    p = SwitchProgress()
    assert p.render() == "" and not p.active
    p.begin("hot dogs", [STEP_CUT, STEP_BUILD, STEP_AD, STEP_ANNOUNCE])
    p.step(STEP_CUT)
    p.skip(STEP_AD)
    p.step(STEP_BUILD)
    assert p.steps == [(STEP_CUT, "done"), (STEP_BUILD, "now"),
                       (STEP_ANNOUNCE, "todo")]
    out = p.render()
    assert "Switching to:** hot dogs" in out and STEP_AD not in out
    p.end("hot dogs")
    assert not p.active and "Switched to:" in p.render()
    p._done_at -= SwitchProgress.LINGER + 1
    assert p.render() == ""


class _Runtime:
    loop = None

    def humans_present(self):
        return True

    async def play_wav(self, wav, timeout=None):
        pass


class _TTS:
    def synth(self, text, voice, **kw):
        return b""


def test_a_typed_switch_does_not_wait_for_premises():
    """The premises call is the long one. It must start, and the switch must
    finish, without the one waiting on the other."""
    import threading
    gate = threading.Event()

    class SlowPremises(FakeLLM):
        def write_premises(self, topic, speakers):
            gate.wait(5)
            return {"Alex": "You want the belt."}

    async def go():
        d = _director(SlowPremises(), premises_enabled=True)
        d.tts, d.runtime = _TTS(), _Runtime()
        d.state.settings.normalize_audio = False
        d.state.settings.speech_limit_enabled = False
        d.queue_topic("hot dogs")
        switched = await d._maybe_rotate_topic(force=True)
        held = d._premise_task is not None and not d._premise_task.done()
        gate.set()
        await d._premise_task
        return d, switched, held

    d, switched, held = asyncio.run(go())
    assert switched and held
    assert d.state.settings.topic == "hot dogs"
    assert d.premises() == {"Alex": "You want the belt."}
    assert not d.switching.active and "Switched to:** hot dogs" in d.switching.render()


def test_a_switch_that_finds_nothing_clears_its_progress():
    async def go():
        d = _director(FakeLLM())
        d.runtime = _Runtime()
        d.state.settings.source_pins_weight = 0.0
        d.switching.begin("", ["preparing the topic"])
        return d, await d._maybe_rotate_topic(force=True)
    d, switched = asyncio.run(go())
    assert not switched and d.switching.render() == ""


def test_local_output_can_be_asked_for_pins():
    from deadinternet.local import LocalRuntime
    assert asyncio.run(LocalRuntime().fetch_pins(["1"])) == []


def test_fixed_seed_replays_the_moves():
    def moves():
        llm = FakeLLM(*["Gary has my belt number %d." % i for i in range(8)])
        d = _director(llm, moves_enabled=True, move_chance=100.0, line_takes=1)
        out = []
        for _ in range(8):
            _line(d)
            out.append(d.comedy_debug["move"])
        return out
    assert moves() == moves()


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok    {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print("PASS" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)
