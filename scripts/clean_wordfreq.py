"""Clean a wordfreq top list: build a per-language patch, then apply it.

wordfreq is a good general list with four kinds of noise: English and other
foreign words (web pages, tweets), misspellings (French "ca" for "ça", every
German ß written "ss", Romanian and Polish without diacritics, Persian without
its zero-width non-joiner), names and places, and junk (abbreviations,
laughter, letters, digits). Cleaning it takes a spelling dictionary, so the
work is split in two:

  build  (Hunspell via spylls; German also spaCy) -> a small JSON patch:
         which entries merge into which spelling, which are dropped and why,
         and the measured rate at which other languages leak into this one.
  apply  (wordfreq only) -> the cleaned ranked list, from the patch and the
         user's own wordfreq. A consumer ships the patch, never wordfreq data.

The rule: every kept entry is a word the language's dictionary knows, after
its spelling has been repaired where it can be. In order:
  1. digits/symbols, another script than the language's own,
     stray letters, affix fragments                           -> drop
  2. spoken spellings (table SPOKEN)                         -> merge
  3. unknown entry the foreign leak explains (step 6)        -> drop, BEFORE
     any repair, so "hard" never becomes Danish "hård"
     unknown entry with a known spelling twin                -> merge
     (accents, ß, ue/oe/ae, œ, ł, ё, ZWNJ, Arabic hamza/yeh, Persian kaf/yeh),
     only into a MORE frequent twin, and never for a name
  4. known only capitalized: a name or place                 -> listed in
     "names", not dropped: apply_patch(keep_names=...) decides (analyzer
     leaves them out, a course keeps countries, nationalities, months)
     German capitalizes nouns: there the dictionary's affix flags propose
     (genitive -s only: Anna/S, Wien/Sm) and a tagger confirms (PROPN)
  5. still unknown: english / chat / abbreviation / other    -> drop
     (KEEP_UNKNOWN languages keep them: Persian's formal dictionary misses
     the colloquial forms the decks teach)
  6. foreign leak: each source language leaks at a stable rate, measured as
     the median Zipf gap on its common words this dictionary rejects. The
     expected leak is subtracted from every entry; an entry it explains for
     >= LEAK_DROP is dropped.
A language without a Hunspell dictionary gets a PARTIAL patch: steps 1, 2, the
chat tokens and 6 (its leak probes are the source's words that are clearly
more common there); everything else is kept. zh/ja need word segmentation and
are not covered.

Environment: a venv of its own, since spylls is only needed to build patches:
    python -m venv .venv-wf && .venv-wf/Scripts/pip install wordfreq==3.1.1 spylls regex
    German only, in the same venv: pip install spacy && python -m spacy download de_core_news_md

Hunspell dictionaries, one folder, files named <lang>.dic / <lang>.aff (plus en):
    https://github.com/wooorm/dictionaries  dictionaries/<lang>/index.dic, index.aff
    Arabic (not in wooorm): https://github.com/LibreOffice/dictionaries  ar/ar.dic, ar.aff
  Licences vary by language (MPL/LGPL/GPL); they are build inputs, never shipped.

Usage:
    python clean_wordfreq.py build fr --hunspell-dir DIR --out-dir DIR [--top-n 50000]
    python clean_wordfreq.py build de --hunspell-dir DIR --out-dir DIR --names-tagger spacy:de_core_news_md
    python clean_wordfreq.py show PATCH.json [--top 100]
Python use:
    from clean_wordfreq import apply_patch
    ranked = apply_patch(json.load(open(patch)))   # [(word, frequency), ...] most frequent first
    ranked = apply_patch(json.load(open(patch)), keep_names=True)   # a course: names stay
"""

from __future__ import annotations

import argparse
import datetime
import itertools
import json
import math
import statistics
import string
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import regex
from wordfreq import top_n_list, word_frequency, zipf_frequency

RULES_VERSION = "1.0"
ZWNJ = "‌"

# --------------------------------------------------------------------------
# per-language tables (small, hand-checked; a new language needs ONE_LETTER)
# --------------------------------------------------------------------------

