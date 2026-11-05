"""Turning per-criterion scores into the weighted total.

Two criteria are scored here rather than by the model. The tool reads card
text; it cannot listen to a recording or look at a picture. So audio and images
are scored on measured coverage, and the report says plainly that presence is
what was measured.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .criteria import CRITERIA, EXTRA_FEATURES, Criterion, NON_NEGOTIABLE
from .llm import Judgement
from . import textutil as tx
from .metrics import Metrics

#: Image coverage at which the criterion is considered fully met. The article
#: treats images as useful for concrete vocabulary and largely irrelevant for
#: grammar and long sentences, so a deck does not need one on every card.
IMAGE_FULL_COVERAGE = 0.50

GRADES = (
    (85, "Excellent", "A carefully built course. Worth your review time."),
    (70, "Good", "Solid material with fixable gaps."),
    (55, "Mixed", "Usable, but you will be working around its weaknesses."),
    (40, "Weak", "Real problems in the material itself. Keep looking."),
    (0, "Poor", "The fundamentals are not there."),
)


@dataclass
class Score:
    criterion: Criterion
    score: int
    confidence: str
    verdict: str
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    #: Filled in for the two measured criteria.
    basis: str = ""
    assessed: bool = True
    error: str | None = None
    #: Scores from every pass, when the criterion was judged more than once.
    scores_seen: list[int] = field(default_factory=list)

    @property
    def spread(self) -> int:
        return max(self.scores_seen) - min(self.scores_seen) if self.scores_seen else 0

    @property
    def weighted(self) -> float:
        return self.score * self.criterion.weight * 10


@dataclass
class Result:
    scores: list[Score]
    total: float
    grade: str
    grade_note: str
    non_negotiable_total: float
    nice_to_have_total: float
    unassessed: list[str] = field(default_factory=list)
    extras: list[tuple[str, bool, str]] = field(default_factory=list)
    #: Filled in after the closing summary call.
    summary: dict = field(default_factory=dict)

    def by_key(self, key: str) -> Score | None:
        return next((s for s in self.scores if s.criterion.key == key), None)


#: Appended to both measured criteria. The tool counts files; it cannot judge
#: what is in them, and the score should say so where the score appears.
QUALITY = (
    " This is presence, not quality: the tool reads the cards and cannot listen "
    "to them, so a studio recording by a native speaker and a decade-old robot "
    "voice score the same here. Play a few cards yourself before you trust it."
)

IMAGE_QUALITY = (
    " Presence, not quality: the tool cannot see the pictures, so it does not "
    "know whether they illustrate the word or are decoration."
)


def score_audio(metrics: Metrics) -> tuple[int, str]:
    coverage = metrics.audio_coverage
    if coverage <= 0:
        return 0, "No sound files and no {{tts}} directive on any card."
    if metrics.uses_tts_directive and metrics.notes_with_audio == 0:
        return (
            9,
            "The templates speak the cards through Anki's built-in {{tts}} "
            "directive, so there are no recordings in the deck at all: what you "
            "hear is whichever voice your own device provides." + QUALITY,
        )
    score = min(10, round(coverage * 10))
    if metrics.silent_notetypes:
        speaking = metrics.note_count - metrics.audio_excluded_notes
        basis = (
            f"{metrics.notes_with_audio:,} of {speaking:,} notes ({coverage:.0%}) "
            "carry a sound file, counting only the card types that use audio at "
            f"all. {metrics.audio_excluded_notes:,} notes "
            f"({metrics.audio_excluded_notes / max(1, metrics.note_count):.0%} of "
            "the deck) belong to types the author left silent throughout — "
            + ", ".join(metrics.silent_notetypes)
            + " — and have nothing to pronounce. Across every note the figure is "
            f"{metrics.audio_coverage_raw:.0%}. "
            f"{metrics.distinct_audio_files:,} distinct recordings."
        )
    else:
        basis = (
            f"{metrics.notes_with_audio:,} of {metrics.note_count:,} notes "
            f"({coverage:.0%}) carry a sound file, "
            f"{metrics.distinct_audio_files:,} distinct recordings."
        )
    if metrics.uses_tts_directive:
        basis += " The templates also use Anki's {{tts}} directive."
    return score, basis + QUALITY


def score_images(metrics: Metrics) -> tuple[int, str]:
    coverage = metrics.image_coverage
    if coverage <= 0:
        return 0, "No image references on any card."
    score = min(10, round(coverage / IMAGE_FULL_COVERAGE * 10))
    return score, (
        f"{metrics.notes_with_images:,} of {metrics.note_count:,} notes "
        f"({coverage:.0%}) carry an image, "
        f"{metrics.distinct_image_files:,} distinct files. Full marks at "
        f"{IMAGE_FULL_COVERAGE:.0%}, since images suit concrete vocabulary and "
        "add little to grammar or long sentences." + IMAGE_QUALITY
    )


def production_share(deck, metrics: Metrics, profile=None) -> tuple[float, str]:
    """How much of the deck asks you to produce the target language.

    The article's point is that production is worth having but tedious on every
    sentence, so what matters is whether it is *selective*. Three states are
    worth telling apart, and none of them is a defect on its own:

      none       a comprehension-first deck. Nothing is forced on you.
      selective  some cards ask for production. What the article recommends.
      every note production on every sentence, which is the tiring case.

    Cards-per-note cannot tell these apart: cloze deletions inflate it without
    any production, and a deck that ships its production cards as a separate
    note type shows 1.00. So this reads the templates instead, and calls a card
    production when its FRONT carries the translation rather than the target
    language.
    """
    producing_notes = 0
    detected = False

    for nt in deck.notetypes.values():
        count = metrics.notetype_counts.get(nt.name, 0)
        if not count:
            continue
        target_idx = profile.target_fields.get(nt.id) if profile else None
        trans_idx = profile.translation_fields.get(nt.id) if profile else None
        # Both roles have to be known. Guessing from one of them turns a cloze
        # card, where the target text is the prompt, into a false positive.
        if target_idx is None or trans_idx is None:
            continue
        if not (0 <= trans_idx < len(nt.fields) and 0 <= target_idx < len(nt.fields)):
            continue
        detected = True
        trans_name, target_name = nt.fields[trans_idx], nt.fields[target_idx]
        for tmpl in nt.templates:
            # A {{type:Field}} box on the front is where the learner types the
            # answer, not text they can read off, so it is left out here and
            # counts as production of that field.
            typed = tx.typed_fields(tmpl.qfmt)
            front = [f for f in tx.shown_fields(tmpl.qfmt) if f not in typed]
            # Production means the prompt is the translation and the target
            # language is not already sitting there to be read off.
            typed_answer = target_name in typed and not nt.is_cloze
            if (trans_name in front and target_name not in front) or typed_answer:
                producing_notes += count
                break

    if not detected:
        # No profile, so fall back to the card count and say it is a guess.
        ratio = metrics.cards_per_note
        if ratio <= 1.02:
            return 0.0, f"{ratio:.2f} cards per note; no reverse cards found"
        if ratio >= 1.98:
            return 1.0, f"{ratio:.2f} cards per note, so probably every note"
        return 0.5, f"{ratio:.2f} cards per note, so probably some notes"

    total = metrics.note_count or 1
    share = producing_notes / total
    if share <= 0.005:
        return 0.0, (
            "no cards ask you to produce Italian-side text; this is a "
            "comprehension-first deck, which is a choice rather than a gap"
        ).replace("Italian-side", "target-language")
    if share >= 0.995:
        return 1.0, (
            f"every note ({producing_notes:,}) asks for production. Useful, but "
            "the tiring case the framework warns about"
        )
    return share, (
        f"{producing_notes:,} of {total:,} notes ({share:.0%}) ask for "
        "production, so it is selective"
    )


def detect_extras(deck, metrics: Metrics, profile=None) -> list[tuple[str, bool, str]]:
    """The article's 'other helpful features' — reported, never scored."""
    out: list[tuple[str, bool, str]] = []
    labels = dict(EXTRA_FEATURES)

    subdecks = [d for d in metrics.deck_tree if "::" in d]
    out.append(
        (
            labels["subdecks"],
            bool(subdecks),
            f"{len(subdecks)} subdecks" if subdecks else "one flat deck",
        )
    )

    table_like = any(
        tx.has_paradigm_table(t.qfmt + t.afmt)
        for nt in deck.notetypes.values()
        for t in nt.templates
    ) or any(tx.has_paradigm_table("".join(n.fields)) for n in deck.notes[:2000])
    out.append(
        (
            labels["grammar_tables"],
            table_like,
            "tables found on cards" if table_like else "no tables found",
        )
    )

    kinds = len(metrics.notetype_counts)
    variety = kinds > 1 or metrics.has_cloze
    detail = f"{kinds} note type{'s' if kinds != 1 else ''}"
    if metrics.has_cloze:
        detail += ", including cloze"
    out.append((labels["card_variety"], variety, detail))

    share, detail = production_share(deck, metrics, profile)
    out.append((labels["active_recall"], share < 1.0, detail))

    note_fields = any(
        any(
            word in name.lower()
            for word in ("note", "hint", "explan", "comment", "usage", "info", "gramm")
        )
        for nt in deck.notetypes.values()
        for name in nt.fields
    )
    out.append(
        (
            labels["notes"],
            note_fields,
            "explanation fields present" if note_fields else "no explanation fields",
        )
    )
    return out


