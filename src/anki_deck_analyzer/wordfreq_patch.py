"""wordfreq lists with their noise taken out, from a per-language patch.

wordfreq is a good general list with four kinds of noise: English and other
foreign words, misspellings (French "ca" for "ça", German "strasse"), names,
and junk. `scripts/clean_wordfreq.py` builds a patch per language against a
spelling dictionary; the patches ship in `data/wordfreq_patches/` and hold
words and decisions only, never wordfreq's frequencies. Applying one needs
nothing but the user's own wordfreq.

`apply_patch` is a copy of the one in `scripts/clean_wordfreq.py`. Keep the
two in step.
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

PATCH_DIR = os.path.join(os.path.dirname(__file__), "data", "wordfreq_patches")
_PATCH_RE = re.compile(r"wordfreq_patch_([a-z]+)_(rev\w+)\.json$")

#: Languages whose dictionary was too thin to judge unknown words: their
#: patches remove names and foreign leak, and keep every unknown word. The
#: patch records "full" for them, so the set is mirrored from the build
#: script's WEAK_DICT.
WEAK_DICT = {"hu", "nb", "hi", "vi", "ko"}


def apply_patch(patch: dict, top_n: int | None = None,
                keep_names: bool = False) -> list[tuple[str, float]]:
    """The cleaned list as (word, frequency), most frequent first.

    Names (capitalized-only words: people, places, brands, but also countries,
    nationalities, and in Dutch/Greek months and days) are listed apart in the
    patch. keep_names=False leaves them out: right for the deck analyzer, which
    leaves names out of the deck's words too. A course keeps them (True), so
    "Deutschland" or "Σεπτεμβρίου" stay on its list.
    """
    from wordfreq import top_n_list, word_frequency  # type: ignore

    lang = patch["lang"]
    merge, drop, leak = patch["merge"], patch["drop"], patch["leak"]
    skip = set(drop) if keep_names else set(drop) | set(patch.get("names", ()))
    freq: dict[str, float] = defaultdict(float)
    for w in top_n_list(lang, top_n or patch["top_n"]):
        if w in skip:
            continue
        freq[merge.get(w, w)] += word_frequency(w, lang)
    out = []
    for w, f in freq.items():
        if w in skip:                    # a merge target the leak explains
            continue
        f -= sum(word_frequency(w, s) * 10 ** (-d) for s, d in leak.items())
        if f > 0:
            out.append((w, f))
    out.sort(key=lambda kv: (-kv[1], kv[0]))
    return out


@dataclass
class Cleaned:
    """A cleaned word order, and what the report says about it."""

    words: list[str]
    #: e.g. "cleaned (patch fr rev261022a)"
    label: str
    #: Caveats for the report, one sentence each: a weak or partial patch, a
    #: version mismatch.
    notes: list[str]


def patch_path(lang: str) -> tuple[str, str] | None:
    """(path, revision) of the newest patch for `lang`, or None."""
    try:
        names = os.listdir(PATCH_DIR)
    except OSError:
        return None
    found = sorted(
        (m.group(2), n) for n in names if (m := _PATCH_RE.match(n)) and m.group(1) == lang
    )
    if not found:
        return None
    rev, name = found[-1]
    return os.path.join(PATCH_DIR, name), rev


def _coverage_note(lang: str, mode: str) -> str:
    if mode.startswith("partial"):
        why = "no dictionary" if "no dictionary" in mode else "no usable dictionary"
        return (
            f"The cleaning is partial: {why}, so only foreign words, chat tokens "
            "and junk were removed; misspellings and names stay on the list."
        )
    if lang in WEAK_DICT:
        return (
            "The cleaning is weak: the dictionary is thin, so names and foreign "
            "words were removed and words it does not know were kept."
        )
    return ""


@lru_cache(maxsize=None)
def cleaned(lang: str, wordfreq_version: str) -> tuple[Cleaned | None, str]:
    """The cleaned word order for `lang`, or (None, why it is raw)."""
    found = patch_path(lang)
    if found is None:
        return None, f"there is no cleaning patch for '{lang}'"
    path, rev = found
    with open(path, encoding="utf-8") as fh:
        patch = json.load(fh)
    try:
        ranked = apply_patch(patch)
    except ImportError as exc:
        # Korean: word_frequency needs MeCab.
        fix = (
            '; it needs MeCab: pip install "wordfreq[mecab]", which on Windows also '
            "needs MeCab itself installed"
        ) if lang == "ko" else ""
        return None, f"the '{lang}' patch could not be applied ({exc}{fix})"
    notes = []
    coverage = _coverage_note(lang, patch.get("mode", ""))
    if coverage:
        notes.append(coverage)
    built_on = patch.get("wordfreq_version", "")
    if built_on and wordfreq_version and built_on != wordfreq_version:
        notes.append(
            f"The patch was built on wordfreq {built_on} and applied to "
            f"{wordfreq_version}; entries new in {wordfreq_version} were not cleaned."
        )
    return Cleaned([w for w, _ in ranked], f"cleaned (patch {lang} {rev})", notes), ""
