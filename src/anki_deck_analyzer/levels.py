"""Level tags: the author's own study path, when the deck tags one.

Some decks are shipped in one order and meant to be studied in another. A
word list tagged d1-d4 whose description tells beginners to suspend d2-d4 is
studied lowest level first, whatever the cards' new-card positions say. Read
only in the default order, its first cards are advanced subtitle sentences and
its greetings sit thousands of cards in. So when the tags describe levels, the
deck is measured a second time in tag order and both readings are reported.
"""

from __future__ import annotations

import dataclasses
import re
import statistics
from dataclasses import dataclass, field

from .apkg import Deck

#: A family counts only if this share of notes carries one of its tags.
MIN_COVERAGE = 0.60
#: The default order already follows the tags when no more than this share of
#: cross-level pairs has the higher level first.
ALREADY_SORTED = 0.05

_CEFR = ["a1", "a2", "b1", "b2", "c1", "c2"]
_WORDS = {
    "beginner": 1, "beginners": 1, "elementary": 2, "preintermediate": 2,
    "intermediate": 3, "upperintermediate": 4, "advanced": 5, "proficient": 6,
}
#: Prefixes that mark a numbered level, lowest number easiest.
_NUMBERED = (
    "d", "diff", "difficulty", "level", "lvl", "lv", "lev", "stufe", "niveau",
    "nivel", "livello", "tier", "grade", "stage", "hsk", "topik",
)
#: Of those, the ones trusted with a three-digit number (frequency bands).
_LONG_NUMBERED = ("level", "lvl", "lev", "stufe", "niveau", "nivel", "livello")
#: With more levels than this, the report summarizes them instead of listing.
LIST_LEVELS_MAX = 8
#: Up to this many levels, one filtered deck or a suspend per level follows
#: the path. Past it, the learner has to reposition the whole deck.
EASY_LEVELS_MAX = 6


@dataclass
class LevelTags:
    """One family of level tags found on the deck."""

    #: How the family is named in the report, e.g. "d1-d4" or "CEFR A1-B2".
    family: str
    #: Level labels, easiest first, as the deck spells them.
    levels: list[str]
    #: label -> notes carrying it
    counts: dict[str, int]
    coverage: float
    #: Of all pairs of notes from different levels, the share where the higher
    #: level comes first in the default order. Near zero means the default
    #: order already follows the tags; about half means it ignores them.
    out_of_order: float
    #: label -> where its notes sit in the default order, as the median
    #: position in percent of the deck.
    median_position: dict[str, float] = field(default_factory=dict)
    #: note id -> rank (0 = easiest). Notes without a level tag are absent.
    rank_of: dict[int, int] = field(default_factory=dict, repr=False)

    @property
    def follows_default_order(self) -> bool:
        return self.out_of_order <= ALREADY_SORTED

    @property
    def easy_to_follow(self) -> bool:
        """Whether a learner can follow the path with one filter or suspend."""
        return len(self.levels) <= EASY_LEVELS_MAX

    def how_to_follow(self) -> str:
        """The steps a learner takes in Anki to study the deck in tag order."""
        first, rest = self.levels[0], self.levels[1:]
        if self.easy_to_follow:
            higher = " or ".join(f"tag:{lvl}" for lvl in rest)
            return (
                f"create a filtered deck with the search tag:{first} and work up a "
                f"level at a time, or suspend the higher levels ({higher}) and "
                "unsuspend each in turn"
            )
        return (
            f"reposition the whole deck, since with {len(self.levels)} levels "
            "filtering level by level is impractical: in the browser, sort the "
            "cards by the level (the Tags column, or a field holding the level set "
            "as the sort field), select all, then Cards > Reposition"
        )

    def describe(self) -> str:
        shown = self.levels
        if len(shown) > LIST_LEVELS_MAX:
            # 161 frequency bands: the first, a few in between, the last.
            step = (len(shown) - 1) / (LIST_LEVELS_MAX - 1)
            shown = [shown[round(i * step)] for i in range(LIST_LEVELS_MAX)]
            parts = (
                f"{len(self.levels)} levels, {self.levels[0]} to {self.levels[-1]}, "
                f"about {statistics.median(self.counts.values()):.0f} notes each"
            )
        else:
            parts = ", ".join(f"{lvl} {self.counts[lvl]:,}" for lvl in shown)
        where = ", ".join(
            f"{lvl} {self.median_position[lvl]:.0f}%"
            for lvl in shown
            if lvl in self.median_position
        )
        text = (
            f"The notes carry level tags ({self.family}): {parts}; "
            f"{self.coverage:.0%} of the deck is tagged. "
        )
        if self.follows_default_order:
            return text + "The default order already follows them."
        return text + (
            f"The default order does not follow them: in {self.out_of_order:.0%} "
            "of the pairs of notes from different levels, the higher level comes "
            "first (0% would follow the tags, about 50% ignores them). How far "
            f"into the deck the median note of each level sits: {where}."
        )