#: One-letter words, including elided pronouns wordfreq stores bare (French
#: "l" for l'). Every other single letter is a letter name or an initial. A
#: language must be listed here before it can be built. None: no letter rule
#: (Korean and Vietnamese write many one-character words; their dictionaries
#: reject the stray Latin letters anyway).
ONE_LETTER = {
    "fr": "àyaôldjcnsmt", "it": "eèaoildcsmtvn", "es": "aeouy", "pt": "aeoàé",
    "ru": "вскуоиая", "ro": "aoeî", "pl": "wziaou", "cs": "aikosuvz", "sk": "aikosuvz",
    "sh": "aikosuvz", "sl": "ahkosvz", "nl": "u", "de": "", "ar": "و", "fa": "و",
    "ur": "و", "bg": "аивсу", "mk": "иаев", "uk": "вуійзаоя", "el": "οηήστμ",
    "ca": "aioeldsmtn", "da": "iåø", "nb": "iå", "sv": "iå", "is": "áí", "hu": "aső",
    "lt": "oį", "lv": "", "tr": "o", "he": "", "hi": "व", "bn": "ওএ", "ta": "",
    "id": "", "ms": "", "fi": "", "fil": "o", "ko": None, "vi": None,
}
#: Scripts a word can be written in; a word in another script than the
#: language's own is foreign (Latin words in a Russian or Arabic list).
SCRIPTS = ["Latin", "Cyrillic", "Greek", "Arabic", "Hebrew", "Hangul", "Devanagari",
           "Bengali", "Tamil"]
#: Letters that are not "letter + combining mark", so NFD stripping misses them.
FOLD = str.maketrans({"ß": "ss", "œ": "oe", "æ": "ae", "ł": "l", "ø": "o",
                      "đ": "d", "ı": "i",
                      # Arabic script: yeh/alef maqsura, kaf, teh marbuta, ZWNJ
                      "ي": "ی", "ى": "ی", "ك": "ک", "ة": "ه", ZWNJ: "",
                      "ς": "σ"})          # Greek final sigma
#: Rewrites tried on an unknown word before the twin lookup.
REWRITE = {
    "ro": [(r"^sînt", "sunt"), (r"(?<=\p{L})î(?=\p{L})", "â"), (r"ã", "ă")],
    "de": [(r"ue", "ü"), (r"ae", "ä"), (r"oe", "ö")],
    "fr": [(r"^(c|j|l|d|n|s|t|m|qu)(?=est$|ai$|ont$)", r"\1'")],
    "fa": [(r"ك", "ک"), (r"[يى]", "ی")],   # Arabic letter forms in Persian text
    "it": [(r"è$", "é")],                   # perchè, nè, sè: wrong accent direction
    "el": [(r"σ$", "ς")],                   # wordfreq's casefold turns final ς into σ
}
#: Dictionaries per language when it is not just <lang>.dic. Serbo-Croatian:
#: Croatian (ijekavian) and Serbian Latin (ekavian: vreme, mesto) together.
#: Portuguese: Brazilian and European spellings (acção, prémio) together.
DICTS = {"sh": ["hr", "sr-Latn"], "pt": ["pt", "pt-PT"]}
#: Dictionaries spylls cannot use to decide what is a word (Hungarian affixes
#: fail: könyvet; Norwegian lacks noe/noen; Hindi rejects रहेगा; Vietnamese
#: tones are phonemic, so a "repair" changes the word: hòa -> hoa). They still
#: spot names; unknown entries are kept, nothing is repaired, and English is
#: caught by the measured leak only (the English dictionary knows hu "te").
#: Korean: wordfreq splits words into MeCab morphemes the dictionary rejects.
WEAK_DICT = {"hu", "nb", "hi", "vi", "ko"}
#: Languages where a stripped accent is not a typing habit: no twin lookup.
ACCENT_REPAIR_OFF = {"de"} | WEAK_DICT
#: A twin must be at least this many times as frequent as the misspelling.
#: Arabic: a missing hamza is a misspelling even at a tie (انا / أنا).
TWIN_RATIO = {"ar": 0.8}
#: Russian е for ё: merge only when the ё form carries at least this share. A
#: true ё-word keeps its ё in a good share of texts (ещё ~25%); a distinct
#: е-word almost never (чем/чём ~1%, все/всё 14%).
YO_SHARE = 0.15
#: Words every list keeps although the dictionaries lack them.
KEEP_ALL = {"ok", "okay"}
#: Spoken or elided forms kept as words.
KEEP = {
    "it": {"dell", "nell", "dall", "sull", "coll", "quell", "bell", "sant", "quest"},
    "fr": {"aujourd'hui"},
    "de": {"na", "nee", "nö", "naja", "tja", "aha", "äh", "hm", "sowas", "jungs"},
}
#: Spoken spellings merged into the standard word.
SPOKEN = {
    "de": {"nen": "einen", "nem": "einem", "ner": "einer", "nich": "nicht",
           "würd": "würde", "wär": "wäre", "kenn": "kenne", "seh": "sehe",
           "gibt's": "gibt", "gibts": "gibt", "geht's": "geht", "gehts": "geht",
           "wenn's": "wenn", "habs": "habe", "wieviel": "wie viel", "fuer": "für"},
    "fr": {"nan": "non", "ya": "y a"},
    "cs": {"dobrej": "dobrý", "každej": "každý", "nějakej": "nějaký",
           "takovej": "takový", "kterej": "který", "celej": "celý", "bejt": "být",
           "svý": "své", "tý": "té", "seš": "jsi"},
    "pt": {"vc": "você"},
    "ru": {"ок": "ok"},
    "ar": {"شئ": "شيء"},
}
#: The dictionary is too formal or too strict for real usage: what it does not
#: know is kept. Persian: colloquial Tehrani (یه, داره); Indonesian: spoken and
#: everyday spellings (nggak, gimana, disini); Hebrew: hspell rejects common
#: spellings (היתה, אמא).
KEEP_UNKNOWN = {"fa", "id", "he"} | WEAK_DICT
#: Affixes written apart from their word (a space where a ZWNJ belongs).
FRAGMENTS = {"fa": {"ها", "های", "هایی", "ام", "اش", "ات", "اند", "ایم", "اید", "ترین",
                    "تر", "شون", "مون", "تون", "اس", "هاست", "العاده", "المللی", "می", "نمی"}}
