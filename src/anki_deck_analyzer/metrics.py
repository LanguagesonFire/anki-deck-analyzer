"""Measurements taken from the deck itself, before any model sees it.

These exist so the judgment calls rest on counts rather than impressions. The
new-word curve in particular is a direct reading of the article's first
criterion: walk the deck in the order a learner meets it, and ask how much
unfamiliar material each card drops on them.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from .apkg import Deck, Note
from . import frequency as fq
from . import lexicon as lx
from . import textutil as tx

#: Minimum word count for a note to be read as a sentence rather than an entry.
SENTENCE_MIN_WORDS = 4
#: From this share of single-word notes on, the deck's format decides its
#: all-notes new-word rate, and the curve is read over its sentences instead.
FORMAT_ENTRY_SHARE = 0.20
#: Fewer sentences than this in a part and their rate is noise.
MIN_SENTENCES_PER_PART = 10
#: A word in an example counts as the card's headword in another form when the
#: two share this many leading letters, and that much of the shorter one: the
#: inflected "uzbroimy" under the headword "uzbroić".
HEADWORD_STEM_MIN = 4
HEADWORD_STEM_SHARE = 0.6

#: Above this median token count, a field is a dictionary dump rather than an
#: example sentence, and measuring it would swamp the curve for every note type
#: that holds one real sentence.
SENTENCE_DUMP_TOKENS = 40

#: Audio coverage is judged over the card types that carry audio at all.
#: Announcements, rule tables and quiz cards have nothing to pronounce, and
#: counting them makes a deck with complete audio look incomplete. A note type
#: at or below this coverage was silent by the author's decision, not by
#: oversight.
SILENT_TYPE_MAX_COVERAGE = 0.02
#: Only applied to decks that clearly set out to have audio. A deck at 9%
#: coverage is missing audio, not structured around it.
AUDIO_DECK_MIN_COVERAGE = 0.50
#: However the note types fall, never excuse more than this much of the deck.
SILENT_TYPE_MAX_SHARE = 0.30


@dataclass
class Bucket:
    """One tenth of the deck, in study order."""

    index: int
    start: int
    end: int
    note_count: int
    new_word_rate: float
    mean_words: float
    median_words: float
    mean_new_words: float
    #: Share of the sentence-length notes in this part that introduce at most
    #: one unfamiliar word. None when the part holds no sentences. On a word
    #: card measured on its example sentence, the headword is the card's one
    #: new item: the example passes only if it brings in nothing else.
    share_at_most_one_new: float | None
    #: Notes in this part long enough to read as a sentence.
    sentence_notes: int
    cumulative_vocab: int
    #: The new-word rate over the sentence-length notes alone. In a word list
    #: nearly every word on a one-word card is new by construction, so the rate
    #: over all notes reads the deck's format, not its order.
    sentence_new_word_rate: float | None = None


@dataclass
class Metrics:
    note_count: int
    card_count: int
    notetype_counts: dict[str, int]
    deck_tree: list[str]
    subdeck_count: int

    audio_coverage: float
    image_coverage: float
    notes_with_audio: int
    notes_with_images: int
    uses_tts_directive: bool
    distinct_audio_files: int
    distinct_image_files: int

    has_cloze: bool
    has_reverse_cards: bool
    templates_per_notetype: dict[str, int]
    cards_per_note: float

    duplicate_note_groups: int
    duplicate_note_share: float
    empty_field_share: float

    analysed_notes: int
    total_vocabulary: int
    mean_words_per_note: float
    type_token_ratio: float
    buckets: list[Bucket] = field(default_factory=list)

    grammar_note_share: float = 0.0
    tag_examples: list[str] = field(default_factory=list)
    notes_missing_target_text: int = 0
    warnings: list[str] = field(default_factory=list)

    #: Coverage across every note, before silent-by-design types are set aside.
    audio_coverage_raw: float = 0.0
    #: note type name -> (notes with audio, notes of that type)
    audio_by_notetype: dict[str, tuple[int, int]] = field(default_factory=dict)
    #: Note types the author clearly never intended to carry audio.
    silent_notetypes: list[str] = field(default_factory=list)
    audio_excluded_notes: int = 0

    #: Notes whose target-language text had already appeared verbatim earlier —
    #: the same material offered in another recall direction. Left out of the
    #: progression curve; see the comment where the curve is built.
    repeated_material_notes: int = 0
    #: Notes sharing a first field with a note in a DIFFERENT subdeck. Usually
    #: parallel strands rather than accidental duplicates.
    parallel_strand_notes: int = 0

    #: How words were counted: by dictionary form or by spelling, and which
    #: names were left out. Reports name it, so they are compared like for like.
    lexicon: lx.Lexicon = field(default_factory=lx.Lexicon)
    frequency: fq.FrequencyProfile = field(default_factory=fq.FrequencyProfile)

    #: Note types that explain the language rather than teach it — grammar
    #: rules, announcements — with their note counts. Their text is left out
    #: of the curve and the frequency comparison and measured on its own.
    explanation_types: dict[str, int] = field(default_factory=dict)
    #: Target-language words that appear in explanation notes and nowhere in
    #: the material: mostly grammar terminology ("partitif", "copule").
    explanation_only_words: list[str] = field(default_factory=list)
    #: Words counted as taught because a field states their dictionary form
    #: ("Verb Infinitive"), though the studied text shows another spelling.
    lemma_field_words: int = 0
    #: Note types left unmeasured because no target-language field was
    #: identified for them, with their note counts.
    unmeasured_types: dict[str, int] = field(default_factory=dict)
    #: Lines inside measured fields that read as the explanation language —
    #: stage directions in a dialogue field, say — and were left out.
    explanation_lines_dropped: int = 0

    #: Cloze deletions long enough to check, and how many of them show their own
    #: hidden text somewhere else on the front. See _cloze_leaks.
    cloze_deletions_checked: int = 0
    cloze_answers_shown: int = 0
    #: (study position, hidden text, where it shows) for the first few.
    cloze_leak_examples: list[tuple[int, str, str]] = field(default_factory=list)

    #: Measured notes shorter than a sentence: single words and short phrases.
    entry_notes: int = 0
    #: Measured notes long enough to read as a sentence.
    sentence_notes: int = 0
    #: Of those, how many add at most one new word (headword included), over
    #: the whole deck. None when the deck has no sentences.
    i_plus_one_share: float | None = None
    #: Sentence notes that come with a headword or stated dictionary form, so
    #: the headword was counted as the card's new item.
    headword_sentences: int = 0

    # ---- readings used by the report ------------------------------------

    @property
    def new_word_rate_overall(self) -> float:
        if not self.buckets:
            return 0.0
        return statistics.fmean(b.new_word_rate for b in self.buckets)

    @property
    def new_word_rate_trend(self) -> float:
        """Late-deck new-word rate minus early-deck rate.

        A well-sequenced deck trends negative: later cards recycle vocabulary
        the learner has already met.
        """
        if len(self.buckets) < 4:
            return 0.0
        early = statistics.fmean(b.new_word_rate for b in self.buckets[:2])
        late = statistics.fmean(b.new_word_rate for b in self.buckets[-2:])
        return late - early

    @property
    def entry_share(self) -> float:
        """Share of measured notes that are words or short phrases."""
        total = self.entry_notes + self.sentence_notes
        return self.entry_notes / total if total else 0.0

    @property
    def curve_reads_sentences(self) -> bool:
        """Whether the curve is described over sentences alone.

        When a fifth or more of the deck is single words, the all-notes rate is
        dominated by those cards, each new by construction. The sentences then
        carry the progression reading, as long as every part holds enough of
        them to measure.
        """
        return (
            self.entry_share >= FORMAT_ENTRY_SHARE
            and len(self.buckets) >= 4
            and all(
                b.sentence_new_word_rate is not None
                and b.sentence_notes >= MIN_SENTENCES_PER_PART
                for b in self.buckets
            )
        )

    @property
    def format_line(self) -> str:
        """What the deck is made of, and what that does to the curve."""
        total = self.entry_notes + self.sentence_notes
        if not total:
            return ""
        text = (
            f"Format: {self.sentence_notes:,} of {total:,} measured notes "
            f"({1 - self.entry_share:.0%}) are sentences of {SENTENCE_MIN_WORDS} "
            f"words or more; {self.entry_notes:,} ({self.entry_share:.0%}) are "
            "single words or short phrases."
        )
        if self.i_plus_one_share is not None:
            text += (
                f" Across the whole deck, {self.i_plus_one_share:.0%} of the "
                "sentences add at most one new word."
            )
        if self.headword_sentences:
            text += (
                f" {self.headword_sentences:,} of the sentences are examples on a "
                "card that names the word it teaches; there the headword is the "
                "card's one new item, so an example passes only if it brings in "
                "no other unknown word."
            )
        if self.entry_share >= FORMAT_ENTRY_SHARE:
            text += (
                " A card that teaches one word introduces one new item, which is "
                "i+1 at card level, so on such cards a high new-word rate is the "
                "format, not a fault."
            )
            text += (
                " The curve's shape below is read over the sentences alone."
                if self.curve_reads_sentences
                else " Too few sentences per part to read the curve over them alone, "
                "so its shape mostly reflects the word cards."
            )
        return text

    @property
    def curve_shape(self) -> str:
        """The curve described in words, the way the rubric reads it.

        A deck that keeps teaching cannot reach zero, so the question is not
        whether the rate keeps falling but whether, after the opening, it holds
        steady or jumps about. Spikes and late rises are named; a plateau is
        described as a plateau.
        """
        b = self.buckets
        if len(b) < 4:
            return ""
        over_sentences = self.curve_reads_sentences
        rates = [
            x.sentence_new_word_rate if over_sentences else x.new_word_rate for x in b
        ]
        what = "new-word rate in sentences" if over_sentences else "new-word rate"
        # Where the opening ends: the first part within a point and a half of
        # the median of everything after it.
        settle = len(rates) - 1
        for i in range(1, len(rates)):
            rest = statistics.median(rates[i:])
            if rates[i] <= rest + 0.015:
                settle = i
                break
        tail = rates[settle:]
        lo, hi = min(tail), max(tail)
        level = statistics.median(tail)
        moves = "falls" if rates[settle] < rates[0] else "rises"
        text = (
            f"The {what} {moves} from {rates[0]:.1%} to {rates[settle]:.1%} by "
            f"part {settle + 1}, then holds between {lo:.1%} and {hi:.1%} "
            f"(median {level:.1%}) for the rest of the deck."
            if settle > 0
            else f"The {what} holds between {lo:.1%} and {hi:.1%} from the start."
        )
        spikes = [
            f"part {settle + j + 1} ({r:.1%})"
            for j, r in enumerate(tail)
            if r > level * 1.5 and r - level > 0.02
        ]
        if spikes:
            text += " Spikes above that level: " + ", ".join(spikes) + "."
        third = max(1, len(tail) // 3)
        if len(tail) >= 6 and statistics.fmean(tail[-third:]) > statistics.fmean(tail[:third]) * 1.25:
            text += " The rate rises again toward the end of the deck."
        return text

    @property
    def length_growth(self) -> float:
        """Ratio of late-deck to early-deck sentence length."""
        if len(self.buckets) < 4:
            return 1.0
        early = statistics.fmean(b.mean_words for b in self.buckets[:2])
        late = statistics.fmean(b.mean_words for b in self.buckets[-2:])
        return late / early if early else 1.0


def _is_headword(token: str, headword: set[str]) -> bool:
    """Whether `token` is the card's headword, in the same or another form."""
    if token in headword:
        return True
    for h in headword:
        n = 0
        for a, b in zip(token, h):
            if a != b:
                break
            n += 1
        if n >= HEADWORD_STEM_MIN and n >= HEADWORD_STEM_SHARE * min(len(token), len(h)):
            return True
    return False


