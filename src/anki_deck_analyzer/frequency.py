"""The deck's vocabulary held against a frequency list.

Without this, the vocabulary criterion is the model's sense of which words
are common, and that sense is unreliable: it has called words "not everyday"
that general lists rank around the 700th to 1,300th most common. So the tool
reads a frequency list and hands the model counts instead of letting it guess.

The default is `wordfreq`'s "best" list for the language: a blend of
subtitles, web text, Wikipedia, books and social media, built without
reference to any course, and the same for every deck. Where the language has
a cleaning patch (`wordfreq_patch`), foreign words, misspellings, names and
junk are taken out of it first.

A language with a better list than wordfreq has it as its default instead
(`DEFAULT_LISTS`). A list qualifies when it is built independently of any
course, published and citable, weighted toward spoken use, lemmatized at the
source, and licensed to ship. French uses Lexique 3.83: wordfreq's French
leans toward news and Wikipedia, and its lemmas are a lemmatizer's guesses.

`--frequency-list` swaps in another one:

- `wordfreq`: wordfreq (cleaned) for a language whose default is another list;
- `wordfreq-raw`: wordfreq as it comes, to reproduce reports made before the
  lists were cleaned;
- `lexique`, or a Lexique 3.x `.tsv` (French): lemmas ranked by the mean of
  their summed film-subtitle and book frequencies;
- a `.json` list of headwords in rank order. That is a custom list, and the
  report says so: a course's own list is not a neutral yardstick.

When a language has no list, the report says so and the criterion is judged
without one.
"""

from __future__ import annotations

import csv
import json
import os
import re
import statistics
from dataclasses import dataclass, field

import logging

# wordfreq logs "The results will be bad" for a script it cannot split
# (Thai). The report states that limitation itself, in its own words.
logging.getLogger("wordfreq").setLevel(logging.ERROR)

from .lexicon import ELIDED_CLITICS, METHOD_CHARACTER, METHOD_LEMMA, Lexicon
from . import textutil as tx

#: How far down a wordfreq list ranks are read. Words past this are "not in
#: the top 50,000", which is as precise as the comparison needs to be.
LIST_DEPTH = 50_000
#: The two coverage figures reported.
COVERAGE_TIERS = (1000, 2000)
#: Bands of the list for "how early do the most common words arrive".
ARRIVAL_BANDS = ((1, 100), (101, 500), (501, 1000), (1001, 2000))
#: Bands whose taught shares differ by this much hold different kinds of
#: sample: the whole band in one, the deck's own picks in the other.
ARRIVAL_COMPARABLE_GAP = 0.30
#: A new word is flagged as rare for its position when it ranks past both of
#: these: a fixed floor, and a multiple of how many words the deck has taught
#: so far. A word ranked 3,000th is ordinary at word 1,500 and odd at word 40.
#: Only words that are on the list are flagged. A word missing from it may be
#: rare, but it may as well be a compound, a pronunciation respelling or a
#: term from the explanations, so those are counted rather than named.
RARE_FLOOR = 5000
RARE_MULTIPLE = 10
RARE_PER_PART = 4
RARE_TOTAL = 24
#: How many of the list's most common words the deck lacks are named.
MISSING_SHOWN = 40
#: A word at least this much more common in the explanation language than in
#: the target language (in Zipf units: 1.5 is about 30 times) is read as part
#: of an English rule or note, not as target vocabulary. Cognates such as
#: "action" or "important" are close to even and stay in.
EXPLANATION_MARGIN = 1.5

WORDFREQ_LIST = "wordfreq"
#: wordfreq without its cleaning patch.
RAW_LIST = "wordfreq-raw"
#: The Lexique 3.83 lemma list shipped in data/ (see LEXIQUE_DATA).
LEXIQUE_LIST = "lexique"
#: Languages whose default is a better list than wordfreq. Each entry has a
#: reason in the module docstring and in the README ("Default lists").
DEFAULT_LISTS = {"fr": LEXIQUE_LIST}
#: Lexique 3.83's lemmas in rank order with their spellings, derived from
#: Lexique383.tsv by scripts/build_lexique_list.py. CC BY-SA 4.0; the notice
#: is in data/LICENSE-Lexique.txt.
LEXIQUE_DATA = os.path.join(os.path.dirname(__file__), "data", "lexique383_lemmas.json")

LEXIQUE_CREDIT = (
    "Lexique {version} (New & Pallier, CC BY-SA 4.0), lemma frequency, mean of "
    "film-subtitle and book frequency"
)
#: Lexique lists the letters of the alphabet as nouns. Those are dropped as
#: single letters; these two single-character lemmas are words — a
#: preposition and a pronoun — and stay.
LEXIQUE_SINGLE_CHARACTER_WORDS = {"à", "y"}

_HAS_LETTER = re.compile(r"[^\W\d_]")
_HAS_DIGIT = re.compile(r"\d")