def combine(
    judgements: dict[str, Judgement], metrics: Metrics, deck, profile=None
) -> Result:
    scores: list[Score] = []
    unassessed: list[str] = []

    for criterion in CRITERIA:
        if criterion.key == "audio":
            value, basis = score_audio(metrics)
            scores.append(
                Score(criterion, value, "high", "", basis=basis)
            )
            continue
        if criterion.key == "images":
            value, basis = score_images(metrics)
            scores.append(
                Score(criterion, value, "high", "", basis=basis)
            )
            continue

        judged = judgements.get(criterion.key)
        if judged is None or judged.error:
            unassessed.append(criterion.title)
            scores.append(
                Score(
                    criterion,
                    0,
                    "low",
                    "",
                    assessed=False,
                    error=(judged.error if judged else "not run"),
                )
            )
            continue
        scores.append(
            Score(
                criterion,
                max(0, min(10, judged.score)),
                judged.confidence,
                judged.verdict,
                strengths=judged.strengths,
                weaknesses=judged.weaknesses,
                evidence=judged.evidence,
                scores_seen=judged.scores_seen,
            )
        )

    assessed = [s for s in scores if s.assessed]
    live_weight = sum(s.criterion.weight for s in assessed)
    if live_weight <= 0:
        raise RuntimeError("No criterion could be assessed.")

    # A criterion that could not be judged is left out rather than scored zero;
    # the remaining weights are rescaled and the omission is reported.
    total = sum(s.score * s.criterion.weight for s in assessed) / live_weight * 10

    def group_total(group: str) -> float:
        members = [s for s in assessed if s.criterion.group == group]
        weight = sum(s.criterion.weight for s in members)
        if not weight:
            return 0.0
        return sum(s.score * s.criterion.weight for s in members) / weight * 10

    grade, note = next((g, n) for cutoff, g, n in GRADES if total >= cutoff)

    return Result(
        scores=scores,
        total=total,
        grade=grade,
        grade_note=note,
        non_negotiable_total=group_total(NON_NEGOTIABLE),
        nice_to_have_total=group_total("nice-to-have"),
        unassessed=unassessed,
        extras=detect_extras(deck, metrics, profile),
    )
