"""The pipeline, from a file on disk to a scored result."""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import __version__
from . import levels
from .apkg import Deck, read_package
from .criteria import CRITERIA
from .evidence import SAMPLE_SEED, DeckProfile, build_pack
from .frequency import default_list
from .llm import DEFAULT_EFFORT, DEFAULT_MODEL, Analyzer
from .metrics import Metrics, choose_measured_fields, compute, with_headwords
from .scoring import Result, combine


@dataclass
class Provenance:
    """Everything a reader needs to reproduce a published score."""

    version: str
    model: str
    effort: str
    repeat: int
    sample_seed: int
    deck_sha256: str
    criteria_judged: list[str]
    complete: bool
    #: How words were counted ("lemma:simplemma 1.1.2", "surface",
    #: "character"). Scores made under different methods are not comparable.
    word_counting: str = ""
    #: The frequency list the vocabulary was held against, or "" for none.
    frequency_list: str = ""
    #: What --frequency-list was given, when it was not the default.
    frequency_option: str = ""

    def command(self, deck_name: str = "deck.apkg") -> str:
        base = f'anki-deck-analyzer "{deck_name}"'
        parts = [base]
        if self.model != DEFAULT_MODEL:
            parts.append(f"--model {self.model}")
        if self.effort != DEFAULT_EFFORT:
            parts.append(f"--effort {self.effort}")
        if self.repeat > 1:
            parts.append(f"--repeat {self.repeat}")
        if not self.complete:
            parts.append("--only " + ",".join(self.criteria_judged))
        if self.frequency_option:
            parts.append(f'--frequency-list "{self.frequency_option}"')
        return " ".join(parts)


@dataclass
class Analysis:
    deck: Deck
    profile: DeckProfile
    metrics: Metrics
    result: Result
    pack: str
    model: str
    cost: float | None
    provenance: Provenance | None = None
    #: The level tags found on the notes, if any.
    level_tags: levels.LevelTags | None = None
    #: The deck measured in level-tag order, when its default order ignores
    #: the tags.
    study_path: levels.StudyPath | None = None


def analyze(
    path: str,
    analyzer: Analyzer,
    summarize: bool = True,
    workers: int = 4,
    progress=None,
    only: set[str] | None = None,
    repeat: int = 1,
    frequency_list: str | None = None,
) -> Analysis:
    """Read, measure, judge and score one deck.

    `progress` is called with a short status string at each step.
    """

    def say(message: str) -> None:
        if progress:
            progress(message)

    say("Reading the deck file")
    deck = read_package(path)

    say(f"Identifying {len(deck.notes):,} notes")
    profile = analyzer.profile(deck)

    say(f"Measuring the deck ({profile.target_language})")
    # Progression is a property of the sentences a learner meets, so a note
    # type carrying both a headword and an example is measured on the example.
    measured, field_warnings = choose_measured_fields(
        deck, profile.target_fields, profile.sentence_fields
    )
    metrics = compute(
        deck,
        measured,
        extra_warnings=field_warnings,
        language=profile.target_language,
        explanation_language=profile.source_language,
        frequency_list=frequency_list,
        explanation_types=profile.explanation_types,
        lemma_fields=with_headwords(profile.lemma_fields, profile.target_fields, measured),
    )
    tags, path = _study_path(
        deck,
        metrics,
        measured,
        say,
        language=profile.target_language,
        explanation_language=profile.source_language,
        frequency_list=frequency_list,
        explanation_types=profile.explanation_types,
        lemma_fields=with_headwords(profile.lemma_fields, profile.target_fields, measured),
    )

    pack = build_pack(deck, metrics, profile, measured, tags=tags, path=path)

    # Audio and images are measured, not judged; everything else goes to the model.
    judged = [c for c in CRITERIA if not c.measured]
    if only:
        judged = [c for c in judged if c.key in only]
    done = 0

    def tick(criterion) -> None:
        nonlocal done
        done += 1
        say(f"Judged {criterion.title} ({done}/{len(judged)})")

    say(f"Judging {len(judged)} criteria")
    judgements = analyzer.judge_all(
        pack, judged, workers=workers, progress=tick, repeat=repeat
    )

    result = combine(judgements, metrics, deck, profile)

    if summarize:
        say("Writing the verdict")
        scored = [
            (s.criterion, s.score, s.verdict or s.basis)
            for s in result.scores
            if s.assessed
        ]
        result.summary = analyzer.summarize(pack, scored, result.total)

    complete = len(judged) == len([c for c in CRITERIA if not c.measured])
    provenance = Provenance(
        version=__version__,
        model=analyzer.model,
        effort=analyzer.effort,
        repeat=repeat,
        sample_seed=SAMPLE_SEED,
        deck_sha256=deck.file_sha256,
        criteria_judged=[c.key for c in judged],
        complete=complete,
        word_counting=metrics.lexicon.label,
        frequency_list=metrics.frequency.source,
        frequency_option=(
            os.path.basename(frequency_list)
            if frequency_list
            and frequency_list != default_list(metrics.lexicon.language)
            else ""
        ),
    )

    return Analysis(
        deck=deck,
        profile=profile,
        metrics=metrics,
        result=result,
        pack=pack,
        model=analyzer.model,
        cost=analyzer.usage.cost(analyzer.model),
        provenance=provenance,
        level_tags=tags,
        study_path=path,
    )