def card_new_items(new: Iterable[str], headword: set[str], seen: set[str]) -> int:
    """How many unfamiliar items one card brings, headword included.

    A word card with an example teaches its headword: that is the card's one
    new item, however many times it appears in the example and in whatever
    form. Every other unfamiliar word in the example is one more. Without a
    headword this is simply the number of distinct new words.
    """
    fresh = set(new)
    if not headword:
        return len(fresh)
    others = {t for t in fresh if not _is_headword(t, headword)}
    teaches = bool(fresh - others) or any(h not in seen for h in headword)
    return len(others) + (1 if teaches else 0)


def _target_text(note: Note, target_field_index: int | None) -> str:
    """The target-language text of a note, as plain text."""
    if target_field_index is not None and 0 <= target_field_index < len(note.fields):
        text = tx.measured_text(note.fields[target_field_index])
        if text:
            return text
    # Fall back to the longest field, which on a sentence card is almost always
    # the sentence and on a vocabulary card the word plus its context.
    candidates = [tx.measured_text(f) for f in note.fields]
    return max(candidates, key=len) if candidates else ""


def with_headwords(
    lemma_fields: dict[int, list[int]] | None,
    target_fields: dict[int, int] | None,
    measured: dict[int, int],
) -> dict[int, list[int]]:
    """Lemma fields plus the headword of every note type measured on its
    example sentence instead.

    A vocabulary card's headword is the deck saying outright which word it
    teaches. Once the curve reads the example, the headword would otherwise go
    unread, and a lemmatizer misreading the example ("blanche" as the noun)
    leaves the word looking untaught.
    """
    out = {k: list(v) for k, v in (lemma_fields or {}).items()}
    for nt_id, index in (target_fields or {}).items():
        if measured.get(nt_id, index) != index and index not in out.get(nt_id, []):
            out.setdefault(nt_id, []).append(index)
    return out


