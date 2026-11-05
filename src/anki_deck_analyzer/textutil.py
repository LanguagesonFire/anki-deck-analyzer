"""Turning Anki field HTML into something measurable."""

from __future__ import annotations

import html
import re
import unicodedata

SOUND_RE = re.compile(r"\[sound:([^\]]+)\]")
IMG_RE = re.compile(r"""<img\b[^>]*?\bsrc\s*=\s*["']?([^"'>\s]+)""", re.I)
TTS_RE = re.compile(r"\{\{\s*tts\b", re.I)
CLOZE_RE = re.compile(r"\{\{c(\d+)::(.*?)(?:::(.*?))?\}\}", re.S)
TAG_RE = re.compile(r"<[^>]+>")
#: Inline tags sit inside running text and render with no gap of their own:
#: `<span>While tidying up</span>, I found` reads "up, I found" on the card.
#: Replacing them with a space invents a gap before the comma, which then
#: looks like a typo in the deck. Ruby is left out: its reading is separate text.
INLINE_TAG_RE = re.compile(
    r"<\s*/?\s*(?:a|abbr|b|bdi|bdo|cite|code|dfn|em|font|i|kbd|mark|q|s|samp|small|"
    r"span|strike|strong|sub|sup|u|var|wbr)\b[^>]*>",
    re.I,
)
BREAK_RE = re.compile(r"<\s*(br|/p|/div|/li|/tr)\b[^>]*>", re.I)
WS_RE = re.compile(r"[ \t ]+")


def _mark_class() -> str:
    """A character class of the combining marks in the Basic Multilingual Plane.

    Python's `re` has no \\p{M}, and a mark is not a word character to it, so
    without this a vowel sign or a decomposed accent ends the word: Hindi
    "नमस्ते" came out as "नमस" and "त", Vietnamese written in NFD as "vie" and
    "t".
    """
    ranges: list[tuple[int, int]] = []
    for cp in range(0x10000):
        if unicodedata.category(chr(cp)) in ("Mn", "Mc", "Me"):
            if ranges and ranges[-1][1] == cp - 1:
                ranges[-1] = (ranges[-1][0], cp)
            else:
                ranges.append((cp, cp))
    return "".join(
        f"\\u{lo:04x}" if lo == hi else f"\\u{lo:04x}-\\u{hi:04x}" for lo, hi in ranges
    )


#: One run of a word: a letter, then letters or the marks written on them.
_RUN = rf"[^\W\d_](?:[^\W\d_]|[{_mark_class()}])*"
#: Runs joined by an apostrophe, a hyphen, or the zero-width non-joiner that
#: Persian writes inside a word ("کتاب\u200cها", "می\u200cخواهم"). A joiner at
#: either edge of a word is not part of it.
WORD_RE = re.compile(rf"{_RUN}(?:['’\-\u200c]{_RUN})*", re.UNICODE)

#: Languages written in Arabic script, whose text is prepared the way wordfreq
#: prepares it before tokenizing, so the deck side and the list side split
#: alike: NFKC, then every nonspacing mark (the short vowels, shadda, sukun)
#: and the tatweel removed. "ذَهَبْتُ" is "ذهبت" on the list. Hebrew and
#: Yiddish are prepared the same way (wordfreq's remove_marks), so a deck
#: written with niqqud matches: "שְׁלוֹשֶׁת" is "שלושת" there. Without it a pointed
#: Hebrew deck matched 1 of the list's top 1,000 words. Only these: stripping
#: marks elsewhere would destroy Vietnamese or Hindi written in decomposed
#: Unicode.
ARABIC_SCRIPT_LANGS = {"ar", "fa", "ur", "he", "yi"}
#: Removed with the marks: the tatweel, and the directional marks that RTL
#: card HTML carries (LRM, RLM, Arabic letter mark).
_ARABIC_SCRIPT_DROP = {0x0640: None, 0x200E: None, 0x200F: None, 0x061C: None}
#: Persian text spells one word several ways that say nothing about
#: vocabulary: with and without the ZWNJ ("میخوام" / "می\u200cخوام"), and with the
#: Arabic letter forms of kaf and yeh. Arabic itself is left alone, where ي
#: and ى are different letters.
_PERSIAN_KEY = str.maketrans({"\u200c": None, "ك": "ک", "ي": "ی", "ى": "ی"})