CHAT = regex.compile(r"^(?:ha|he|ja|je|hi|ah|ka|ke|xa){2,}h?$"
                     r"|^(?:xd+|lol|lmao|mdr|ptdr|kkk+|hm+|ahah\w*|haha\w*|hehe\w*)$")
#: Languages whose nouns are capitalized: capital-only is normal there.
CAP_NOUNS = {"de"}
NAME_FLAGS = set("SmM")                 # German: genitive -s and its variants
#: Languages whose text leaks into others' lists (quotes, names, bilingual
#: pages), per script: a source in another script is caught by the script rule.
#: Ukrainian lists carry Russian (что, это, может); Persian lists carry Arabic.
LEAK_SOURCES = {"Latin": ["en", "fr", "es", "it", "pt", "de", "nl"],
                "Cyrillic": ["ru", "uk", "bg"],
                "Arabic": ["ar", "fa", "ur"]}
LEAK_PROBES = 30
#: Drop when the leak explains >= this share (checked on fr/de/it/es: the
#: 30-50% band held only foreign words: in, do, out, de, these, first).
LEAK_DROP = 0.3


def key(w: str) -> str:
    """Spelling-blind key: no accents, folded special letters, no ZWNJ."""
    w = unicodedata.normalize("NFD", w.translate(FOLD))
    return "".join(c for c in w if unicodedata.category(c) != "Mn")


# --------------------------------------------------------------------------
# apply (wordfreq only): what a consumer copies
# --------------------------------------------------------------------------


def apply_patch(patch: dict, top_n: int | None = None,
                keep_names: bool = False) -> list[tuple[str, float]]:
    """The cleaned list as (word, frequency), most frequent first.

    Names (capitalized-only words: people, places, brands, but also countries,
    nationalities, and in Dutch/Greek months and days) are listed apart in the
    patch. keep_names=False leaves them out: right for the deck analyzer, which
    leaves names out of the deck's words too. A course keeps them (True), so
    "Deutschland" or "Σεπτεμβρίου" stay on its list.
    """
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


# --------------------------------------------------------------------------
# build (Hunspell; German also a tagger)
# --------------------------------------------------------------------------