@dataclass
class FrequencyList:
    """A ranked list, ready to match against a deck."""

    #: Credit line for the report.
    source: str
    #: What one entry means: "dictionary forms", "word forms", "lemmas", ...
    unit: str
    #: (headword, the keys that count as having it), in rank order.
    entries: list[tuple[str, frozenset]]
    #: True for a list someone tuned, e.g. a course's own list.
    custom: bool = False
    #: headword -> spellings the list's source gives for it (Lexique's form
    #: table). Used only to look up a deck word's rank, never for coverage:
    #: "est" is a form of "être" and of "est" (east), and counting a homograph
    #: as teaching both would inflate coverage.
    forms: dict[str, frozenset] = field(default_factory=dict)
    #: Caveats about the list itself, one sentence each.
    notes: list[str] = field(default_factory=list)


@dataclass
class PartBands:
    """The new words of one tenth of the deck, by where they sit on the list."""

    index: int
    new_words: int
    top_1000: float
    top_5000: float
    beyond: float  # past the top 5,000, or not on the list at all


@dataclass
class FrequencyProfile:
    available: bool = False
    #: e.g. "wordfreq 3.1.1, French ('fr') list"
    source: str = ""
    #: Why there is no comparison, when there is none.
    reason: str = ""
    #: "dictionary forms" or "word forms": what one entry on the list means here.
    unit: str = "word forms"
    custom: bool = False
    #: Caveats about the list itself, one sentence each.
    notes: list[str] = field(default_factory=list)
    #: ISO code of the deck's language, for caveats that belong to one list.
    language: str = ""
    #: Entries on the list after cleaning.
    list_size: int = 0
    #: Entries dropped from the list before matching, by reason.
    dropped: dict[str, int] = field(default_factory=dict)
    #: tier -> (list words the deck teaches, list words in that tier)
    coverage: dict[int, tuple[int, int]] = field(default_factory=dict)
    #: (band, taught, median position as a share of the deck, share in first quarter)
    arrival: list[tuple[tuple[int, int], int, float | None, float | None]] = field(
        default_factory=list
    )
    parts: list[PartBands] = field(default_factory=list)
    #: (word, rank, share of the deck, vocabulary so far)
    rare_early: list[tuple[str, int, float, int]] = field(default_factory=list)
    #: Every word flagged as rare for its position, before the display cap.
    rare_count: int = 0
    #: (rank, headword) of the list's most common entries the deck never uses.
    missing: list[tuple[int, str]] = field(default_factory=list)
    #: Those of `missing` that grammar or announcement notes do use.
    missing_explained: list[str] = field(default_factory=list)
    #: Distinct words the deck teaches that are not on the list at all.
    unlisted: int = 0
    taught: int = 0
    #: List entries counted as taught only because a field on the card states
    #: their dictionary form ("Verb Infinitive": offrir), while the studied
    #: text shows a spelling the lemmatizer misreads ("offre").
    stated_only: int = 0
    #: Words left out because they belong to the explanation language.
    explanation_words: int = 0
    explanation_language: str = ""


# --------------------------------------------------------------------------
# loading a list
# --------------------------------------------------------------------------


def _version(package: str) -> str:
    try:
        from importlib.metadata import version

        return version(package)
    except Exception:  # pragma: no cover
        return ""


#: Spelling variants that say nothing about vocabulary: a curly apostrophe,
#: and the ligatures Lexique writes out ("coeur" for "cœur").
_FOLD = str.maketrans({"’": "'", "œ": "oe", "Œ": "Oe", "æ": "ae", "Æ": "Ae"})


def _fold(word: str, lang: str | None = None) -> str:
    """`word` folded for matching; `lang` adds the language's own key
    (`textutil.match_key`), so it is passed wherever a key is compared."""
    return tx.match_key(word.translate(_FOLD), lang)


def _quote(word: str) -> str:
    """Straight apostrophes only: what the lemmatizer should see."""
    return word.replace("’", "'")


def _own_script_only(words: list[str]) -> tuple[list[str], int]:
    """A raw list without the entries written in a script other than the
    list's own, which is taken from its 200 most common entries. Only for a
    list in a non-Latin script: English words are web noise there, but a
    Latin-script list cannot be told apart from its loanwords this way."""
    import collections

    counts = collections.Counter(_script(w) for w in words[:200] if _script(w))
    if not counts:
        return words, 0
    main = counts.most_common(1)[0][0]
    if main == "LATIN":
        return words, 0
    kept = [w for w in words if _script(w) in (main, "")]
    return kept, len(words) - len(kept)