def choose_measured_fields(
    deck: Deck,
    target_fields: dict[int, int] | None = None,
    sentence_fields: dict[int, int] | None = None,
) -> tuple[dict[int, int], list[str]]:
    """Decide which field the progression curve reads, per note type.

    An example-sentence field wins over the recalled field, because progression
    is a property of the sentences a learner meets. But some decks put a whole
    dictionary entry in that field. Measuring one of those against note types
    holding a single sentence would make the curve meaningless, so a field whose
    median length is far past a sentence is rejected and the reason recorded.
    """
    target_fields = dict(target_fields or {})
    sentence_fields = sentence_fields or {}
    chosen = dict(target_fields)
    warnings: list[str] = []

    by_type: dict[int, list[Note]] = {}
    for note in deck.notes:
        if note.notetype_id in sentence_fields:
            bucket = by_type.setdefault(note.notetype_id, [])
            if len(bucket) < 300:
                bucket.append(note)

    for nt_id, index in sentence_fields.items():
        sample = by_type.get(nt_id, [])
        lengths = [
            len(tx.words(tx.strip_html(n.fields[index])))
            for n in sample
            if 0 <= index < len(n.fields)
        ]
        lengths = [n for n in lengths if n]
        if not lengths:
            continue
        median = statistics.median(lengths)
        nt = deck.notetypes.get(nt_id)
        name = nt.fields[index] if nt and 0 <= index < len(nt.fields) else f"field{index}"
        if median > SENTENCE_DUMP_TOKENS:
            warnings.append(
                f"The \"{name}\" field on {nt.name if nt else nt_id} holds about "
                f"{median:.0f} words per note — a dictionary entry rather than one "
                "example sentence. The curve reads that note type's main field "
                "instead."
            )
            continue
        chosen[nt_id] = index

    return chosen, warnings