def _stem_flags(path: Path) -> dict[str, set[str]]:
    """Capitalized stem -> affix-flag strings (German name rule)."""
    out: dict[str, set[str]] = defaultdict(set)
    with open(path, encoding="utf-8", errors="replace") as fh:
        next(fh)
        for line in fh:
            if not line.strip() or line[0] in "\t#":
                continue
            stem, _, fl = line.split()[0].partition("/")
            if stem[:1].isupper():
                out[stem].add(fl)
    return out


class _Union:
    """Several dictionaries as one: a word is known if any of them knows it."""

    def __init__(self, dicts):
        self.dicts = dicts

    def lookup(self, w: str) -> bool:
        return any(d.lookup(w) for d in self.dicts)


def _script(w: str) -> str | None:
    for s in SCRIPTS:
        if regex.match(rf"\p{{Script={s}}}", w):
            return s
    return None


def _leak_rate(src: str, lang: str, known) -> float | None:
    """Median (source - target) Zipf gap over the source's common words that
    are foreign to the target. `known` is the target dictionary; without one,
    a word counts as foreign when it is clearly more common in the source."""
    if known is None:
        known = lambda w: zipf_frequency(w, lang) > zipf_frequency(w, src) - 1.5
    probes = [w for w in top_n_list(src, 400)
              if len(w) >= 2 and w.isalpha() and not known(w)][:LEAK_PROBES]
    gaps = [zipf_frequency(w, src) - zipf_frequency(w, lang) for w in probes
            if zipf_frequency(w, lang) > 0]
    if len(gaps) < 10:
        return None
    med = statistics.median(gaps)
    return statistics.median([g for g in gaps if abs(g - med) < 1.0])


def _tagger(spec: str | None):
    """`spacy:<model>` -> word -> True when tagged PROPN in most of 3 frames."""
    if not spec:
        return None
    kind, _, model = spec.partition(":")
    if kind != "spacy":
        raise SystemExit(f"unknown --names-tagger {spec!r}; use spacy:<model>")
    import spacy
    nlp = spacy.load(model, disable=["parser", "lemmatizer"])
    frames = ["Ich habe {} gesehen.", "Das ist {}.", "Wir sprechen über {}."]

    def is_propn(w: str) -> bool:
        W = w[:1].upper() + w[1:]
        tags = [next((t.pos_ for t in nlp(f.format(W)) if t.text == W), "?") for f in frames]
        return tags.count("PROPN") >= 2
    return is_propn


