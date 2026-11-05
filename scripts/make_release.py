"""Build the download: dist/anki-deck-analyzer-<version>.zip.

Only the files a user needs go in, from an explicit list, so working files in
this folder (reports, decks, notes, scratch) can never ship by accident.

Two details matter for the launchers:
  - analyze-deck.command is stored as a Unix file with mode 755. A zip made on
    Windows marks entries as MS-DOS files, and macOS then unpacks the launcher
    without its executable bit: double-clicking it fails with "you do not have
    appropriate access privileges".
  - analyze-deck.bat gets CRLF line endings and the .command LF. cmd can
    misjump to a label in a batch file with LF endings; bash cannot read CR.

Usage:
    python scripts/make_release.py
"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Top-level files, then folders shipped whole (minus caches).
FILES = ["README.md", "CHANGELOG.md", "LICENSE", "pyproject.toml",
         "analyze-deck.bat", "analyze-deck.command"]
FOLDERS = ["src", "tests"]
#: Maintainer tools: rebuild the shipped word-list patches and French list.
SCRIPTS = ["scripts/clean_wordfreq.py", "scripts/build_lexique_list.py",
           "scripts/make_release.py"]
SKIP_PARTS = {"__pycache__", ".pytest_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'^version = "([^"]+)"', text, re.M).group(1)


def files() -> list[Path]:
    out = [ROOT / f for f in FILES + SCRIPTS if (ROOT / f).is_file()]
    missing = [f for f in FILES + SCRIPTS if not (ROOT / f).is_file()]
    if missing:
        sys.exit(f"missing: {', '.join(missing)}")
    for folder in FOLDERS:
        for p in sorted((ROOT / folder).rglob("*")):
            if p.is_file() and not (SKIP_PARTS & set(p.parts)) and p.suffix not in SKIP_SUFFIXES:
                out.append(p)
    return out


def main() -> None:
    ver = version()
    base = f"anki-deck-analyzer-{ver}"
    target = ROOT / "dist" / f"{base}.zip"
    target.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files():
            rel = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            mode = 0o644
            if path.name == "analyze-deck.bat":
                data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            elif path.name == "analyze-deck.command":
                data = data.replace(b"\r\n", b"\n")
                mode = 0o755
            info = zipfile.ZipInfo(f"{base}/{rel}", date_time=(2026, 1, 1, 0, 0, 0))
            info.create_system = 3  # Unix, so macOS honors the mode bits
            info.external_attr = (0o100000 | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    with zipfile.ZipFile(target) as zf:
        names = zf.namelist()
    print(f"{target.relative_to(ROOT)}: {len(names)} files, {target.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