def prepare(text: str, lang: str | None = None) -> str:
    """`text` as wordfreq reads it before tokenizing, for languages where
    that differs from the text on the card. Everything else is unchanged."""
    if lang not in ARABIC_SCRIPT_LANGS or not text:
        return text
    text = unicodedata.normalize("NFKC", text).translate(_ARABIC_SCRIPT_DROP)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def match_key(word: str, lang: str | None = None) -> str:
    """The form two spellings of one word are compared under. Only Persian
    has one; everywhere else it is the word itself."""
    return word.translate(_PERSIAN_KEY) if lang == "fa" else word
#: Inline markup that changes what a card asks of the learner, as opposed to
#: markup that only decorates it. A deck that marks one word inside a
#: translation is naming the word to produce; flatten that away and the card
#: reads as though it simply hands over the answer. Measurement still goes
#: through strip_html — only the text a model reads keeps these.
EMPHASIS_TAG_RE = re.compile(
    r"<\s*(b|strong|i|em|u|mark)\b[^>]*>(.*?)<\s*/\s*\1\s*>", re.I | re.S
)
COLORED_SPAN_RE = re.compile(
    r"<\s*(font|span)\b[^>]*\bcolor\s*[:=][^>]*>(.*?)<\s*/\s*\1\s*>", re.I | re.S
)
_MARK_RUN_RE = re.compile(r"«{2,}|»{2,}")
FIELD_REF_RE = re.compile(r"\{\{[#^/]?([^}]+)\}\}")
#: Only fields the card actually shows. {{#Field}} ... {{/Field}} wraps a
#: section on a condition; it does not put the field on the card, and reading
#: it as though it did makes a production card look like a recognition one.
SHOWN_REF_RE = re.compile(r"\{\{(?![#^/])([^}]+)\}\}")
#: Anki renders {{type:Field}} as a box you type the answer into, which is the
#: most explicit form of production there is.
TYPE_PREFIX = "type:"
SENT_SPLIT_RE = re.compile(r"[.!?…。！？]+[\s\"'”»]*")

#: Scripts whose writing systems do not separate words with spaces.
_CJK_RANGES = (
    (0x3040, 0x30FF),  # kana
    (0x3400, 0x4DBF),  # CJK ext A
    (0x4E00, 0x9FFF),  # CJK unified
    (0xF900, 0xFAFF),  # compatibility ideographs
)


