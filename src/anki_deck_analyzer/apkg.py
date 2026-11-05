"""Read an Anki .apkg / .colpkg file without Anki installed.

Handles all three collection layouts that exist in the wild:

  collection.anki2    schema 11  - plain SQLite, note types as JSON in `col.models`
  collection.anki21   schema 11+ - same, written alongside a legacy stub
  collection.anki21b  schema 18  - zstd-compressed SQLite, note types in real
                                   tables with protobuf-encoded config blobs

The protobuf blobs are read with a ~40-line wire-format scanner rather than a
generated stub, so the package has no protobuf dependency. Only three fields
are needed and their numbers are stable in Anki's schema:

  Notetype.Config          field 1 = kind (0 normal, 1 cloze), field 3 = css
  Notetype.Template.Config field 1 = question format, field 2 = answer format
"""

from __future__ import annotations

import hashlib
import html as htmllib
import io
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass, field
from typing import Any

from .textutil import block_classes, mark_inline_blocks

# Anki joins subdeck components with this character in schema 18; older
# collections store the "::" form directly.
DECK_SEP = "\x1f"


class DeckReadError(Exception):
    """The file could not be read as an Anki package."""


# --------------------------------------------------------------------------
# minimal protobuf wire-format reader
# --------------------------------------------------------------------------


def _varint(buf: bytes, i: int) -> tuple[int | None, int]:
    result = 0
    shift = 0
    while i < len(buf):
        byte = buf[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, i
        shift += 7
        if shift > 63:
            return None, i
    return None, i


def _pb_fields(buf: bytes) -> dict[int, list[Any]]:
    """Scan a protobuf message into {field_number: [values]}."""
    out: dict[int, list[Any]] = {}
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        if key is None:
            break
        num, wire = key >> 3, key & 7
        if wire == 0:
            val, i = _varint(buf, i)
            if val is None:
                break
        elif wire == 1:
            val, i = buf[i : i + 8], i + 8
        elif wire == 2:
            length, i = _varint(buf, i)
            if length is None:
                break
            val, i = buf[i : i + length], i + length
        elif wire == 5:
            val, i = buf[i : i + 4], i + 4
        else:  # groups (3, 4) are not used by Anki
            break
        out.setdefault(num, []).append(val)
    return out


def _pb_str(fields: dict[int, list[Any]], num: int) -> str:
    vals = fields.get(num)
    if not vals or not isinstance(vals[0], bytes):
        return ""
    return vals[0].decode("utf-8", "replace")


def _pb_int(fields: dict[int, list[Any]], num: int, default: int = 0) -> int:
    vals = fields.get(num)
    if not vals or not isinstance(vals[0], int):
        return default
    return vals[0]


# --------------------------------------------------------------------------
# data model
# --------------------------------------------------------------------------


@dataclass
class Template:
    name: str
    qfmt: str
    afmt: str


@dataclass
class NoteType:
    id: int
    name: str
    fields: list[str]
    templates: list[Template]
    css: str
    is_cloze: bool
    sort_field: int = 0


@dataclass
class Note:
    id: int
    notetype_id: int
    fields: list[str]
    tags: list[str]
    #: Study position: the `due` value of this note's first new card, else None.
    position: int | None = None
    #: Full name of the deck this note's first card lives in.
    deck: str = ""


@dataclass
class Deck:
    name: str
    path: str
    notes: list[Note]
    notetypes: dict[int, NoteType]
    deck_names: list[str] = field(default_factory=list)
    card_count: int = 0
    new_card_count: int = 0
    media_names: list[str] = field(default_factory=list)
    schema_version: int = 0
    #: SHA-256 of the package file. Published alongside a score so a reader can
    #: check they are scoring the same bytes.
    file_sha256: str = ""
    #: How `notes` was ordered: "study order" or "creation order".
    order_basis: str = "creation order"
    #: Non-fatal problems encountered while reading.
    warnings: list[str] = field(default_factory=list)
    #: The description the author gave the deck in Anki, as plain text. Shared
    #: decks often say only "see the shared deck page"; the page itself is not
    #: in the file.
    description: str = ""

    def notetype_of(self, note: Note) -> NoteType | None:
        return self.notetypes.get(note.notetype_id)


# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------


def _extract_collection(zf: zipfile.ZipFile, workdir: str) -> tuple[str, list[str]]:
    """Write the collection database to disk and return (path, warnings)."""
    warnings: list[str] = []
    names = set(zf.namelist())
    dest = os.path.join(workdir, "collection.sqlite")

    if "collection.anki21b" in names:
        try:
            import zstandard
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise DeckReadError(
                "This package uses the newer compressed Anki format and needs the "
                "'zstandard' library. Install it with: pip install zstandard"
            ) from exc
        raw = zf.read("collection.anki21b")
        reader = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(raw))
        with open(dest, "wb") as fh:
            while True:
                chunk = reader.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
        return dest, warnings

    for candidate in ("collection.anki21", "collection.anki2"):
        if candidate in names:
            with open(dest, "wb") as fh:
                fh.write(zf.read(candidate))
            return dest, warnings

    raise DeckReadError(
        "No Anki collection found inside the package. Expected one of "
        "collection.anki21b, collection.anki21 or collection.anki2."
    )