def build(lang: str, hunspell_dir: Path, top_n: int, names_tagger: str | None) -> dict:
    from spylls.hunspell import Dictionary

    if lang not in ONE_LETTER:
        raise SystemExit(f"'{lang}' has no ONE_LETTER entry: list its one-letter words first "
                         "(empty string if it has none)")
    if lang in CAP_NOUNS and not names_tagger:
        raise SystemExit(f"'{lang}' capitalizes nouns: pass --names-tagger spacy:<model>")
    # No dictionary for the language (or one spylls cannot read): a partial
    # patch (digits, letters, chat, spoken forms, foreign script, foreign leak);
    # every other entry is kept.
    dict_files = DICTS.get(lang, [lang])
    full, why_partial = all((hunspell_dir / f"{n}.dic").exists() for n in dict_files), "no dictionary"
    d = None
    if full:
        try:
            d = _Union([Dictionary.from_files(str(hunspell_dir / n)) for n in dict_files])
        except Exception as exc:         # e.g. bn/ta: .dic lines spylls cannot parse
            full, why_partial = False, f"dictionary unreadable: {exc}"[:200]
    en = Dictionary.from_files(str(hunspell_dir / "en"))
    flags = _stem_flags(hunspell_dir / f"{lang}.dic") if lang in CAP_NOUNS else {}
    is_propn = _tagger(names_tagger)
    main_script = Counter(_script(w) for w in top_n_list(lang, 300)).most_common(1)[0][0]
    one_letter = ONE_LETTER[lang]

    def up(w):                           # Turkish i capitalizes to İ
        return ("İ" + w[1:]) if lang in ("tr", "az") and w[:1] == "i" else w[:1].upper() + w[1:]

    def low(w):
        return d.lookup(w) if full else True

    def cap(w):                          # known only capitalized
        return full and (not d.lookup(w)) and d.lookup(up(w))

    def known(w):
        return low(w) or (lang in CAP_NOUNS and cap(w))

    def is_name(w):
        if lang not in CAP_NOUNS:
            return cap(w)
        sets = flags.get(w[:1].upper() + w[1:])
        if not sets or low(w):
            return False
        if all(set(s) <= NAME_FLAGS and "S" in s for s in sets):
            return is_propn(w)
        # flagless capitalized entry: a name when English also only capitalizes it
        return all(s == "" for s in sets) and (not en.lookup(w)) and en.lookup(w[:1].upper() + w[1:])

    words = top_n_list(lang, top_n)
    by_key: dict[str, list[str]] = defaultdict(list)   # key -> known spellings, by rank
    for w in top_n_list(lang, max(top_n * 2, 100_000)):
        if known(w):
            by_key[key(w)].append(w)
        elif REWRITE.get(lang):          # a spelling wordfreq never writes (Greek
            cand = w                     # final ς): its rewrite is the known twin
            for pat, rep in REWRITE[lang]:
                cand = regex.sub(pat, rep, cand)
            if cand != w and known(cand):
                by_key[key(cand)].append(cand)

    # Leak rates first: a foreign word must be dropped, not "repaired" into a
    # native one (da hard -> hård, sk system -> systém, ca pero -> però).
    any_case = (lambda x: d.lookup(x) or d.lookup(up(x))) if full else None
    leak = {s: _leak_rate(s, lang, any_case) for s in LEAK_SOURCES.get(main_script, []) if s != lang}
    leak = {s: round(v, 3) for s, v in leak.items() if v is not None}

    def leaked(w, f):
        return sum(word_frequency(w, s) * 10 ** (-v) for s, v in leak.items()) >= LEAK_DROP * f

    merge: dict[str, str] = {}
    drop: dict[str, str] = {}
    names: set[str] = set()
    freq: dict[str, float] = defaultdict(float)

    def keep(target, w, f):
        freq[target] += f
        if target != w:
            merge[w] = target

    for w in words:
        f = word_frequency(w, lang)
        if regex.search(r"\d", w) or not regex.fullmatch(r"[\p{L}\p{M}'\-‌]+", w) \
                or w.startswith(ZWNJ) or w.endswith(ZWNJ):
            drop[w] = "digits/symbols"; continue
        if _script(w) != main_script:
            drop[w] = "foreign script"; continue
        if one_letter is None and len(w) == 1 and main_script == "Latin" and w in "bcdefghijklmnpqrstvwxz":
            drop[w] = "letter"; continue  # vi: real one-letter words carry tone marks
        if one_letter is not None and len(w) == 1:
            if w not in one_letter:
                drop[w] = "letter"; continue
            keep(w, w, f); continue      # listed: a word (ca d' t'), never a name
        if w in FRAGMENTS.get(lang, ()):
            drop[w] = "fragment"; continue
        if w in SPOKEN.get(lang, {}):
            keep(SPOKEN[lang][w], w, f); continue
        if w in KEEP.get(lang, ()) or w in KEEP_ALL:
            keep(w, w, f); continue
        if not known(w) and leaked(w, f):
            drop[w] = "foreign leak"; continue
        if not known(w) and not (lang not in CAP_NOUNS and cap(w)):   # names are not misspellings
            target = None
            cand = w
            for pat, rep in REWRITE.get(lang, []):
                cand = regex.sub(pat, rep, cand)
            if cand != w and known(cand):
                target = cand
            if target is None and lang == "de" and "ss" in w and len(w) > 3:
                parts = w.split("ss")    # wordfreq writes every ß as ss
                for combo in itertools.product(("ss", "ß"), repeat=len(parts) - 1):
                    t = parts[0] + "".join(c + p for c, p in zip(combo, parts[1:]))
                    if t != w and known(t):
                        target = t; break
            if target is None and lang not in ACCENT_REPAIR_OFF:
                for t in by_key.get(key(w), []):
                    only_zwnj = t.replace(ZWNJ, "") == w
                    if t != w and (only_zwnj or word_frequency(t, lang) >= f * TWIN_RATIO.get(lang, 1.0001)):
                        target = t; break
            if target:
                keep(target, w, f); continue
        if lang == "ru" and "е" in w:
            yo = [t for t in by_key.get(key(w), []) if "ё" in t and t != w
                  and word_frequency(t, lang) >= YO_SHARE * f]
            if yo:
                keep(yo[0], w, f); continue
        if is_name(w):
            names.add(w); keep(w, w, f); continue   # listed apart; the leak still applies
        if known(w):
            keep(w, w, f); continue
        if CHAT.match(w):
            drop[w] = "chat"
        elif lang not in WEAK_DICT and (en.lookup(w) or en.lookup(w[:1].upper() + w[1:])):
            drop[w] = "english"
        elif lang in KEEP_UNKNOWN:
            keep(w, w, f)
        elif len(w) <= 4 or not regex.search(r"[aeiouyäöüàâéèêëîïôûùáíóúăąęěůыэюяаеиоу]", w):
            drop[w] = "abbreviation"
        else:
            drop[w] = "unknown"

    for w, f in freq.items():            # known words and merge targets
        if leaked(w, f):
            drop[w] = "foreign leak"

    from importlib.metadata import version
    return {
        "lang": lang,
        "built": datetime.date.today().isoformat(),
        "rules_version": RULES_VERSION,
        "wordfreq_version": version("wordfreq"),
        "top_n": top_n,
        "mode": "full" if full else f"partial ({why_partial})",
        "hunspell": (", ".join(f"{n}.dic ({(hunspell_dir / f'{n}.dic').stat().st_size:,} bytes)"
                               for n in dict_files) if full else None),
        "names_tagger": names_tagger,
        "leak": leak,
        "names": sorted(names - set(drop)),
        # Languages that capitalize nouns: kept entries the dictionary knows
        # ONLY capitalized (bett -> Bett, spaß -> Spaß). wordfreq lowercases, and
        # a lemmatizer fed "bett" reads a verb (beten); a consumer capitalizes
        # these before lemmatizing. Not needed by apply_patch.
        **({"capitalized": sorted(w for w in freq if w not in drop and cap(w))}
           if lang in CAP_NOUNS else {}),
        "merge": dict(sorted(merge.items())),
        "drop": dict(sorted(drop.items())),
    }