def _study_path(deck: Deck, metrics: Metrics, measured: dict[int, int], say=None, **options):
    """Level tags on the deck, and the deck measured in their order.

    A deck tagged by level and shipped in another order is often meant to be
    studied level by level. The second reading uses the same fields and the
    same word counting as the first, so the two can be set side by side.
    """
    tags = levels.detect(deck)
    if tags is None or tags.follows_default_order:
        return tags, None
    if say:
        say(f"Measuring the deck again in level-tag order ({tags.family})")
    tag_deck = levels.in_tag_order(deck, tags)
    tag_metrics = compute(tag_deck, measured, lexicon=metrics.lexicon, **options)
    return tags, levels.StudyPath(tags=tags, deck=tag_deck, metrics=tag_metrics)


def preview(
    path: str,
    language: str | None = None,
    fields: dict[str, str] | None = None,
    frequency_list: str | None = None,
    explanation_types: list[str] | None = None,
    lemma_fields: dict[str, list[str]] | None = None,
    explanation_language: str = "English",
) -> tuple[Deck, Metrics, str]:
    """Everything the tool can do without calling the API.

    Used by `--dry-run`, so you can see exactly what would be sent. Without
    the identification call the target language is unknown, so words are
    counted by spelling and no frequency list is used unless `language` names
    it. Nor are the target-language fields known: every note is measured on
    its longest field, which is often the English one, unless `fields` maps
    note type names to the field holding the target language. Likewise
    `explanation_types` names note types that explain rather than teach, and
    `lemma_fields` maps note type names to fields stating a dictionary form —
    what the identification call would otherwise decide.
    """
    deck = read_package(path)
    target_fields: dict[int, int] = {}
    for type_name, name in (fields or {}).items():
        # Several note types can share a name ("Basic" twice is common);
        # each one that has the field is measured on it.
        same = [nt for nt in deck.notetypes.values() if nt.name == type_name]
        hits = [nt for nt in same if name in nt.fields]
        if same and not hits:
            raise ValueError(f"note type {type_name!r} has no field {name!r}")
        for nt in hits:
            target_fields[nt.id] = nt.fields.index(name)
    by_name = {nt.name: nt for nt in deck.notetypes.values()}
    explain: set[int] = set()
    for name in explanation_types or ():
        if name not in by_name:
            raise ValueError(f"no note type named {name!r}")
        explain.add(by_name[name].id)
    stated: dict[int, list[int]] = {}
    for name, field_names in (lemma_fields or {}).items():
        nt = by_name.get(name)
        if nt is None:
            raise ValueError(f"no note type named {name!r}")
        for f in field_names:
            if f not in nt.fields:
                raise ValueError(f"note type {name!r} has no field {f!r}")
        stated[nt.id] = [nt.fields.index(f) for f in field_names]
    options = dict(
        language=language,
        explanation_language=explanation_language,
        frequency_list=frequency_list,
        explanation_types=explain,
        lemma_fields=stated,
    )
    metrics = compute(deck, target_fields, **options)
    tags, path = _study_path(deck, metrics, target_fields, **options)
    profile = DeckProfile(
        target_language=language or "unknown",
        source_language=explanation_language,
        target_fields=target_fields,
        explanation_types=explain,
        lemma_fields=stated,
    )
    return deck, metrics, build_pack(deck, metrics, profile, tags=tags, path=path)