def _read_media_names(zf: zipfile.ZipFile) -> list[str]:
    """Best-effort list of media filenames. Absent in some exports."""
    if "media" not in zf.namelist():
        return []
    raw = zf.read("media")
    try:
        return list(json.loads(raw.decode("utf-8")).values())
    except Exception:
        pass
    # Newer packages store the map as zstd-compressed protobuf. The filenames
    # are the only length-delimited strings in it, so a scan is enough.
    try:
        import zstandard

        raw = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(raw)).read()
    except Exception:
        pass
    names: list[str] = []
    for entries in _pb_fields(raw).values():
        for entry in entries:
            if not isinstance(entry, bytes):
                continue
            inner = _pb_fields(entry)
            name = _pb_str(inner, 1)
            if name and "." in name and "\x00" not in name:
                names.append(name)
    return names


def _notetypes_from_json(models_json: str) -> dict[int, NoteType]:
    out: dict[int, NoteType] = {}
    for raw in json.loads(models_json).values():
        nt_id = int(raw["id"])
        out[nt_id] = NoteType(
            id=nt_id,
            name=raw.get("name", ""),
            fields=[f.get("name", "") for f in raw.get("flds", [])],
            templates=[
                Template(t.get("name", ""), t.get("qfmt", ""), t.get("afmt", ""))
                for t in raw.get("tmpls", [])
            ],
            css=raw.get("css", ""),
            is_cloze=int(raw.get("type", 0)) == 1,
            sort_field=int(raw.get("sortf", 0)),
        )
    return out


def _notetypes_from_tables(con: sqlite3.Connection) -> dict[int, NoteType]:
    out: dict[int, NoteType] = {}
    for nt_id, name, config in con.execute("SELECT id, name, config FROM notetypes"):
        cfg = _pb_fields(config or b"")
        out[int(nt_id)] = NoteType(
            id=int(nt_id),
            name=name,
            fields=[],
            templates=[],
            css=_pb_str(cfg, 3),
            is_cloze=_pb_int(cfg, 1) == 1,
            sort_field=_pb_int(cfg, 2),
        )
    for nt_id, _ord, fname, _cfg in con.execute(
        "SELECT ntid, ord, name, config FROM fields ORDER BY ntid, ord"
    ):
        if int(nt_id) in out:
            out[int(nt_id)].fields.append(fname)
    for nt_id, _ord, tname, config in con.execute(
        "SELECT ntid, ord, name, config FROM templates ORDER BY ntid, ord"
    ):
        if int(nt_id) not in out:
            continue
        cfg = _pb_fields(config or b"")
        out[int(nt_id)].templates.append(
            Template(tname, _pb_str(cfg, 1), _pb_str(cfg, 2))
        )
    return out