def _audio_coverage(
    raw: float,
    audio_by_type: Counter,
    notetype_counts: Counter,
    total: int,
) -> tuple[float, list[str], int]:
    """Audio coverage over the card types that carry audio at all.

    A deck can have a recording on every sentence and still measure at 92%,
    because its announcements and grammar tables have nothing to pronounce.
    Scoring that as a gap is wrong, and worse, it reads as a real criticism in
    the report. So note types the author left silent as a group are set aside
    and both figures are reported.

    This only applies to decks that clearly set out to have audio, a whole note
    type at a time, and never to more than a third of the deck — so a deck that
    is simply missing recordings cannot be rescued by it.
    """
    if not total or raw < AUDIO_DECK_MIN_COVERAGE:
        return raw, [], 0

    silent = [
        name
        for name, count in notetype_counts.items()
        if count and audio_by_type.get(name, 0) / count <= SILENT_TYPE_MAX_COVERAGE
    ]
    excluded = sum(notetype_counts[name] for name in silent)
    if not excluded or excluded > SILENT_TYPE_MAX_SHARE * total:
        return raw, [], 0

    speaking = total - excluded
    if speaking <= 0:
        return raw, [], 0
    return sum(audio_by_type.values()) / speaking, silent, excluded


#: Shorter answers ("a", "le") turn up in almost any sentence by chance, in
#: either language, so a match says nothing about the card.
CLOZE_MIN_LETTERS = 3
CLOZE_LEAK_EXAMPLES = 8


def _cloze_leaks(deck: Deck) -> tuple[int, int, list[tuple[int, str, str]]]:
    """Cloze deletions whose hidden text is visible on the card's own front.

    The answer to a cloze is the target-language text it hides. A translation
    beside the blank gives the meaning, which is what prompts the learner to
    produce the word; it only gives the answer away when it contains that same
    text — a cognate, a name, an untranslated word. The same goes for the hint
    and for the rest of the cloze field, where the other deletions show as
    plain text. Measuring this keeps the model from reading every translation
    on a cloze front as a leak, and still catches the decks where it is one.
    """
    checked = shown = 0
    examples: list[tuple[int, str, str]] = []

    def norm(raw: str) -> str:
        return tx.strip_html(raw).lower()

    def contains(needle: str, text: str) -> bool:
        return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", text) is not None

    for position, note in enumerate(deck.notes, start=1):
        nt = deck.notetype_of(note)
        if not nt or not nt.is_cloze:
            continue
        for template in nt.templates:
            hidden_in = [f for f in tx.cloze_fields(template.qfmt) if f in nt.fields]
            beside = [
                f for f in tx.shown_fields(template.qfmt)
                if f in nt.fields and f not in hidden_in
            ]
            for name in hidden_in:
                raw = note.fields[nt.fields.index(name)]
                for m in tx.CLOZE_RE.finditer(raw):
                    answer = norm(m.group(2))
                    if len(re.sub(r"\W", "", answer)) < CLOZE_MIN_LETTERS:
                        continue
                    checked += 1
                    num = m.group(1)
                    rest = norm(tx.CLOZE_RE.sub(
                        lambda k: "" if k.group(1) == num else k.group(2), raw
                    ))
                    where = None
                    if m.group(3) and contains(answer, norm(m.group(3))):
                        where = "its own hint"
                    elif contains(answer, rest):
                        where = f"the rest of {name}"
                    else:
                        for other in beside:
                            if contains(answer, norm(note.fields[nt.fields.index(other)])):
                                where = f"field {other}"
                                break
                    if where:
                        shown += 1
                        if len(examples) < CLOZE_LEAK_EXAMPLES:
                            examples.append((position, answer, where))
    return checked, shown, examples


def _grammar_signal(note: Note, notetype_name: str) -> bool:
    haystack = " ".join(note.tags).lower() + " " + notetype_name.lower()
    return any(
        marker in haystack
        for marker in ("grammar", "grammatik", "gramm", "conjug", "declen", "case", "verb-")
    )