def _wordfreq_list(
    lex: Lexicon, language_name: str, raw: bool = False
) -> tuple[FrequencyList | None, str]:
    try:
        import wordfreq  # type: ignore
    except ImportError:
        return None, "the wordfreq package is not installed (pip install wordfreq)"
    try:
        words = wordfreq.top_n_list(lex.language, LIST_DEPTH)
    except Exception:  # an unknown language code raises in some versions
        words = []
    if not words:
        return None, f"wordfreq has no list for '{lex.language}'"
    version = _version("wordfreq")
    label, notes = "raw", []
    if raw:
        notes.append("wordfreq is used raw, as asked (--frequency-list wordfreq-raw).")
    else:
        from .wordfreq_patch import cleaned

        patched, why_raw = cleaned(lex.language, version)
        if patched is None:
            words, n_foreign = _own_script_only(words)
            notes.append(
                f"wordfreq is used raw: {why_raw}. Misspellings and junk on the "
                "list stay in it"
                + (
                    f"; {n_foreign:,} entries in another script than the list's own "
                    "(English words on a Korean list, say) were left out."
                    if n_foreign else "."
                )
            )
        else:
            words, label = patched.words[:LIST_DEPTH], patched.label
            notes += patched.notes
        if lex.language == "ko" and tx.korean_splitter() == "Kiwi":
            # MeCab, which built the list, keeps contractions whole (했, 할,
            # 합니다); Kiwi splits them (하 + 었), so they can never be found.
            kept = [w for w in words if len(tx._KO_STATE["fn"](w)) <= 1]
            if len(kept) < len(words):
                notes.append(
                    f"{len(words) - len(kept):,} entries that Kiwi splits further "
                    "(contractions such as 했 = 하 + 었) were left out, since the "
                    "deck is split the same way and could never match them."
                )
            words = kept

    clitics = ELIDED_CLITICS.get(lex.language or "", ())
    # In dictionary-form mode the list's spellings are folded into one entry
    # per dictionary form, ranked where its most common spelling sits. The
    # spelling stays a key of its own, so a lemmatizer slip on either side
    # ("paris" -> "pari") does not lose the match.
    keys: dict[str, set[str]] = {}
    # The headword the report names: the list's own spelling, which keeps a
    # Persian ZWNJ that the matching key drops.
    heads: dict[str, str] = {}
    order: list[str] = []
    lang = lex.language
    for word in words:
        if word in lex.names:
            # Names never count on the deck side, so they cannot count here.
            continue
        if lex.method == METHOD_LEMMA:
            surface = word + "'" if word in clitics else word
            head = lex.shown(surface)
        else:
            surface = head = word
        if not head:
            continue
        key, surface = _fold(head, lang), _fold(surface, lang)
        if key not in keys:
            keys[key] = {key}
            heads[key] = _fold(head)
            order.append(key)
        keys[key].add(surface)
        if lex.method == METHOD_LEMMA:
            extra = lex.overruled(word + "'" if word in clitics else word)
            if extra:
                keys[key].add(_fold(extra, lang))
    language = f"{language_name} ('{lex.language}')" if language_name else f"'{lex.language}'"
    return (
        FrequencyList(
            source=f"wordfreq {version}, {label}, {language} list",
            unit="dictionary forms" if lex.method == METHOD_LEMMA else "word forms",
            entries=[(heads[k], frozenset(keys[k])) for k in order],
            notes=notes,
        ),
        "",
    )


def read_lexique(path: str) -> list[tuple[str, list[str]]]:
    """Lexique lemmas in rank order, each with the spellings Lexique gives
    for it, from a Lexique 3.x `.tsv`.

    Per lemma, `freqfilms2` and `freqlivres` are each summed over its forms
    (per million words), and the lemma is ranked by the mean of the two sums.
    """
    films: dict[str, float] = {}
    books: dict[str, float] = {}
    spellings: dict[str, set[str]] = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            lemma = (row.get("lemme") or "").strip()
            if not lemma:
                continue
            films[lemma] = films.get(lemma, 0.0) + float(row.get("freqfilms2") or 0)
            spellings.setdefault(lemma, set()).add((row.get("ortho") or "").strip())
            books[lemma] = books.get(lemma, 0.0) + float(row.get("freqlivres") or 0)
    ranked = sorted(films, key=lambda k: (-(films[k] + books[k]) / 2, k))
    return [(lemma, sorted(spellings[lemma])) for lemma in ranked]


def _lexique_list(path: str, lex: Lexicon) -> tuple[FrequencyList | None, str]:
    """Lexique lemmas, ranked by the mean of summed film and book frequency:
    from a Lexique `.tsv`, or from the shipped list (LEXIQUE_DATA)."""
    if lex.language not in (None, "fr"):
        return None, "Lexique is a French list and this deck is not French"
    if path == LEXIQUE_DATA:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        version, ranked = data["version"], data["lemmas"]
    else:
        match = re.search(r"(\d)(\d+)", os.path.basename(path))
        version = f"{match.group(1)}.{match.group(2)}" if match else "3"
        ranked = read_lexique(path)
    spellings = {lemma: {_fold(f) for f in forms} for lemma, forms in ranked}
    entries = []
    for lemma, _ in ranked:
        if len(lemma) == 1 and lemma not in LEXIQUE_SINGLE_CHARACTER_WORDS:
            continue  # a letter of the alphabet, listed as a noun
        # A few entries list spelling variants together: "mou,mol".
        variants = [_fold(v.strip()) for v in lemma.split(",") if v.strip()]
        entries.append((lemma, frozenset(variants)))
    return (
        FrequencyList(
            source=LEXIQUE_CREDIT.format(version=version),
            unit="lemmas as Lexique gives them, not re-lemmatized",
            entries=entries,
            forms={k: frozenset(v) for k, v in spellings.items()},
        ),
        "",
    )


