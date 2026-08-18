"""LLM backends: in-character line generation plus the interrupt router.

Two providers behind one interface. Ollama runs locally and shares the GPU
with the TTS server (see the VRAM note in DEADINTERNET.md); OpenAI is remote,
so it costs money and adds network latency but leaves the whole card free for
speech. Subclasses implement only _chat().
"""
import json
import os
import re

import requests

# Keeps an Ollama model resident between turns; a cold reload costs seconds
# and would show up as dead air in the voice channel.
KEEP_ALIVE = "30m"

# Shortest prefix worth keeping when trimming a truncated line back to its
# last complete sentence.
MIN_SENTENCE_CHARS = 15

# The ad brief, when settings.adbreak_prompt is left blank. Editable there;
# this is what the box resets to.
DEFAULT_AD_PROMPT = (
    "You write the sponsor read for a podcast. Invent a product or service "
    "that plausibly does not exist, named after something the hosts actually "
    "just said, and sell it with total confidence.\n\n"
    "Rules:\n"
    "- Two or three sentences. It is read out loud over music.\n"
    "- Reply with ONLY the words spoken. No 'Ad:' prefix, no stage directions, "
    "no markdown, no emoji.\n"
    "- Refer to something specific from the segment. That callback is the whole "
    "joke.\n"
    "- Play it straight. Advertising voice, not comedy voice."
)

# Ceiling on a rewritten persona. Repeated rewriting only ever adds -- each
# pass has something new to account for and no reason to drop anything -- so
# without a hard limit a character becomes a page of hedged mush after a dozen
# topics. Enforced in the prompt and again in code, because the prompt alone
# is a request rather than a guarantee.
MAX_PERSONA_CHARS = 1200

# How much more room a request gets when the model is allowed to think. The
# per-line budget is sized for one or two spoken sentences; reasoning has to
# fit *inside* the same budget, so without this the model spends all of it
# thinking and returns empty content.
THINK_BUDGET = 8

PROVIDER_OLLAMA = "ollama"
PROVIDER_OPENAI = "openai"
PROVIDERS = [PROVIDER_OLLAMA, PROVIDER_OPENAI]