def is_cjk_char(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


#: Audio can also be an HTML5 player: a tap-to-play <audio src=...>, or an
#: <audio> holding <source src=...> elements. Anki plays both from the media
#: folder, so a deck that uses them has audio as surely as one using [sound:].
AUDIO_TAG_RE = re.compile(r"""<audio\b[^>]*?\bsrc\s*=\s*["']?([^"'>\s]+)""", re.I)
AUDIO_BLOCK_RE = re.compile(r"<audio\b.*?</audio\s*>", re.I | re.S)
SOURCE_RE = re.compile(r"""<source\b[^>]*?\bsrc\s*=\s*["']?([^"'>\s]+)""", re.I)


def sounds(raw: str) -> list[str]:
    """Filenames of the audio a field plays: [sound:...] tags, and
    <audio src=...> or <audio><source src=...></audio> players."""
    raw = raw or ""
    found = SOUND_RE.findall(raw) + AUDIO_TAG_RE.findall(raw)
    for block in AUDIO_BLOCK_RE.findall(raw):
        found += SOURCE_RE.findall(block)
    return found


def images(raw: str) -> list[str]:
    """Filenames referenced by <img src=...>."""
    return IMG_RE.findall(raw or "")


_TTS_LANG_RE = re.compile(r"\{\{\s*tts\s+([A-Za-z]{2,3})(?:[_-][A-Za-z]+)?\b", re.I)


def has_target_tts(raw: str, target_code: str | None) -> bool:
    """Whether a {{tts}} directive speaks the target language. Anki's tag
    names its language first ({{tts fr_FR:Front}}); one that names another
    language reads the translation aloud, which is no audio for the
    language being learned. With the target unknown, any directive counts."""
    if not has_tts_tag(raw):
        return False
    if not target_code:
        return True
    langs = [m.lower() for m in _TTS_LANG_RE.findall(raw or "")]
    return not langs or any(code == target_code.lower() for code in langs)


def has_tts_tag(raw: str) -> bool:
    """True if the text uses Anki's built-in {{tts}} speech directive."""
    return bool(TTS_RE.search(raw or ""))


#: An inline tag can still render as its own line: a deck's stylesheet may give
#: `<span class="subtitle">` `display: block`. Read that from the CSS, or the
#: line it starts runs into the text before it: "Au marchéAt the market" for a
#: title and the subtitle printed under it.
_BLOCK_DISPLAY_RE = re.compile(r"display\s*:\s*(?:block|flex|grid|list-item|table)\b", re.I)
_CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_INLINE_OPEN_RE = re.compile(
    r"<\s*(a|abbr|b|bdi|bdo|cite|code|dfn|em|font|i|kbd|mark|q|s|samp|small|"
    r"span|strike|strong|sub|sup|u|var)\b([^>]*)>",
    re.I,
)
_CLASS_ATTR_RE = re.compile(r"""\bclass\s*=\s*["']?([^"'>]+)""", re.I)


def block_classes(css: str) -> set[str]:
    """Classes a stylesheet displays as blocks. Only the element a selector
    lands on counts: in `.title .sub`, that is `.sub`."""
    out: set[str] = set()
    for selectors, body in _CSS_RULE_RE.findall(_CSS_COMMENT_RE.sub("", css or "")):
        if not _BLOCK_DISPLAY_RE.search(body):
            continue
        for selector in selectors.split(","):
            parts = re.split(r"[\s>+~]+", selector.strip())
            out.update(re.findall(r"\.([\w-]+)", parts[-1] if parts else ""))
    return out


def mark_inline_blocks(raw: str, classes: set[str]) -> str:
    """Put a line break around inline elements that render as blocks, through
    a class in `classes` or their own `style`, so stripping the tags keeps the
    lines apart."""
    if not raw or "<" not in raw:
        return raw
    out: list[str] = []
    pos = 0
    for m in _INLINE_OPEN_RE.finditer(raw):
        attrs = m.group(2)
        cls = _CLASS_ATTR_RE.search(attrs)
        is_block = bool(_BLOCK_DISPLAY_RE.search(attrs)) or bool(
            cls and classes and classes & set(cls.group(1).split())
        )
        if not is_block or m.start() < pos:
            continue
        # Find this element's own closing tag, past any nested ones of its kind.
        tag = m.group(1).lower()
        depth, end = 1, len(raw)
        for t in re.finditer(rf"<\s*(/?)\s*{tag}\b[^>]*>", raw[m.end():], re.I):
            depth += -1 if t.group(1) else 1
            if not depth:
                end = m.end() + t.end()
                break
        out += [raw[pos : m.start()], "<br>", raw[m.start() : end], "<br>"]
        pos = end
    out.append(raw[pos:])
    return "".join(out)


def strip_html(raw: str, keep_cloze_text: bool = True) -> str:
    """Plain, readable text from an Anki field."""
    if not raw:
        return ""
    text = raw
    if keep_cloze_text:
        text = CLOZE_RE.sub(lambda m: m.group(2), text)
    text = SOUND_RE.sub(" ", text)
    text = BREAK_RE.sub("\n", text)
    text = INLINE_TAG_RE.sub("", text)
    text = TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = text.replace(" ", " ")
    text = WS_RE.sub(" ", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def _emphasis_marker(m: re.Match) -> str:
    """Mark the emphasised word, not the whitespace around it.

    Editors leave a lot of empty formatting behind — a coloured span holding
    a single space, a bold tag closed after the trailing blank. Marking those
    puts noise like `«best»« » people` in front of the model.
    """
    inner = _mark_nested(m.group(2))
    core = inner.strip()
    if not core:
        return inner
    lead = inner[: len(inner) - len(inner.lstrip())]
    trail = inner[len(inner.rstrip()) :]
    return f"{lead}«{core}»{trail}"


def _mark_nested(inner: str) -> str:
    """Emphasis inside emphasis — one letter in bold within an italic
    example, `<em>j'é<b>t</b>ait</em>` — marked ‹t› rather than with a second
    «», which reads as a typo in the deck: «j'é«t»ait». Formatting that wraps
    the whole of the outer mark adds nothing and is dropped."""

    def nested(m: re.Match) -> str:
        text = _mark_nested(m.group(2))
        if not text.strip() or m.group(0).strip() == inner.strip():
            return text
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()) :]
        return f"{lead}‹{text.strip()}›{trail}"

    for pattern in (EMPHASIS_TAG_RE, COLORED_SPAN_RE):
        inner = pattern.sub(nested, inner)
    return inner


