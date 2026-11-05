"""Build the shipped French list from Lexique 3.83.

Lexique383.tsv is 26 MB, of which the analyzer needs each lemma's rank and
its spellings. This writes those, in rank order, to
src/anki_deck_analyzer/data/lexique383_lemmas.json, through the analyzer's
own reader (`frequency.read_lexique`), so the shipped list and
`--frequency-list Lexique383.tsv` give the same report.

Source: http://www.lexique.org/databases/Lexique383/Lexique383.zip
License: CC BY-SA 4.0 (New, Pallier et al.); the derived file is under the
same license, see data/LICENSE-Lexique.txt.

Usage:
    python scripts/build_lexique_list.py path/to/Lexique383.tsv
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from anki_deck_analyzer.frequency import LEXIQUE_DATA, read_lexique  # noqa: E402


def main(tsv: str) -> None:
    ranked = read_lexique(tsv)
    data = {
        "source": "Lexique 3.83 (New, Pallier et al.), http://www.lexique.org",
        "license": "CC BY-SA 4.0, https://creativecommons.org/licenses/by-sa/4.0/",
        "derived": "lemmas ranked by the mean of freqfilms2 and freqlivres, each "
        "summed over the lemma's forms; spellings from the ortho column",
        "version": "3.83",
        "tsv_md5": hashlib.md5(Path(tsv).read_bytes()).hexdigest(),
        "lemmas": ranked,
    }
    with open(LEXIQUE_DATA, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"{len(ranked):,} lemmas -> {LEXIQUE_DATA}")


if __name__ == "__main__":
    main(sys.argv[1])