class BaseLLM:
    def __init__(self, model: str, timeout: int = 180):
        self.model = model
        self.timeout = timeout
        self.http = requests.Session()
        # Providers that cannot do it leave this False and ignore it.
        self.think = False

    # ---- provider hooks ------------------------------------------------
    def models(self):
        return []

    def available(self):
        """(ok, message) -- whether this backend can actually be used."""
        return True, ""

    def _chat(self, messages, num_predict=80, temperature=0.9, fmt=None,
              think=None) -> str:
        """think: None follows the client's setting, False forces it off.

        Forcing it off is for calls where reasoning buys nothing and the
        latency is felt -- the router, in practice.
        """
        raise NotImplementedError

    def _image_message(self, text: str, b64: str, mime: str) -> dict:
        """A user message carrying an inline image. The two providers disagree
        about the wire format, so each subclass builds its own."""
        return {"role": "user", "content": text}

    def supports_images(self) -> bool:
        return False

    # ---- output cleanup --------------------------------------------------
    @staticmethod
    def _clean(text: str, name: str) -> str:
        """Strip the artefacts that make TTS sound wrong: a leading 'Name:'
        the model adds by habit, stage directions in asterisks or brackets,
        and surrounding quotes."""
        text = re.sub(r"^\s*%s\s*:\s*" % re.escape(name), "", text, flags=re.I)
        text = re.sub(r"\*[^*]{0,80}\*", " ", text)
        text = re.sub(r"\((?:[^()]{0,80})\)", " ", text)
        text = re.sub(r"\[[^\]]{0,80}\]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        text = text.strip('"').strip()
        # A token limit often cuts mid-sentence, which the TTS then reads as a
        # dangling fragment. Trim back to the last complete sentence, keeping
        # the fragment only when there is no usable sentence to fall back to.
        if text and text[-1] not in ".!?":
            cut = max(text.rfind(". "), text.rfind("! "), text.rfind("? "))
            if cut >= MIN_SENTENCE_CHARS:
                text = text[: cut + 1].strip()
        return text

    # ---- line generation ---------------------------------------------------
    def speak_as(self, speaker, transcript, topic, addressed_to=None,
                 num_predict=80, temperature=0.9, stim=None, image=None,
                 solo=None) -> str:
        roster_line = ""
        others = [t.speaker for t in transcript if t.kind == "bot" and t.speaker != speaker.name]
        if others:
            roster_line = f" Others in the conversation: {', '.join(sorted(set(others)))}."

        # Whether anyone else could possibly answer. The caller knows this
        # from the actual roster; falling back to "has anyone else spoken
        # yet" is only an approximation and gets the opening line of a real
        # multi-speaker conversation wrong (nobody else has spoken *yet*).
        if solo is None:
            solo = not others
        if not solo:
            call = f"You are in a live voice call. Topic: {topic}.{roster_line}"
        else:
            # Nobody else is going to reply -- framing this as a "call" leads
            # the model to wait for answers that never come, or to address
            # people who aren't there. Told plainly, it commits to carrying
            # the whole thing itself.
            call = (
                f"You are talking to a live audience by yourself. Topic: {topic}. "
                "Nobody else is going to reply, so don't ask questions and wait "
                "for an answer, and don't address anyone by name. Riff on it: "
                "develop an angle, tell a story, argue a position -- a new beat "
                "each time, not a restatement of the topic."
            )
        system = (
            f"{speaker.system_prompt()}\n\n"
            f"{call}\n"
            "Reply with ONLY the words you say out loud -- no name prefix, no "
            "stage directions, no emoji, no markdown. One or two sentences.\n"
            "Do not just reword or restate what was said to you -- that includes "
            "the topic itself, which is context, not a line to paraphrase. Bring "
            "something the other line didn't already say: your own take, a "
            "specific detail, a disagreement, a joke that goes somewhere new."
        )
        if image:
            # Said out loud, so the listeners understand why the subject
            # changed to something they cannot see referenced in the text.
            system += (
                "\nThe group is looking at an image together. React to what is "
                "actually in it, in character. Talk about it the way someone "
                "would when everyone is looking at the same picture."
            )
        if addressed_to:
            system += f"\nA real person, {addressed_to}, just spoke to the group. Respond to them directly."
        if stim:
            # A verbal tic. Asking for it in-prompt sounds better than bolting
            # it on afterwards; the director appends it only if this is ignored.
            system += (f"\nYou have a verbal tic: say \"{stim}\" somewhere in this "
                       "reply. Blurt it out the way someone with a catchphrase "
                       "does - do not explain it or build the sentence around it.")

        messages = [{"role": "system", "content": system}]
        if image:
            mime, b64 = image
            messages.append(self._image_message(
                "This is the image we are all looking at.", b64, mime))
        for t in transcript:
            # The model plays one character; everyone else is 'user' input
            # tagged with who said it.
            if t.kind == "bot" and t.speaker == speaker.name:
                messages.append({"role": "assistant", "content": t.text})
            else:
                messages.append({"role": "user", "content": f"{t.speaker}: {t.text}"})

        # The list must end on a `user` turn, or the model is being asked to
        # continue straight out of its own previous line with nothing new to
        # react to. With two or more speakers that never happens -- whoever
        # spoke last always shows up as `user` to whoever answers them. With
        # exactly one active speaker it happens on *every* turn after the
        # first: their own last line is the most recent thing in the
        # transcript, there is no one else's line to interleave, and the
        # model falls back to the one piece of fresh-looking text still in
        # front of it -- the topic line in the system prompt -- and just
        # restates that instead of carrying the thought forward.
        if not messages or messages[-1]["role"] != "user":
            if not transcript:
                opener = ("Start the conversation about what you can see."
                          if image else f"Start the conversation about {topic}.")
            else:
                opener = ("Keep going -- don't just repeat the topic. Build on "
                          "what you just said: a new angle, a specific example, "
                          "a follow-up thought.")
            messages.append({"role": "user", "content": opener})

        return self._clean(self._chat(messages, num_predict, temperature), speaker.name)

    # ---- vision ---------------------------------------------------------------
    def describe_image(self, mime: str, b64: str, caption: str = "",
                       num_predict: int = 120) -> str:
        """Describe an image for people who cannot see it.

        Done once per topic, not once per turn: the description goes into the
        transcript as the announcer's spoken line, so every later speaker
        works from the same text and only one vision call is ever made.
        """
        system = (
            "You are on a live voice call, describing an image to people who "
            "cannot see it. Say what is actually in it: the subject, what is "
            "happening, any text you can read. Two or three sentences of plain "
            "spoken English. No markdown, no bullet points, no preamble like "
            "'the image shows' -- just describe it."
        )
        prompt = "Describe this image."
        if caption:
            prompt += f" It was posted with the caption: {caption}"
        messages = [
            {"role": "system", "content": system},
            self._image_message(prompt, b64, mime),
        ]
        # Low temperature: this is reportage, not personality.
        return self._clean(self._chat(messages, num_predict, 0.3), "narrator")

    # ---- persona authoring ---------------------------------------------------
    def build_persona(self, name: str, samples, note: str = "") -> str:
        """Write a system prompt for `name` from things they actually said.

        Guessing at a personality produces a caricature; real messages carry
        the diction, the opinions and the rhythm. The catch is that they are
        *written* -- Discord shorthand read aloud sounds nothing like the
        person -- so the generated prompt has to separate what to imitate
        (vocabulary, attitude, opinions) from what to drop (formatting).
        """
        corpus = "\n".join(f"- {s}" for s in samples)
        system = (
            "You write character prompts for a voice chatbot. You will be shown "
            "real chat messages written by one person. Infer how they think and "
            "talk, and write a system prompt that makes an LLM sound like them "
            "in conversation.\n\n"
            "Cover: how they talk (vocabulary, sentence length, humour, how "
            "blunt or hedging they are), what they care about and keep coming "
            "back to, their opinions and pet peeves, and any recurring phrases.\n\n"
            "Rules:\n"
            "- Write it as instructions addressed to the model, starting "
            f"\"You are {name}.\"\n"
            "- These are typed messages but the output is SPOKEN. Tell the model "
            "to keep their vocabulary and attitude but speak in ordinary "
            "sentences -- no emoji, no abbreviations spelled out, no links.\n"
            "- Say to keep replies to one or two sentences.\n"
            "- No preamble, no headings, no markdown. Just the prompt itself.\n"
            "- Describe them plainly. Do not editorialise about whether they "
            "are a good person."
        )
        user = f"Messages written by {name}:\n{corpus}"
        if note:
            user += f"\n\nAdditional context about them: {note}"
        # Personas are long-form; the per-line token budget would truncate one
        # into nonsense.
        return self._chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            num_predict=700,
            temperature=0.7,
        ).strip()

    # ---- evolving characters --------------------------------------------------
    def evolve_persona(self, name: str, base: str, dynamic: str, lines,
                       topic: str) -> str:
        """Rewrite one character from what they actually said. -> new prompt.

        Sent with think=True whatever the global setting is: this is an
        analysis rather than a line of dialogue, it happens during an ad break
        where the latency is covered, and it is the one place reasoning
        obviously earns its cost.

        The base prompt is always in the prompt as an anchor. Without it each
        rewrite is derived from the last and the character drifts off with
        nothing pulling it back; with it, twenty topics of drift still
        recognisably starts from who you wrote.
        """
        said = "\n".join(f"- {t}" for t in lines if t.strip())
        if not said.strip():
            return ""
        current = (dynamic or "").strip() or (base or "").strip()
        system = (
            "You maintain the character sheet for a fictional podcast host. "
            "You will be given who they were written to be, who they currently "
            "are, and everything they said in the segment that just ended.\n\n"
            "Rewrite their character sheet so it accounts for how they actually "
            "behaved. Keep what is still true. Let genuine tendencies you can "
            "see in their lines -- an obsession, a running joke, a stance they "
            "keep taking, a way of arguing -- become part of who they are. "
            "Drop traits nothing supports.\n\n"
            "Rules:\n"
            "- Stay recognisably the character in ORIGINAL. You are evolving "
            "them, not replacing them.\n"
            "- Write it as a system prompt, second person, same voice as the "
            "originals. No preamble, no commentary, no markdown headings.\n"
            f"- Hard limit {MAX_PERSONA_CHARS} characters. Shorter is better. "
            "Cut something before you add something.\n"
            "- Do not mention this segment, the topic, or that you rewrote "
            "anything. It is a character sheet, not a report."
        )
        user = (
            f"CHARACTER: {name}\n\n"
            f"ORIGINAL (never changes, this is the anchor):\n{base or '(none)'}\n\n"
            f"CURRENT:\n{current or '(none)'}\n\n"
            f"SEGMENT TOPIC: {topic}\n\n"
            f"WHAT {name} SAID:\n{said}\n\n"
            "Their new character sheet:"
        )
        out = self._chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            num_predict=400,
            temperature=0.7,
            think=True,
        ).strip()
        # Models wrap a rewritten prompt in quotes or a "Here is..." lead-in
        # often enough to be worth handling; and the length rule above is a
        # request, so it is enforced here too.
        out = re.sub(r"^\s*(here(?:'s| is)[^\n:]{0,60}:)\s*", "", out, flags=re.I)
        out = out.strip().strip('"').strip()
        if len(out) > MAX_PERSONA_CHARS:
            cut = out.rfind(". ", 0, MAX_PERSONA_CHARS)
            out = (out[: cut + 1] if cut > MAX_PERSONA_CHARS // 2
                   else out[:MAX_PERSONA_CHARS]).strip()
        return out

    # ---- ad break ---------------------------------------------------------
    def write_ad(self, topic: str, lines, speaker_name: str = "",
                 system: str = "", persona: str = "") -> str:
        """Write the ad read for the break. -> spoken words only.

        `system` is the editable brief from settings; blank falls back to
        DEFAULT_AD_PROMPT. The segment's topic and transcript are appended as
        the user message either way, so a rewritten brief cannot accidentally
        drop them.

        `persona` is the reader's own system prompt, so the spot is read in
        character rather than by an anonymous voice wearing their clone. It
        goes first and the brief goes last: the brief carries the hard format
        rules ("only the words spoken"), and the last instruction is the one a
        model follows when a chatty persona disagrees with it.

        Deliberately not a thinking call. It runs while the audience is
        listening to silence, it is two sentences of nonsense, and reasoning
        about it would only make the break longer.
        """
        heard = "\n".join(f"- {t}" for t in lines if t.strip())
        brief = (system or "").strip() or DEFAULT_AD_PROMPT
        persona = (persona or "").strip()
        system = (f"{persona}\n\nYou are reading a sponsor spot, in character.\n\n{brief}"
                  if persona else brief)
        user = (f"The segment was about: {topic}\n\n"
                f"What was said:\n{heard or '(nothing much)'}\n\n"
                "Write the sponsor read.")
        return self._clean(
            self._chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                num_predict=140,
                temperature=1.0,
                think=False,
            ),
            speaker_name or "narrator")

    # ---- interrupt router ----------------------------------------------------
    def route(self, user_name: str, user_text: str, speakers, transcript) -> str:
        """Pick which persona should answer a real user. Falls back to the
        speaker who was talking most recently."""
        names = [s.name for s in speakers]
        if not names:
            return ""
        if len(names) == 1:
            return names[0]

        roster = "\n".join(f"- {s.name}: {(s.persona or '').strip()[:200]}" for s in speakers)
        history = "\n".join(f"{t.speaker}: {t.text}" for t in transcript[-6:])
        system = (
            "You route a live conversation. Given what a real person just said, "
            "choose which participant should reply. Consider who was addressed by "
            "name, whose expertise or personality fits, and who spoke last.\n"
            f"Participants:\n{roster}\n\n"
            'Answer with JSON only: {"speaker": "<exact name>"}'
        )
        user = f"Recent conversation:\n{history}\n\n{user_name} just said: {user_text}\n\nWho replies?"

        try:
            raw = self._chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                num_predict=40,
                temperature=0.2,
                fmt="json",
                # Never think here. This is a one-word classification on the
                # critical path of answering a real person, and every second
                # of it is silence in the channel.
                think=False,
            )
            pick = json.loads(raw).get("speaker", "")
        except (requests.RequestException, json.JSONDecodeError, KeyError, TypeError, RuntimeError):
            pick = ""

        # Validate against the roster -- a hallucinated name would 404 at
        # the TTS server.
        for n in names:
            if pick and n.lower() == pick.strip().lower():
                return n
        for n in names:
            if pick and n.lower() in pick.lower():
                return n
        # Someone addressed by name in the utterance itself beats a guess.
        for n in names:
            if re.search(rf"\b{re.escape(n)}\b", user_text, re.I):
                return n
        for t in reversed(transcript):
            if t.kind == "bot" and t.speaker in names:
                return t.speaker
        return names[0]