def _json_list(path: str) -> tuple[FrequencyList | None, str]:
    """A list of headwords: [{"word", "rank"}, ...], bare strings in rank
    order, or either under a "frequency_list" key."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        data = data.get("frequency_list")
    if not isinstance(data, list) or not data:
        return None, f"{os.path.basename(path)} holds no frequency list"
    if isinstance(data[0], dict):
        data = [e["word"] for e in sorted(data, key=lambda e: e.get("rank", 0))]
    # The headwords are already dictionary forms in the list's own scheme, so
    # they are not re-lemmatized: an entry counts as covered when the deck has
    # it as a spelling or as a dictionary form.
    entries = [(w, frozenset({_fold(w.lower())})) for w in data if isinstance(w, str)]
    return (
        FrequencyList(
            source=f"custom list {os.path.basename(path)} ({len(entries):,} entries)",
            unit="the list's own headwords, not re-lemmatized",
            entries=entries,
            custom=True,
        ),
        "",
    )


def default_list(language: str | None) -> str:
    """The list a deck in `language` is measured against by default."""
    return DEFAULT_LISTS.get(language or "", WORDFREQ_LIST)


def load_list(
    spec: str | None, lex: Lexicon, language_name: str = ""
) -> tuple[FrequencyList | None, str]:
    """The list `spec` names: None for the language's default (`default_list`),
    "wordfreq" (cleaned where a patch exists), "wordfreq-raw", "lexique", or
    a file path."""
    if lex.method == METHOD_CHARACTER:
        return None, (
            "the deck is counted per character and the frequency lists are per "
            "word, so the two cannot be matched"
        )
    if not spec or spec in (WORDFREQ_LIST, RAW_LIST, LEXIQUE_LIST):
        if not lex.language:
            return None, "the target language was not identified"
        if lex.language == "ko" and not tx.korean_morphemes():
            return None, (
                "wordfreq's Korean list is split into morphemes (학교, 는, 를) and "
                "without a Korean splitter the deck can only be split at spaces (학교는, 학교를), "
                'so the two cannot be matched; pip install kiwipiepy'
            )
        spec = spec or default_list(lex.language)
        if spec == LEXIQUE_LIST:
            return _lexique_list(LEXIQUE_DATA, lex)
        return _wordfreq_list(lex, language_name, raw=spec == RAW_LIST)
    if not os.path.isfile(spec):
        return None, f"the frequency list {spec} was not found"
    if spec.lower().endswith(".json"):
        return _json_list(spec)
    with open(spec, encoding="utf-8") as fh:
        header = fh.readline().rstrip("\r\n").split("\t")
    if "lemme" in header and "freqfilms2" in header and "freqlivres" in header:
        return _lexique_list(spec, lex)
    return None, f"{os.path.basename(spec)} is not a format this tool reads"


#: Fragments a spelling dictionary lets through because they are on web
#: pages everywhere, but that no deck teaches: URL pieces and the Roman
#: numerals of chapter and century headings. Only numerals no language uses
#: as a word: "mi", "di", "ci", "vi" and "xi" are valid numerals and also
#: everyday words.
_NOT_WORDS = frozenset(
    "http https www html php ii iii iv vii viii ix xii xiii xiv xv xvi xvii "
    "xviii xix xx xxi".split()
)


def _clean(flist: FrequencyList, names: frozenset) -> tuple[list[tuple[str, frozenset]], dict]:
    """Entries that can be matched at all: no digits or symbols, no names,
    no web or numbering fragments."""
    kept = []
    dropped = {"digits or symbols": 0, "names": 0, "web addresses and Roman numerals": 0}
    for head, keys in flist.entries:
        if _HAS_DIGIT.search(head) or not _HAS_LETTER.search(head):
            dropped["digits or symbols"] += 1
            continue
        if head.lower() in _NOT_WORDS:
            dropped["web addresses and Roman numerals"] += 1
            continue
        if head.lower() in names:
            dropped["names"] += 1
            continue
        kept.append((head, keys))
    return kept, {k: v for k, v in dropped.items() if v}


# --------------------------------------------------------------------------
# matching the deck
# --------------------------------------------------------------------------


def deck_keys(lex: Lexicon, text: str, skip=None) -> list[tuple[str, str]]:
    """(dictionary key, spelling) for each word of `text`, names left out.

    Elisions are always split here, even when counting by spelling, because
    lists have "homme", never "l'homme". `skip` is called with each spelling
    and drops it when it returns true. The spelling is the deck's own, and
    is matched through `_fold(spelling, lex.language)`; the key is already
    matchable.
    """
    clitics = ELIDED_CLITICS.get(lex.language or "", set())
    out: list[tuple[str, str]] = []
    for tok in tx.words(text, lex.language):
        if tok in lex.names:
            continue
        head, sep, rest = _quote(tok).partition("'")
        pieces = [head + "'", rest] if sep and rest and head in clitics else [tok]
        for piece in pieces:
            if skip is not None and skip(piece.rstrip("'")):
                continue
            key = lex.normalize(piece) if lex.method == METHOD_LEMMA else piece.rstrip("'")
            out.append((_fold(key, lex.language), _fold(piece)))
    return out


def shown(lex: Lexicon, key: str, spelling: str) -> str:
    """The form a report names for a deck word found by `deck_keys`: its key,
    unless the key drops something the deck writes (a Persian ZWNJ), in
    which case the same form with it kept."""
    form = _fold(lex.shown(spelling))
    return form if form != key and _fold(form, lex.language) == key else key


def _forms_seen(lex: Lexicon, text: str, skip=None) -> set[str]:
    """Every key under which a list entry could find this text's words: whole
    tokens ("jusqu'à", "d'accord"), split pieces with and without their
    apostrophe, and dictionary forms."""
    out: set[str] = set()
    for tok in tx.words(text, lex.language):
        if tok in lex.names:
            continue
        whole = _quote(tok)
        if skip is not None and skip(whole):
            continue
        forms = {whole}
        head, sep, rest = whole.partition("'")
        if sep and rest:
            forms.update({head + "'", head, rest})
        # The list never holds a hyphenated word: wordfreq splits at every
        # hyphen, so "peut-être" is "peut" and "être" there.
        if "-" in whole:
            forms.update(p for p in whole.split("-") if p)
        if lex.method == METHOD_LEMMA:
            forms.update({lex.normalize(f) for f in forms if f}
                         | {lex.overruled(f) for f in forms if f})
            forms.discard("")
        # Folded last: the lemmatizer knows "sœurs", not "soeurs".
        out |= {_fold(f, lex.language) for f in forms}
    return out


def _phrase_pattern(phrase: str) -> re.Pattern:
    words = [re.escape(w) for w in phrase.split()]
    return re.compile(r"(?<![^\W_])" + r"\s+".join(words) + r"(?![^\W_])")


def _script(word: str) -> str:
    """The Unicode script of a word's first letter: LATIN, CJK, THAI, ..."""
    import unicodedata

    for ch in word:
        if ch.isalpha():
            try:
                return unicodedata.name(ch).split()[0]
            except ValueError:
                return ""
    return ""