def compute(
    deck: Deck,
    target_fields: dict[int, int] | None = None,
    bucket_count: int = 10,
    extra_warnings: list[str] | None = None,
    language: str | None = None,
    explanation_language: str | None = None,
    frequency_list: str | None = None,
    explanation_types: set[int] | None = None,
    lemma_fields: dict[int, list[int]] | None = None,
    lexicon: lx.Lexicon | None = None,
) -> Metrics:
    """Measure `deck`.

    `target_fields` maps a note type id to the index of its target-language
    field. Anything missing falls back to the longest field on the note.
    `language` is the target language's English name; it decides how words
    are counted and which frequency list the vocabulary is held against;
    `explanation_language` is the language of translations and rules, whose
    words are kept out of the frequency comparison. `frequency_list` is
    None (the language's default list) or a list name or path; see
    frequency.load_list.

    `explanation_types` are note type ids whose cards explain rather than
    teach; `lemma_fields` maps a note type id to fields stating the dictionary
    form of a word the card teaches. Both come from the identification call.
    `lexicon` reuses one already built for the same deck in another order, so
    both readings count words the same way.
    """
    explanation_types = explanation_types or set()
    lemma_fields = lemma_fields or {}
    target_fields = target_fields or {}
    notes = deck.notes
    n = len(notes)
    #: Text-to-speech only counts as audio when it speaks this language.
    target_code = lx.language_code(language)

    notetype_counts: Counter[str] = Counter()
    templates_per_notetype: dict[str, int] = {}
    has_cloze = False
    has_reverse = False

    # Only note types some note uses: a shared deck often carries Anki's
    # stock Cloze or Basic type with no notes in it, which says nothing about
    # the cards a learner gets.
    in_use = {note.notetype_id for note in notes}
    for nt in deck.notetypes.values():
        if nt.id not in in_use:
            continue
        templates_per_notetype[nt.name] = len(nt.templates)
        if nt.is_cloze:
            has_cloze = True
        if len(nt.templates) > 1:
            has_reverse = True

    audio_notes = 0
    image_notes = 0
    audio_by_type: Counter[str] = Counter()
    tts = False
    audio_files: set[str] = set()
    image_files: set[str] = set()
    #: per note type: how many notes fill each field, to tell a field that is
    #: genuinely missing from one that the note type simply does not use.
    field_fill: dict[int, Counter[int]] = {}
    per_type_counts: Counter[int] = Counter()
    filled_by_note: list[tuple[int, set[int]]] = []
    first_field_seen: Counter[str] = Counter()
    first_field_decks: dict[str, set[str]] = {}
    grammar_notes = 0
    tag_counter: Counter[str] = Counter()

    texts: list[str] = []
    #: per entry of `texts`: dictionary forms its note states in lemma fields
    stated: list[list[str]] = []
    #: per entry of `texts`: the note's other fields, where a name that only
    #: ever opens a sentence shows up again in the translation
    companions: list[str] = []
    explanation_texts: list[str] = []
    explanation_counts: Counter[str] = Counter()
    unmeasured: Counter[str] = Counter()
    # Once any note type has an identified target field, a type without one
    # has nothing to measure. Falling back to its longest field would measure
    # an English quiz prompt or announcement as if it were French. The
    # fallback stays for a bare dry run, where nothing has been identified.
    mapped = bool(target_fields)
    for note in notes:
        nt = deck.notetype_of(note)
        nt_name = nt.name if nt else f"(unknown {note.notetype_id})"
        notetype_counts[nt_name] += 1

        joined = "\n".join(note.fields)
        snd = tx.sounds(joined)
        img = tx.images(joined)
        if snd:
            audio_notes += 1
            audio_by_type[nt_name] += 1
            audio_files.update(snd)
        if img:
            image_notes += 1
            image_files.update(img)
        if not tts and tx.has_target_tts(joined, target_code):
            tts = True

        filled = {
            i for i, f in enumerate(note.fields) if tx.strip_html(f) or tx.sounds(f) or tx.images(f)
        }
        field_fill.setdefault(note.notetype_id, Counter()).update(filled)
        per_type_counts[note.notetype_id] += 1
        filled_by_note.append((note.notetype_id, filled))

        key = tx.strip_html(note.fields[0]).lower() if note.fields else ""
        if key:
            first_field_seen[key] += 1
            first_field_decks.setdefault(key, set()).add(note.deck)

        if _grammar_signal(note, nt_name):
            grammar_notes += 1
        tag_counter.update(note.tags)

        if note.notetype_id in explanation_types:
            explanation_counts[nt_name] += 1
            if not mapped or note.notetype_id in target_fields:
                explanation_texts.append(
                    _target_text(note, target_fields.get(note.notetype_id))
                )
            continue
        if mapped and note.notetype_id not in target_fields:
            unmeasured[nt_name] += 1
            continue
        index = target_fields.get(note.notetype_id)
        text = _target_text(note, index)
        texts.append(text)
        companions.append(
            "\n".join(tx.strip_html(f) for i, f in enumerate(note.fields) if i != index)
        )
        stated.append(
            [
                tx.strip_html(note.fields[i])
                for i in lemma_fields.get(note.notetype_id, ())
                if 0 <= i < len(note.fields) and tx.strip_html(note.fields[i])
            ]
        )

    # Template-driven audio: a deck can speak every card through {{tts}} without
    # a single sound file, and that should not read as "no audio".
    for nt in deck.notetypes.values():
        for tmpl in nt.templates:
            if tx.has_target_tts(tmpl.qfmt, target_code) or tx.has_target_tts(tmpl.afmt, target_code):
                tts = True

    audio_raw = audio_notes / n if n else 0.0
    audio_coverage, silent_types, excluded = _audio_coverage(
        audio_raw, audio_by_type, notetype_counts, n
    )
    if tts and audio_coverage < 1.0:
        # A template that speaks the card needs no sound file.
        audio_coverage, silent_types, excluded = 1.0, [], 0

    dup_groups = sum(1 for c in first_field_seen.values() if c > 1)
    cloze_checked, cloze_shown, cloze_examples = _cloze_leaks(deck)
    dup_notes = sum(c for c in first_field_seen.values() if c > 1)
    # A repeat inside one subdeck is a duplicate card. The same prompt in a
    # different subdeck is usually a recall-direction variant — one sentence
    # offered as reading, as listening and as production — which is a choice
    # the learner makes, not a fault. The two are counted separately.
    parallel_notes = sum(
        count
        for key, count in first_field_seen.items()
        if count > 1 and len(first_field_decks.get(key, ())) > 1
    )

    # A field counts as expected when most notes of that type fill it. Notes
    # missing an expected field have a real gap; a field the note type never
    # uses is not a defect.
    expected: dict[int, set[int]] = {
        nt_id: {i for i, filled in counts.items() if filled >= 0.6 * per_type_counts[nt_id]}
        for nt_id, counts in field_fill.items()
    }
    incomplete = sum(
        1 for nt_id, filled in filled_by_note if expected.get(nt_id, set()) - filled
    )

    # ---- vocabulary and progression ------------------------------------

    seen: set[str] = set()
    per_note_new: list[int] = []
    per_note_total: list[int] = []
    missing_text = 0

    # A deck that offers the same sentences in several recall directions —
    # read it, hear it, produce it — is giving the learner a menu, not teaching
    # the material three times. Walking every copy would show the repeat
    # strands as long stretches introducing no new vocabulary, which is an
    # artifact of how the deck was packaged and not anything a learner meets:
    # they study one strand. So the curve follows each piece of material the
    # first time it appears, and the repeats are counted and reported.
    # A measured field can still hold lines in the explanation language: the
    # stage directions of a dialogue ("Claire takes the package."). Words like
    # "package" pass the word-level filter because French borrows them, so
    # the line is judged as a whole.
    lines_dropped = 0
    other_fields = list(companions)
    # Kept whole for one check below: a French example inside an English
    # explanation line is still the word appearing on a grammar card.
    explanation_full = list(explanation_texts)
    line_filter = fq.explanation_filter(
        lx.Lexicon(language=lx.language_code(language)), explanation_language or ""
    )
    if line_filter is not None:
        kept_texts = []
        for text in texts:
            kept, dropped = fq.drop_explanation_lines(text, line_filter)
            kept_texts.append(kept)
            lines_dropped += dropped
        texts = kept_texts
        explanation_texts = [fq.drop_explanation_lines(t, line_filter)[0] for t in explanation_texts]
        # Only the translation tells a name apart: the note's other French
        # fields repeat a capitalized "Inutile" or "Justement" just as well.
        # The other way round, only French lines clear one: "nice" in an
        # English gloss says nothing about the city.
        def english_lines(text: str, keep: bool) -> str:
            return "\n".join(
                line for line in text.split("\n")
                if line.strip() and line_filter.line_is_explanation(
                    [w.lower() for w in tx.WORD_RE.findall(line)]
                ) is keep
            )

        companions = [english_lines(c, True) for c in companions]
        other_fields = [english_lines(c, False) for c in other_fields]
    else:
        companions = [""] * len(texts)

    unique_texts: list[str] = []
    material_seen: dict[str, int] = {}
    #: (position in unique_texts, a dictionary form that note states)
    stated_forms: list[tuple[int, str]] = []
    unique_companions: list[str] = []
    #: per entry of unique_texts: the forms its first note states
    unique_stated: list[list[str]] = []
    for text, forms, beside in zip(texts, stated, companions):
        # Keyed on the text itself, not on its word tokens: tokenizing drops
        # digits, which would merge "I am 20 years old" into "I am 30 years old".
        key = " ".join(text.split()).casefold()
        if key and key in material_seen:
            stated_forms += [(material_seen[key], f) for f in forms]
            unique_companions[material_seen[key]] += "\n" + beside
            continue
        if key:
            material_seen[key] = len(unique_texts)
        stated_forms += [(len(unique_texts), f) for f in forms]
        unique_stated.append(forms)
        unique_texts.append(text)
        unique_companions.append(beside)
    repeated_notes = len(texts) - len(unique_texts)

    lex = lexicon or lx.build(unique_texts, language, unique_companions, other_fields)
    tokens = [lex.words(text) for text in unique_texts]

    #: per note: unfamiliar items on the card, headword counted once
    per_note_items: list[int] = []
    headword_sentences = 0
    for toks, forms in zip(tokens, unique_stated):
        if not toks:
            missing_text += 1
        headword = {t for f in forms for t in lex.words(f)}
        new = [t for t in toks if t not in seen]
        per_note_items.append(card_new_items(new, headword, seen))
        if headword and len(toks) >= SENTENCE_MIN_WORDS:
            headword_sentences += 1
        seen.update(toks)
        # The card names the word it teaches, so later cards meet it as known.
        seen.update(headword)
        per_note_new.append(len(new))
        per_note_total.append(len(toks))

    analysed = sum(1 for t in per_note_total if t)
    buckets: list[Bucket] = []
    if analysed >= bucket_count * 2:
        span = len(unique_texts)
        size = span / bucket_count
        running: set[str] = set()
        cursor = 0
        for i in range(bucket_count):
            start = int(round(i * size))
            end = int(round((i + 1) * size)) if i < bucket_count - 1 else span
            chunk_new = per_note_new[start:end]
            chunk_tot = per_note_total[start:end]
            chunk_items = per_note_items[start:end]
            live = [(a, b) for a, b in zip(chunk_new, chunk_tot) if b]
            sentence_rows = [
                (a, b, c)
                for a, b, c in zip(chunk_new, chunk_tot, chunk_items)
                if b >= SENTENCE_MIN_WORDS
            ]
            while cursor < end:
                running.update(tokens[cursor])
                cursor += 1
            if not live:
                continue
            new_counts = [a for a, _ in live]
            tot_counts = [b for _, b in live]
            # The i+1 reading only means something for sentences. A one-word
            # vocabulary card introduces one new word by construction, and
            # counting those would flatter every vocabulary deck.
            sentences = [c for _, _, c in sentence_rows]
            sentence_tokens = sum(b for _, b, _ in sentence_rows)
            buckets.append(
                Bucket(
                    index=i + 1,
                    start=start + 1,
                    end=end,
                    note_count=len(live),
                    new_word_rate=sum(new_counts) / sum(tot_counts),
                    mean_words=statistics.fmean(tot_counts),
                    median_words=statistics.median(tot_counts),
                    mean_new_words=statistics.fmean(new_counts),
                    share_at_most_one_new=(
                        sum(1 for c in sentences if c <= 1) / len(sentences)
                        if sentences
                        else None
                    ),
                    sentence_notes=len(sentences),
                    cumulative_vocab=len(running),
                    sentence_new_word_rate=(
                        sum(a for a, _, _ in sentence_rows) / sentence_tokens
                        if sentence_tokens
                        else None
                    ),
                )
            )

    bounds = []
    if buckets:
        span = len(unique_texts)
        size = span / bucket_count
        bounds = [
            (
                i + 1,
                int(round(i * size)),
                int(round((i + 1) * size)) if i < bucket_count - 1 else span,
            )
            for i in range(bucket_count)
        ]
    freq = fq.compute(
        lex,
        unique_texts,
        bounds,
        language or "",
        explanation_language or "",
        frequency_list,
        stated_forms,
    )

    # Explanation notes, measured on their own: the target-language words they
    # use that the material never does. English explanation text is set aside
    # the same way the frequency comparison sets it aside.
    english = fq.explanation_filter(lex, explanation_language or "")
    material_keys = {k for t in unique_texts for k, _ in fq.deck_keys(lex, t)}
    explanation_only: list[str] = []
    noted: set[str] = set()
    explained: set[str] = set()
    for text, strict in [(t, False) for t in explanation_texts] + [
        (t, True) for t in explanation_full
    ]:
        for key, surface in fq.deck_keys(lex, text):
            word = surface.rstrip("'")
            # Inside an English line a word must be rare in English to count
            # as French: "the action to itself", "a habit or repeated action".
            if english and (
                english.leans(word) or (strict and english.english_zipf(word) >= 3.0)
            ):
                continue
            explained |= {key, word.lower()}
    for text in explanation_texts:
        for key, surface in fq.deck_keys(lex, text):
            word = surface.rstrip("'")
            if key in material_keys or key in noted or len(key) < 3:
                continue
            # Only target-language words: on the target list, and not more
            # common in the explanation language. That leaves the terminology
            # ("masculin", "partitif") and drops English and pronunciation
            # respellings ("eel-zon").
            if english and (english.leans(word) or not english.on_target_list(word)):
                continue
            noted.add(key)
            explanation_only.append(fq.shown(lex, key, surface))
    # A common word missing from the material may still be explained once on
    # a grammar card. That is not the same as never appearing, so say which.
    freq.missing_explained = [w for _, w in freq.missing if w.lower() in explained]

    total_tokens = sum(per_note_total)
    sentence_items = [
        c for c, t in zip(per_note_items, per_note_total) if t >= SENTENCE_MIN_WORDS
    ]
    entry_notes = sum(1 for t in per_note_total if 0 < t < SENTENCE_MIN_WORDS)
    warnings: list[str] = list(extra_warnings or [])
    if analysed < n * 0.5:
        warnings.append(
            "Over half the notes had no readable target-language text, so the "
            "progression measurements cover only part of the deck."
        )

    return Metrics(
        note_count=n,
        card_count=deck.card_count,
        notetype_counts=dict(notetype_counts.most_common()),
        deck_tree=deck.deck_names,
        subdeck_count=max(0, len([d for d in deck.deck_names if "::" in d])),
        audio_coverage=audio_coverage,
        audio_coverage_raw=audio_raw,
        audio_by_notetype={
            name: (audio_by_type.get(name, 0), count)
            for name, count in notetype_counts.items()
        },
        silent_notetypes=silent_types,
        audio_excluded_notes=excluded,
        image_coverage=image_notes / n if n else 0.0,
        notes_with_audio=audio_notes,
        notes_with_images=image_notes,
        uses_tts_directive=tts,
        distinct_audio_files=len(audio_files),
        distinct_image_files=len(image_files),
        has_cloze=has_cloze,
        has_reverse_cards=has_reverse,
        templates_per_notetype=templates_per_notetype,
        cards_per_note=deck.card_count / n if n else 0.0,
        duplicate_note_groups=dup_groups,
        duplicate_note_share=dup_notes / n if n else 0.0,
        parallel_strand_notes=parallel_notes,
        repeated_material_notes=repeated_notes,
        empty_field_share=incomplete / n if n else 0.0,
        analysed_notes=analysed,
        total_vocabulary=len(seen),
        mean_words_per_note=total_tokens / analysed if analysed else 0.0,
        type_token_ratio=len(seen) / total_tokens if total_tokens else 0.0,
        buckets=buckets,
        grammar_note_share=grammar_notes / n if n else 0.0,
        tag_examples=[t for t, _ in tag_counter.most_common(25)],
        notes_missing_target_text=missing_text,
        warnings=warnings,
        lexicon=lex,
        frequency=freq,
        explanation_types=dict(explanation_counts),
        explanation_only_words=explanation_only,
        lemma_field_words=freq.stated_only,
        unmeasured_types=dict(unmeasured),
        explanation_lines_dropped=lines_dropped,
        cloze_deletions_checked=cloze_checked,
        cloze_answers_shown=cloze_shown,
        cloze_leak_examples=cloze_examples,
        entry_notes=entry_notes,
        sentence_notes=len(sentence_items),
        i_plus_one_share=(
            sum(1 for c in sentence_items if c <= 1) / len(sentence_items)
            if sentence_items
            else None
        ),
        headword_sentences=headword_sentences,
    )


