"""Word splitters that are installed the first time a deck needs one.

Korean, Chinese and Japanese can't be split into words at spaces, and each
needs its own library. Together they would more than double the install, and
most people never open a deck in those languages, so the tool asks when one
turns up. The answer is put to the person at the keyboard only: an automated
run is never held up and never installs anything by itself. A deck analyzed
without its splitter still gets a report, which says what it couldn't do.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from dataclasses import dataclass

from . import lexicon
from . import textutil as tx


@dataclass(frozen=True)
class Addon:
    language: str
    code: str
    tool: str
    #: Measured installed size, with what it pulls in.
    megabytes: int
    #: The same requirement as the extra of this name in pyproject.toml.
    extra: str
    requirement: str


ADDONS = {
    "ko": Addon("Korean", "ko", "Kiwi", 200, "korean", "kiwipiepy>=0.17"),
    "zh": Addon("Chinese", "zh", "jieba", 45, "chinese", "jieba>=0.42"),
    "ja": Addon("Japanese", "ja", "Janome", 270, "japanese", "janome>=0.5"),
}

#: One question per language per run, however many decks are in it.
_asked: set[str] = set()


def _present(code: str) -> bool:
    return tx.korean_morphemes() if code == "ko" else bool(tx.cjk_splitter(code))


def _forget(code: str) -> None:
    """Drop the remembered "not installed" so the new package is found."""
    importlib.invalidate_caches()
    tx._KO_STATE.clear()
    tx._SPLITTERS.pop(code, None)
    tx._analyzed.cache_clear()


def needed(language_name: str | None) -> Addon | None:
    """The add-on this language lacks, or None if it needs nothing more.

    Cantonese stays on character counting whatever is installed: jieba's
    dictionary is Mandarin (see lexicon._word_splitting).
    """
    if "cantonese" in (language_name or "").lower():
        return None
    addon = ADDONS.get(lexicon.language_code(language_name) or "")
    return None if addon is None or _present(addon.code) else addon


def install(addon: Addon) -> bool:
    """Install into the Python this tool runs in. True once it can be used."""
    try:
        done = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet",
             "--disable-pip-version-check", addon.requirement],
            check=False,
        )
    except OSError:
        return False
    _forget(addon.code)
    return done.returncode == 0 and _present(addon.code)


def offer(language_name: str | None, quiet: bool = False) -> bool:
    """Ask to install what this language needs. True if it was installed."""
    addon = needed(language_name)
    if addon is None or addon.code in _asked:
        return False
    _asked.add(addon.code)
    manual = f'pip install "{addon.requirement}"'
    if quiet:
        return False
    if not (sys.stdin and sys.stdin.isatty()):
        print(
            f"  {addon.language} needs {addon.tool} to count words accurately; "
            f"carrying on without it. To add it: {manual}",
            file=sys.stderr,
        )
        return False
    print(
        f"\nThis deck is {addon.language}. Counting its words needs {addon.tool}, an "
        f"add-on of about {addon.megabytes} MB\nthat is installed once. Without it the "
        "report has no word-frequency table and counts\nnew words less accurately.",
        file=sys.stderr,
    )
    try:
        print("Install it now? [Y/n] ", end="", file=sys.stderr, flush=True)
        answer = input().strip().lower()
    except EOFError:
        answer = "n"
    if answer not in ("", "y", "yes"):
        print("Carrying on without it. The report will say so.\n", file=sys.stderr)
        return False
    print(f"Installing {addon.tool}. This takes a minute or two.", file=sys.stderr, flush=True)
    if install(addon):
        print("Done.\n", file=sys.stderr)
        return True
    print(
        f"{addon.tool} could not be installed, so this report will go without it. "
        f"To try again: {manual}\n",
        file=sys.stderr,
    )
    return False