class _EnglishFilter:
    """True for a word far more common in the explanation language than in
    the target language. Remembers what it set aside."""

    def __init__(self, explain_code: str, target_code: str) -> None:
        import wordfreq  # type: ignore

        self._wf = wordfreq
        self.explain, self.target = explain_code, target_code
        self.verdicts: dict[str, bool] = {}
        self.skipped: set[str] = set()
        self._zcache: dict[str, tuple[float, float]] = {}
        # A language wordfreq cannot look up here counts as 0 throughout.
        # Chinese, Japanese and Korean need jieba or MeCab, optional extras
        # a plain install lacks; Thai has no list. Each failed lookup raises,
        # and the module import behind it is retried on every call, so it is
        # probed once instead. Without this the target side failed silently
        # on every word and no line was ever set aside.
        self.unavailable: set[str] = set()
        for code in (explain_code, target_code):
            try:
                wordfreq.zipf_frequency("a", code)
            except Exception:
                self.unavailable.add(code)
        # With the target side at 0, the explanation list's noise decides
        # alone, and the English list holds CJK and Thai characters from web
        # pages (不 1.32, の 2.28, ツ 3.17): a Chinese headword would read as
        # English. So only words in the explanation language's own script,
        # taken from its most common word, are weighed then.
        self._explain_script = ""
        if target_code in self.unavailable and explain_code not in self.unavailable:
            try:
                self._explain_script = _script(wordfreq.top_n_list(explain_code, 1)[0])
            except Exception:
                pass

    def __call__(self, word: str) -> bool:
        hit = self.verdicts.get(word)
        if hit is None:
            explain, target = self._zipf(word)
            hit = explain - target >= EXPLANATION_MARGIN
            self.verdicts[word] = hit
        if hit:
            self.skipped.add(word)
        return hit

    def _zipf(self, word: str) -> tuple[float, float]:
        # Cached: every line of every field is judged word by word, and for
        # Chinese and Japanese each lookup runs wordfreq's jieba/MeCab
        # tokenizer. Uncached, a 5,000-note HSK deck with dictionary fields
        # took over ten minutes on this step alone.
        hit = self._zcache.get(word)
        if hit is None:
            hit = (self._one(word, self.explain), self._one(word, self.target))
            self._zcache[word] = hit
        return hit

    def _one(self, word: str, code: str) -> float:
        if code in self.unavailable:
            return 0.0
        if self._explain_script and code == self.explain and _script(word) != self._explain_script:
            return 0.0
        try:
            return self._wf.zipf_frequency(word, code)
        except Exception:
            return 0.0

    def leans(self, word: str) -> bool:
        """More common in the explanation language than in the target one.

        Looser than the call above, which needs a clear margin because it
        decides what counts as vocabulary at all. This one only decides what
        may be held up as a rare target-language word, and wordfreq's French
        list absorbs a good deal of English: "sugar" scores 3.4 there."""
        explain, target = self._zipf(word)
        return explain > target

    def english_zipf(self, word: str) -> float:
        """How common the word is in the explanation language."""
        return self._zipf(word)[0]

    def on_target_list(self, word: str) -> bool:
        return self._zipf(word)[1] > 0

    def line_is_explanation(self, words: list[str]) -> bool:
        """Whether a line, taken as a whole, reads as the explanation
        language: its words are more common there in total. One borrowed
        word cannot tip a French line, and one French word cannot save an
        English stage direction."""
        explain = target = 0.0
        for w in words:
            e, t = self._zipf(w)
            explain += e
            target += t
        return explain > target


