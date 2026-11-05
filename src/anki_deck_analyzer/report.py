"""Rendering the result: terminal, Markdown, HTML, JSON."""

from __future__ import annotations

import html
import json
import os
import re
import sys
from dataclasses import asdict
from datetime import date

from .apkg import Deck
from .criteria import FUNDAMENTAL
from .evidence import DeckProfile
from . import frequency as fq
from .metrics import Metrics, curve_table
from .scoring import Result

BAR_FULL = "█"
BAR_EMPTY = "░"


# --------------------------------------------------------------------------
# terminal
# --------------------------------------------------------------------------



#: The evidence pack's own notation, which the model sometimes copies into a
#: quote: first-seen counts, media placeholders, field-line bars. A reader of
#: the report never saw the pack, so it is taken out of what they read.
_PACK_MARKS_RE = re.compile(
    r"\s*<(?:first seen here|image|audio)[^>]*>|^\s*\|\s?", re.M
)


def _clean_quote(quote) -> str:
    text = _PACK_MARKS_RE.sub("", str(quote or ""))
    return " ".join(text.split())


def _use_color(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return hasattr(stream, "isatty") and stream.isatty()


class Style:
    def __init__(self, enabled: bool) -> None:
        self.on = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.on else text

    def bold(self, t): return self._wrap("1", t)
    def dim(self, t): return self._wrap("2", t)
    def green(self, t): return self._wrap("32", t)
    def yellow(self, t): return self._wrap("33", t)
    def red(self, t): return self._wrap("31", t)
    def cyan(self, t): return self._wrap("36", t)

    def for_score(self, score: int, text: str) -> str:
        if score >= 8:
            return self.green(text)
        if score >= 6:
            return self.cyan(text)
        if score >= 4:
            return self.yellow(text)
        return self.red(text)


def bar(score: int, width: int = 10) -> str:
    filled = max(0, min(width, round(score / 10 * width)))
    return BAR_FULL * filled + BAR_EMPTY * (width - filled)


def _wrap(text: str, width: int = 78, indent: str = "") -> str:
    import textwrap

    out = []
    for para in (text or "").split("\n"):
        if not para.strip():
            out.append("")
            continue
        out += textwrap.wrap(
            para, width=width, initial_indent=indent, subsequent_indent=indent
        )
    return "\n".join(out)


def render_terminal(
    deck: Deck,
    profile: DeckProfile,
    metrics: Metrics,
    result: Result,
    cost: float | None,
    model: str,
    stream=None,
    provenance=None,
) -> str:
    stream = stream or sys.stdout
    s = Style(_use_color(stream))
    L: list[str] = []

    L.append("")
    L.append(s.bold(deck.name))
    L.append(
        s.dim(
            f"{profile.target_language} · {profile.deck_kind} · "
            f"{metrics.note_count:,} notes · {metrics.card_count:,} cards"
        )
    )
    L.append("")

    headline = f"{result.total:.0f}/100   {result.grade}"
    L.append("  " + s.for_score(round(result.total / 10), s.bold(headline)))
    L.append("  " + s.dim(result.grade_note))
    L.append("")

    L.append(s.bold("  The five fundamentals") + s.dim("   (80% of the score)"))
    for group, label in ((FUNDAMENTAL, None), ("nice-to-have", "  The four nice-to-haves")):
        if label:
            L.append("")
            L.append(s.bold(label) + s.dim("   (20% of the score)"))
        for sc in result.scores:
            if sc.criterion.group != group:
                continue
            if not sc.assessed:
                L.append(
                    f"    {'—':>2}/10 {s.dim(bar(0))}  {sc.criterion.title} "
                    + s.dim("(not assessed)")
                )
                continue
            line = (
                f"    {sc.score:>2}/10 {s.for_score(sc.score, bar(sc.score))}  "
                f"{sc.criterion.title} {s.dim(f'({sc.criterion.weight:.0%})')}"
            )
            if sc.criterion.measured:
                line += s.dim("  presence only")
            elif sc.spread:
                line += s.dim(
                    f"  {'-'.join(str(v) for v in (min(sc.scores_seen), max(sc.scores_seen)))}"
                    f" over {len(sc.scores_seen)} passes"
                )
            elif sc.confidence == "low":
                line += s.dim("  low confidence")
            L.append(line)
    L.append("")

    if result.by_key("progression") and metrics.buckets:
        L.append(s.bold("  How much new language each part of the deck throws at you"))
        for line in curve_table(metrics).split("\n"):
            L.append("    " + s.dim(line) if line.startswith("-") else "    " + line)
        L.append("")

    if result.by_key("vocabulary") and metrics.frequency.available:
        L.append(s.bold("  The deck's vocabulary against a general frequency list"))
        for line in fq.table(metrics.frequency).split("\n"):
            L.append("    " + s.dim(line) if line.startswith("-") else "    " + line)
        L.append("")

    for sc in result.scores:
        if not sc.assessed:
            L.append(s.bold(f"  {sc.criterion.title}"))
            L.append(_wrap(f"Could not be assessed: {sc.error}", indent="    "))
            L.append("")
            continue
        head = f"  {sc.criterion.title} — {sc.score}/10"
        L.append(s.for_score(sc.score, s.bold(head)))
        body = sc.verdict or sc.basis
        if body:
            L.append(_wrap(body, indent="    "))
        for item in sc.strengths:
            L.append(_wrap(f"+ {item}", indent="    ", width=76))
        for item in sc.weaknesses:
            L.append(_wrap(f"- {item}", indent="    ", width=76))
        for ev in sc.evidence[:3]:
            pos = f"#{ev['position']}" if ev.get("position") else "measured"
            L.append(
                s.dim(_wrap(f"“{_clean_quote(ev.get('quote'))}”  [{pos}]", indent="      "))
            )
        L.append("")

    summary = result.summary if hasattr(result, "summary") else None
    if summary:
        L.append(s.bold("  Verdict"))
        L.append(_wrap(summary.get("verdict", ""), indent="    "))
        L.append("")
        for title, items in (
            ("Worth it if", summary.get("best_for", [])),
            ("Not for you if", summary.get("not_for", [])),
            ("You could fix yourself", summary.get("fixable", [])),
            ("You could not fix", summary.get("not_fixable", [])),
        ):
            if not items:
                continue
            L.append(s.bold(f"  {title}"))
            for item in items:
                L.append(_wrap(f"· {item}", indent="    ", width=76))
            L.append("")

    if result.extras:
        L.append(s.bold("  Other helpful features") + s.dim("  (not part of the score)"))
        for label, present, detail in result.extras:
            mark = s.green("yes") if present else s.dim(" no")
            L.append(f"    {mark}  {label} {s.dim('— ' + detail)}")
        L.append("")

    caveats = list(deck.warnings) + list(metrics.warnings)
    caveats.append(metrics.lexicon.describe())
    if not metrics.frequency.available:
        caveats.append(f"No frequency list was used: {metrics.frequency.reason}.")
    caveats.append(
        "Audio and images are scored on whether they are there. This tool reads "
        "the cards; it cannot listen to a recording or look at a picture."
    )
    caveats.append(
        f"The judged criteria come from a sample of the deck, read by {model}. "
        "They are one reading, not a measurement."
    )
    if result.unassessed:
        caveats.append(
            "Not assessed, and left out of the weighting: "
            + ", ".join(result.unassessed)
        )
    L.append(s.bold("  Read this with"))
    for c in caveats:
        L.append(_wrap(f"· {c}", indent="    ", width=76))
    L.append("")

    if provenance is not None:
        L.append(s.bold("  To reproduce this score"))
        L.append(_wrap(provenance.command(os.path.basename(deck.path)), indent="    "))
        L.append(
            s.dim(
                _wrap(
                    f"analyzer {provenance.version} · sample seed "
                    f"{provenance.sample_seed} · words counted "
                    f"{provenance.word_counting or 'surface'} · deck file "
                    f"fingerprint (SHA-256) {provenance.deck_sha256[:16]}…",
                    indent="    ",
                )
            )
        )
        if not provenance.complete:
            L.append(
                _wrap(
                    "PARTIAL RUN. Only some criteria were judged and the total is "
                    "rescaled over those. Not comparable with a full run.",
                    indent="    ",
                    width=76,
                )
            )
        L.append("")

    if cost is not None:
        L.append(s.dim(f"  API cost for this run: ${cost:.2f}"))
        L.append("")

    return "\n".join(L)


# --------------------------------------------------------------------------
# markdown
# --------------------------------------------------------------------------


def render_markdown(
    deck: Deck,
    profile: DeckProfile,
    metrics: Metrics,
    result: Result,
    model: str,
) -> str:
    summary = getattr(result, "summary", None) or {}
    L = [
        f"# {deck.name}",
        "",
        f"**{result.total:.0f}/100 — {result.grade}.** {result.grade_note}",
        "",
        f"{profile.target_language} · {profile.deck_kind} · "
        f"{metrics.note_count:,} notes · {metrics.card_count:,} cards · "
        f"analyzed {_today()}",
        "",
    ]
    if summary.get("headline"):
        L += [summary["headline"], ""]

    L += ["## Scores", "", "| Criterion | Weight | Score |", "|---|---|---|"]
    for sc in result.scores:
        value = f"{sc.score}/10" if sc.assessed else "not assessed"
        L.append(f"| {sc.criterion.title} | {sc.criterion.weight:.0%} | {value} |")
    L += ["", f"Fundamentals: {result.fundamentals_total:.0f}/100. "
          f"Nice-to-haves: {result.nice_to_have_total:.0f}/100.", ""]

    if summary.get("verdict"):
        L += ["## Verdict", "", summary["verdict"], ""]
    for title, key in (
        ("Worth it if", "best_for"),
        ("Not for you if", "not_for"),
        ("You could fix this yourself", "fixable"),
        ("You could not fix this", "not_fixable"),
    ):
        items = summary.get(key) or []
        if items:
            L += [f"### {title}", ""] + [f"- {i}" for i in items] + [""]

    L += ["## Criterion by criterion", ""]
    for sc in result.scores:
        L.append(f"### {sc.criterion.title} — "
                 + (f"{sc.score}/10" if sc.assessed else "not assessed"))
        L.append("")
        if sc.verdict or sc.basis:
            L += [sc.verdict or sc.basis, ""]
        if sc.strengths:
            L += ["**Working:**", ""] + [f"- {i}" for i in sc.strengths] + [""]
        if sc.weaknesses:
            L += ["**Not working:**", ""] + [f"- {i}" for i in sc.weaknesses] + [""]
        if sc.evidence:
            L += ["**From the cards:**", ""]
            for ev in sc.evidence:
                pos = f" (card #{ev['position']})" if ev.get("position") else ""
                L.append(f"- “{_clean_quote(ev.get('quote'))}”{pos} — {ev.get('shows','')}")
            L.append("")

    L += ["## Other helpful features", ""]
    for label, present, detail in result.extras:
        L.append(f"- {'Yes' if present else 'No'} — {label} ({detail})")
    L.append("")

    if metrics.buckets:
        L += [
            "## Progression curve",
            "",
            "The deck split into ten equal parts in study order. "
            "'New-word rate' is the share of words in that part not seen earlier.",
            "",
            "```",
            curve_table(metrics),
            "```",
            "",
        ]
    if metrics.frequency.available:
        L += [
            "## Vocabulary against a frequency list",
            "",
            "```",
            fq.table(metrics.frequency),
            "```",
            "",
        ]

    L += [
        "",
        "## How to read this",
        "",
        f"- {metrics.lexicon.describe()}",
        *(
            []
            if metrics.frequency.available
            else [f"- No frequency list was used: {metrics.frequency.reason}."]
        ),
        "- Audio and images are scored on whether they are present. This tool "
        "reads the cards; it cannot listen to a recording or look at a picture.",
        f"- The other seven criteria are judged by {model} from a sample of the "
        "deck: the opening, three runs from the middle, the closing, and a random "
        "spread. They are one reading, not a measurement.",
        "- The weighting is the one published in "
        "[How to choose an Anki deck you'll actually use]"
        "(https://languages-on-fire.com), and reflects one approach to language "
        "learning rather than a validated formula.",
        "",
    ]
    return "\n".join(L)


# --------------------------------------------------------------------------
# html
# --------------------------------------------------------------------------

_CSS = """
:root{--bg:#fbfaf8;--fg:#1c1a18;--muted:#6b6560;--line:#e5e0d8;--card:#fff;
--good:#2f7d4f;--ok:#2b6a86;--warn:#9a6a10;--bad:#9d3535;--accent:#c1440e;
color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
--bg:#16150f;--fg:#ece7de;--muted:#9c958a;--line:#332f28;--card:#1e1c16;
--good:#6fbf8b;--ok:#6fb3cf;--warn:#d6a441;--bad:#e07a7a;--accent:#f0803c;
color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);overflow-wrap:break-word;
font:16px/1.6 ui-serif,Georgia,'Iowan Old Style',serif}
.wrap{max-width:820px;margin:0 auto;padding-block:48px 80px;padding-inline:16px;
min-width:0}
img{max-width:100%}
h1{font-size:2rem;line-height:1.2;margin:0 0 .2em}
h2{font-size:1.25rem;margin:2.4em 0 .6em;padding-bottom:.3em;
border-bottom:1px solid var(--line)}
h3{font-size:1.02rem;margin:1.8em 0 .3em}
.sub{color:var(--muted);font-size:.9rem;margin:0 0 2em;
font-family:ui-sans-serif,system-ui,sans-serif}
.verdict{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:22px 24px;margin:0 0 28px}
.big{font-size:2.6rem;font-weight:700;line-height:1;font-family:ui-sans-serif,system-ui,sans-serif}
.grade{font-size:1.05rem;color:var(--muted);margin-left:.5em}
/* A table is the one thing allowed to be wider than the page, and only
   inside its own scroller, so the body itself never scrolls sideways. */
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-family:ui-sans-serif,system-ui,sans-serif;
font-size:.9rem}
th,td{text-align:left;padding:9px 8px;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:600;font-size:.78rem;text-transform:uppercase;
letter-spacing:.06em}
td.n{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.meter{display:block;height:7px;border-radius:4px;background:var(--line);overflow:hidden;
min-width:80px}
@media (max-width:540px){
  th:last-child,td:last-child{display:none}   /* the bar duplicates the number */
  .wrap{padding-block:32px 56px}
  h1{font-size:1.6rem}
  .big{font-size:2.1rem}
}
.meter i{display:block;height:100%;border-radius:4px}
.s9 i,.s8 i{background:var(--good)}.s7 i,.s6 i{background:var(--ok)}
.s5 i,.s4 i{background:var(--warn)}.s3 i,.s2 i,.s1 i,.s0 i{background:var(--bad)}
.s10 i{background:var(--good)}
ul{padding-left:1.2em}li{margin:.3em 0}
blockquote{margin:.7em 0;padding:.4em 0 .4em 1em;border-left:3px solid var(--line);
color:var(--muted);font-size:.95rem}
blockquote span{display:block;font-size:.8rem;margin-top:.3em;
font-family:ui-sans-serif,system-ui,sans-serif}
pre{overflow-x:auto;background:var(--card);border:1px solid var(--line);
border-radius:8px;padding:14px;font-size:.78rem;line-height:1.5}
.cols{display:flex;flex-wrap:wrap;gap:24px}
.cols>div{flex:1 1 260px;min-width:0}
blockquote,li,p,h1,h2,h3{min-width:0}
.note{color:var(--muted);font-size:.85rem;
font-family:ui-sans-serif,system-ui,sans-serif}
.pill{display:inline-block;font-size:.72rem;padding:1px 7px;border-radius:99px;
border:1px solid var(--line);color:var(--muted);margin-left:.4em;
font-family:ui-sans-serif,system-ui,sans-serif;vertical-align:1px}
footer{margin-top:3em;padding-top:1.2em;border-top:1px solid var(--line)}
"""


def _today() -> str:
    today = date.today()
    return f"{today.strftime('%B')} {today.day}, {today.year}"


def _e(text) -> str:
    return html.escape(str(text or ""))


def _rows(result: Result, group: str) -> str:
    out = []
    for sc in result.scores:
        if sc.criterion.group != group:
            continue
        if sc.assessed:
            value = f"{sc.score}/10"
            meter = (
                f'<span class="meter s{sc.score}"><i style="width:{sc.score * 10}%">'
                "</i></span>"
            )
        else:
            value = "—"
            meter = '<span class="meter s0"><i style="width:0"></i></span>'
        pill = ""
        if sc.spread:
            pill = (
                f'<span class="pill">{min(sc.scores_seen)}-{max(sc.scores_seen)} '
                f"over {len(sc.scores_seen)}</span>"
            )
        elif sc.criterion.measured:
            pill = '<span class="pill" title="Counted, not judged. The tool cannot hear or see the files.">presence only</span>'
        elif sc.confidence == "low":
            pill = '<span class="pill">low confidence</span>'
        out.append(
            f"<tr><td>{_e(sc.criterion.title)}{pill}</td>"
            f'<td class="n">{sc.criterion.weight:.0%}</td>'
            f'<td class="n">{value}</td><td>{meter}</td></tr>'
        )
    return "\n".join(out)


def _score_table(result: Result, group: str) -> str:
    return (
        '<div class="scroll"><table><tr><th>Criterion</th><th>Weight</th>'
        f"<th>Score</th><th></th></tr>{_rows(result, group)}</table></div>"
    )


def render_html(
    deck: Deck,
    profile: DeckProfile,
    metrics: Metrics,
    result: Result,
    model: str,
    provenance=None,
) -> str:
    summary = getattr(result, "summary", None) or {}
    today = date.today()
    parts: list[str] = []

    parts.append(f"<h1>{_e(deck.name)}</h1>")
    parts.append(
        f'<p class="sub">{_e(profile.target_language)} &middot; '
        f"{_e(profile.deck_kind)} &middot; {metrics.note_count:,} notes &middot; "
        f"{metrics.card_count:,} cards &middot; analyzed "
        f"{today.strftime('%B')} {today.day}, {today.year}</p>"
    )

    parts.append(
        '<div class="verdict">'
        f'<div><span class="big">{result.total:.0f}</span>'
        f'<span class="grade">/100 &nbsp;{_e(result.grade)}</span></div>'
        f'<p style="margin:.6em 0 0">{_e(summary.get("headline") or result.grade_note)}</p>'
        "</div>"
    )

    parts.append("<h2>The five fundamentals</h2>")
    parts.append(
        '<p class="note">80% of the score. These concern the learning material '
        "itself — the part that cannot be fixed without rewriting the deck.</p>"
    )
    parts.append(_score_table(result, FUNDAMENTAL))

    parts.append("<h2>The four nice-to-haves</h2>")
    parts.append(
        '<p class="note">20% of the score. Worth having, and mostly addable '
        "later.</p>"
    )
    parts.append(_score_table(result, "nice-to-have"))

    if summary.get("verdict"):
        parts.append("<h2>Verdict</h2>")
        for para in summary["verdict"].split("\n\n"):
            if para.strip():
                parts.append(f"<p>{_e(para.strip())}</p>")

    col_pairs = [
        ("Worth it if", summary.get("best_for")),
        ("Not for you if", summary.get("not_for")),
        ("You could fix this yourself", summary.get("fixable")),
        ("You could not fix this", summary.get("not_fixable")),
    ]
    live = [(t, items) for t, items in col_pairs if items]
    if live:
        parts.append('<div class="cols">')
        for title, items in live:
            lis = "".join(f"<li>{_e(i)}</li>" for i in items)
            parts.append(f"<div><h3>{_e(title)}</h3><ul>{lis}</ul></div>")
        parts.append("</div>")

    parts.append("<h2>Criterion by criterion</h2>")
    for sc in result.scores:
        value = f"{sc.score}/10" if sc.assessed else "not assessed"
        parts.append(f"<h3>{_e(sc.criterion.title)} — {value}</h3>")
        body = sc.verdict or sc.basis
        if not sc.assessed:
            body = f"This criterion could not be assessed: {sc.error}"
        if body:
            parts.append(f"<p>{_e(body)}</p>")
        if sc.strengths:
            parts.append(
                "<ul>" + "".join(f"<li>{_e(i)}</li>" for i in sc.strengths) + "</ul>"
            )
        if sc.weaknesses:
            parts.append(
                "<ul>" + "".join(f"<li>{_e(i)}</li>" for i in sc.weaknesses) + "</ul>"
            )
        for ev in sc.evidence:
            pos = f"card #{ev['position']}" if ev.get("position") else "from the measurements"
            parts.append(
                f"<blockquote>{_e(_clean_quote(ev.get('quote')))}"
                f"<span>{_e(pos)} — {_e(ev.get('shows'))}</span></blockquote>"
            )

    parts.append("<h2>Other helpful features</h2>")
    parts.append(
        '<p class="note">The article lists these as convenient but keeps them out '
        "of the weighted score.</p><ul>"
        + "".join(
            f"<li><strong>{'Yes' if present else 'No'}</strong> — {_e(label)} "
            f'<span class="note">({_e(detail)})</span></li>'
            for label, present, detail in result.extras
        )
        + "</ul>"
    )

    if metrics.buckets:
        parts.append("<h2>Progression curve</h2>")
        parts.append(
            '<p class="note">The deck split into ten equal parts in study order. '
            "The new-word rate is the share of words in each part that had not "
            "appeared earlier; a deck that builds properly trends downward.</p>"
        )
        parts.append(f"<pre>{_e(curve_table(metrics))}</pre>")

    if metrics.frequency.available:
        parts.append("<h2>Vocabulary against a frequency list</h2>")
        parts.append(
            '<p class="note">One general-purpose list per language, the same for '
            "every deck, so the vocabulary criterion rests on ranks rather than on "
            "the model's sense of which words are common.</p>"
        )
        parts.append(f"<pre>{_e(fq.table(metrics.frequency))}</pre>")

    caveats = [
        "Audio and images are scored on whether they are present. This tool reads "
        "the cards; it cannot listen to a recording or look at a picture.",
        f"The other seven criteria are judged by {_e(model)} from a sample: the "
        "opening, three runs from the middle, the closing, and a random spread. "
        "They are one reading, not a measurement.",
        "The weighting reflects one approach to language learning. It is a "
        "consistent way to compare decks, not a validated formula.",
    ]
    caveats.append(_e(metrics.lexicon.describe()))
    if not metrics.frequency.available:
        caveats.append(_e(f"No frequency list was used: {metrics.frequency.reason}."))
    caveats += [_e(w) for w in list(deck.warnings) + list(metrics.warnings)]
    if result.unassessed:
        caveats.append(
            "Not assessed, and left out of the weighting: "
            + _e(", ".join(result.unassessed))
        )
    if provenance is not None:
        partial = (
            '<p class="note"><strong>Partial run.</strong> Only some criteria were '
            "judged and the total is rescaled over those, so it is not comparable "
            "with a full run.</p>"
            if not provenance.complete
            else ""
        )
        repeated = (
            f"<li>Each criterion was judged {provenance.repeat} times; the figure "
            "shown is the median and the range appears beside it.</li>"
            if provenance.repeat > 1
            else ""
        )
        parts.append(
            "<h2>Reproducing this</h2>"
            + partial
            + "<pre>"
            + _e(provenance.command(os.path.basename(deck.path)))
            + "</pre><ul>"
            + f"<li>analyzer {_e(provenance.version)}, model "
            f"{_e(provenance.model)}, effort {_e(provenance.effort)}</li>"
            + f"<li>sample seed {provenance.sample_seed} — the same cards are read "
            "on every run</li>"
            + f"<li>words counted: {_e(provenance.word_counting or 'surface')}; "
            f"frequency list: {_e(provenance.frequency_list or 'none')}</li>"
            + f'<li>deck file fingerprint (SHA-256) <code title="{_e(provenance.deck_sha256)}">'
            f"{_e(provenance.deck_sha256[:16])}…</code>: a code computed from the "
            "file's exact bytes. If the same calculation on your copy gives the "
            "same code (<code>Get-FileHash</code> on Windows, <code>shasum -a 256</code> "
            "on macOS), you have exactly the deck that was scored.</li>"
            + (repeated or "<li>Each criterion was judged once; that reading is the "
               "score shown. The judged criteria are a language model's reading, so "
               "running the same command again can move one of them by a point.</li>")
            + "</ul>"
        )

    parts.append(
        "<footer><h3>How to read this</h3><ul>"
        + "".join(f'<li class="note">{c}</li>' for c in caveats)
        + "</ul><p class=\"note\">Scored with the Anki Deck Analyzer, against the "
        "framework in “How to choose an Anki deck you’ll actually use”.</p></footer>"
    )

    return (
        f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_e(deck.name)} — deck analysis</title>"
        f"<style>{_CSS}</style></head><body><div class=\"wrap\">"
        + "\n".join(parts)
        + "</div></body></html>"
    )


