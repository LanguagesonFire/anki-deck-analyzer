"""Command line entry point."""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__, addons
from .analyze import analyze, preview
from .apkg import DeckReadError
from .criteria import CRITERIA
from .llm import DEFAULT_EFFORT, DEFAULT_MODEL, PUBLISH_MODEL, Analyzer
from .metrics import curve_table
from .report import render_html, render_json, render_markdown, render_terminal

EPILOG = """\
examples:
  anki-deck-analyzer deck.apkg
  anki-deck-analyzer deck.apkg -o report.html
  anki-deck-analyzer deck.apkg --model claude-opus-5-5
  anki-deck-analyzer deck.apkg --dry-run      (no API call, no cost)

The analyzer needs an Anthropic API key. Either set ANTHROPIC_API_KEY in your
environment or pass --api-key. Get one at https://console.anthropic.com.
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="anki-deck-analyzer",
        description=(
            "Score an Anki deck against the nine criteria from 'How to choose an "
            "Anki deck you'll actually use'."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("deck", nargs="+", help="one or more .apkg or .colpkg files")
    p.add_argument(
        "-o",
        "--out",
        metavar="PATH",
        help=(
            "where to write the report. The format follows the extension: "
            ".html, .md or .json. With several decks, this is treated as a "
            'directory. Default: "<deck name> - report.html" in the current folder'
        ),
    )
    p.add_argument(
        "--no-report",
        action="store_true",
        help="print the result in the terminal only, without writing a report file",
    )
    p.add_argument(
        "--model",
        default=os.environ.get("ANKI_ANALYZER_MODEL", DEFAULT_MODEL),
        help=f"Claude model to judge with (default: {DEFAULT_MODEL})",
    )
    p.add_argument(
        "--effort",
        default=DEFAULT_EFFORT,
        choices=["low", "medium", "high", "xhigh", "max"],
        help=f"how hard the model thinks about each criterion (default: {DEFAULT_EFFORT})",
    )
    p.add_argument(
        "--repeat",
        type=int,
        default=1,
        metavar="N",
        help=(
            "judge each criterion N times instead of once and report the median "
            "and its range. Use an odd number. Default 1: every score is one pass"
        ),
    )
    p.add_argument(
        "--only",
        metavar="KEYS",
        help=(
            "judge only these criteria, comma separated, e.g. "
            "progression,accuracy. The score is rescaled over what ran and the "
            "report says so. For tuning one rubric without paying for nine"
        ),
    )
    p.add_argument(
        "--draft",
        action="store_true",
        help="cheapest settings, for a first look: low effort, no summary",
    )
    # Kept so the reproduce commands printed by reports before 1.7 still run:
    # it is the same as --model claude-opus-5-5 --effort high.
    p.add_argument("--publish", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--api-key", help="Anthropic API key (default: $ANTHROPIC_API_KEY)")
    p.add_argument(
        "--workers",
        type=int,
        default=4,
        help="how many criteria to judge at once (default: 4)",
    )
    p.add_argument(
        "--no-summary",
        action="store_true",
        help="skip the closing verdict, saving one API call",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "read and measure the deck and print exactly what would be sent to "
            "the API, without sending it or spending anything"
        ),
    )
    p.add_argument(
        "--language",
        metavar="NAME",
        help=(
            "with --dry-run: the deck's target language, e.g. French, so words are "
            "counted by dictionary form and held against a frequency list. A real "
            "run identifies the language itself"
        ),
    )
    p.add_argument(
        "--explanation-language",
        metavar="NAME",
        default="English",
        help=(
            "with --dry-run: the language of the deck's translations and "
            "explanations (default English), e.g. Vietnamese for a Chinese deck "
            "glossed in Vietnamese. A real run identifies it itself"
        ),
    )
    p.add_argument(
        "--fields",
        metavar="MAP",
        help=(
            "with --dry-run: which field holds the target language, per note type, "
            'e.g. "Sentence=Front,Vocab=Word". Without it a dry run measures each '
            "note's longest field, which is often the translation"
        ),
    )
    p.add_argument(
        "--explanation-types",
        metavar="NAMES",
        help=(
            "with --dry-run: note types that explain rather than teach (grammar "
            "rules, announcements), comma separated. Measured apart from the material"
        ),
    )
    p.add_argument(
        "--lemma-fields",
        metavar="MAP",
        help=(
            "with --dry-run: fields stating the dictionary form of a word the card "
            'teaches, e.g. "Sentence=Verb Infinitive|Verb Infinitive2". Their '
            "values count as taught in the frequency comparison"
        ),
    )
    p.add_argument(
        "--frequency-list",
        metavar="LIST",
        default=None,
        help=(
            "the list vocabulary is measured against. The default is the "
            "language's own: Lexique 3.83 for French, wordfreq (cleaned where "
            "the language has a patch) for the rest. Others: wordfreq, "
            "wordfreq-raw (as it comes, to reproduce older reports), lexique, a "
            "Lexique 3 .tsv for French, or a .json list of headwords in rank order. "
            "Any list but the default is named in the report and the reproduce line"
        ),
    )
    p.add_argument(
        "--description",
        metavar="TEXT",
        default="",
        help=(
            "the deck's description from its shared-deck page, or a path to a text "
            "file holding it. The .apkg file usually carries none, and the page "
            "is often where the author says how to study the deck"
        ),
    )
    p.add_argument("-q", "--quiet", action="store_true", help="no progress output")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _write(path: str, content: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _output_path(out: str | None, deck_path: str, several: bool) -> str:
    stem = os.path.splitext(os.path.basename(deck_path))[0]
    if not out:
        return os.path.join(os.getcwd(), f"{stem} - report.html")
    if not several:
        return out
    ext = os.path.splitext(out)[1] or ".html"
    return os.path.join(out, f"{stem}{ext}")


def main(argv: list[str] | None = None) -> int:
    # Anki decks are full of characters cp1252 cannot encode.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover
            pass

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.draft and args.publish:
        parser.error("--draft and --publish ask for opposite things; pick one.")
    # The presets only fill in what the user did not set explicitly, so
    # `--draft --effort medium` does what it looks like.
    explicit = set(argv if argv is not None else sys.argv[1:])
    if args.draft:
        if not any(a.startswith("--model") for a in explicit):
            args.model = DEFAULT_MODEL
        if not any(a.startswith("--effort") for a in explicit):
            args.effort = "low"
        args.no_summary = True
    if args.publish:
        if not any(a.startswith("--model") for a in explicit):
            args.model = PUBLISH_MODEL
        if not any(a.startswith("--effort") for a in explicit):
            args.effort = "high"

    only: set[str] | None = None
    if args.only:
        keys = {k.strip() for k in args.only.split(",") if k.strip()}
        known = {c.key for c in CRITERIA if not c.measured}
        unknown = keys - known
        if unknown:
            parser.error(
                "unknown criteria: "
                + ", ".join(sorted(unknown))
                + ". Choose from: "
                + ", ".join(sorted(known))
            )
        only = keys

    several = len(args.deck) > 1

    def status(message: str) -> None:
        if not args.quiet:
            print(f"  … {message}", file=sys.stderr, flush=True)

    if args.dry_run:
        addons.offer(args.language, quiet=args.quiet)
        for path in args.deck:
            try:
                deck, metrics, pack = preview(
                    path,
                    args.language,
                    _field_map(args.fields),
                    args.frequency_list,
                    [n.strip() for n in (args.explanation_types or "").split(",") if n.strip()],
                    {k: v.split("|") for k, v in (_field_map(args.lemma_fields) or {}).items()},
                    explanation_language=args.explanation_language,
                    description=_description(args.description),
                )
            except (DeckReadError, ValueError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            print(f"{deck.name} — {len(deck.notes):,} notes, "
                  f"{deck.card_count:,} cards, read in {deck.order_basis}")
            print()
            print(curve_table(metrics))
            print()
            print("The text below is what would be sent to the Anthropic API.")
            print("=" * 72)
            print(pack)
        return 0

    try:
        analyzer = Analyzer(
            client=_client(args.api_key),
            model=args.model,
            effort=args.effort,
        )
    except Exception as exc:
        _explain_missing_key(exc)
        return 2

    failures = 0
    for path in args.deck:
        try:
            analysis = analyze(
                path,
                analyzer,
                summarize=not args.no_summary,
                workers=max(1, args.workers),
                progress=status,
                only=only,
                repeat=max(1, args.repeat),
                frequency_list=args.frequency_list,
                description=_description(args.description),
                on_language=lambda name: addons.offer(name, quiet=args.quiet),
            )
        except DeckReadError as exc:
            print(f"error: {os.path.basename(path)}: {exc}", file=sys.stderr)
            failures += 1
            continue
        except Exception as exc:
            # The SDK resolves credentials lazily, so a missing key surfaces
            # here rather than when the client is built. Say the useful thing.
            if _is_auth_failure(exc):
                _explain_missing_key(exc)
                return 2
            hint = _api_hint(exc)
            print(f"error: {os.path.basename(path)}: {exc}", file=sys.stderr)
            if hint:
                print(f"\n{hint}\n", file=sys.stderr)
                if hint.startswith(_STOP):
                    return 2
            failures += 1
            continue

        print(
            render_terminal(
                analysis.deck,
                analysis.profile,
                analysis.metrics,
                analysis.result,
                analysis.cost,
                analysis.model,
                provenance=analysis.provenance,
                study_path=analysis.study_path,
            )
        )

        if not args.no_report:
            target = _output_path(args.out, path, several)
            ext = os.path.splitext(target)[1].lower()
            if ext == ".json":
                body = render_json(
                    analysis.deck,
                    analysis.profile,
                    analysis.metrics,
                    analysis.result,
                    analysis.model,
                    analysis.cost,
                    analysis.provenance,
                    study_path=analysis.study_path,
                    level_tags=analysis.level_tags,
                )
            elif ext in (".md", ".markdown", ".txt"):
                body = render_markdown(
                    analysis.deck,
                    analysis.profile,
                    analysis.metrics,
                    analysis.result,
                    analysis.model,
                    study_path=analysis.study_path,
                )
            else:
                body = render_html(
                    analysis.deck,
                    analysis.profile,
                    analysis.metrics,
                    analysis.result,
                    analysis.model,
                    analysis.provenance,
                    study_path=analysis.study_path,
                )
            try:
                _write(target, body)
            except OSError as exc:
                # The analysis is done and already on screen; a bad output path
                # should not throw it away.
                print(f"error: could not write {target}: {exc}", file=sys.stderr)
                failures += 1
            else:
                print(f"  Report written to {target}", file=sys.stderr)

    return 1 if failures else 0


def _field_map(spec: str | None) -> dict[str, str] | None:
    if not spec:
        return None
    out: dict[str, str] = {}
    for part in spec.split(","):
        name, sep, field = part.partition("=")
        if sep and name.strip() and field.strip():
            out[name.strip()] = field.strip()
    return out


def _is_auth_failure(exc: Exception) -> bool:
    import anthropic

    if isinstance(exc, anthropic.AuthenticationError):
        return True
    text = str(exc).lower()
    return "authentication" in text or "api_key" in text or "api key" in text


_KEY_FILE_NOTE = (
    "If you started the tool with analyze-deck.bat or analyze-deck.command, "
    "delete api-key.txt in its folder and run it again to enter a new key."
)

_SET_KEY = """  Windows PowerShell:  $env:ANTHROPIC_API_KEY = "sk-ant-..."
  macOS / Linux:       export ANTHROPIC_API_KEY="sk-ant-..."
  or pass it once:     anki-deck-analyzer deck.apkg --api-key sk-ant-..."""

#: Hints that start with this stop the run: the next deck would fail the same way.
_STOP = "Nothing more will be analyzed"


def _explain_missing_key(exc: Exception) -> None:
    import anthropic

    rejected = isinstance(exc, anthropic.AuthenticationError)
    if rejected:
        print(
            "error: Anthropic rejected the API key. It may be mistyped, "
            "incomplete, or revoked.\n\n"
            "Copy the key again from https://console.anthropic.com/settings/keys "
            "(it starts with sk-ant-) and set it:\n\n"
            f"{_SET_KEY}\n\n{_KEY_FILE_NOTE}",
            file=sys.stderr,
        )
        return
    print(
        f"""error: no Anthropic API key was found.

