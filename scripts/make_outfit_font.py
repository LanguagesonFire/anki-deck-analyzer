"""Build src/anki_deck_analyzer/data/Outfit-latin.woff2, the report's font.

Reports are files people open offline and pass around, so the font is embedded
in each report instead of loaded from Google Fonts: no request to a third party
when a report is opened, and the same look with no connection.

The source is the variable Outfit from Google Fonts (SIL OFL 1.1, license in
data/LICENSE-Outfit.txt). This keeps the weight axis but limits it to 400-700,
the weights the report uses, and keeps only Latin glyphs plus the punctuation
and symbols the report prints. Text in other scripts (Cyrillic, Greek, CJK...)
falls back to the system font glyph by glyph, which is what the browser does
for any character a web font lacks.

Usage:
    python scripts/make_outfit_font.py path/to/Outfit[wght].ttf

Needs fonttools and brotli (maintainer-only; not a dependency of the tool).
"""

from __future__ import annotations

import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "anki_deck_analyzer" / "data" / "Outfit-latin.woff2"

#: Basic Latin, Latin-1, Latin Extended-A, general punctuation (dashes,
#: curly quotes, ellipsis), euro sign, arrows (→ in reports), minus, ≤ ≥.
UNICODES = (
    list(range(0x20, 0x7F)) + list(range(0xA0, 0x180))
    + list(range(0x2000, 0x2070)) + [0x20AC]
    + list(range(0x2190, 0x2200)) + [0x2212, 0x2264, 0x2265]
)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    font = TTFont(sys.argv[1])
    font = instancer.instantiateVariableFont(font, {"wght": (400, 700)})
    opts = subset.Options()
    opts.flavor = "woff2"
    opts.layout_features = ["*"]
    opts.name_IDs = ["*"]
    opts.notdef_outline = True
    sub = subset.Subsetter(opts)
    sub.populate(unicodes=UNICODES)
    sub.subset(font)
    font.flavor = "woff2"
    font.save(OUT)
    print(f"{OUT.relative_to(ROOT)}: {OUT.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