def _cloze_marker(m: re.Match) -> str:
    answer, hint = m.group(2), m.group(3)
    inside = f"{answer} | hint: {hint}" if hint else answer
    return f"[cloze: {inside}]"


#: Layout markup, rendered the way Anki shows it rather than flattened. A
#: `<br>` inside a list item is a second line, not a run-together bullet, and
#: content inside `<details>` is hidden until clicked — both change what the
#: learner actually sees.
_DETAILS_OPEN_RE = re.compile(
    r"<\s*details\b[^>]*>\s*(?:<\s*summary\b[^>]*>(.*?)<\s*/\s*summary\s*>)?", re.I | re.S
)
_DETAILS_CLOSE_RE = re.compile(r"<\s*/\s*details\s*>", re.I)
_LI_RE = re.compile(r"<\s*li\b[^>]*>", re.I)
_CELL_END_RE = re.compile(r"<\s*/\s*t[dh]\s*>", re.I)
_BLOCK_OPEN_RE = re.compile(r"<\s*(div|p|ul|ol|table|tr|h[1-6]|hr|blockquote)\b[^>]*>", re.I)
_BLOCK_CLOSE_RE = re.compile(r"<\s*/\s*(ul|ol|table|h[1-6]|blockquote)\s*>", re.I)
_TRAILING_PIPE_RE = re.compile(r"[ \t]*\|[ \t]*$", re.M)
_BLANK_RUN_RE = re.compile(r"\n{2,}")
_SCRIPT_RE = re.compile(r"(<\s*script\b[^>]*>)(.*?)(<\s*/\s*script\s*>)", re.I | re.S)
#: A field reference, or an HTML element in a string, means the script puts card content on screen.
_SCRIPT_RENDERS_RE = re.compile(r"\{\{[^}]+\}\}|<\s*/?\s*(?:div|p|span|details|summary|table|ul|ol|li|h\d|img|br)\b", re.I)


def _details_open(m: re.Match) -> str:
    label = TAG_RE.sub(" ", m.group(1) or "").strip() or "details"
    label = WS_RE.sub(" ", html.unescape(label))
    return f"\n[collapsed: {label}] "


def _render_layout(text: str) -> str:
    text = _DETAILS_OPEN_RE.sub(_details_open, text)
    text = _DETAILS_CLOSE_RE.sub(" [/collapsed]\n", text)
    text = _LI_RE.sub("\n• ", text)
    text = _CELL_END_RE.sub(" | ", text)
    text = _BLOCK_OPEN_RE.sub("\n", text)
    text = _BLOCK_CLOSE_RE.sub("\n", text)
    return text


def compact_template(template: str) -> str:
    """A card template with script bodies left out.

    Scripts usually decide behaviour, not layout, and they can be long enough
    to push the markup that does decide layout out of the pack. A script that
    carries field references or HTML is the exception: it renders the card
    (some decks build the whole back by inserting a markup string), so it
    stays, or the model would judge a back it cannot see.
    """

    def _omit(m: re.Match) -> str:
        body = m.group(2).strip()
        if len(body) <= 200 or _SCRIPT_RENDERS_RE.search(body):
            return m.group(0)
        return f"{m.group(1)} /* {len(body):,} characters of script omitted */ {m.group(3)}"

    return _BLANK_RUN_RE.sub("\n", _SCRIPT_RE.sub(_omit, (template or "").strip()))