class OllamaClient(BaseLLM):
    def __init__(self, base_url: str, model: str, timeout: int = 180):
        super().__init__(model, timeout)
        self.base_url = base_url.rstrip("/")
        # Whether to let the model reason before answering. Set from settings
        # by app.rebuild_llm(); this client outlives any one setting change,
        # so it is an attribute rather than a constructor argument.
        self.think = False

    def models(self):
        try:
            r = self.http.get(f"{self.base_url}/api/tags", timeout=10)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
        except requests.RequestException:
            return []

    def available(self):
        if not self.models():
            return False, f"Ollama not reachable at {self.base_url}"
        return True, ""

    def supports_images(self):
        return True

    def _image_message(self, text, b64, mime):
        # Ollama takes bare base64 in a sibling `images` list -- no data URI,
        # no mime type. A text-only model ignores the field and answers from
        # the text alone, which is why this cannot hard-fail.
        return {"role": "user", "content": text, "images": [b64]}

    def _post(self, messages, num_predict, temperature, fmt=None, think=False):
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": KEEP_ALIVE,
            # Off by default: a reasoning model otherwise spends the whole
            # budget in `thinking` and hands back empty content, and the
            # latency shows up as dead air in a live call. Models with no
            # thinking mode ignore the field either way.
            "think": bool(think),
            "options": {"num_predict": num_predict, "temperature": temperature},
        }
        if fmt:
            payload["format"] = fmt
        r = self.http.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
        r.raise_for_status()
        return r.json().get("message", {})

    def _chat(self, messages, num_predict=80, temperature=0.9, fmt=None,
              think=None) -> str:
        want = self.think if think is None else think
        # Thinking has to fit inside num_predict alongside the answer, so a
        # request that asks for it is given room for it up front rather than
        # coming back empty and paying for a second round trip every line.
        budget = num_predict * THINK_BUDGET if want else num_predict
        msg = self._post(messages, budget, temperature, fmt, want)
        content = (msg.get("content") or "").strip()
        if content:
            return content
        # Some builds think regardless of the flag. Retry once with enough
        # room to finish reasoning and still emit an answer.
        if msg.get("thinking") and budget == num_predict:
            msg = self._post(messages, num_predict * THINK_BUDGET, temperature,
                             fmt, want)
            content = (msg.get("content") or "").strip()
        return content