def _deck_names(con: sqlite3.Connection, schema: int) -> dict[int, str]:
    if schema >= 18:
        try:
            rows = con.execute("SELECT id, name FROM decks").fetchall()
            return {int(i): n.replace(DECK_SEP, "::") for i, n in rows}
        except sqlite3.Error:
            pass
    try:
        raw = con.execute("SELECT decks FROM col").fetchone()[0]
        if raw:
            return {
                int(k): v.get("name", "").replace(DECK_SEP, "::")
                for k, v in json.loads(raw).items()
            }
    except Exception:
        pass
    return {}


#: Longest deck description passed on.
DESCRIPTION_LIMIT = 2000


def _deck_descriptions(con: sqlite3.Connection, schema: int) -> dict[str, str]:
    """Deck name -> its description, for collections that keep it as JSON.

    Schema 18 stores it inside a protobuf blob, which is not read here.
    """
    try:
        raw = con.execute("SELECT decks FROM col").fetchone()[0]
        if not raw:
            return {}
        return {
            v.get("name", "").replace(DECK_SEP, "::"): v.get("desc", "") or ""
            for v in json.loads(raw).values()
        }
    except Exception:
        return {}


def _plain(html_text: str) -> str:
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", html_text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = htmllib.unescape(text)
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def read_package(path: str) -> Deck:
    """Read an .apkg or .colpkg file into a :class:`Deck`."""
    if not os.path.exists(path):
        raise DeckReadError(f"File not found: {path}")
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise DeckReadError(
            f"{os.path.basename(path)} is not a valid Anki package "
            "(.apkg files are zip archives)."
        ) from exc

    warnings: list[str] = []
    with zf, tempfile.TemporaryDirectory() as workdir:
        db_path, extract_warnings = _extract_collection(zf, workdir)
        warnings += extract_warnings
        media_names = _read_media_names(zf)

        con = sqlite3.connect(db_path)
        con.text_factory = lambda b: b.decode("utf-8", "replace")
        try:
            schema = int(con.execute("SELECT ver FROM col").fetchone()[0])

            models_json = ""
            try:
                models_json = con.execute("SELECT models FROM col").fetchone()[0] or ""
            except sqlite3.Error:
                pass
            if models_json.strip():
                notetypes = _notetypes_from_json(models_json)
            else:
                try:
                    notetypes = _notetypes_from_tables(con)
                except sqlite3.Error as exc:
                    notetypes = {}
                    warnings.append(f"Could not read note types: {exc}")

            deck_names = _deck_names(con, schema)
            descriptions = _deck_descriptions(con, schema)

            # Card-level data: study position and home deck, per note.
            # A note with several cards is first met when its earliest new card
            # comes up, so its position is the lowest `due` among them, not the
            # `due` of whichever card the database happens to return first. A
            # reverse card can be scheduled thousands of positions before its
            # forward sibling.
            positions: dict[int, int] = {}
            note_deck: dict[int, str] = {}
            card_count = 0
            new_count = 0
            try:
                for nid, did, ctype, due in con.execute(
                    "SELECT nid, did, type, due FROM cards ORDER BY id"
                ):
                    card_count += 1
                    nid = int(nid)
                    if ctype == 0:
                        new_count += 1
                        due = int(due)
                        if nid not in positions or due < positions[nid]:
                            positions[nid] = due
                            note_deck[nid] = deck_names.get(int(did), "")
                    if nid not in note_deck:
                        note_deck[nid] = deck_names.get(int(did), "")
            except sqlite3.Error as exc:
                warnings.append(f"Could not read cards: {exc}")

            notes: list[Note] = []
            for nid, mid, flds, tags in con.execute(
                "SELECT id, mid, flds, tags FROM notes"
            ):
                nid = int(nid)
                notes.append(
                    Note(
                        id=nid,
                        notetype_id=int(mid),
                        fields=(flds or "").split("\x1f"),
                        tags=[t for t in (tags or "").split(" ") if t],
                        position=positions.get(nid),
                        deck=note_deck.get(nid, ""),
                    )
                )
        finally:
            con.close()

    if not notes:
        raise DeckReadError("The package contains no notes.")

    # Inline tags are stripped without a space, which is how they render,
    # unless the note type's stylesheet makes one a block of its own.
    blocks = {nt.id: block_classes(nt.css) for nt in notetypes.values()}
    for note in notes:
        classes = blocks.get(note.notetype_id, set())
        note.fields = [mark_inline_blocks(f, classes) for f in note.fields]

    # Prefer the order the deck is actually studied in. A shared deck that has
    # never been used has a `due` position on every card; a collection export
    # from someone mid-study may not, and creation order is the better guess.
    # A position orders the deck only if it separates the notes. Some exports
    # carry `due = 0` on every new card: the field is fully populated and says
    # nothing, and the sort below falls through to note id, which *is* creation
    # order. Report that rather than claim a study order the file never had.
    # Partial ties are different — they still group the deck, and (position, id)
    # beats id alone — so those keep the study order and get a caveat instead.
    #
    # Anki gathers new cards subdeck by subdeck, in the order of the deck list,
    # unless the learner changes the gather order. A deck whose "01 Core 1-500"
    # subdeck was filled last is still studied from it first, so the subdeck
    # comes before the position.
    positions = [n.position for n in notes if n.position is not None]
    distinct = len(set(positions))
    if len(positions) >= 0.6 * len(notes) and distinct > 1:
        notes.sort(
            key=lambda n: (
                _deck_order(n.deck),
                n.position if n.position is not None else 1 << 40,
                n.id,
            )
        )
        order_basis = "study order"
        if distinct < 0.5 * len(positions):
            warnings.append(
                f"{len(positions):,} cards share only {distinct:,} distinct "
                "new-card positions, so the deck is ordered in groups and notes "
                "within a group are read in creation order."
            )
    else:
        notes.sort(key=lambda n: (_deck_order(n.deck), n.id))
        order_basis = "creation order"
        within = (
            "subdeck by subdeck, in the order Anki studies them, and in creation "
            "order within each"
            if len({n.deck for n in notes}) > 1
            else "in note creation order"
        )
        if positions and distinct <= 1:
            warnings.append(
                "Every card in this package carries the same new-card position, "
                "so the deck's intended order is not recoverable from the file. "
                f"Progression was measured {within} instead."
            )
        elif positions:
            warnings.append(
                "Most cards have already been studied, so the deck's intended new-card "
                f"order is no longer recoverable. Progression was measured {within} "
                "instead."
            )

    names = sorted({n.deck for n in notes if n.deck})
    seen_desc: list[str] = []
    for name in names:
        text = _plain(descriptions.get(name, ""))
        if text and text not in seen_desc:
            seen_desc.append(text)
    description = "\n\n".join(seen_desc)[:DESCRIPTION_LIMIT]
    top = _top_deck_name(names) or os.path.splitext(os.path.basename(path))[0]

    return Deck(
        name=top,
        path=path,
        file_sha256=_file_sha256(path),
        notes=notes,
        notetypes=notetypes,
        deck_names=names,
        description=description,
        card_count=card_count,
        new_card_count=new_count,
        media_names=media_names,
        schema_version=schema,
        order_basis=order_basis,
        warnings=warnings,
    )


def _deck_order(name: str) -> tuple[str, ...]:
    """Where a subdeck falls in Anki's deck list: by name, level by level,
    ignoring case. Anki does not sort numbers naturally, so "Unit 10" comes
    before "Unit 2" there too."""
    return tuple(part.casefold() for part in (name or "").split("::"))


def _file_sha256(path: str, chunk: int = 1 << 20) -> str:
    """Fingerprint the package, so a published score names the exact file."""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while True:
                block = fh.read(chunk)
                if not block:
                    break
                digest.update(block)
    except OSError:
        return ""
    return digest.hexdigest()


def _top_deck_name(names: list[str]) -> str:
    """The name a human would give this package.

    A shared deck usually exports as a parent plus its subdecks, and the parent
    itself often holds no notes — so the answer is the deepest path prefix every
    deck shares, not the shallowest name present.
    """
    real = [n for n in names if n and n != "Default"]
    if not real:
        return ""
    if len(real) == 1:
        return real[0]
    parts = [n.split("::") for n in real]
    shared: list[str] = []
    for column in zip(*parts):
        if len(set(column)) != 1:
            break
        shared.append(column[0])
    if shared:
        return "::".join(shared)
    return min(real, key=lambda n: (n.count("::"), len(n)))