# --------------------------------------------------------------------------
# json
# --------------------------------------------------------------------------


def render_json(
    deck: Deck,
    profile: DeckProfile,
    metrics: Metrics,
    result: Result,
    model: str,
    cost: float | None,
    provenance=None,
) -> str:
    payload = {
        "deck": {
            "name": deck.name,
            "file": os.path.basename(deck.path),
            "notes": metrics.note_count,
            "cards": metrics.card_count,
            "note_types": metrics.notetype_counts,
            "subdecks": metrics.deck_tree,
            "order_basis": deck.order_basis,
            "schema_version": deck.schema_version,
        },
        "profile": {
            "target_language": profile.target_language,
            "source_language": profile.source_language,
            "deck_kind": profile.deck_kind,
            "level_range": profile.level_range,
        },
        "score": {
            "total": round(result.total, 1),
            "grade": result.grade,
            "fundamentals": round(result.fundamentals_total, 1),
            "nice_to_haves": round(result.nice_to_have_total, 1),
            "unassessed": result.unassessed,
        },
        "criteria": [
            {
                "key": sc.criterion.key,
                "title": sc.criterion.title,
                "weight": sc.criterion.weight,
                "group": sc.criterion.group,
                "assessed": sc.assessed,
                "score": sc.score if sc.assessed else None,
                "confidence": sc.confidence,
                "verdict": sc.verdict or sc.basis,
                "strengths": sc.strengths,
                "weaknesses": sc.weaknesses,
                "evidence": sc.evidence,
                "measured_not_judged": sc.criterion.measured,
                "scores_seen": sc.scores_seen,
                "spread": sc.spread,
                "error": sc.error,
            }
            for sc in result.scores
        ],
        "summary": getattr(result, "summary", None) or {},
        "measurements": {
            "audio_coverage": round(metrics.audio_coverage, 4),
            "image_coverage": round(metrics.image_coverage, 4),
            "distinct_audio_files": metrics.distinct_audio_files,
            "distinct_image_files": metrics.distinct_image_files,
            "vocabulary_size": metrics.total_vocabulary,
            "mean_words_per_note": round(metrics.mean_words_per_note, 2),
            "type_token_ratio": round(metrics.type_token_ratio, 4),
            "duplicate_note_share": round(metrics.duplicate_note_share, 4),
            "empty_field_share": round(metrics.empty_field_share, 4),
            "new_word_rate_overall": round(metrics.new_word_rate_overall, 4),
            "new_word_rate_trend": round(metrics.new_word_rate_trend, 4),
            "length_growth": round(metrics.length_growth, 3),
            "buckets": [asdict(b) for b in metrics.buckets],
            "word_counting": metrics.lexicon.describe(),
            "names_left_out": sorted(metrics.lexicon.names),
            "frequency": asdict(metrics.frequency),
        },
        "extras": [
            {"feature": label, "present": present, "detail": detail}
            for label, present, detail in result.extras
        ],
        "run": {
            "model": model,
            "date": date.today().isoformat(),
            "api_cost_usd": round(cost, 4) if cost is not None else None,
            "warnings": list(deck.warnings) + list(metrics.warnings),
        },
        "provenance": (
            {
                "analyzer_version": provenance.version,
                "model": provenance.model,
                "effort": provenance.effort,
                "repeat": provenance.repeat,
                "sample_seed": provenance.sample_seed,
                "deck_sha256": provenance.deck_sha256,
                "criteria_judged": provenance.criteria_judged,
                "complete_run": provenance.complete,
                "word_counting": provenance.word_counting,
                "frequency_list": provenance.frequency_list,
                "command": provenance.command(os.path.basename(deck.path)),
            }
            if provenance is not None
            else None
        ),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)
