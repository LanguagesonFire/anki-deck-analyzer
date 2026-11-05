"""What counts as one word when the deck is measured.

Counting surface spellings makes every inflected form a new word: "suis",
"es", "est" and "sommes" are four entries for a French deck that has taught
one verb. On top of that, every character name counts as vocabulary. Both
inflate the new-word rate, and they inflate it most for inflected languages
and story decks, which is exactly the comparison the tool exists to make.

So tokens are reduced to their dictionary form where a lemmatizer for the
language is installed (`pip install anki-deck-analyzer[lemma]`), and names are
left out in every mode. Without a lemmatizer the tool falls back to surface
forms and says so, so that two reports are only compared like for like.

There are no per-deck exceptions here on purpose: a rule tuned to one deck
would favour the decks it was tuned on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import textutil as tx

#: English language names, as the identification call returns them, to ISO
#: 639-1 codes. Matched on the first known name inside the profile's string,
#: so "Brazilian Portuguese" and "Mandarin Chinese" resolve too.
LANGUAGE_CODES = {
    "arabic": "ar", "bengali": "bn", "bulgarian": "bg", "catalan": "ca",
    "chinese": "zh", "mandarin": "zh", "cantonese": "zh", "croatian": "hr",
    "czech": "cs", "danish": "da", "dutch": "nl", "english": "en",
    "estonian": "et", "finnish": "fi", "french": "fr", "german": "de",
    "greek": "el", "hebrew": "he", "hindi": "hi", "hungarian": "hu",
    "icelandic": "is", "indonesian": "id", "irish": "ga", "italian": "it",
    "japanese": "ja", "korean": "ko", "latvian": "lv", "lithuanian": "lt",
    "macedonian": "mk", "malay": "ms", "norwegian": "nb", "persian": "fa",
    "farsi": "fa", "polish": "pl", "portuguese": "pt", "romanian": "ro",
    "russian": "ru", "serbian": "sr", "slovak": "sk", "slovenian": "sl",
    "slovene": "sl", "spanish": "es", "swedish": "sv", "tagalog": "fil",
    "filipino": "fil", "tamil": "ta", "turkish": "tr", "ukrainian": "uk",
    "urdu": "ur", "vietnamese": "vi", "welsh": "cy", "latin": "la",
    "thai": "th", "lao": "lo", "khmer": "km", "burmese": "my", "georgian": "ka",
    "armenian": "hy", "swahili": "sw", "nepali": "ne", "marathi": "mr",
    "punjabi": "pa", "gujarati": "gu", "telugu": "te", "kannada": "kn",
    "malayalam": "ml", "sinhala": "si", "amharic": "am", "yiddish": "yi",
    "esperanto": "eo", "basque": "eu", "galician": "gl", "albanian": "sq",
    "afrikaans": "af", "azerbaijani": "az", "kazakh": "kk", "uzbek": "uz",
    "mongolian": "mn", "bosnian": "bs", "norwegian bokmål": "nb",
}

#: Languages that capitalize every noun. Capitalization says nothing about
#: whether a word is a name there, so name detection is switched off.
CAPITALIZED_NOUNS = {"de", "lb"}

#: Languages whose script has no capital letters, so a name looks like any
#: other word. Names are not detected and count as vocabulary, on the deck's
#: side and the list's alike.
UNCASED = {
    "ar", "fa", "ur", "he", "yi", "ko", "ka", "hy", "am", "th", "lo", "km", "my",
    "hi", "mr", "ne", "bn", "pa", "gu", "ta", "te", "kn", "ml", "si",
}

#: Languages that write an elided article or preposition onto the next word.
#: In dictionary-form mode "l'homme" is two words, "le" and "homme", which is
#: how a learner meets them and how frequency lists count them.
ELIDED_CLITICS = {
    "fr": {"l", "d", "j", "c", "qu", "n", "s", "m", "t", "jusqu", "lorsqu",
           "puisqu", "quoiqu"},
    "it": {"l", "d", "c", "s", "m", "t", "v", "n", "un", "dell", "dall", "nell",
           "sull", "all", "coll", "quest", "quell", "sant", "po"},
    "ca": {"l", "d", "s", "m", "t", "n"},
}

#: Pronouns written onto a verb with a hyphen: "tournez-vous", "dis-moi",
#: "donne-le-moi", "a-t-il" (the -t- only eases the sound). Read as one token,
#: no lemmatizer resolves them and the verb is never credited, so in
#: dictionary-form mode they are split like the elisions above.
HYPHEN_CLITICS = {
    "fr": {"moi", "toi", "soi", "lui", "nous", "vous", "leur", "le", "la", "les",
           "en", "y", "je", "tu", "il", "elle", "on", "ils", "elles", "ce", "t"},
    "pt": {"me", "te", "se", "lhe", "lhes", "nos", "vos", "o", "a", "os", "as",
           "lo", "la", "los", "las", "no", "na", "nas"},
}
#: The clipped forms a hyphen leaves when tx.HYPHEN_SPLIT splits a word,
#: read as the word they stand for before the lemmatizer sees them: alone,
#: simplemma reads Romanian "s" (s-a) as sud and "n" (n-am) as nord. The
#: list's own "s", "n", "m" come from the same splits, so both sides agree.
HYPHEN_PIECES = {
    "ro": {"s": "se", "m": "mă", "n": "nu", "l": "îl", "v": "vă", "i": "îi",
           "într": "în", "dintr": "din", "printr": "prin"},
}

#: Particles that close a hyphenated word without being split from it
#: ("celle-ci", "ce jour-là"). Only used to tell such a word from a name.
HYPHEN_PARTICLES = {"fr": {"ci", "là"}}
#: Hyphenated words that end like a verb and pronoun but are words in their
#: own right.
HYPHENATED_WORDS = {
    "fr": {"rendez-vous", "m'as-tu-vu", "qu'en-dira-t-on", "va-et-vient",
           "sauve-qui-peut", "je-ne-sais-quoi", "on-dit", "chez-soi"},
    "pt": set(),
}

#: A capital is only evidence of a name when the word sits inside running
#: text: right after a letter, a digit or a comma ("avec Paul", "Bernard,
#: Claire"). After anything else — a full stop, a colon, a bullet, an emoji,
#: a quote mark — it may just be the start of a line.
_INSIDE_CHARS = set(",'’")

METHOD_SURFACE = "surface"
METHOD_LEMMA = "lemma"
METHOD_CHARACTER = "character"


def language_code(name: str | None) -> str | None:
    """ISO code for a language name, or None when it is not recognised."""
    if not name:
        return None
    lowered = name.lower()
    for word, code in LANGUAGE_CODES.items():
        if re.search(rf"\b{word}\b", lowered):
            return code
    stripped = lowered.strip()
    if stripped in set(LANGUAGE_CODES.values()):
        return stripped
    return None


def _load_lemmatizer(code: str):
    """(function, label) for the best installed lemmatizer, or (None, reason)."""
    if code in ("ru", "uk"):
        try:
            import pymorphy3  # type: ignore

            morph = pymorphy3.MorphAnalyzer(lang=code)
            version = getattr(pymorphy3, "__version__", "")
            return (
                lambda w: morph.parse(w)[0].normal_form,
                f"pymorphy3 {version}".strip(),
            )
        except Exception:
            pass  # dictionaries for the language missing: try simplemma
    try:
        import simplemma  # type: ignore
    except ImportError:
        return None, "no lemmatizer installed"
    try:
        simplemma.lemmatize("test", lang=code)
    except Exception:
        return None, f"simplemma does not cover '{code}'"
    try:
        from importlib.metadata import version as _v

        label = f"simplemma {_v('simplemma')}"
    except Exception:  # pragma: no cover
        label = "simplemma"
    return _guarded(lambda w: simplemma.lemmatize(w, lang=code), code), label


#: How much rarer than the word itself (in zipf, i.e. powers of ten) a
#: dictionary form may be before it is distrusted.
LEMMA_GAP = 2.5
#: Zipf 5.0: about the 300 most common words of a language.
COMMON_ZIPF = 5.0


def _guarded(lemmatize, code: str):
    """`lemmatize`, but a common word keeps its own spelling when its
    dictionary form is a far rarer word on the frequency list.

    simplemma's tables send some of the most common words to homographs:
    Romanian ca -> calciu ("calcium"), care -> car, are -> ara ("to plow");
    Swedish jag -> jaga ("to hunt"), är -> ära ("honor"); Italian libro ->
    librare; German hast -> hasten; Czech dobrý -> dokonavý. A dictionary form
    is rarely a hundred times rarer than one of its own forms, and when it is
    and the form is still a word on the list, it is nearly always one of
    these. A correct but rare dictionary form that is not on the list at all
    (Hungarian remélem -> remél) is left alone. Counting the word by spelling
    costs a little grouping; the wrong form merges unrelated words and puts
    the homograph in the report.
    """
    try:
        import wordfreq  # type: ignore

        wordfreq.zipf_frequency("a", code)
    except Exception:
        return lemmatize
    zipf = wordfreq.zipf_frequency

    def guarded(word: str) -> str:
        lemma = lemmatize(word)
        if not lemma or lemma.lower() == word.lower():
            return lemma
        z_lemma, z_word = zipf(lemma, code), zipf(word, code)
        if z_lemma > 0 and z_word - z_lemma >= LEMMA_GAP:
            return word
        # One of the few hundred most common words whose "dictionary form"
        # is on no list at all: Polish mnie -> miąć, Portuguese vezes ->
        # vezar, Dutch jij -> jijen. Only a slip produces that.
        if z_lemma == 0 and z_word >= COMMON_ZIPF:
            return word
        return lemma

    guarded.raw = lemmatize
    return guarded


@dataclass
class Lexicon:
    """Turns field text into the words the tool counts."""

    language: str | None = None
    method: str = METHOD_SURFACE
    #: Which library did the reduction, e.g. "simplemma 1.1.2".
    tool: str = ""
    #: Why dictionary forms were not used, when they were not.
    fallback_reason: str = ""
    names: frozenset = field(default_factory=frozenset)
    #: The script writes no spaces between words (Thai, Lao, Khmer, Burmese)
    #: and no segmenter is used, so a "word" here is a run of text up to the
    #: next space or punctuation mark, often a whole phrase. The curve and the
    #: first-seen marks are withheld for such a deck rather than shown wrong.
    unsegmented: bool = False
    _lemma = None
    _cache: dict = field(default_factory=dict)

    # -- the one call everything measures through --------------------------

    def words(self, text: str) -> list[str]:
        """Counted words in `text`: names dropped, reduced as `method` says."""
        if not text:
            return []
        if self.method == METHOD_CHARACTER:
            return tx.words(text, self.language)
        out: list[str] = []
        for token in self.split(text):
            if token in self.names:
                continue
            out.append(self.normalize(token))
        return out

    def split(self, text: str) -> list[str]:
        """Lowercased surface tokens, with elisions split in dictionary mode."""
        toks = tx.words(text, self.language)
        if self.method != METHOD_LEMMA:
            return toks
        clitics = ELIDED_CLITICS.get(self.language or "", set())
        trailing = HYPHEN_CLITICS.get(self.language or "", set())
        whole = HYPHENATED_WORDS.get(self.language or "", set())
        if not clitics and not trailing:
            return toks
        out: list[str] = []
        for tok in toks:
            if tok.replace("’", "'") in whole:
                out.append(tok)
                continue
            head, sep, rest = tok.replace("’", "'").partition("'")
            if sep and rest and head in clitics:
                out.append(head + "'")
                tok = rest
            out += self._split_hyphen(tok, trailing, whole)
        return out

    @staticmethod
    def _split_hyphen(tok: str, trailing: set, whole: set) -> list[str]:
        """"donne-le-moi" -> donne, le, moi; "a-t-il" -> a, il."""
        if "-" not in tok or not trailing or tok in whole:
            return [tok]
        parts = tok.split("-")
        tail: list[str] = []
        while len(parts) > 1 and parts[-1] in trailing:
            tail.insert(0, parts.pop())
        if not tail:
            return [tok]
        return ["-".join(parts)] + [p for p in tail if p != "t"]

    def normalize(self, token: str) -> str:
        """The key a word is counted and matched under."""
        return tx.match_key(self.shown(token), self.language)

    def shown(self, token: str) -> str:
        """The word as the report names it: its dictionary form where there
        is one, spelled as the deck spells it (a Persian ZWNJ stays)."""
        if self.method != METHOD_LEMMA or self._lemma is None:
            return token
        hit = self._cache.get(token)
        if hit is None:
            token = HYPHEN_PIECES.get(self.language or "", {}).get(token, token)
            try:
                # lower(), not casefold(): casefold() spells German ß as ss
                # ("strasse", "gross") and Greek final ς as σ ("δρόμοσ"),
                # and the report would print those spellings.
                hit = (self._lemma(token) or token).lower()
            except Exception:
                hit = token
            self._cache[token] = hit
        return hit

    def overruled(self, token: str) -> str:
        """The lemmatizer's own dictionary form where `_guarded` kept the
        spelling instead, else "". Matching accepts it as one more key, so a
        correct but rare form (Polish niektórych -> niektóry) still meets the
        deck's headword; the report keeps naming the spelling."""
        raw = getattr(self._lemma, "raw", None)
        if raw is None or self.method != METHOD_LEMMA:
            return ""
        token = HYPHEN_PIECES.get(self.language or "", {}).get(token, token)
        try:
            form = (raw(token) or "").lower()
        except Exception:
            return ""
        return form if form and form != self.shown(token) else ""

    # -- for the report ----------------------------------------------------

    def describe(self) -> str:
        """One line naming the counting method, for every report format."""
        if self.unsegmented:
            return (
                "This script is written without spaces between words, and the tool "
                "has no word splitter for it, so words cannot be counted: a run of "
                "text up to the next space is often a whole phrase. The progression "
                "curve and the first-seen marks are not measured, and the vocabulary "
                "size and words-per-field figures count those runs, not words. "
                "Judge progression from the cards themselves."
            )
        if self.method == METHOD_CHARACTER:
            base = "Words are counted per character, since the script is not space-separated"
        elif self.method == METHOD_LEMMA:
            base = f"Words are counted by dictionary form ({self.tool})"
        else:
            # Arabic says why in its own sentence below.
            shown = self.fallback_reason and self.language != "ar"
            reason = f" — {self.fallback_reason}" if shown else ""
            base = f"Words are counted by spelling, each inflected form separately{reason}"
        if self.method != METHOD_CHARACTER:
            if self.language in CAPITALIZED_NOUNS:
                base += "; names are not detected, because this language capitalizes every noun"
            elif self.language in UNCASED:
                base += (
                    "; names are not detected, because the script has no capital "
                    "letters, so they count as vocabulary here and on the frequency list"
                )
            else:
                base += f"; {len(self.names):,} names left out"
        base += "."
        if self.language == "ar" and self.method != METHOD_LEMMA:
            base += (
                " Arabic is counted by spelled form, because simplemma has no Arabic: "
                "a word with و, ب, ل or ال attached is a separate word, so والكتاب "
                "and الكتاب count as two."
            )
        elif self.language in ("he", "yi") and self.method != METHOD_LEMMA:
            base += (
                " Hebrew is counted by spelled form, with the vowel points removed as "
                "the frequency list removes them: a word with ו, ש, ה, ב, ל, מ or כ "
                "attached is a separate word, so ולא and לא count as two."
            )
        elif self.language == "ko" and tx.korean_morphemes():
            base += (
                f" Korean is split into morphemes with {tx.korean_splitter()}, the way "
                "the frequency list is, so a particle such as 는 or 를 or an ending "
                "such as 어요 counts as a word of its own."
            )
        elif self.language == "fa":
            base += (
                " A Persian word spelled with or without the zero-width non-joiner "
                "(می\u200cخوام, میخوام), or with the Arabic forms of kaf and yeh, counts "
                "as one word."
            )
        return base

    @property
    def label(self) -> str:
        """Short tag stored in provenance: 'lemma:simplemma 1.1.2', 'surface'."""
        if self.method == METHOD_LEMMA:
            return f"lemma:{self.tool}"
        return self.method


