"""Building the evidence pack the model reads.

One pack is assembled per deck and reused for every criterion, so the whole
thing sits behind a prompt-cache breakpoint and is paid for once.

Sampling follows the manual review the article recommends: look at cards from
the beginning, the middle and the later parts, plus a random spread.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .apkg import Deck, Note
from . import frequency as fq
from .lexicon import Lexicon
from .metrics import CLOZE_MIN_LETTERS, Metrics, curve_table
from . import levels
from . import textutil as tx

#: Fixed so that two runs of the same deck read the same cards. Recorded in
#: the report, because a published score should name the sample it came from.
SAMPLE_SEED = 7

FIELD_CHAR_LIMIT = 400
#: Below this many distinct words a first-seen count says nothing worth the
#: tokens — an ID or a one-word headword is new or not, and the field text
#: already shows which.
MIN_WORDS_TO_MARK = 3
#: Every note type's front and back goes into the pack, so card design is
#: judged on the real layout and never guessed from field names. The budget
#: is shared out across all templates; a deck with a dozen card types gets a
#: shorter look at each, not a look at only some of them.
TEMPLATE_BUDGET = 40_000
TEMPLATE_CHAR_MIN = 700
TEMPLATE_CHAR_MAX = 2200
CSS_CHAR_LIMIT = 1200


@dataclass
class DeckProfile:
    """What the deck is, established in one cheap call before anything else."""

    target_language: str = "unknown"
    source_language: str = "unknown"
    deck_kind: str = "unknown"
    level_range: str = "unknown"
    #: note type id -> index of the target-language field
    target_fields: dict[int, int] = None
    #: note type id -> index of the translation field
    translation_fields: dict[int, int] = None
    #: note type id -> index of a target-language example sentence, where that
    #: is a different field from the one being recalled. Progression is
    #: measured over sentences, so this wins over target_fields when present.
    sentence_fields: dict[int, int] = None
    #: note type ids whose cards explain the language rather than teach it:
    #: grammar rules, announcements. Measured apart from the material.
    explanation_types: set[int] = None
    #: note type id -> indices of fields stating the dictionary form of a word
    #: the card teaches ("Verb Infinitive"). Evidence the word is taught.
    lemma_fields: dict[int, list[int]] = None
    notes: str = ""

    def __post_init__(self) -> None:
        if self.explanation_types is None:
            self.explanation_types = set()
        if self.lemma_fields is None:
            self.lemma_fields = {}
        if self.target_fields is None:
            self.target_fields = {}
        if self.translation_fields is None:
            self.translation_fields = {}
        if self.sentence_fields is None:
            self.sentence_fields = {}

    def measured_fields(self) -> dict[int, int]:
        """Which field the progression curve is measured over, per note type."""
        merged = dict(self.target_fields)
        merged.update(self.sentence_fields)
        return merged


def first_seen_marks(
    deck: Deck, sampled: set[int], lexicon: Lexicon | None = None
) -> dict[int, dict[int, tuple[int, int]]]:
    """Per sampled note, per field: (words appearing here first, words in field).

    The model reads about ninety scattered notes, not the deck, so on its own it
    cannot tell a word being introduced from one the deck has already used fifty
    cards earlier. It guesses from how much text the field holds, which makes a
    long gloss or a long sentence look like a pile of new material. The curve is
    measured over the whole deck and already knows the difference; this hands the
    same fact to the judge, for every field of every note type.

    A word is "first seen" if it has not appeared in any field of any earlier
    note. Counted over distinct words, in the order the deck is read, and the
    same way the curve counts them: by dictionary form where it does, with
    names left out.
    """
    count = lexicon.words if lexicon is not None else tx.words
    seen: set[str] = set()
    marks: dict[int, dict[int, tuple[int, int]]] = {}
    last = max(sampled, default=-1)
    for index, note in enumerate(deck.notes):
        if index > last:
            break
        per_field = [set(count(tx.measured_text(raw))) for raw in note.fields]
        if index in sampled:
            marks[index] = {
                i: (len(toks - seen), len(toks))
                for i, toks in enumerate(per_field)
                if len(toks) >= MIN_WORDS_TO_MARK
            }
        for toks in per_field:
            seen |= toks
    return marks


def render_note(
    deck: Deck,
    note: Note,
    position: int | None = None,
    marks: dict[int, tuple[int, int]] | None = None,
) -> str:
    nt = deck.notetype_of(note)
    names = nt.fields if nt else []
    head = f"[#{position}]" if position is not None else f"[note {note.id}]"
    if nt:
        head += f" type={nt.name}"
    if note.deck:
        head += f" deck={note.deck}"
    lines = [head]
    for i, raw in enumerate(note.fields):
        label = names[i] if i < len(names) else f"field{i + 1}"
        text = tx.readable_field(raw)
        extras = []
        if tx.sounds(raw):
            extras.append(f"audio:{len(tx.sounds(raw))}")
        if tx.images(raw):
            extras.append(f"image:{len(tx.images(raw))}")
        if marks and i in marks:
            fresh, total = marks[i]
            extras.append(f"first seen here: {fresh} of {total}")
        suffix = f"  <{', '.join(extras)}>" if extras else ""
        if not text and not suffix:
            continue
        # A field Anki shows on several lines is shown on several lines here,
        # each indented under the field name, so a line break never reads as
        # two items run together.
        body = tx.truncate(text, FIELD_CHAR_LIMIT).split("\n")
        if len(body) == 1:
            lines.append(f"  {label}: {body[0]}{suffix}")
        else:
            lines.append(f"  {label}:{suffix}")
            lines += [f"    | {line}" for line in body]
    if note.tags:
        lines.append(f"  tags: {' '.join(note.tags[:12])}")
    return "\n".join(lines)


def _block(title: str, body: str) -> str:
    return f"### {title}\n{body}\n"


def sample_indices(total: int, seed: int = SAMPLE_SEED) -> dict[str, list[int]]:
    """Pick which notes the model sees, by position in study order."""
    rng = random.Random(seed)
    opening = min(30, total)
    closing = min(15, max(0, total - opening))

    picks: dict[str, list[int]] = {"opening": list(range(opening))}

    spread: list[int] = []
    if total > 80:
        per_stop = 10
        for fraction in (0.25, 0.5, 0.75):
            start = int(total * fraction)
            spread += [i for i in range(start, min(start + per_stop, total))]
    picks["spread"] = sorted(set(spread))

    used = set(picks["opening"]) | set(picks["spread"])
    tail = list(range(max(0, total - closing), total))
    picks["closing"] = [i for i in tail if i not in used]
    used |= set(picks["closing"])

    pool = [i for i in range(total) if i not in used]
    picks["random"] = sorted(rng.sample(pool, min(35, len(pool))))
    return picks


def profile_prompt(deck: Deck) -> str:
    """A small sample plus the field layout, for the identification call."""
    total = len(deck.notes)
    step = max(1, total // 14)
    sample = [deck.notes[i] for i in range(0, total, step)][:14]

    layout = []
    for nt in deck.notetypes.values():
        used = sum(1 for n in deck.notes if n.notetype_id == nt.id)
        if not used:
            continue
        fields = ", ".join(f"{i}={name}" for i, name in enumerate(nt.fields))
        layout.append(
            f"- note type {nt.id} \"{nt.name}\" ({used:,} notes, "
            f"{len(nt.templates)} card template(s)"
            f"{', cloze' if nt.is_cloze else ''}): {fields}"
        )

    body = [
        f"Deck: {deck.name}",
        f"Notes: {total:,}   Cards: {deck.card_count:,}",
        "",
        "Note types and their fields:",
        *layout,
        "",
        "Sample notes, evenly spaced through the deck:",
        "",
    ]
    for note in sample:
        body.append(render_note(deck, note))
        body.append("")
    return "\n".join(body)


def _cloze_leak_lines(metrics: Metrics) -> list[str]:
    """Whether cloze fronts show the text they hide, counted over every note."""
    checked = metrics.cloze_deletions_checked
    if not checked:
        return []
    shown = metrics.cloze_answers_shown
    lines = [
        f"  Cloze answers visible on their own front: {shown:,} of {checked:,} "
        f"deletions ({shown / checked:.1%}), checked across every cloze note. "
        "The answer to a cloze is the target-language text it hides. A "
        "translation or meaning beside the blank is the cue to produce it, as "
        "the English prompt is on a reverse card; it gives the answer away only "
        "when it contains the hidden text itself. Counted: the hidden text "
        "appearing word for word in another field the front shows, in the rest "
        "of the cloze text, or in its own hint. Answers under "
        f"{CLOZE_MIN_LETTERS} letters are not checked. A count near zero is "
        "what a working cloze deck should show: it rules out a flaw, it is not "
        "a strength, so do not list it as one."
    ]
    for position, answer, where in metrics.cloze_leak_examples:
        lines.append(f"    [#{position}] «{answer}» also shows in {where}")
    return lines


def _duplicate_lines(metrics: Metrics) -> str:
    """Repeated prompts, split into the two very different things they mean."""
    share = metrics.duplicate_note_share
    lines = [
        f"Notes whose first field matches another note: {share:.1%} "
        f"({metrics.duplicate_note_groups:,} repeated values)."
    ]
    parallel = metrics.parallel_strand_notes
    if parallel:
        lines.append(
            f"  Of those, {parallel:,} sit in a DIFFERENT subdeck from their twin. "
            "That is the shape of a deck offering the same material in several "
            "recall directions — read it, hear it, produce it — which is a menu "
            "the learner chooses from, not redundancy. Do not score it as "
            "duplication unless the strands are genuinely identical in use."
        )
    if metrics.repeated_material_notes:
        lines.append(
            f"  {metrics.repeated_material_notes:,} notes repeat target-language "
            "text that appeared earlier. The progression curve below skips those "
            "repeats and follows each piece of material once, in the order it "
            "first appears — so a flat stretch in the curve is the deck's own "
            "sequencing, not an echo of a parallel strand."
        )
    return "\n".join(lines)


def _audio_lines(metrics: Metrics) -> str:
    """Audio coverage, broken out per note type.

    The breakdown matters: a deck can be at 92% overall and still have a
    recording on every card that needs one. Without these rows the model reads
    the single figure as a gap and writes it up as a fault.
    """
    lines = [
        f"Audio: {metrics.notes_with_audio:,} of {metrics.note_count:,} notes carry a "
        f"sound file ({metrics.audio_coverage_raw:.0%} across every note), "
        f"{metrics.distinct_audio_files:,} distinct files"
        + (
            "; the templates also use Anki's {{tts}} directive"
            if metrics.uses_tts_directive
            else ""
        )
    ]
    if metrics.audio_by_notetype:
        lines.append("Audio per note type:")
        for name, (with_audio, total) in metrics.audio_by_notetype.items():
            share = with_audio / total if total else 0.0
            lines.append(f"  {name}: {with_audio:,}/{total:,} ({share:.0%})")
    if metrics.silent_notetypes:
        lines.append(
            "These note types are silent throughout — "
            + ", ".join(metrics.silent_notetypes)
            + f" ({metrics.audio_excluded_notes:,} notes) — so the author did not "
            "intend them to carry audio. The audio criterion is scored over the "
            "remaining note types only, where coverage is "
            f"{metrics.audio_coverage:.0%}. Do not treat the silent types as "
            "missing audio."
        )
    return "\n".join(lines)


def _measured_fields_line(
    deck: Deck, profile: DeckProfile, measured: dict[int, int] | None = None
) -> str:
    """Name the field the word counts came from, so the model can discount them."""
    measured = profile.measured_fields() if measured is None else measured
    if not measured:
        return (
            "Word counts below were taken from the longest field on each note, "
            "because no target-language field could be identified."
        )
    parts = []
    for nt_id, index in sorted(measured.items()):
        nt = deck.notetypes.get(nt_id)
        if not nt:
            continue
        name = nt.fields[index] if 0 <= index < len(nt.fields) else f"field{index}"
        marker = (
            " (example sentence)"
            if profile.sentence_fields.get(nt_id) == index
            else ""
        )
        parts.append(f"{nt.name} -> {name}{marker}")
    if not parts:
        return ""
    return (
        "Word counts and the curve below were taken from these fields: "
        + "; ".join(parts)
        + ". Everything else on the note was ignored for counting."
    )


def _explanation_lines(metrics: Metrics) -> list[str]:
    """Explanation note types, said once and measured on their own."""
    lines: list[str] = []
    if metrics.unmeasured_types:
        kinds = ", ".join(f"{n} ({c:,} notes)" for n, c in metrics.unmeasured_types.items())
        lines.append(
            f"Not measured, because no target-language field was identified: {kinds}."
        )
    if metrics.explanation_lines_dropped:
        lines.append(
            f"{metrics.explanation_lines_dropped:,} lines inside measured fields read as "
            "explanation-language text (stage directions in a dialogue, for example) "
            "and were left out of every count."
        )
    if not metrics.explanation_types:
        return lines
    kinds = ", ".join(f"{n} ({c:,} notes)" for n, c in metrics.explanation_types.items())
    lines += [
        f"These note types explain the language rather than teach it: {kinds}. "
        "Their text is left out of the curve, the vocabulary totals and the "
        "frequency comparison, so grammar terminology is not read as vocabulary."
    ]
    words = metrics.explanation_only_words
    if words:
        lines.append(
            f"They use {len(words):,} target-language words the material never "
            "does (terminology, conjugation stems, or vocabulary a learner meets "
            "only on these cards). The first ones: " + ", ".join(words[:25]) + "."
        )
    return lines


DESCRIPTION_CHAR_LIMIT = 2000


def _description_line(deck: Deck, given: str = "") -> str:
    """What the author tells the learner about the deck, and where it came from."""
    parts = []
    if deck.description:
        parts.append(
            "Deck description, from the file: "
            + tx.truncate(deck.description, DESCRIPTION_CHAR_LIMIT)
        )
    if given:
        parts.append(
            "Description from the shared-deck page, supplied by the person running "
            "the analysis: " + tx.truncate(given, DESCRIPTION_CHAR_LIMIT)
        )
    if not parts:
        return "Deck description: none in the file, and none supplied."
    return "\n".join(parts)


#: Notes shown from the start of the level-tag study path.
PATH_OPENING = 20


def _study_path_text(tags: levels.LevelTags, path: levels.StudyPath | None) -> str:
    """The deck read lowest level first, measured the same way as above."""
    lines = [tags.describe()]
    if path is None:
        lines.append(
            "Progression above is measured in that order already; nothing to add."
        )
        return "\n".join(lines)
    m = path.metrics
    credit = 2 if tags.easy_to_follow else 1
    lines += [
        "Below is the deck measured lowest level first, the default order within "
        "each level, with the same fields and the same word counting as above.",
        "",
        "A learner who opens the deck studies it in the default order, so score "
        "progression and vocabulary on the default order. The tag path can only "
        f"raise those scores, by at most {credit} point{'s' if credit > 1 else ''} "
        "here, and only by as much as it is better. "
        + (
            "Up to 2 points needs a deck description (see The deck, above) that "
            "tells the learner to study by level; without one, at most 1. "
            if tags.easy_to_follow
            else "At most 1 point, because following the path means repositioning "
            "the whole deck. "
        )
        + "Say which order you scored, how much the tag path added, and why.",
        f"To follow the path, a learner would {tags.how_to_follow()}. That is "
        "something a learner could fix themselves: list it under what can be "
        "fixed, with those steps, when the tag path is clearly better.",
        "",
        m.format_line,
        "",
        curve_table(m),
    ]
    if m.buckets and not m.lexicon.unsegmented:
        lines += ["", m.curve_shape]
    if m.frequency.available:
        lines += [
            "",
            "Vocabulary against the same frequency list, along this path:",
            fq.table(m.frequency),
        ]
    first = list(range(min(PATH_OPENING, len(path.deck.notes))))
    marks = (
        {} if m.lexicon.unsegmented
        else first_seen_marks(path.deck, set(first), m.lexicon)
    )
    lines += [
        "",
        f"The first {len(first)} notes along this path. Positions are along the "
        "path; first-seen marks count earlier notes on the path.",
        "",
        "\n\n".join(
            render_note(path.deck, path.deck.notes[i], position=i + 1, marks=marks.get(i))
            .replace(f"[#{i + 1}]", f"[path #{i + 1}]", 1)
            for i in first
        ),
    ]
    return "\n".join(lines)


def build_pack(
    deck: Deck,
    metrics: Metrics,
    profile: DeckProfile,
    measured_fields: dict[int, int] | None = None,
    tags: levels.LevelTags | None = None,
    path: levels.StudyPath | None = None,
    description: str = "",
) -> str:
    """The shared evidence block every criterion call reads.

    `tags` are level tags found on the notes; `path` is the deck measured in
    their order, when the default order ignores them. `description` is the
    shared-deck page text, when the user supplied it.
    """
    picks = sample_indices(len(deck.notes))
    sampled = {i for idxs in picks.values() for i in idxs}
    marks = (
        {} if metrics.lexicon.unsegmented
        else first_seen_marks(deck, sampled, metrics.lexicon)
    )
    parts: list[str] = []

    # -- what the deck is -------------------------------------------------
    overview = [
        f"Name: {deck.name}",
        f"File: {deck.path.rsplit('/', 1)[-1].rsplit(chr(92), 1)[-1]}",
        f"Notes: {metrics.note_count:,}   Cards: {metrics.card_count:,} "
        f"({metrics.cards_per_note:.2f} cards per note)",
        f"Target language: {profile.target_language}   "
        f"Explanations in: {profile.source_language}",
        f"Deck kind: {profile.deck_kind}   Stated level: {profile.level_range}",
        f"Notes are listed in {deck.order_basis}.",
    ]
    if profile.notes:
        overview.append(f"Identification notes: {profile.notes}")
    overview.append(_description_line(deck, description))
    parts.append(_block("The deck", "\n".join(overview)))

    # -- structure --------------------------------------------------------
    tree = deck.deck_names[:40]
    structure = [
        "Decks and subdecks:",
        *(f"  {name}" for name in tree),
    ]
    if len(deck.deck_names) > 40:
        structure.append(f"  … and {len(deck.deck_names) - 40} more")
    structure.append("")
    structure.append("Note types in use:")
    for name, count in metrics.notetype_counts.items():
        tmpl = metrics.templates_per_notetype.get(name, 0)
        structure.append(f"  {name}: {count:,} notes, {tmpl} card template(s)")
    if metrics.tag_examples:
        structure.append("")
        structure.append("Most common tags: " + ", ".join(metrics.tag_examples))
    parts.append(_block("Structure", "\n".join(structure)))

    # -- measurements -----------------------------------------------------
    measured = [
        "These are counted from the deck file, not estimated.",
        "",
        _audio_lines(metrics),
        f"Images: {metrics.notes_with_images:,} notes ({metrics.image_coverage:.0%}), "
        f"{metrics.distinct_image_files:,} distinct files",
        f"Cloze note types present: {'yes' if metrics.has_cloze else 'no'}",
        *_cloze_leak_lines(metrics),
        f"Note types producing more than one card: "
        f"{'yes' if metrics.has_reverse_cards else 'no'}",
        _duplicate_lines(metrics),
        f"Notes missing a field that most notes of their type fill: "
        f"{metrics.empty_field_share:.1%}",
        f"Distinct words in the target language: {metrics.total_vocabulary:,}",
        f"Mean words per target-language field: {metrics.mean_words_per_note:.1f}",
        "",
        metrics.lexicon.describe() + " Every word count in this pack (the "
        "curve, the vocabulary totals and the first-seen marks) uses this "
        "method.",
        _measured_fields_line(deck, profile, measured_fields),
        *_explanation_lines(metrics),
        "",
        metrics.format_line,
        "",
        "Progression curve — the deck split into ten equal parts in study order.",
        "'new-word rate' is the share of word tokens in that part that had not "
        "appeared in any earlier note; 'in sentences' is the same rate over the "
        "sentence notes alone. '<=1 new word' is the share of sentence notes in "
        "that part introducing at most one unfamiliar word — the i+1 reading.",
        "",
        curve_table(metrics),
    ]
    if metrics.buckets and not metrics.lexicon.unsegmented:
        measured += [
            "",
            metrics.curve_shape,
            "A deck that keeps teaching new material cannot reach a zero rate, so "
            "a steady, low rate after the opening is the expected shape.",
            f"Mean sentence length changes by a factor of {metrics.length_growth:.2f} "
            "from the opening to the close.",
        ]
    measured += [
        "",
        "Vocabulary against a general frequency list (the same list for every "
        "deck in this language, chosen without reference to any course):",
        fq.table(metrics.frequency),
    ]
    for warning in metrics.warnings:
        measured += ["", f"Caveat: {warning}"]
    parts.append(_block("What was measured", "\n".join(measured)))

    if tags is not None:
        parts.append(_block("Study path by level tags", _study_path_text(tags, path)))

    # -- samples ----------------------------------------------------------
    labels = {
        "opening": "The opening — the first notes a learner meets, in order",
        "spread": "Spread samples — consecutive runs taken at 25%, 50% and 75%",
        "closing": "The closing — the last notes in the deck",
        "random": "A random sample from everywhere else",
    }
    parts.append(
        _block(
            "How to read the sampled notes",
            "Field text is shown the way Anki renders it. A field on several "
            "lines is printed with each line on its own, indented under the "
            "field name and starting with '|'. Within a line:\n"
            "  • item            a list item (<li>)\n"
            "  [collapsed: label] ... [/collapsed]   inside a <details> element, "
            "hidden until the learner clicks the label\n"
            "  a | b             table cells side by side\n"
            "And three markers that carry meaning:\n"
            "  «word»            the deck marks this word — bold, italic, "
            "underlined or colored\n"
            "  «j'é‹t›ait»       a mark inside a mark: here one letter bold "
            "within an italic example\n"
            "These markers are this pack's notation for the deck's formatting. "
            "They are not characters on the card, so never report them as the "
            "deck's typography or punctuation.\n"
            "  [cloze: word]     hidden on the front, typed or recalled by the "
            "learner; the text shown here is the answer\n"
            + (
                "No first-seen marks are given: this deck's script has no spaces "
                "between words, so new words could not be counted. Judge how "
                "much a note introduces from the cards themselves, and say that "
                "the figure is your own reading."
                if metrics.lexicon.unsegmented else
                "  <first seen here: 1 of 6>   of the 6 distinct words in that "
                "field, 1 appears nowhere in any earlier note. Counted over the "
                "whole deck, not over this sample.\n"
                "\n"
                "You are reading about ninety notes out of "
                f"{len(deck.notes):,}, so you cannot see for yourself whether a "
                "word is new. Use the marker. A field holding many words of which "
                "few are first seen here is repeating or glossing known material, "
                "however long it looks; that is not a cluster of new vocabulary. "
                "Do not infer how much a note introduces from how much text it "
                "carries."
            ),
        )
    )
    for key in ("opening", "spread", "closing", "random"):
        idxs = picks.get(key) or []
        if not idxs:
            continue
        body = "\n\n".join(
            render_note(deck, deck.notes[i], position=i + 1, marks=marks.get(i))
            for i in idxs
        )
        parts.append(_block(labels[key], body))

    # -- templates --------------------------------------------------------
    tmpl_parts: list[str] = []
    ranked = sorted(
        (nt for nt in deck.notetypes.values() if metrics.notetype_counts.get(nt.name)),
        key=lambda nt: -metrics.notetype_counts.get(nt.name, 0),
    )
    sides = 2 * sum(len(nt.templates) for nt in ranked) or 1
    limit = max(TEMPLATE_CHAR_MIN, min(TEMPLATE_CHAR_MAX, TEMPLATE_BUDGET // sides))
    css_seen: dict[str, str] = {}
    for nt in ranked:
        tmpl_parts.append(f"Note type: {nt.name}  (fields: {', '.join(nt.fields)})")
        for tmpl in nt.templates:
            tmpl_parts.append(f"  --- card \"{tmpl.name}\" front ---")
            tmpl_parts.append(tx.truncate(tx.compact_template(tmpl.qfmt), limit))
            tmpl_parts.append(f"  --- card \"{tmpl.name}\" back ---")
            tmpl_parts.append(tx.truncate(tx.compact_template(tmpl.afmt), limit))
        css = nt.css.strip()
        if css and css in css_seen:
            tmpl_parts.append(f"  --- stylesheet: same as {css_seen[css]} ---")
        elif css:
            css_seen[css] = nt.name
            tmpl_parts.append("  --- stylesheet ---")
            tmpl_parts.append(tx.truncate(css, CSS_CHAR_LIMIT))
        tmpl_parts.append("")
    if not tmpl_parts:
        tmpl_parts = ["(no card templates could be read from this package)"]
    parts.append(_block("Card templates and styling", "\n".join(tmpl_parts)))

    return "\n".join(parts)