def curve_table(metrics: Metrics) -> str:
    """The new-word curve as a plain-text table for the model and the report."""
    if getattr(metrics.lexicon, "unsegmented", False):
        return (
            "(not measured: the script is written without spaces between words "
            "and there is no word splitter for it)"
        )
    if not metrics.buckets:
        return "(deck too small to measure a progression curve)"
    lines = [
        "part |  notes | new-word rate | sentences | in sentences | <=1 new word | mean words | vocab so far",
        "-----+--------+---------------+-----------+--------------+--------------+------------+-------------",
    ]

    def share(value: float | None) -> str:
        return f"{value:>12.1%}" if value is not None else f"{'-':>12}"

    for b in metrics.buckets:
        lines.append(
            f"{b.index:>4} | {b.note_count:>6} | "
            f"{b.new_word_rate:>12.1%} | {b.sentence_notes:>9} |"
            f"{share(b.sentence_new_word_rate)} |{share(b.share_at_most_one_new)} | "
            f"{b.mean_words:>10.1f} | {b.cumulative_vocab:>12,}"
        )
    lines.append("")
    lines.append(
        f"'sentences' counts the notes in that part holding at least "
        f"{SENTENCE_MIN_WORDS} words; 'in sentences' is the new-word rate over those "
        "alone. The i+1 column is measured over them too, since a one-word "
        "vocabulary card introduces one new word by construction."
    )
    note = comparability_note(metrics.lexicon)
    if note:
        lines.append(note)
    return "\n".join(lines)


def comparability_note(lexicon: lx.Lexicon) -> str:
    """Said next to the curve when its figures don't compare with word counts."""
    if lexicon.segmented:
        return (
            f"Split into words with {lexicon.tool}, where one word is often a "
            "single particle or ending: compare this curve only with decks in "
            "the same language measured the same way."
        )
    if lexicon.method == lx.METHOD_CHARACTER:
        return (
            "Counted per character, not per word: this curve and its i+1 share "
            "can't be compared with decks whose words are counted."
        )
    if lexicon.language == "ko" and not tx.korean_morphemes():
        return (
            "Korean is counted by spelled form here, because Kiwi isn't installed: "
            "a word with a different particle or ending counts as new. Compare "
            "this curve only with Korean decks counted the same way."
        )
    return ""


def iter_target_texts(
    deck: Deck, target_fields: dict[int, int] | None = None
) -> Iterable[str]:
    target_fields = target_fields or {}
    for note in deck.notes:
        yield _target_text(note, target_fields.get(note.notetype_id))
