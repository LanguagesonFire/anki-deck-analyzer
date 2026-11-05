"""The Claude calls.

One small call identifies the deck, then one call per judged criterion. Every
criterion call reads the same evidence pack, so the pack sits behind a prompt
cache breakpoint and is charged at full price once and at a tenth thereafter.
"""

from __future__ import annotations

import inspect
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import anthropic

from .apkg import Deck
from .criteria import Criterion
from .evidence import DeckProfile, profile_prompt

#: The everyday model. Sonnet finds the same structural problems as Opus at a
#: fraction of the cost, which keeps a single deck check cheap enough that
#: someone will actually run it.
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_EFFORT = "high"

#: What the hidden --publish alias selects, kept for reproduce commands from
#: before 1.7. Choose it with --model; Opus is sharper on specific errors.
PUBLISH_MODEL = "claude-opus-5-5"

#: Published rates per million tokens, for the cost estimate the tool prints:
#: input, output, and cache reads where they aren't the usual tenth of input.
#: Older models stay listed so a reproduce command from an earlier report
#: still prints its cost. Unknown models fall back to no estimate rather than
#: a wrong one.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5-1": (10.0, 50.0),
}

FRAMEWORK = """\
You are assessing a premade Anki deck for language learners against a fixed \
published framework. You are one part of a tool: a separate pass has already \
measured the deck file, and you are given those measurements alongside samples \
of the actual cards.

How to judge:

- Work from what is in front of you. Quote the cards. A claim you cannot point \
at is not a finding.
- The measurements are counts taken from the file. Trust them over your \
impression, and say so when a sample contradicts them.
- You are seeing a sample, not the deck. Say "in the sample" when that is what \
you mean, and let that limit how confident you are.
- Judge the deck for the learner it is aimed at. A beginner deck and a deck for \
advanced readers fail in different ways.
- Score against the rubric, not against perfection. A 7 means good. A 10 \
means the deck fully meets the criterion for the learner it is aimed at, with \
no material weakness the evidence shows. Every deck could be improved \
somewhere; that alone is no reason to withhold a 10.
- A deduction needs a material reason specific to this criterion: something \
that changes what the learner learns or how easily they study. A cosmetic \
inconsistency, or optional detail kept out of the way such as a collapsed \
table, does not cost a point on its own. You may still list it as a minor \
weakness, and an empty weaknesses list is fine.
- Keep score and confidence apart. A sample that shows no material weakness \
supports a high score at medium or low confidence; uncertainty alone is no \
reason to score lower.
- Do not be generous either: a score the evidence does not support is as wrong \
as a deduction it does not justify. If the deck is careful work, say so plainly.
- You cannot hear audio or see images. Never judge either.

How to write:

- Plain American English, second person where you address the reader.
- Specific over emphatic. "The first 40 cards introduce 3.1 new words each" \
beats "progression is poor".
- No marketing language, no hedging filler, no exclamation marks.
"""

PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "target_language": {
            "type": "string",
            "description": "The language being learned, in English, e.g. 'Italian'.",
        },
        "source_language": {
            "type": "string",
            "description": "The language translations and explanations are written in.",
        },
        "deck_kind": {
            "type": "string",
            "description": (
                "One short phrase: 'sentence deck', 'vocabulary list', "
                "'cloze course', 'dialogue-based course', 'mixed', or similar."
            ),
        },
        "level_range": {
            "type": "string",
            "description": "Level the deck appears aimed at, e.g. 'A1-B1' or 'unclear'.",
        },
        "notes": {
            "type": "string",
            "description": (
                "One or two sentences on anything about the layout a later pass "
                "should know. Empty string if there is nothing."
            ),
        },
        "note_types": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                    "target_field_index": {
                        "type": ["integer", "null"],
                        "description": (
                            "Zero-based index of the field holding the "
                            "target-language text, or null if there is none."
                        ),
                    },
                    "translation_field_index": {"type": ["integer", "null"]},
                    "role": {
                        "type": "string",
                        "enum": ["material", "explanation"],
                        "description": (
                            "'material' when the card presents language for the "
                            "learner to learn: sentences, vocabulary, dialogues, "
                            "cloze and quiz items. 'explanation' when its main "
                            "text explains the language or the course: grammar "
                            "rules, usage notes, announcements, instructions."
                        ),
                    },
                    "lemma_field_indices": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": (
                            "Zero-based indices of fields that state the "
                            "DICTIONARY FORM of a word the card teaches, separate "
                            "from the text being studied: a 'Verb Infinitive', "
                            "'Lemma', 'Base form' or 'Headword' field, for example. "
                            "Not the studied text itself, not a translation. "
                            "Empty when there is none."
                        ),
                    },
                    "sentence_field_index": {
                        "type": ["integer", "null"],
                        "description": (
                            "Zero-based index of the field holding a full "
                            "target-language EXAMPLE SENTENCE, when that is a "
                            "different field from the one being recalled. On a "
                            "vocabulary note type with a headword plus an example, "
                            "this is the example. Null when the target field is "
                            "already the sentence, or when there is no sentence."
                        ),
                    },
                },
                "required": [
                    "id",
                    "name",
                    "target_field_index",
                    "translation_field_index",
                    "sentence_field_index",
                    "role",
                    "lemma_field_indices",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "target_language",
        "source_language",
        "deck_kind",
        "level_range",
        "notes",
        "note_types",
    ],
    "additionalProperties": False,
}