def _stamped(out_dir: Path, lang: str) -> Path:
    """wordfreq_patch_<lang>_rev<YYMMDD><a..z>.json, never over an existing one."""
    day = datetime.date.today().strftime("%y%m%d")
    for letter in string.ascii_lowercase:
        p = out_dir / f"wordfreq_patch_{lang}_rev{day}{letter}.json"
        if not p.exists():
            return p
    raise SystemExit(f"26 patches for {lang} today in {out_dir}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build a patch for one language")
    b.add_argument("lang", help="wordfreq language code, e.g. fr")
    b.add_argument("--hunspell-dir", type=Path, required=True)
    b.add_argument("--out-dir", type=Path, required=True)
    b.add_argument("--top-n", type=int, default=50_000)
    b.add_argument("--names-tagger", help="spacy:<model>, for languages that capitalize nouns")
    s = sub.add_parser("show", help="summarize a patch and print the cleaned top of the list")
    s.add_argument("patch", type=Path)
    s.add_argument("--top", type=int, default=100)
    s.add_argument("--keep-names", action="store_true", help="keep the names list (a course's view)")
    a = ap.parse_args(argv)

    if a.cmd == "build":
        patch = build(a.lang, a.hunspell_dir, a.top_n, a.names_tagger)
        a.out_dir.mkdir(parents=True, exist_ok=True)
        out = _stamped(a.out_dir, a.lang)
        out.write_text(json.dumps(patch, ensure_ascii=False, indent=1), encoding="utf-8")
        reasons = Counter(patch["drop"].values())
        print(f"{a.lang}: {len(patch['merge'])} merged, {len(patch['names'])} names, "
              f"{len(patch['drop'])} dropped "
              f"({', '.join(f'{r} {n}' for r, n in reasons.most_common())}) -> {out}")
        return 0

    patch = json.loads(a.patch.read_text(encoding="utf-8"))
    ranked = apply_patch(patch, keep_names=a.keep_names)
    print(f"{patch['lang']} patch {a.patch.name}: {len(ranked):,} entries kept of {patch['top_n']:,}; "
          f"leak {patch['leak']}")
    print(" ".join(f"{w}({9 + math.log10(f):.2f})" for w, f in ranked[:a.top]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
