"""Evolved personas stay inside their budget.

The failure this guards is slow rather than loud: every rewrite adds a
sentence, none removes one, and after a session each character is a
130-character base carrying 1100 characters of one-segment fixations. Nothing
errors; the prompts just get worse.

No model is needed -- chat() is replaced with canned answers, because what is
under test is what the code does with a model that ignores the length rule.

    .venv-app/bin/python deadinternet/tests/test_evolve_size.py
    .venv-app/bin/python -m pytest deadinternet/tests/
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from deadinternet.llm import (BaseLLM, DEFAULT_PERSONA_CHARS,  # noqa: E402
                              MAX_PERSONA_CHARS, persona_budget)

BASE = "You are Alex. You are a business major."
LONG = " ".join(f"You have trait number {i} and it matters a great deal."
                for i in range(40))


class FakeLLM(BaseLLM):
    """Answers from a queue and remembers what it was asked."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def chat(self, messages, num_predict=80, temperature=0.9, fmt=None,
             think=None, label=""):
        self.calls.append({"messages": messages, "label": label, "think": think})
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


def test_budget_follows_setting_but_never_undercuts_the_base():
    assert persona_budget(BASE, 600) == 600
    assert persona_budget(BASE, None) == DEFAULT_PERSONA_CHARS
    assert persona_budget("x" * 900, 600) == 900
    assert persona_budget("x" * 5000, 600) == MAX_PERSONA_CHARS
    assert persona_budget(BASE, 99999) == MAX_PERSONA_CHARS


def test_short_rewrite_is_accepted_without_a_second_call():
    llm = FakeLLM('"You are Alex, all ROI."')
    out = llm.evolve_persona("Alex", BASE, "", ["synergy"], "games", 600)
    assert out == "You are Alex, all ROI."
    assert len(llm.calls) == 1


def test_over_budget_rewrite_is_condensed_not_cut():
    tight = "You are Alex, a business major who prices everything."
    llm = FakeLLM(LONG, tight)
    out = llm.evolve_persona("Alex", BASE, "", ["synergy"], "games", 600)
    assert out == tight
    assert [c["label"] for c in llm.calls] == ["evolve: Alex",
                                               "evolve: Alex (condense)"]
    assert llm.calls[0]["think"] is False, "thinking is opt-in"
    assert llm.calls[1]["think"] is False


def test_empty_thinking_pass_is_retried_without_only_when_thinking():
    llm = FakeLLM("", "You are Alex.")
    out = llm.evolve_persona("Alex", BASE, "", ["synergy"], "games", 600,
                             think=True)
    assert out == "You are Alex."
    assert [c["think"] for c in llm.calls] == [True, False]

    # Without thinking an empty answer is just empty: asking the same
    # question the same way again is a wasted call.
    llm = FakeLLM("")
    assert llm.evolve_persona("Alex", BASE, "", ["synergy"], "games", 600) == ""
    assert len(llm.calls) == 1


def test_stims_are_named_as_off_limits():
    llm = FakeLLM("You are Alex.")
    llm.evolve_persona("Alex", BASE, "", ["oh my days! synergy"], "games", 600,
                       stims=["oh my days!", " "])
    rules = llm.calls[0]["messages"][0]["content"]
    assert '"oh my days!"' in rules and "separate system" in rules

    llm = FakeLLM("You are Alex.")
    llm.evolve_persona("Alex", BASE, "", ["synergy"], "games", 600)
    assert "separate system" not in llm.calls[0]["messages"][0]["content"]


def test_picking_skips_one_liners_and_fronts_bloated_sheets():
    from types import SimpleNamespace as NS
    from deadinternet.adbreak import pick_for_evolution

    cast = {
        "Chatty": NS(persona=BASE, dynamic_persona="You are tidy."),
        "OneLine": NS(persona=BASE, dynamic_persona=""),
        "Bloated": NS(persona=BASE, dynamic_persona=LONG),
        "Stale": NS(persona=BASE, dynamic_persona=""),
    }
    state = NS(get=cast.get, settings=NS(evolve_max_chars=600),
               evolve_stamps={"Chatty": 50.0, "Bloated": 99.0, "Stale": 1.0})
    said = {"Chatty": ["a", "b"], "OneLine": ["a"], "Bloated": ["a"],
            "Stale": ["a", "b", "c"], "Gone": ["a", "b"]}

    # Bloated jumps the queue on one line and the newest stamp; OneLine and
    # the deleted speaker are not candidates; the rest go oldest first.
    assert pick_for_evolution(state, said, 6) == ["Bloated", "Stale", "Chatty"]
    assert pick_for_evolution(state, said, 1) == ["Bloated"]


def test_condense_that_fails_or_ignores_the_limit_still_fits():
    for second in (RuntimeError("server went away"), LONG, ""):
        llm = FakeLLM(LONG, second)
        out = llm.evolve_persona("Alex", BASE, "", ["synergy"], "games", 600)
        assert 0 < len(out) <= 600
        assert out.endswith("."), "cut mid-sentence"


def test_bloated_current_sheet_is_told_to_shrink():
    llm = FakeLLM("You are Alex.")
    llm.evolve_persona("Alex", BASE, LONG, ["synergy"], "games", 600)
    asked = llm.calls[0]["messages"][1]["content"]
    assert f"CURRENT is {len(LONG)} characters" in asked

    llm = FakeLLM("You are Alex.")
    llm.evolve_persona("Alex", BASE, "You are Alex, tidy.", ["synergy"], "games", 600)
    assert "over the" not in llm.calls[0]["messages"][1]["content"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