CRITERION_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {
            "type": "integer",
            "enum": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
            "description": "Score for this criterion on the rubric given.",
        },
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
            "description": (
                "How far the evidence actually settles the question. Low when the "
                "sample cannot show what the criterion asks about."
            ),
        },
        "verdict": {
            "type": "string",
            "description": "One or two sentences. The finding, not a summary of the criterion.",
        },
        "strengths": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Up to four specific things the deck gets right here. May be empty.",
        },
        "weaknesses": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Up to four specific problems. May be empty.",
        },
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "quote": {
                        "type": "string",
                        "description": (
                            "Text copied from one card, or a measurement copied from "
                            "the pack. Copy the card's text only, without the pack's "
                            "markers such as <first seen here: …>. To show several "
                            "neighboring cards, quote them all and give the first "
                            "card's position; say in 'shows' how many cards it spans."
                        ),
                    },
                    "position": {
                        "type": ["integer", "null"],
                        "description": "The [#n] position shown with the note, or null.",
                    },
                    "shows": {
                        "type": "string",
                        "description": "What this one piece of evidence demonstrates.",
                    },
                },
                "required": ["quote", "position", "shows"],
                "additionalProperties": False,
            },
            "description": "Two to five items. Each must be traceable to the pack.",
        },
    },
    "required": ["score", "confidence", "verdict", "strengths", "weaknesses", "evidence"],
    "additionalProperties": False,
}

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {
            "type": "string",
            "description": "One sentence: what this deck is and whether it is worth studying.",
        },
        "verdict": {
            "type": "string",
            "description": (
                "Two short paragraphs. The first says what the deck does well and "
                "badly. The second says who should use it and who should not."
            ),
        },
        "best_for": {"type": "array", "items": {"type": "string"}},
        "not_for": {"type": "array", "items": {"type": "string"}},
        "fixable": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Weaknesses a learner could repair themselves — templates, missing "
                "audio, subdeck splits. Describe the step in Anki's own terms. Name an "
                "add-on only when you are sure it does exactly that job; otherwise "
                "describe what to do without naming one."
            ),
        },
        "not_fixable": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Weaknesses that would mean rewriting the material.",
        },
    },
    "required": ["headline", "verdict", "best_for", "not_for", "fixable", "not_fixable"],
    "additionalProperties": False,
}


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write: int = 0
    cache_read: int = 0
    calls: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_write += other.cache_write
        self.cache_read += other.cache_read
        self.calls += other.calls

    def cost(self, model: str) -> float | None:
        price = PRICES.get(model)
        if price is None:
            return None
        inp, out = price[:2]
        cached = price[2] if len(price) > 2 else 0.1 * inp
        billed = (self.input_tokens + 1.25 * self.cache_write) * inp
        billed += self.cache_read * cached + self.output_tokens * out
        return billed / 1_000_000


@dataclass
class Judgement:
    key: str
    score: int
    confidence: str
    verdict: str
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    error: str | None = None
    #: Every score this criterion came back with, when judged more than once.
    #: One reading of a deck is one opinion; the spread is worth publishing.
    scores_seen: list[int] = field(default_factory=list)

    @property
    def spread(self) -> int:
        return max(self.scores_seen) - min(self.scores_seen) if self.scores_seen else 0


class AnalysisError(Exception):
    pass