#: Scripts written without spaces between words, which no splitter here
#: handles. Japanese and Chinese are counted per character instead.
UNSEGMENTED_SCRIPTS = {"THAI", "LAO", "KHMER", "MYANMAR", "TIBETAN"}


def _is_unsegmented_text(text: str) -> bool:
    import unicodedata

    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    hits = 0
    for ch in letters:
        try:
            hits += unicodedata.name(ch).split()[0] in UNSEGMENTED_SCRIPTS
        except ValueError:
            pass
    return hits / len(letters) > 0.5


def _is_cjk_text(text: str) -> bool:
    cjk = sum(1 for ch in text if tx.is_cjk_char(ch))
    letters = sum(1 for ch in text if ch.isalpha())
    return bool(letters) and cjk / letters > 0.5


def find_names(
    texts: list[str],
    language: str | None = None,
    companions: list[str] | None = None,
    elsewhere: list[str] | None = None,
) -> frozenset:
    """Words that are only ever written with a capital, and either at least
    once somewhere other than the start of a sentence, or with the same
    capital in the translation beside it.

    "Paul" opening a line could be any word; "avec Paul" can only be a name,
    and once that is established "Paul" is a name at the start of a line too.
    A name that only ever opens a sentence ("Léa a beaucoup de choses à
    porter") still shows itself in the translation ("Léa has lots of things
    to carry"), which `companions` holds, one entry per text.

    A word the deck also writes in lowercase anywhere is ordinary vocabulary,
    including in fields the curve does not read (`elsewhere`): the headword
    "inutile" clears the "Inutile" that opens a sentence and that a commentary
    quotes. A hyphenated word is cleared by its first part ("dis" in "Dis-moi").
    """
    if language in CAPITALIZED_NOUNS:
        return frozenset()
    capital_inside: set[str] = set()
    lowercase: set[str] = set()
    for text in elsewhere or ():
        for token in tx.WORD_RE.findall(tx.prepare(text or "", language)):
            if not token[0].isupper():
                lowercase.add(token.lower())
    for i, text in enumerate(texts):
        text = tx.prepare(text or "", language)
        beside = (companions[i] if companions and i < len(companions) else "") or ""
        capital_beside = (
            {w for w in tx.WORD_RE.findall(beside) if w[0].isupper()} if beside else set()
        )
        for m in tx.WORD_RE.finditer(text):
            token = m.group(0)
            low = token.lower()
            if not token[0].isupper():
                lowercase.add(low)
                continue
            if len(token) == 1:  # "I", "A" — never a name
                continue
            before = text[: m.start()].rstrip()
            if before and (before[-1].isalnum() or before[-1] in _INSIDE_CHARS):
                capital_inside.add(low)
            elif token in capital_beside:
                capital_inside.add(low)
    trailing = HYPHEN_CLITICS.get(language or "", set()) | HYPHEN_PARTICLES.get(
        language or "", set()
    )

    def verb_with_pronoun(w: str) -> bool:  # "dis-moi", "celle-ci"; not "saint-denis"
        parts = w.split("-")
        return len(parts) > 1 and parts[-1] in trailing and parts[0] in lowercase

    return frozenset(w for w in capital_inside - lowercase if not verb_with_pronoun(w))