def readable_field(raw: str) -> str:
    """Field text for a model to read.

    Plain like `strip_html`, except for the two kinds of markup that decide
    what the card actually asks: inline emphasis, which a deck uses to point
    at the word being tested, and cloze deletions, which say which part of the
    text is hidden on the front. Both are rendered as text so they survive
    into the evidence pack. Layout — line breaks, list items, table cells,
    collapsed sections — is kept the way Anki displays it; everything else is
    stripped as usual.
    """
    if not raw:
        return ""
    text = _render_layout(raw)
    for pattern in (EMPHASIS_TAG_RE, COLORED_SPAN_RE):
        # A marked word may sit inside a coloured span, or the other way
        # round, so run each until it stops matching rather than once.
        for _ in range(3):
            text, found = pattern.subn(_emphasis_marker, text)
            if not found:
                break
    text = _MARK_RUN_RE.sub(lambda m: m.group(0)[0], text)
    text = CLOZE_RE.sub(_cloze_marker, text)
    text = strip_html(text, keep_cloze_text=False)
    text = _TRAILING_PIPE_RE.sub("", text)
    return _BLANK_RUN_RE.sub("\n", text).strip()


#: Languages whose hyphen joins clitics onto words as grammar, not as part
#: of a word: Romanian s-a, m-am, n-am, într-o, scuzați-mă, dă-mi-l. wordfreq
#: splits every hyphen, so kept whole none of them would ever match the list
#: and every one would count as a new word. Split, the pieces are words the
#: learner meets everywhere else (see lexicon.HYPHEN_PIECES).
HYPHEN_SPLIT = {"ro"}


_KO_STATE: dict = {}


def korean_morphemes() -> bool:
    """Whether Korean can be split into morphemes the way wordfreq's Korean
    list is.

    The list holds 학교, 는, 를, 에 as separate entries, while a space-split
    deck holds 학교는 and 학교에: matched that way, about a third of a
    Korean deck's words are found on the list. Two splitters are used, in
    this order: Kiwi (`pip install kiwipiepy`), which installs on Windows,
    macOS and Linux alike, and MeCab through wordfreq (`wordfreq[mecab]`),
    which on Windows needs MeCab installed separately. On a Korean sentence
    deck Kiwi's pieces matched the list 87% of the time."""
    if "ok" not in _KO_STATE:
        _KO_STATE["ok"] = False
        try:
            from kiwipiepy import Kiwi  # type: ignore

            kiwi = Kiwi()
            kiwi.tokenize("학교에")
            _KO_STATE.update(ok=True, tool="Kiwi",
                             fn=lambda t: [x.form for x in kiwi.tokenize(t)])
        except Exception:
            try:
                from wordfreq import tokenize  # type: ignore

                tokenize("학교에", "ko")
                _KO_STATE.update(ok=True, tool="MeCab",
                                 fn=lambda t: tokenize(t, "ko"))
            except Exception:
                pass
    return _KO_STATE["ok"]


def korean_splitter() -> str:
    """"Kiwi" or "MeCab", whichever `korean_morphemes` found, else ""."""
    return _KO_STATE.get("tool", "") if korean_morphemes() else ""


#: Kiwi splits a closing consonant off as a morpheme of its own (할 -> 하 +
#: ᆯ). It is an ending, not a word, and the list never holds one.
_JAMO_ONLY_RE = re.compile(r"[ᄀ-ᇿ㄰-㆏]+")


def _ko_tokenize(text: str) -> list[str]:
    return [w for w in _KO_STATE["fn"](text) if not _JAMO_ONLY_RE.fullmatch(w)]


def words(text: str, lang: str | None = None) -> list[str]:
    """Lowercased word tokens.

    Space-separated scripts are tokenized on word characters. Text that is
    mostly CJK is tokenized per character, which is the closest cheap analogue
    to a word for measuring how much new material a card introduces. `lang`
    is the deck's ISO code; Arabic-script text is prepared first (`prepare`).
    """
    if not text:
        return []
    text = prepare(text, lang)
    cjk = sum(1 for ch in text if is_cjk_char(ch))
    letters = sum(1 for ch in text if ch.isalpha())
    if letters and cjk / letters > 0.5:
        return [ch for ch in text if is_cjk_char(ch)]
    if lang == "ko" and korean_morphemes():
        return [w for w in _ko_tokenize(text) if WORD_RE.fullmatch(w)]
    toks = [w.lower() for w in WORD_RE.findall(text)]
    if lang in HYPHEN_SPLIT:
        toks = [p for t in toks for p in t.split("-") if p]
    if lang == "el":
        toks = [_greek_single_accent(t) for t in toks]
    return toks