def _key(tag: str) -> str:
    """The last part of a hierarchical tag, lowercased, separators dropped."""
    last = tag.split("::")[-1].lower()
    return re.sub(r"[\s_\-.:]", "", last)


def _classify(tag: str) -> tuple[str, int] | None:
    """(family, rank) for a tag that names a level, else None."""
    key = _key(tag)
    if not key:
        return None
    m = re.fullmatch(r"(?:cefr)?([abc][12])", key)
    if m:
        return "cefr", _CEFR.index(m.group(1))
    m = re.fullmatch(r"jlptn?([1-5])|n([1-5])", key)
    if m:
        # N5 is the easiest JLPT level.
        return "jlpt", 5 - int(m.group(1) or m.group(2))
    if key in _WORDS:
        return "words", _WORDS[key]
    m = re.fullmatch(r"([a-z]+)(\d{1,3})", key)
    if m and m.group(1) in _NUMBERED:
        # Three digits only after a word that says "level": Level161 is a
        # frequency band, d161 could be anything.
        if len(m.group(2)) == 3 and m.group(1) not in _LONG_NUMBERED:
            return None
        return m.group(1), int(m.group(2))
    return None


def _family_name(family: str, labels: list[str]) -> str:
    span = f"{labels[0]}-{labels[-1]}" if len(labels) > 1 else labels[0]
    if family == "cefr":
        return f"CEFR {span.upper()}"
    if family == "jlpt":
        return f"JLPT {span.upper()}"
    return span


def detect(deck: Deck) -> LevelTags | None:
    """The best-covered level-tag family on `deck`, or None."""
    notes = deck.notes
    if len(notes) < 20:
        return None
    #: family -> rank -> label as spelled on the most notes
    spellings: dict[str, dict[int, dict[str, int]]] = {}
    #: family -> note id -> lowest rank on that note
    ranks: dict[str, dict[int, int]] = {}
    for note in notes:
        for tag in note.tags:
            hit = _classify(tag)
            if not hit:
                continue
            family, rank = hit
            by_rank = spellings.setdefault(family, {}).setdefault(rank, {})
            by_rank[tag] = by_rank.get(tag, 0) + 1
            fam = ranks.setdefault(family, {})
            fam[note.id] = min(rank, fam.get(note.id, rank))

    best: LevelTags | None = None
    for family, by_note in ranks.items():
        coverage = len(by_note) / len(notes)
        ordered = sorted(spellings[family])
        if coverage < MIN_COVERAGE or len(ordered) < 2:
            continue
        label_of = {
            r: max(spellings[family][r], key=spellings[family][r].get) for r in ordered
        }
        dense = {r: i for i, r in enumerate(ordered)}
        rank_of = {nid: dense[r] for nid, r in by_note.items()}
        counts: dict[str, int] = {}
        positions: dict[str, list[int]] = {}
        # Pairs of notes from different levels where the higher level comes
        # first. 0 when the deck follows its tags, about half when it ignores
        # them, all of them when it runs backward.
        before = [0] * len(ordered)
        reversed_pairs = 0
        for pos, note in enumerate(notes):
            r = rank_of.get(note.id)
            if r is None:
                continue
            label = label_of[ordered[r]]
            counts[label] = counts.get(label, 0) + 1
            positions.setdefault(label, []).append(pos)
            reversed_pairs += sum(before[r + 1:])
            before[r] += 1
        cross_pairs = (len(rank_of) ** 2 - sum(c * c for c in before)) // 2
        levels = [label_of[r] for r in ordered]
        found = LevelTags(
            family=_family_name(family, levels),
            levels=levels,
            counts=counts,
            coverage=coverage,
            out_of_order=reversed_pairs / cross_pairs if cross_pairs else 0.0,
            median_position={
                lvl: 100 * statistics.median(p) / len(notes) for lvl, p in positions.items()
            },
            rank_of=rank_of,
        )
        if best is None or found.coverage > best.coverage:
            best = found
    return best


def in_tag_order(deck: Deck, tags: LevelTags) -> Deck:
    """`deck` with its notes sorted lowest level first, default order within a
    level, and untagged notes last."""
    last = len(tags.levels)
    order = sorted(
        range(len(deck.notes)),
        key=lambda i: (tags.rank_of.get(deck.notes[i].id, last), i),
    )
    return dataclasses.replace(
        deck,
        notes=[deck.notes[i] for i in order],
        order_basis=f"level-tag order ({tags.family}, lowest first)",
    )


@dataclass
class StudyPath:
    """The deck read a second time, lowest level first."""

    tags: LevelTags
    deck: Deck
    #: metrics.Metrics for `deck`, measured exactly as the default order was.
    metrics: object