class Analyzer:
    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        model: str = DEFAULT_MODEL,
        effort: str = DEFAULT_EFFORT,
        max_tokens: int = 24000,
    ) -> None:
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.usage = Usage()
        self._lock = threading.Lock()
        self._supports_effort = self._probe("output_config")

    def _probe(self, param: str) -> bool:
        try:
            sig = inspect.signature(self.client.messages.create)
        except (TypeError, ValueError):  # pragma: no cover
            return False
        return param in sig.parameters

    # -- transport --------------------------------------------------------

    def _call(self, system: list[dict], content: list[dict], schema: dict) -> dict:
        output_config: dict = {"format": {"type": "json_schema", "schema": schema}}
        if self._supports_effort and self.effort:
            output_config["effort"] = self.effort

        kwargs = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            thinking={"type": "adaptive"},
            output_config=output_config,
        )

        try:
            with self.client.messages.stream(**kwargs) as stream:
                message = stream.get_final_message()
        except anthropic.BadRequestError as exc:
            # Older or non-Anthropic endpoints may reject adaptive thinking.
            if "thinking" not in str(exc):
                raise
            kwargs.pop("thinking")
            with self.client.messages.stream(**kwargs) as stream:
                message = stream.get_final_message()

        if getattr(message, "stop_reason", None) == "refusal":
            raise AnalysisError(
                "The model declined to answer. This is unexpected for deck analysis; "
                "if it persists, please report the deck that triggered it."
            )

        u = message.usage
        with self._lock:
            self.usage.add(
                Usage(
                    input_tokens=getattr(u, "input_tokens", 0) or 0,
                    output_tokens=getattr(u, "output_tokens", 0) or 0,
                    cache_write=getattr(u, "cache_creation_input_tokens", 0) or 0,
                    cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
                    calls=1,
                )
            )

        text = next((b.text for b in message.content if b.type == "text"), "")
        if not text.strip():
            raise AnalysisError("The model returned an empty response.")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise AnalysisError(f"Could not parse the model's response: {exc}") from exc

    # -- step 1: what is this deck ----------------------------------------

    def profile(self, deck: Deck) -> DeckProfile:
        data = self._call(
            system=[{"type": "text", "text": FRAMEWORK}],
            content=[
                {
                    "type": "text",
                    "text": (
                        "Identify this Anki deck before it is assessed.\n\n"
                        "For every note type, say which field holds the "
                        "target-language text a learner is meant to read or produce, "
                        "and which holds the translation. Use the zero-based indices "
                        "shown in the field list. Use null where a note type has "
                        "neither — an announcement or a grammar table, for example.\n\n"
                        + profile_prompt(deck)
                    ),
                }
            ],
            schema=PROFILE_SCHEMA,
        )
        targets: dict[int, int] = {}
        translations: dict[int, int] = {}
        sentences: dict[int, int] = {}
        explanation: set[int] = set()
        lemma_fields: dict[int, list[int]] = {}
        for entry in data.get("note_types", []):
            nt_id = entry.get("id")
            if nt_id is None:
                continue
            if entry.get("role") == "explanation":
                explanation.add(int(nt_id))
            indices = [i for i in entry.get("lemma_field_indices") or [] if isinstance(i, int)]
            if indices:
                lemma_fields[int(nt_id)] = indices
            if isinstance(entry.get("target_field_index"), int):
                targets[int(nt_id)] = entry["target_field_index"]
            if isinstance(entry.get("translation_field_index"), int):
                translations[int(nt_id)] = entry["translation_field_index"]
            if isinstance(entry.get("sentence_field_index"), int):
                sentences[int(nt_id)] = entry["sentence_field_index"]
        return DeckProfile(
            target_language=data.get("target_language", "unknown"),
            source_language=data.get("source_language", "unknown"),
            deck_kind=data.get("deck_kind", "unknown"),
            level_range=data.get("level_range", "unknown"),
            target_fields=targets,
            translation_fields=translations,
            sentence_fields=sentences,
            explanation_types=explanation,
            lemma_fields=lemma_fields,
            notes=data.get("notes", ""),
        )

    # -- step 2: one criterion at a time ----------------------------------

    def _criterion_content(self, pack: str, criterion: Criterion) -> list[dict]:
        question = (
            f"## The criterion you are judging: {criterion.title}\n\n"
            f"{criterion.definition}\n\n"
            f"### Scoring rubric (0-10)\n{criterion.rubric}\n\n"
            f"### What answers this\n"
            f"Look at {criterion.evidence}.\n\n"
            "Judge only this criterion. Other criteria are being judged separately, "
            "so do not spend the score on a problem that belongs to one of them. "
            "Every item in `evidence` must be copied from the pack above."
        )
        return [
            {"type": "text", "text": pack, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": question},
        ]

    def judge(self, pack: str, criterion: Criterion) -> Judgement:
        try:
            data = self._call(
                system=[
                    {
                        "type": "text",
                        "text": FRAMEWORK,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                content=self._criterion_content(pack, criterion),
                schema=CRITERION_SCHEMA,
            )
        except Exception as exc:  # one failed criterion should not lose the run
            return Judgement(
                key=criterion.key,
                score=0,
                confidence="low",
                verdict="",
                error=str(exc),
            )
        return Judgement(
            key=criterion.key,
            score=int(data.get("score", 0)),
            confidence=data.get("confidence", "low"),
            verdict=data.get("verdict", ""),
            strengths=list(data.get("strengths", []))[:4],
            weaknesses=list(data.get("weaknesses", []))[:4],
            evidence=list(data.get("evidence", []))[:5],
        )

    def judge_repeatedly(self, pack: str, criterion: Criterion, times: int) -> Judgement:
        """Judge one criterion several times and keep the middle reading.

        A single pass is one opinion, and the same deck can come back a point
        apart on two runs. Taking the median of an odd number of passes makes
        the published figure stable, and the spread is reported rather than
        hidden — it is the honest error bar on an LLM judgment.
        """
        if times <= 1:
            return self.judge(pack, criterion)

        passes = [self.judge(pack, criterion) for _ in range(times)]
        good = [p for p in passes if not p.error]
        if not good:
            return passes[0]

        good.sort(key=lambda p: p.score)
        chosen = good[len(good) // 2]
        chosen.scores_seen = [p.score for p in good]
        return chosen

    def judge_all(
        self,
        pack: str,
        criteria: list[Criterion],
        workers: int = 4,
        progress=None,
        repeat: int = 1,
    ) -> dict[str, Judgement]:
        """Judge every criterion, warming the shared cache on the first one."""
        results: dict[str, Judgement] = {}
        if not criteria:
            return results

        # The first criterion runs alone so it writes the shared prompt cache;
        # firing all of them at once would make every call pay full price.
        first, rest = criteria[0], criteria[1:]
        results[first.key] = self.judge_repeatedly(pack, first, repeat)
        if progress:
            progress(first)

        if rest:
            with ThreadPoolExecutor(max_workers=min(workers, len(rest))) as pool:
                futures = {
                    pool.submit(self.judge_repeatedly, pack, c, repeat): c for c in rest
                }
                for future in as_completed(futures):
                    criterion = futures[future]
                    results[criterion.key] = future.result()
                    if progress:
                        progress(criterion)
        return results

    # -- step 3: pull it together -----------------------------------------

    def summarize(
        self, pack: str, scored: list[tuple[Criterion, int, str]], total: float
    ) -> dict:
        lines = [
            f"- {c.title} ({c.weight:.0%}): {score}/10 — {verdict}"
            for c, score, verdict in scored
        ]
        question = (
            "Every criterion has now been judged. Write the verdict a reader sees "
            "first.\n\n"
            f"Weighted total: {total:.0f} out of 100.\n\n"
            "Per-criterion results:\n" + "\n".join(lines) + "\n\n"
            "Separate what a learner could fix themselves — card templates, missing "
            "audio added with a text-to-speech add-on, splitting into subdecks — from what "
            "would mean rewriting the material. That distinction is the point of the "
            "framework: the first five criteria are weighted at 80% because they are "
            "the expensive ones to get wrong.\n\n"
            "Do not restate the scores. Say what they mean for someone deciding "
            "whether to study this deck."
        )
        try:
            return self._call(
                system=[
                    {
                        "type": "text",
                        "text": FRAMEWORK,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                content=[
                    {"type": "text", "text": pack, "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": question},
                ],
                schema=SUMMARY_SCHEMA,
            )
        except Exception as exc:
            return {
                "headline": "",
                "verdict": f"(The closing summary could not be generated: {exc})",
                "best_for": [],
                "not_for": [],
                "fixable": [],
                "not_fixable": [],
            }