def _greek_single_accent(word: str) -> str:
    """Greek puts a second accent on a word before an enclitic: "τα
    μαθήματά μου". The word itself is "μαθήματα"; keep its first accent."""
    nfd = unicodedata.normalize("NFD", word)
    if nfd.count("́") < 2:
        return word
    i = nfd.rindex("́")
    return unicodedata.normalize("NFC", nfd[:i] + nfd[i + 1:])


def sentence_count(text: str) -> int:
    parts = [p for p in SENT_SPLIT_RE.split(text or "") if p.strip()]
    return max(1, len(parts)) if text.strip() else 0


def script_profile(text: str) -> dict[str, int]:
    """Count letters by Unicode script family, to tell languages apart."""
    counts: dict[str, int] = {}
    for ch in text or "":
        if not ch.isalpha():
            continue
        try:
            name = unicodedata.name(ch)
        except ValueError:
            continue
        family = name.split(" ")[0]
        counts[family] = counts.get(family, 0) + 1
    return counts


def field_refs(template: str) -> list[str]:
    """Field names referenced by a card template, in order of appearance."""
    out: list[str] = []
    for ref in FIELD_REF_RE.findall(template or ""):
        name = ref.strip().split(":")[-1].strip()
        if name and name not in out and not name.startswith(("#", "^", "/")):
            out.append(name)
    return out


def shown_fields(template: str) -> list[str]:
    """Field names the front or back of a card actually displays.

    Conditional wrappers and Anki's own specials are left out, so this answers
    "what does the learner see", which is what tells a production card from a
    recognition one.
    """
    specials = {"FrontSide", "Tags", "Type", "Deck", "Subdeck", "Card", "CardFlag"}
    out: list[str] = []
    for ref in SHOWN_REF_RE.findall(template or ""):
        name = ref.strip()
        if name.lower().startswith(TYPE_PREFIX):
            name = name[len(TYPE_PREFIX):]
        # Strip any remaining filters: {{furigana:text:Field}} -> Field
        name = name.split(":")[-1].strip()
        if name and name not in out and name not in specials:
            out.append(name)
    return out


_TABLE_RE = re.compile(r"(?is)<table\b.*?</table\s*>")
_ROW_RE = re.compile(r"(?is)<tr\b[^>]*>(.*?)</tr\s*>")
_CELL_RE = re.compile(r"(?is)<t[dh]\b[^>]*>(.*?)</t[dh]\s*>")


def has_paradigm_table(markup: str) -> bool:
    """Whether the markup holds a table that reads like a conjugation or
    declension table: at least three rows of two or more short entries side
    by side ("eu | sunt", "{{VC_1stS}} | {{VC_1stP}}"). A table used only for
    layout, such as a dictionary entry with an icon cell and a long
    definition, does not count."""
    for table in _TABLE_RE.findall(markup or ""):
        rows = 0
        for row in _ROW_RE.findall(table):
            cells = [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", c)).split())
                     for c in _CELL_RE.findall(row)]
            filled = [c for c in cells if c]
            if len(filled) >= 2 and all(len(c.split()) <= 4 for c in filled):
                rows += 1
        if rows >= 3:
            return True
    return False


def typed_fields(template: str) -> list[str]:
    """Fields a template asks the learner to type: {{type:Field}}."""
    out: list[str] = []
    for ref in SHOWN_REF_RE.findall(template or ""):
        name = ref.strip()
        if name.lower().startswith(TYPE_PREFIX):
            name = name[len(TYPE_PREFIX):].split(":")[-1].strip()
            if name and name not in out:
                out.append(name)
    return out


def cloze_fields(template: str) -> list[str]:
    """Field names a template renders as cloze deletions ({{cloze:Text}})."""
    out: list[str] = []
    for ref in SHOWN_REF_RE.findall(template or ""):
        parts = [p.strip() for p in ref.split(":")]
        if len(parts) > 1 and "cloze" in (p.lower() for p in parts[:-1]):
            if parts[-1] not in out:
                out.append(parts[-1])
    return out


def truncate(text: str, limit: int, marker: str = " …") -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(marker))].rstrip() + marker