#: Lines shorter than this are never judged: too little to go on.
EXPLANATION_LINE_MIN_WORDS = 3


def drop_explanation_lines(text: str, language_filter) -> tuple[str, int]:
    """`text` without its explanation-language lines, and how many went."""
    kept, dropped = [], 0
    for line in (text or "").split("\n"):
        words = tx.words(line, getattr(language_filter, "target", None))
        if len(words) >= EXPLANATION_LINE_MIN_WORDS and language_filter.line_is_explanation(
            words
        ):
            dropped += 1
            continue
        kept.append(line)
    return "\n".join(kept), dropped


def explanation_filter(lex: Lexicon, explanation_language: str):
    """A filter for explanation-language words, or None when there is no
    second language to filter or no list to filter it with."""
    from .lexicon import language_code

    explain_code = language_code(explanation_language)
    if not (explain_code and lex.language and explain_code != lex.language):
        return None
    try:
        return _EnglishFilter(explain_code, lex.language)
    except ImportError:
        return None


def compute(
    lex: Lexicon,
    texts: list[str],
    bounds: list[tuple[int, int, int]],
    language_name: str = "",
    explanation_language: str = "",
    frequency_list: str | None = None,
    stated: list[tuple[int, str]] | None = None,
) -> FrequencyProfile:
    """Compare the deck's vocabulary with the list.

    `stated` is (position in `texts`, dictionary form) for every value of a
    field that states the dictionary form of a word the card teaches. Those
    count as taught at that position; a lemmatizer is less reliable than the
    deck's own statement of which verb "offre" is.

    `texts` is the measured text in study order, one entry per note the curve
    reads; `bounds` is (part index, start, end) for each tenth, zero-based
    and end-exclusive, the same split the curve uses. `frequency_list` is
    None (the language's default) or a list name or path; see `load_list`.
    """
    flist, reason = load_list(frequency_list, lex, language_name)
    if flist is None:
        return FrequencyProfile(available=False, reason=reason)
    entries, dropped = _clean(flist, lex.names)

    # Translations and notes inside the material are in another language.
    # Their words are not vocabulary the deck teaches, and on the target list
    # they show up as rare words. A word is set aside when it is far more
    # common in the explanation language than in the target language.
    skip = explanation_filter(lex, explanation_language)

    lang = lex.language
    total = len(texts) or 1
    # The deck's words, for bands and rarity: one key per dictionary form.
    first_at: dict[str, int] = {}
    spelling: dict[str, str] = {}
    order: list[str] = []
    # Every form the deck shows, for coverage: an entry counts as taught if
    # any of its keys turns up here.
    seen_at: dict[str, int] = {}
    for i, text in enumerate(texts):
        for key, surface in deck_keys(lex, text, skip):
            if key not in first_at:
                first_at[key] = i
                spelling[key] = surface
                order.append(key)
        for form in _forms_seen(lex, text, skip):
            seen_at.setdefault(form, i)
    stated_new: set[str] = set()
    for i, value in stated or ():
        value = tx.prepare(value.lower().strip(), lang)
        # Whole, for a multi-word entry ("avoir besoin"), and word by word,
        # since a field states "se tourner" or a headword with its feminine,
        # "blanc, blanche".
        forms = {_fold(value, lang), _fold(lex.normalize(value), lang)}
        forms |= {_fold(w, lang) for w in lex.split(value) if w not in lex.names}
        forms |= {_fold(w, lang) for w in lex.words(value)}
        for form in forms:
            if form and (form not in seen_at or seen_at[form] > i):
                if form not in seen_at:
                    stated_new.add(form)
                seen_at[form] = i
    lowered = [" ".join(_fold(tx.prepare(t, lang), lang).lower().split()) for t in texts]

    def first_use(keys: frozenset) -> int | None:
        hits = [seen_at[k] for k in keys if k in seen_at]
        for k in keys:
            if " " in k:  # a multi-word entry: "parce que", "est-ce que"
                pattern = _phrase_pattern(k)
                hit = next((i for i, t in enumerate(lowered) if pattern.search(t)), None)
                if hit is not None:
                    hits.append(hit)
        return min(hits) if hits else None

    used = [first_use(keys) for _, keys in entries]

    rank_of: dict[str, int] = {}
    form_rank: dict[str, int] = {}
    for rank, (head, keys) in enumerate(entries, start=1):
        for k in keys:
            rank_of.setdefault(k, rank)
            # The words of a multi-word entry ("parce" of "parce que",
            # "plupart" of "la plupart des") take the entry's rank: on their
            # own they are not rare words. A word's most common reading wins,
            # so this never makes "la" or "des" look rarer than they are.
            if " " in k:
                for word in k.split():
                    form_rank.setdefault(word, rank)
        for f in flist.forms.get(head, ()):
            form_rank.setdefault(f, rank)

    def deck_rank(key: str) -> int | None:
        """The most common reading of a deck word: its dictionary form, its
        spelling, or (where the list has a form table) the headword that
        spelling belongs to. "toilettes" is ranked as "toilette", whatever the
        lemmatizer made of it, so a slip never flags a common word as rare."""
        surface = _fold(spelling.get(key, ""), lang)
        ranks = [
            r
            for r in (rank_of.get(key), rank_of.get(surface), form_rank.get(surface))
            if r
        ]
        # A hyphenated compound ("quatre-vingt-un", "belle-fille") is rare as
        # one token and common as its parts; read it as its parts, ranked by
        # the rarest, when every part is on the list.
        if "-" in surface:
            parts = [
                min([r for r in (rank_of.get(x), form_rank.get(x)) if r], default=None)
                for x in surface.split("-")
                if x
            ]
            if parts and all(parts):
                ranks.append(max(parts))
        return min(ranks) if ranks else None

    coverage = {}
    for tier in COVERAGE_TIERS:
        top = used[:tier]
        coverage[tier] = (sum(1 for u in top if u is not None), len(top))

    arrival = []
    for lo, hi in ARRIVAL_BANDS:
        positions = [u / total for u in used[lo - 1 : hi] if u is not None]
        arrival.append(
            (
                (lo, hi),
                len(positions),
                statistics.median(positions) if positions else None,
                sum(1 for p in positions if p < 0.25) / len(positions) if positions else None,
            )
        )

    missing = [
        (rank, head)
        for rank, ((head, _), u) in enumerate(zip(entries, used), start=1)
        if u is None
    ][:MISSING_SHOWN]

    parts: list[PartBands] = []
    rare: list[tuple[str, int, float, int]] = []
    rare_count = 0
    learned = 0
    for index, start, end in bounds:
        fresh = [k for k in order if start <= first_at[k] < end]
        if fresh:
            rk = [deck_rank(k) for k in fresh]
            parts.append(
                PartBands(
                    index=index,
                    new_words=len(fresh),
                    top_1000=sum(1 for r in rk if r and r <= 1000) / len(fresh),
                    top_5000=sum(1 for r in rk if r and r <= 5000) / len(fresh),
                    beyond=sum(1 for r in rk if not r or r > 5000) / len(fresh),
                )
            )
        flagged = []
        for offset, key in enumerate(fresh):
            known = learned + offset
            r = deck_rank(key)
            limit = max(RARE_FLOOR, RARE_MULTIPLE * known)
            if (
                r is not None
                and r > limit
                and len(key) > 2
                and not (skip and skip.leans(spelling.get(key, key).rstrip("'")))
            ):
                flagged.append(
                    (shown(lex, key, spelling[key]), r, first_at[key] / total, known)
                )
        flagged.sort(key=lambda f: -f[1])
        rare_count += len(flagged)
        rare += flagged[:RARE_PER_PART]
        learned += len(fresh)

    return FrequencyProfile(
        available=True,
        source=flist.source,
        unit=flist.unit,
        custom=flist.custom,
        notes=flist.notes,
        language=lang or "",
        list_size=len(entries),
        dropped=dropped,
        coverage=coverage,
        arrival=arrival,
        parts=parts,
        rare_early=rare[:RARE_TOTAL],
        rare_count=rare_count,
        missing=missing,
        explanation_words=len(skip.skipped) if skip else 0,
        stated_only=sum(
            1
            for (_, keys), u in zip(entries, used)
            if u is not None and keys & stated_new and not keys & (set(seen_at) - stated_new)
        ),
        unlisted=sum(1 for k in order if deck_rank(k) is None),
        taught=len(order),
        explanation_language=explanation_language if skip and skip.skipped else "",
    )