def build(
    texts: list[str],
    language_name: str | None = None,
    companions: list[str] | None = None,
    elsewhere: list[str] | None = None,
) -> Lexicon:
    """A Lexicon for this deck's measured text.

    `texts` is the target-language text the curve reads; names are found there.
    `companions` is each text's translation and `elsewhere` the deck's other
    fields, see find_names.
    """
    code = language_code(language_name)
    sample = " ".join(texts[:400])
    if _is_cjk_text(sample) or code in ("zh", "ja"):
        return Lexicon(language=code, method=METHOD_CHARACTER)

    unsegmented = _is_unsegmented_text(sample)
    names = find_names(texts, code, companions, elsewhere)
    if unsegmented:
        return Lexicon(language=code, fallback_reason="", names=names, unsegmented=True)
    if code is None:
        reason = (
            "the target language was not identified"
            if not language_name or language_name == "unknown"
            else f"no lemmatizer is set up for {language_name}"
        )
        return Lexicon(language=None, fallback_reason=reason, names=names)

    lemma, label = _load_lemmatizer(code)
    if lemma is None:
        return Lexicon(language=code, fallback_reason=label, names=names)
    lex = Lexicon(language=code, method=METHOD_LEMMA, tool=label, names=names)
    lex._lemma = lemma
    return lex