class OpenAIClient(BaseLLM):
    """OpenAI-compatible chat completions.

    The key is read from the environment only -- never entered in, or stored
    by, the web UI.
    """

    def __init__(self, base_url: str, model: str, timeout: int = 180,
                 api_key_env: str = "OPENAI_API_KEY"):
        super().__init__(model, timeout)
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        # Newer models reject max_tokens in favour of max_completion_tokens,
        # and some reject a non-default temperature. Both are discovered from
        # the first 400 and remembered for the rest of the session.
        self._token_param = "max_completion_tokens"
        self._send_temperature = True

    @property
    def api_key(self):
        return os.environ.get(self.api_key_env, "")

    def available(self):
        if not self.api_key:
            return False, (f"No API key -- put {self.api_key_env}=... in .env "
                           "(or export it) and restart the app.")
        return True, ""

    def supports_images(self):
        return True

    def _image_message(self, text, b64, mime):
        # OpenAI wants a data URI inside a content-part list.
        return {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {"type": "image_url",
                 "image_url": {"url": f"data:{mime or 'image/png'};base64,{b64}"}},
            ],
        }

    def models(self):
        if not self.api_key:
            return []
        try:
            r = self.http.get(f"{self.base_url}/models",
                              headers={"Authorization": f"Bearer {self.api_key}"}, timeout=15)
            r.raise_for_status()
            return sorted(m["id"] for m in r.json().get("data", []))
        except (requests.RequestException, KeyError, TypeError):
            return []

    def _build(self, messages, num_predict, temperature, fmt):
        payload = {"model": self.model, "messages": messages,
                   self._token_param: num_predict}
        if self._send_temperature:
            payload["temperature"] = temperature
        if fmt == "json":
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _chat(self, messages, num_predict=80, temperature=0.9, fmt=None,
              think=None) -> str:
        # think is accepted and ignored: it maps onto Ollama's `think` field,
        # which has no equivalent here. Reasoning on an OpenAI-compatible
        # endpoint is a property of the model you pick, not of the request.
        # Accepting it is not optional -- route() passes think=False on every
        # provider, and a signature that refuses it fails the turn.
        if not self.api_key:
            raise RuntimeError(f"{self.api_key_env} is not set")
        headers = {"Authorization": f"Bearer {self.api_key}"}

        for _ in range(3):
            payload = self._build(messages, num_predict, temperature, fmt)
            r = self.http.post(f"{self.base_url}/chat/completions", json=payload,
                               headers=headers, timeout=self.timeout)
            if r.status_code == 400:
                # Adapt to whichever parameter shape this model wants and
                # retry, rather than losing the turn.
                err = r.text.lower()
                if "max_tokens" in err and self._token_param != "max_tokens":
                    self._token_param = "max_tokens"
                    continue
                if "max_completion_tokens" in err and self._token_param != "max_completion_tokens":
                    self._token_param = "max_completion_tokens"
                    continue
                if "temperature" in err and self._send_temperature:
                    self._send_temperature = False
                    continue
            r.raise_for_status()
            choices = r.json().get("choices") or []
            if not choices:
                return ""
            return (choices[0].get("message", {}).get("content") or "").strip()
        return ""


def make_llm(settings) -> BaseLLM:
    """Build whichever client the settings ask for."""
    if getattr(settings, "provider", PROVIDER_OLLAMA) == PROVIDER_OPENAI:
        return OpenAIClient(settings.openai_url, settings.openai_model)
    return OllamaClient(settings.ollama_url, settings.ollama_model)