This tool reads the deck on your own computer, but the assessment itself is
done by Claude, so it needs an Anthropic API key.

  1. Create an account at https://console.anthropic.com and add credit under
     Billing (the API is prepaid; the smallest top-up covers many decks).
  2. Create a key under API keys. It starts with sk-ant-.
  3. Set it:

{_SET_KEY}

You pay Anthropic directly for what you use, billed to your own account:
around $0.45 to analyze one deck. Nothing is charged by this tool.

To see everything that can be measured without an API key, and exactly what
would be sent if you had one, run the same command with --dry-run.""",
        file=sys.stderr,
    )


def _api_hint(exc: Exception) -> str:
    """A plain next step for the API errors a first run meets, else ""."""
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        return ""
    text = str(exc).lower()
    if "credit balance" in text or "billing" in text:
        return (
            f"{_STOP}: the Anthropic account has no credit left. Add some under "
            "Billing at https://console.anthropic.com/settings/billing and run "
            "the same command again."
        )
    if isinstance(exc, anthropic.PermissionDeniedError):
        return (
            f"{_STOP}: the key is valid but not allowed to use this model. Check "
            "the key's workspace at https://console.anthropic.com, or choose "
            "another model with --model."
        )
    if isinstance(exc, anthropic.NotFoundError) and "model" in text:
        return f"{_STOP}: that model name was not found. Leave out --model to use the default."
    if isinstance(exc, anthropic.APIConnectionError):
        return (
            f"{_STOP}: Anthropic could not be reached. Check the internet "
            "connection (or a proxy or firewall) and run the same command again."
        )
    if isinstance(exc, anthropic.RateLimitError):
        return (
            "Anthropic is limiting how fast this key can send requests. Wait a "
            "minute and run it again, or use --workers 1 to send them one at a time."
        )
    if isinstance(exc, anthropic.APIStatusError) and getattr(exc, "status_code", 0) >= 500:
        return (
            "Anthropic's service is busy or had an error. Nothing was wrong with "
            "the deck; try again in a few minutes."
        )
    return ""


def _client(api_key: str | None):
    import anthropic

    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


def _description(value: str) -> str:
    """--description as given, or the contents of the file it names."""
    if value and os.path.isfile(value):
        with open(value, encoding="utf-8") as handle:
            return handle.read().strip()
    return (value or "").strip()