def table(profile: FrequencyProfile) -> str:
    """The frequency comparison as plain text, for the model and the report."""
    if not profile.available:
        return f"No frequency list was used: {profile.reason}."
    lines = [
        f"Frequency list: {profile.source}. Entries are compared as {profile.unit}; "
        "an entry counts as taught when the deck uses it as a spelling or as a "
        "dictionary form.",
    ]
    lines += profile.notes
    if profile.custom:
        lines.append(
            "This is a custom list, not a neutral one: a course measured against "
            "its own list will cover it well by construction."
        )
    if profile.language == "ar" and not profile.custom:
        lines.append(
            "The Arabic list is mostly Modern Standard Arabic, so a deck in a spoken "
            "dialect (Egyptian, Levantine, Gulf, ...) will see its everyday words "
            "ranked as rare or missing from the list. Read the comparison with that "
            "in mind."
        )
    if profile.language == "pt" and not profile.custom:
        lines.append(
            "The Portuguese list is mostly Brazilian Portuguese, so a deck in "
            "European Portuguese will see some everyday words ranked as rare "
            "(autocarro, dezassete, comboio, casa de banho) and Brazilian ones "
            "(ônibus, trem, pra) missing. Read the comparison with that in mind."
        )
    if profile.dropped:
        lines.append(
            "Left out of the list before matching: "
            + ", ".join(f"{n:,} {why}" for why, n in profile.dropped.items())
            + "."
        )
    lines += ["", "Coverage of the most common words on the list:"]
    for tier, (taught, size) in profile.coverage.items():
        share = taught / size if size else 0.0
        line = f"  top {tier:,}: the deck teaches {taught:,} of {size:,} ({share:.0%})"
        # A deck teaching fewer words than the tier holds cannot cover it, so
        # the share alone reads as a gap that is really the deck's size.
        if profile.taught and profile.taught < size:
            line += (
                f"; with {profile.taught:,} words in all, it could cover at most "
                f"{profile.taught / size:.0%}"
            )
        lines.append(line)
    lines += ["", "When the most common words arrive (position as a share of the deck):"]
    shares = []
    for (lo, hi), taught, median, early in profile.arrival:
        size = hi - lo + 1
        if not taught:
            lines.append(f"  ranks {lo:,}-{hi:,}: none taught")
            continue
        shares.append(taught / size)
        lines.append(
            f"  ranks {lo:,}-{hi:,}: {taught:,} of {size:,} taught ({taught / size:.0%}), "
            f"median first appearance at {median:.0%} of the deck, {early:.0%} of "
            "them in the first quarter"
        )
    if shares and max(shares) - min(shares) >= ARRIVAL_COMPARABLE_GAP:
        lines.append(
            "  Each median covers only the words taught from that band. Where most "
            "of a band is untaught, the ones taught are the deck's selection from "
            "it, often words a lesson needed, so that median is not comparable with "
            "the median of a band the deck covers almost completely. Compare bands "
            "of similar coverage, or read the per-tenth table below."
        )
    if profile.parts:
        lines += [
            "",
            "New words in each tenth of the deck, by rank on the list:",
            "part | new words | top 1,000 | top 5,000 | past 5,000 or unlisted",
            "-----+-----------+-----------+-----------+-----------------------",
        ]
        for p in profile.parts:
            lines.append(
                f"{p.index:>4} | {p.new_words:>9,} | {p.top_1000:>9.0%} | "
                f"{p.top_5000:>9.0%} | {p.beyond:>21.0%}"
            )
    if profile.stated_only:
        lines += [
            "",
            f"{profile.stated_only:,} list entries count as taught because a field on "
            "the card states the word's dictionary form (a 'Verb Infinitive' field, "
            "for example), though the studied text shows another spelling of it.",
        ]
    if profile.missing:
        lines += [
            "",
            f"The {len(profile.missing)} most common words on the list that the "
            "material never uses"
            + (
                " (those marked * appear only in grammar or announcement notes: "
                "explained, not practiced)"
                if profile.missing_explained
                else ""
            )
            + ":",
            "  " + ", ".join(
                f"{w}{'*' if w in profile.missing_explained else ''} ({r:,})"
                for r, w in profile.missing
            ),
        ]
    if profile.rare_early:
        lines += [
            "",
            f"Words that are rare for where they appear: {profile.rare_count:,} in all, "
            f"the rarest few per tenth shown (ranked past {RARE_FLOOR:,} and past "
            f"{RARE_MULTIPLE}x the number of words taught before them). Check each "
            "against the cards before calling it a vocabulary choice: grammar "
            "terminology and pronunciation respellings in explanation fields land "
            "here too.",
        ]
        for word, rank, where, known in profile.rare_early:
            lines.append(
                f"  {word} — rank {rank:,}, at {where:.0%} of the deck, after {known:,} words"
            )
    if profile.explanation_words:
        lines += [
            "",
            f"{profile.explanation_words:,} distinct words were left out as "
            f"{profile.explanation_language} explanation text: each is at least 30 "
            f"times more common in {profile.explanation_language} than in the target "
            "language.",
        ]
    if profile.taught:
        lines += [
            "",
            f"{profile.unlisted:,} of the {profile.taught:,} distinct words the deck "
            f"teaches are not among the list's {profile.list_size:,} entries. That "
            "count mixes rare vocabulary with compounds, rare inflections, spelling "
            "variants and notation, so none of them are named above as rare; "
            "names are already left out.",
        ]
    return "\n".join(lines)
