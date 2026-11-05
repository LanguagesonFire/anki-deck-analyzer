"""Everything that works without calling the API.

Run with:  python -m pytest tests -q
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import types
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from anki_deck_analyzer import criteria, metrics, report, scoring, textutil as tx  # noqa: E402
from anki_deck_analyzer.apkg import DeckReadError, read_package  # noqa: E402
from anki_deck_analyzer.evidence import DeckProfile, build_pack, sample_indices  # noqa: E402
from anki_deck_analyzer.llm import Judgement  # noqa: E402


# --------------------------------------------------------------------------
# a synthetic deck, so the tests need no fixture file
# --------------------------------------------------------------------------

SCHEMA_11 = """
CREATE TABLE col (id integer primary key, crt integer, mod integer, scm integer,
  ver integer, dty integer, usn integer, ls integer, conf text, models text,
  decks text, dconf text, tags text);
CREATE TABLE notes (id integer primary key, guid text, mid integer, mod integer,
  usn integer, tags text, flds text, sfld integer, csum integer, flags integer,
  data text);
CREATE TABLE cards (id integer primary key, nid integer, did integer, ord integer,
  mod integer, usn integer, type integer, queue integer, due integer, ivl integer,
  factor integer, reps integer, lapses integer, left integer, odue integer,
  odid integer, flags integer, data text);
"""

MODEL = {
    "1": {
        "id": 1,
        "name": "Sentence",
        "type": 0,
        "sortf": 0,
        "css": ".card { font-size: 20px; }",
        "flds": [{"name": "Italian"}, {"name": "English"}, {"name": "Audio"}],
        "tmpls": [
            {
                "name": "Recognition",
                "qfmt": "{{Italian}}{{Audio}}",
                "afmt": "{{FrontSide}}<hr id=answer>{{English}}",
            }
        ],
    }
}

SENTENCES = [
    ("Io sono Sara.", "I am Sara.", True),
    ("Tu sei Elena.", "You are Elena.", True),
    ("Io sono a casa.", "I am at home.", True),
    ("Tu sei a casa.", "You are at home.", False),
    ("Sara e Elena sono a casa.", "Sara and Elena are at home.", True),
    ("Io sono nuova qui.", "I am new here.", True),
    ("Tu sei nuova qui.", "You are new here.", True),
    ("Sara sono io.", "I am Sara.", True),
    ("Elena e io siamo a casa.", "Elena and I are at home.", True),
    ("Siamo nuove qui.", "We are new here.", True),
    ("Loro sono a casa.", "They are at home.", True),
    ("Voi siete nuove qui.", "You are new here.", True),
    ("Noi siamo a Roma.", "We are in Rome.", True),
    ("Lei e a Roma.", "She is in Rome.", True),
    ("Lui e nuovo qui.", "He is new here.", True),
    ("Loro sono nuovi qui.", "They are new here.", True),
    ("Io sono a Roma ora.", "I am in Rome now.", True),
    ("Tu sei a Roma ora.", "You are in Rome now.", True),
    ("Noi siamo a casa ora.", "We are at home now.", True),
    ("Voi siete a casa ora.", "You are at home now.", True),
]


def make_apkg(path: str, with_audio: bool = True, with_image: bool = False) -> None:
    with tempfile.TemporaryDirectory() as workdir:
        db = os.path.join(workdir, "collection.anki2")
        con = sqlite3.connect(db)
        con.executescript(SCHEMA_11)
        con.execute(
            "INSERT INTO col VALUES (1,0,0,0,11,0,0,0,'{}',?,?,'{}','')",
            (
                json.dumps(MODEL),
                json.dumps(
                    {
                        "1": {"id": 1, "name": "Default"},
                        "2": {"id": 2, "name": "Italian\x1fUnit 01"},
                    }
                ),
            ),
        )
        for i, (it, en, audio) in enumerate(SENTENCES, start=1):
            extra = f"[sound:s{i}.mp3]" if (with_audio and audio) else ""
            img = f'<img src="p{i}.jpg">' if with_image and i % 2 == 0 else ""
            con.execute(
                "INSERT INTO notes VALUES (?,?,1,0,0,' unit01 ',?,?,0,0,'')",
                (i, f"g{i}", f"{it}\x1f{en}{img}\x1f{extra}", it),
            )
            con.execute(
                "INSERT INTO cards VALUES (?,?,2,0,0,0,0,0,?,0,0,0,0,0,0,0,0,'')",
                (i, i, i),
            )
        con.commit()
        con.close()
        with zipfile.ZipFile(path, "w") as zf:
            zf.write(db, "collection.anki2")
            zf.writestr("media", json.dumps({"0": "s1.mp3"}))


@pytest.fixture(scope="module")
def deck_path(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("decks") / "test.apkg")
    make_apkg(path, with_audio=True, with_image=True)
    return path


def _word(n: int) -> str:
    """A distinct alphabetic pseudo-word. Digits are not word tokens, so test
    fixtures must not rely on them to tell two sentences apart."""
    letters = "abcdefghijklmnopqrstuvwxyz"
    return "zo" + letters[n // 676 % 26] + letters[n // 26 % 26] + letters[n % 26]


# --------------------------------------------------------------------------
# text handling
# --------------------------------------------------------------------------


def test_strip_html_keeps_cloze_text():
    assert tx.strip_html("Io {{c1::sono}} qui") == "Io sono qui"
    assert tx.strip_html("Io {{c1::sono::verb}} qui") == "Io sono qui"


def test_strip_html_removes_markup_and_sound():
    raw = '<div>Ciao<br>a <b>tutti</b></div>[sound:x.mp3]&nbsp;'
    assert tx.strip_html(raw) == "Ciao\na tutti"


def test_inline_tags_add_no_space_before_punctuation():
    raw = '<span class="cloze-hint">While tidying up</span>, I found a <b>photo</b>.'
    assert tx.strip_html(raw) == "While tidying up, I found a photo."
    assert tx.readable_field(raw) == "While tidying up, I found a «photo»."
    assert tx.strip_html("l'<i>ami</i>e") == "l'amie"
    assert tx.strip_html("un<div>deux</div>") == "un deux"


def test_sounds_and_images():
    assert tx.sounds("a[sound:one.mp3]b[sound:two.ogg]") == ["one.mp3", "two.ogg"]
    assert tx.images('<img src="a.jpg"> <IMG SRC=\'b.png\'>') == ["a.jpg", "b.png"]


def test_tts_directive_detected():
    assert tx.has_tts_tag("{{tts it_IT:Italian}}")
    assert not tx.has_tts_tag("{{Italian}}")


def test_words_handles_spaceless_scripts():
    assert tx.words("Ciao a tutti") == ["ciao", "a", "tutti"]
    assert tx.words("我是学生") == ["我", "是", "学", "生"]


def test_words_keeps_apostrophes_and_hyphens():
    assert tx.words("l'amico est-ce") == ["l'amico", "est-ce"]


# --------------------------------------------------------------------------
# package reading
# --------------------------------------------------------------------------


def test_reads_a_package(deck_path):
    deck = read_package(deck_path)
    assert len(deck.notes) == len(SENTENCES)
    assert deck.card_count == len(SENTENCES)
    assert deck.schema_version == 11
    assert deck.order_basis == "study order"
    assert deck.name == "Italian::Unit 01"
    nt = deck.notetype_of(deck.notes[0])
    assert nt.fields == ["Italian", "English", "Audio"]
    assert nt.templates[0].qfmt == "{{Italian}}{{Audio}}"
    assert not nt.is_cloze


def test_notes_come_back_in_study_order(deck_path):
    deck = read_package(deck_path)
    assert [n.position for n in deck.notes] == list(range(1, len(SENTENCES) + 1))
    assert tx.strip_html(deck.notes[0].fields[0]) == SENTENCES[0][0]


def test_rejects_a_non_package(tmp_path):
    bad = tmp_path / "not-a-deck.apkg"
    bad.write_text("hello")
    with pytest.raises(DeckReadError):
        read_package(str(bad))


def test_reports_a_missing_file():
    with pytest.raises(DeckReadError):
        read_package("no-such-file.apkg")


# --------------------------------------------------------------------------
# measurement
# --------------------------------------------------------------------------


def test_metrics_count_media(deck_path):
    deck = read_package(deck_path)
    m = metrics.compute(deck, {1: 0})
    assert 0.9 < m.audio_coverage <= 1.0
    assert m.image_coverage == pytest.approx(0.5)
    assert m.distinct_image_files == 10
    assert m.note_count == len(SENTENCES)


def test_new_word_rate_falls_across_a_repetitive_deck(deck_path):
    deck = read_package(deck_path)
    m = metrics.compute(deck, {1: 0}, bucket_count=4)
    assert m.buckets
    assert m.buckets[0].new_word_rate > m.buckets[-1].new_word_rate
    assert m.new_word_rate_trend < 0


def test_i_plus_one_ignores_short_notes():
    """A deck of one-word cards must not score full marks on the i+1 column."""
    from anki_deck_analyzer.apkg import Deck, Note, NoteType, Template

    nt = NoteType(1, "Vocab", ["Word"], [Template("c", "{{Word}}", "")], "", False)
    notes = [Note(i, 1, [_word(i)], [], position=i) for i in range(40)]
    deck = Deck("v", "v.apkg", notes, {1: nt}, card_count=40)
    m = metrics.compute(deck, {1: 0}, bucket_count=4)
    assert all(b.sentence_notes == 0 for b in m.buckets)
    assert all(b.share_at_most_one_new is None for b in m.buckets)


def test_curve_table_survives_missing_sentences():
    from anki_deck_analyzer.apkg import Deck, Note, NoteType, Template

    nt = NoteType(1, "Vocab", ["Word"], [Template("c", "{{Word}}", "")], "", False)
    notes = [Note(i, 1, [_word(i)], [], position=i) for i in range(40)]
    deck = Deck("v", "v.apkg", notes, {1: nt}, card_count=40)
    table = metrics.curve_table(metrics.compute(deck, {1: 0}, bucket_count=4))
    assert "sentences" in table
    assert "%" in table


# --------------------------------------------------------------------------
# sampling and the evidence pack
# --------------------------------------------------------------------------


def test_sampling_never_repeats_a_note():
    picks = sample_indices(500)
    flat = [i for group in picks.values() for i in group]
    assert len(flat) == len(set(flat))
    assert max(flat) < 500


def test_sampling_copes_with_a_tiny_deck():
    picks = sample_indices(5)
    flat = [i for group in picks.values() for i in group]
    assert sorted(set(flat)) == [0, 1, 2, 3, 4]


def test_pack_contains_the_evidence(deck_path):
    deck = read_package(deck_path)
    m = metrics.compute(deck, {1: 0})
    pack = build_pack(deck, m, DeckProfile(target_language="Italian"))
    for heading in ("The deck", "Structure", "What was measured", "The opening"):
        assert f"### {heading}" in pack
    assert "Io sono Sara." in pack
    assert "{{Italian}}{{Audio}}" in pack  # the template went in
    assert "font-size: 20px" in pack  # so did the stylesheet


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------


def _judgements(score: int = 7) -> dict:
    return {
        c.key: Judgement(c.key, score, "high", f"{c.title} verdict")
        for c in criteria.CRITERIA
        if not c.measured
    }


def test_weights_sum_to_one():
    assert sum(c.weight for c in criteria.CRITERIA) == pytest.approx(1.0)
    non_neg = sum(
        c.weight for c in criteria.CRITERIA if c.group == criteria.NON_NEGOTIABLE
    )
    assert non_neg == pytest.approx(0.80)


def test_total_reflects_the_weighting(deck_path):
    deck = read_package(deck_path)
    m = metrics.compute(deck, {1: 0})
    result = scoring.combine(_judgements(8), m, deck)
    assert result.grade in {g for _, g, _ in scoring.GRADES}
    assert 0 <= result.total <= 100
    # Every judged criterion scored 8, so the judged part must land on 80.
    assert result.non_negotiable_total == pytest.approx(80.0)


def test_a_failed_criterion_is_dropped_not_zeroed(deck_path):
    deck = read_package(deck_path)
    m = metrics.compute(deck, {1: 0})
    judged = _judgements(8)
    judged["accuracy"] = Judgement("accuracy", 0, "low", "", error="boom")
    result = scoring.combine(judged, m, deck)
    assert "Accuracy" in result.unassessed
    # Dropping it must not drag the total down toward zero.
    assert result.total > 70


def test_audio_scores_zero_without_sound(tmp_path):
    path = str(tmp_path / "silent.apkg")
    make_apkg(path, with_audio=False)
    deck = read_package(path)
    m = metrics.compute(deck, {1: 0})
    assert m.audio_coverage == 0
    assert scoring.score_audio(m)[0] == 0


def test_images_reach_full_marks_at_half_coverage(deck_path):
    deck = read_package(deck_path)
    m = metrics.compute(deck, {1: 0})
    assert m.image_coverage == pytest.approx(scoring.IMAGE_FULL_COVERAGE)
    assert scoring.score_images(m)[0] == 10


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def _rendered(deck_path):
    deck = read_package(deck_path)
    m = metrics.compute(deck, {1: 0})
    result = scoring.combine(_judgements(7), m, deck)
    result.summary = {
        "headline": "A small test deck.",
        "verdict": "First paragraph.\n\nSecond paragraph.",
        "best_for": ["beginners"],
        "not_for": ["advanced learners"],
        "fixable": ["add audio with HyperTTS"],
        "not_fixable": ["rewrite the sentences"],
    }
    return deck, DeckProfile(target_language="Italian"), m, result


def test_terminal_report_renders(deck_path):
    deck, profile, m, result = _rendered(deck_path)
    out = report.render_terminal(deck, profile, m, result, 0.42, "claude-opus-5")
    assert "Italian" in out
    assert "/100" in out
    assert "$0.42" in out
    assert "cannot listen" in out


def test_html_report_is_self_contained(deck_path):
    deck, profile, m, result = _rendered(deck_path)
    out = report.render_html(deck, profile, m, result, "claude-opus-5")
    assert out.startswith("<!doctype html>")
    assert out.rstrip().endswith("</html>")
    assert "<style>" in out and "http-equiv" not in out
    assert "prefers-color-scheme" in out  # readable in dark mode
    assert "A small test deck." in out


def test_html_escapes_deck_content(deck_path):
    deck, profile, m, result = _rendered(deck_path)
    deck.name = '<script>alert("x")</script>'
    out = report.render_html(deck, profile, m, result, "m")
    assert "<script>alert" not in out
    assert "&lt;script&gt;" in out


def test_markdown_and_json_render(deck_path):
    deck, profile, m, result = _rendered(deck_path)
    md = report.render_markdown(deck, profile, m, result, "claude-opus-5")
    assert md.startswith("# ")
    assert "| Criterion | Weight | Score |" in md

    payload = json.loads(
        report.render_json(deck, profile, m, result, "claude-opus-5", 0.4)
    )
    assert payload["score"]["total"] == pytest.approx(result.total, abs=0.1)
    assert len(payload["criteria"]) == len(criteria.CRITERIA)
    assert payload["run"]["api_cost_usd"] == 0.4


# --------------------------------------------------------------------------
# cli plumbing
# --------------------------------------------------------------------------


def test_dry_run_makes_no_api_call(deck_path, capsys):
    from anki_deck_analyzer.cli import main

    assert main([deck_path, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would be sent to the Anthropic API" in out
    assert "Io sono Sara." in out


def test_cost_accounting():
    from anki_deck_analyzer.llm import Usage

    u = Usage(input_tokens=1_000_000, output_tokens=0, calls=1)
    assert u.cost("claude-opus-5") == pytest.approx(5.0)
    # Cached reads are a tenth of the input price.
    u = Usage(input_tokens=0, cache_read=1_000_000, calls=1)
    assert u.cost("claude-opus-5") == pytest.approx(0.5)
    assert u.cost("some-unknown-model") is None
    # Opus 5.5 lists its cache-read price separately: a twentieth, not a tenth.
    assert u.cost("claude-opus-5-5") == pytest.approx(0.2)
    assert u.cost("claude-sonnet-5-5") == pytest.approx(0.2)


# --------------------------------------------------------------------------
# choosing which field the curve reads
# --------------------------------------------------------------------------


def _two_field_deck(sentence_words: int):
    """A note type with a headword and a second field of the given length."""
    from anki_deck_analyzer.apkg import Deck, Note, NoteType, Template

    nt = NoteType(
        1,
        "Vocab",
        ["Word", "Example"],
        [Template("c", "{{Word}}", "{{Example}}")],
        "",
        False,
    )
    notes = [
        Note(
            i,
            1,
            [_word(i), " ".join(_word(i * 200 + k) for k in range(sentence_words))],
            [],
        )
        for i in range(30)
    ]
    return Deck("d", "d.apkg", notes, {1: nt}, card_count=30)


def test_an_example_sentence_field_wins_over_the_headword():
    deck = _two_field_deck(sentence_words=9)
    chosen, warnings = metrics.choose_measured_fields(deck, {1: 0}, {1: 1})
    assert chosen == {1: 1}
    assert warnings == []


def test_a_dictionary_dump_field_is_rejected():
    deck = _two_field_deck(sentence_words=metrics.SENTENCE_DUMP_TOKENS * 3)
    chosen, warnings = metrics.choose_measured_fields(deck, {1: 0}, {1: 1})
    assert chosen == {1: 0}, "should fall back to the headword"
    assert warnings and "dictionary entry" in warnings[0]
    assert "Example" in warnings[0]


def test_rejection_warning_reaches_the_report():
    deck = _two_field_deck(sentence_words=metrics.SENTENCE_DUMP_TOKENS * 3)
    chosen, warnings = metrics.choose_measured_fields(deck, {1: 0}, {1: 1})
    m = metrics.compute(deck, chosen, extra_warnings=warnings)
    assert any("dictionary entry" in w for w in m.warnings)


def test_pack_names_the_field_the_curve_read():
    deck = _two_field_deck(sentence_words=9)
    profile = DeckProfile(target_fields={1: 0}, sentence_fields={1: 1})
    chosen, _ = metrics.choose_measured_fields(deck, {1: 0}, {1: 1})
    pack = build_pack(deck, metrics.compute(deck, chosen), profile, chosen)
    assert "Vocab -> Example (example sentence)" in pack


# --------------------------------------------------------------------------
# audio coverage over the note types that use audio
# --------------------------------------------------------------------------


def _mixed_audio_deck(silent_share: float, speaking_coverage: float = 1.0):
    """A deck of sentence notes plus a block of silent reference notes."""
    from anki_deck_analyzer.apkg import Deck, Note, NoteType, Template

    types = {
        1: NoteType(1, "Sentence", ["Text"], [Template("c", "{{Text}}", "")], "", False),
        2: NoteType(2, "Rules", ["Text"], [Template("c", "{{Text}}", "")], "", False),
    }
    total = 200
    silent = int(total * silent_share)
    notes = []
    for i in range(total - silent):
        audio = "[sound:s%d.mp3]" % i if i < (total - silent) * speaking_coverage else ""
        notes.append(
            Note(i, 1, [f"frase {_word(i)} qui {audio}"], [], position=i)
        )
    for i in range(silent):
        notes.append(
            Note(1000 + i, 2, [f"tabella {_word(5000 + i)}"], [], position=1000 + i)
        )
    return Deck("d", "d.apkg", notes, types, card_count=total)


def test_silent_reference_types_do_not_count_against_audio():
    deck = _mixed_audio_deck(silent_share=0.10)
    m = metrics.compute(deck)
    assert m.audio_coverage_raw == pytest.approx(0.90)
    assert m.audio_coverage == pytest.approx(1.0)
    assert m.silent_notetypes == ["Rules"]
    score, basis = scoring.score_audio(m)
    assert score == 10
    assert "90%" in basis and "Rules" in basis


def test_a_gap_inside_a_speaking_type_still_counts():
    """Missing recordings on cards that should have them remain a fault."""
    deck = _mixed_audio_deck(silent_share=0.10, speaking_coverage=0.70)
    m = metrics.compute(deck)
    assert m.audio_coverage < 0.75
    assert scoring.score_audio(m)[0] <= 7


def test_a_deck_with_little_audio_is_not_rescued():
    deck = _mixed_audio_deck(silent_share=0.10, speaking_coverage=0.20)
    m = metrics.compute(deck)
    assert m.silent_notetypes == [], "the rule must not fire below the deck threshold"
    assert scoring.score_audio(m)[0] <= 2


def test_too_much_of_the_deck_cannot_be_excused():
    deck = _mixed_audio_deck(silent_share=0.50)
    m = metrics.compute(deck)
    assert m.silent_notetypes == []
    assert m.audio_coverage == pytest.approx(m.audio_coverage_raw)


def test_pack_tells_the_model_not_to_read_silent_types_as_a_gap():
    deck = _mixed_audio_deck(silent_share=0.10)
    m = metrics.compute(deck)
    pack = build_pack(deck, m, DeckProfile())
    assert "Audio per note type:" in pack
    assert "Sentence: 180/180 (100%)" in pack
    assert "Do not treat the silent types as missing audio." in pack


# --------------------------------------------------------------------------
# parallel recall strands
# --------------------------------------------------------------------------


def _strand_deck(strands: int):
    """The same 60 sentences offered in `strands` recall directions."""
    from anki_deck_analyzer.apkg import Deck, Note, NoteType, Template

    sentences = [
        " ".join(_word(i * 100 + k) for k in range(6)) for i in range(60)
    ]
    types, notes, pos = {}, [], 0
    for s in range(strands):
        nt_id = s + 1
        types[nt_id] = NoteType(
            nt_id, f"Strand{s}", ["Text"], [Template("c", "{{Text}}", "")], "", False
        )
        for text in sentences:
            notes.append(
                Note(pos, nt_id, [text], [], position=pos, deck=f"Course::Strand {s}")
            )
            pos += 1
    return Deck("Course", "c.apkg", notes, types, card_count=len(notes))


def test_repeat_strands_leave_the_curve_alone():
    """Three copies of one sentence set must measure like one copy."""
    one = metrics.compute(_strand_deck(1), bucket_count=5)
    three = metrics.compute(_strand_deck(3), bucket_count=5)

    assert three.repeated_material_notes == 120
    assert three.total_vocabulary == one.total_vocabulary
    rates_one = [round(b.new_word_rate, 6) for b in one.buckets]
    rates_three = [round(b.new_word_rate, 6) for b in three.buckets]
    assert rates_three == rates_one, "the extra strands must not flatten the curve"


def test_cross_subdeck_twins_are_counted_as_strands_not_duplicates():
    m = metrics.compute(_strand_deck(3))
    assert m.duplicate_note_share == pytest.approx(1.0)
    assert m.parallel_strand_notes == 180, "all of them live in other subdecks"


def test_repeats_inside_one_subdeck_are_not_called_strands():
    from anki_deck_analyzer.apkg import Deck, Note, NoteType, Template

    nt = NoteType(1, "S", ["Text"], [Template("c", "{{Text}}", "")], "", False)
    notes = [
        Note(i, 1, ["la stessa frase qui"], [], position=i, deck="Course::One")
        for i in range(20)
    ]
    m = metrics.compute(Deck("c", "c.apkg", notes, {1: nt}, card_count=20))
    assert m.duplicate_note_share == pytest.approx(1.0)
    assert m.parallel_strand_notes == 0, "one subdeck, so these are real duplicates"


def test_pack_separates_strands_from_duplicates():
    m = metrics.compute(_strand_deck(3))
    pack = build_pack(_strand_deck(3), m, DeckProfile())
    assert "sit in a DIFFERENT subdeck" in pack
    assert "Do not score it as duplication" in pack
    assert "follows each piece of material once" in pack


# --------------------------------------------------------------------------
# draft vs publish, and reproducibility
# --------------------------------------------------------------------------


def test_draft_and_publish_set_opposite_defaults():
    from anki_deck_analyzer.cli import build_parser

    p = build_parser()
    draft = p.parse_args(["d.apkg", "--draft"])
    publish = p.parse_args(["d.apkg", "--publish"])
    assert draft.draft and not draft.publish
    assert publish.publish and publish.repeat == 1  # applied in main(), not the parser


def test_presets_do_not_override_an_explicit_flag(monkeypatch, deck_path):
    """--draft --effort medium must keep medium."""
    from anki_deck_analyzer import cli

    seen = {}

    def fake_analyze(path, analyzer, **kw):
        seen["model"] = analyzer.model
        seen["effort"] = analyzer.effort
        seen["repeat"] = kw.get("repeat")
        raise DeckReadError("stop here — the settings are what we are checking")

    def fake_client(_key):
        # Analyzer inspects client.messages.create to see which params exist.
        def create(*, model=None, max_tokens=None, output_config=None, **kw):
            raise AssertionError("no request should be made in this test")

        return types.SimpleNamespace(messages=types.SimpleNamespace(create=create))

    monkeypatch.setattr(cli, "analyze", fake_analyze)
    monkeypatch.setattr(cli, "_client", fake_client)

    from anki_deck_analyzer.llm import DEFAULT_MODEL, PUBLISH_MODEL

    cli.main([deck_path, "--draft", "--effort", "medium"])
    assert seen["model"] == DEFAULT_MODEL
    assert seen["effort"] == "medium", "an explicit flag must win over the preset"

    cli.main([deck_path, "--publish"])
    assert seen["model"] == PUBLISH_MODEL, "publishing uses the sharper model"
    assert seen["repeat"] == 1, (
        "publishing must use the same single pass a reader gets, or the "
        "published score is not one they can reproduce"
    )


def test_only_rejects_an_unknown_criterion(deck_path, capsys):
    from anki_deck_analyzer.cli import main

    with pytest.raises(SystemExit):
        main([deck_path, "--only", "progresssion"])
    assert "unknown criteria" in capsys.readouterr().err


def test_a_deck_fingerprint_is_recorded(deck_path):
    deck = read_package(deck_path)
    assert len(deck.file_sha256) == 64
    assert read_package(deck_path).file_sha256 == deck.file_sha256


def test_the_same_deck_is_always_sampled_the_same_way():
    """A published score must name a sample a reader can reproduce."""
    from anki_deck_analyzer.evidence import SAMPLE_SEED

    assert sample_indices(5000) == sample_indices(5000)
    assert sample_indices(5000, SAMPLE_SEED) == sample_indices(5000)


def test_repeated_passes_publish_the_median_and_keep_the_spread():
    from anki_deck_analyzer.llm import Analyzer, Judgement

    scores = iter([4, 9, 7])

    class Stub(Analyzer):
        def __init__(self):
            self.model, self.effort = "m", "high"

        def judge(self, pack, criterion):
            return Judgement(criterion.key, next(scores), "high", "v")

    chosen = Stub().judge_repeatedly("pack", criteria.BY_KEY["accuracy"], 3)
    assert chosen.score == 7, "the middle reading, not the first or the mean"
    assert sorted(chosen.scores_seen) == [4, 7, 9]
    assert chosen.spread == 5


def test_provenance_rebuilds_the_command():
    from anki_deck_analyzer.analyze import Provenance

    from anki_deck_analyzer.llm import DEFAULT_MODEL, PUBLISH_MODEL

    default = Provenance("1.0.0", DEFAULT_MODEL, "high", 1, 7, "abc", ["accuracy"], True)
    assert default.command("d.apkg") == 'anki-deck-analyzer "d.apkg"'

    # An Opus run reproduces with the model named, not a preset.
    opus = Provenance("1.0.0", PUBLISH_MODEL, "high", 1, 7, "abc", ["a"], True)
    assert opus.command("d.apkg") == f'anki-deck-analyzer "d.apkg" --model {PUBLISH_MODEL}'

    tuned = Provenance(
        "1.0.0", "claude-opus-5", "low", 3, 7, "abc", ["accuracy"], False
    )
    cmd = tuned.command("d.apkg")
    assert "--model claude-opus-5" in cmd
    assert "--effort low" in cmd
    assert "--repeat 3" in cmd
    assert "--only accuracy" in cmd


def test_a_missing_key_explains_itself(deck_path, capsys, monkeypatch):
    """The SDK fails lazily, so the friendly message must survive that."""
    from anki_deck_analyzer import cli

    def fake_analyze(path, analyzer, **kw):
        # The wording the SDK actually produces when no key is configured.
        raise RuntimeError(
            "Could not resolve authentication method. Expected either api_key "
            "or auth_token to be set."
        )

    monkeypatch.setattr(cli, "analyze", fake_analyze)
    assert cli.main([deck_path]) == 2
    err = capsys.readouterr().err
    assert "console.anthropic.com" in err
    assert "--dry-run" in err, "the free option must be offered"
    assert "$0.45" in err


def test_a_real_deck_error_is_not_mistaken_for_an_auth_problem(deck_path, capsys, monkeypatch):
    from anki_deck_analyzer import cli

    def fake_analyze(path, analyzer, **kw):
        raise ValueError("something else went wrong")

    monkeypatch.setattr(cli, "analyze", fake_analyze)
    monkeypatch.setattr(
        cli,
        "_client",
        lambda key: types.SimpleNamespace(
            messages=types.SimpleNamespace(create=lambda **kw: None)
        ),
    )
    assert cli.main([deck_path]) == 1
    assert "console.anthropic.com" not in capsys.readouterr().err


# --------------------------------------------------------------------------
# selective production
# --------------------------------------------------------------------------


def _recall_deck(fronts):
    """One note type whose templates show the given fields on the front."""
    from anki_deck_analyzer.apkg import Deck, Note, NoteType, Template

    tmpls = [
        Template(f"Card {i + 1}", " ".join("{{%s}}" % f for f in front), "{{FrontSide}}")
        for i, front in enumerate(fronts)
    ]
    nt = NoteType(1, "S", ["Target", "English", "Audio"], tmpls, "", False)
    notes = [
        Note(i, 1, [_word(i), "translation " + _word(i), ""], [], position=i)
        for i in range(20)
    ]
    deck = Deck("d", "d.apkg", notes, {1: nt}, card_count=20 * len(tmpls))
    profile = DeckProfile(target_fields={1: 0}, translation_fields={1: 1})
    return deck, metrics.compute(deck, {1: 0}), profile


def test_no_production_is_a_choice_not_a_gap():
    """A comprehension-first deck must not be marked as missing a feature."""
    share, detail = scoring.production_share(*_recall_deck([["Target", "Audio"]]))
    assert share == 0.0
    assert "choice rather than a gap" in detail


def test_production_on_every_note_is_flagged():
    share, detail = scoring.production_share(
        *_recall_deck([["Target"], ["English"]])
    )
    assert share == pytest.approx(1.0)
    assert "every note" in detail and "tiring" in detail


def test_a_conditional_wrapper_is_not_a_displayed_field():
    """{{#Target}}...{{/Target}} wraps a section; it does not show the field."""
    assert tx.shown_fields("{{#Target}}{{English}}{{/Target}}") == ["English"]
    assert "Target" in tx.field_refs("{{#Target}}{{English}}{{/Target}}")


def test_a_type_in_box_counts_as_the_field_it_asks_for():
    assert tx.shown_fields("{{English}} {{type:Target}}") == ["English", "Target"]


def test_showing_the_target_on_the_front_is_not_production():
    """If the answer is already on the card you are not producing anything."""
    share, _ = scoring.production_share(
        *_recall_deck([["Target"], ["Target", "English"]])
    )
    assert share == 0.0


def test_production_needs_both_field_roles_known():
    """Without the target field a cloze card would read as production."""
    deck, m, _ = _recall_deck([["English"]])
    share, detail = scoring.production_share(deck, m, DeckProfile(translation_fields={1: 1}))
    assert share in (0.0, 1.0)
    assert "cards per note" in detail, "should fall back and say it is a guess"


def test_the_audio_score_carries_its_own_caveat(deck_path):
    """A criterion titled 'High-quality audio' must not imply quality was judged."""
    deck = read_package(deck_path)
    m = metrics.compute(deck, {1: 0})
    _, basis = scoring.score_audio(m)
    assert "presence, not quality" in basis
    assert "cannot listen" in basis

    m_silent = metrics.compute(read_package(deck_path), {1: 0})
    m_silent.uses_tts_directive = True
    m_silent.notes_with_audio = 0
    assert "presence, not quality" in scoring.score_audio(m_silent)[1]


def test_the_image_score_carries_its_own_caveat(deck_path):
    deck = read_package(deck_path)
    _, basis = scoring.score_images(metrics.compute(deck, {1: 0}))
    assert "cannot see the pictures" in basis


def test_measured_criteria_are_marked_in_both_views(deck_path):
    deck, profile, m, result = _rendered(deck_path)
    terminal = report.render_terminal(deck, profile, m, result, None, "m")
    assert "presence only" in terminal
    html = report.render_html(deck, profile, m, result, "m")
    assert "presence only" in html
    # and the caveat travels into every serialised form
    assert "presence, not quality" in report.render_markdown(deck, profile, m, result, "m")
    assert "presence, not quality" in report.render_json(deck, profile, m, result, "m", None)


# --------------------------------------------------------------------------
# a position every card shares is not an order
# --------------------------------------------------------------------------


def _apkg_with_due(path: str, due_for: "callable") -> None:
    """The fixture deck, with the new-card position under the test's control."""
    with tempfile.TemporaryDirectory() as workdir:
        db = os.path.join(workdir, "collection.anki2")
        con = sqlite3.connect(db)
        con.executescript(SCHEMA_11)
        con.execute(
            "INSERT INTO col VALUES (1,0,0,0,11,0,0,0,'{}',?,?,'{}','')",
            (json.dumps(MODEL), json.dumps({"1": {"id": 1, "name": "Default"}})),
        )
        for i, (it, en, _audio) in enumerate(SENTENCES, start=1):
            con.execute(
                "INSERT INTO notes VALUES (?,?,1,0,0,'',?,?,0,0,'')",
                (i, f"g{i}", f"{it}\x1f{en}\x1f", it),
            )
            con.execute(
                "INSERT INTO cards VALUES (?,?,1,0,0,0,0,0,?,0,0,0,0,0,0,0,0,'')",
                (i, i, due_for(i)),
            )
        con.commit()
        con.close()
        with zipfile.ZipFile(path, "w") as zf:
            zf.write(db, "collection.anki2")


def test_distinct_positions_are_read_as_a_study_order(tmp_path):
    path = str(tmp_path / "ordered.apkg")
    _apkg_with_due(path, lambda i: i)
    deck = read_package(path)
    assert deck.order_basis == "study order"
    assert not [w for w in deck.warnings if "creation order" in w]


def test_one_position_shared_by_every_card_is_not_a_study_order(tmp_path):
    """`due = 0` throughout is a populated field carrying no order at all.

    The sort falls through to note id, which is creation order, so the report
    has to say creation order rather than claim an order the file never had.
    """
    path = str(tmp_path / "flat.apkg")
    _apkg_with_due(path, lambda i: 0)
    deck = read_package(path)
    assert deck.order_basis == "creation order"
    assert [w for w in deck.warnings if "same new-card position" in w]


# --------------------------------------------------------------------------
# first-seen marks
# --------------------------------------------------------------------------


def _repetitive_deck():
    """A deck whose second field re-glosses words the deck already used."""
    from anki_deck_analyzer.apkg import Deck, Note, NoteType, Template
    from anki_deck_analyzer.evidence import first_seen_marks

    nt = NoteType(
        1,
        "Sentence",
        ["Sentence", "Vocabulary"],
        [Template("c", "{{Sentence}}", "{{Vocabulary}}")],
        "",
        False,
    )
    notes = [
        Note(i, 1, [f"il gatto beve {_word(i)}", "il gatto beve"], [], position=i)
        for i in range(30)
    ]
    return Deck("d", "d.apkg", notes, {1: nt}, card_count=30), first_seen_marks


def test_a_field_repeating_known_words_is_marked_as_introducing_none():
    deck, first_seen_marks = _repetitive_deck()
    marks = first_seen_marks(deck, {0, 20})
    # The opening note meets all four words for the first time.
    assert marks[0][0] == (4, 4)
    # Note 20 carries the same three-word gloss, by then long familiar.
    assert marks[20][1] == (0, 3)
    # Only its one unique sentence word is new.
    assert marks[20][0] == (1, 4)


def test_the_pack_tells_the_model_what_the_marks_mean():
    deck, _ = _repetitive_deck()
    pack = build_pack(deck, metrics.compute(deck, {1: 0}), DeckProfile())
    assert "first seen here" in pack
    assert "Do not infer how much a note introduces" in pack


# --------------------------------------------------------------------------
# 1.1: dictionary forms, names, frequency list, templates, rendering, rubric
# --------------------------------------------------------------------------

from anki_deck_analyzer import frequency as fq  # noqa: E402
from anki_deck_analyzer import lexicon as lx  # noqa: E402


def _has(module: str) -> bool:
    try:
        __import__(module)
        return True
    except ImportError:
        return False


def test_language_names_resolve_to_codes():
    assert lx.language_code("French") == "fr"
    assert lx.language_code("Brazilian Portuguese") == "pt"
    assert lx.language_code("Mandarin Chinese") == "zh"
    assert lx.language_code("unknown") is None
    assert lx.language_code(None) is None


def test_a_name_is_found_from_its_use_inside_a_sentence():
    texts = ["Paul ! Tu es là ?", "Je parle avec Paul.", "Bonjour, madame."]
    names = lx.find_names(texts, "fr")
    assert "paul" in names
    # Capitalized only at the start of a line: could be any word.
    assert "bonjour" not in names


def test_a_word_also_written_in_lowercase_is_not_a_name():
    names = lx.find_names(["Il pleut, Pierre.", "La pierre est grise."], "fr")
    assert "pierre" not in names


def test_a_capital_after_a_bullet_or_emoji_is_not_evidence_of_a_name():
    names = lx.find_names(["\U0001f3e0 Emménagement", "• Liens utiles"], "fr")
    assert not names


def test_german_nouns_are_never_taken_for_names():
    assert lx.find_names(["Ich sehe das Haus.", "Das Haus ist alt."], "de") == frozenset()


def test_names_never_count_as_new_words():
    lex = lx.Lexicon(names=frozenset({"paul"}))
    assert lex.words("Je parle avec Paul") == ["je", "parle", "avec"]


def test_without_a_lemmatizer_words_are_counted_by_spelling():
    lex = lx.build(["Je suis ici.", "Tu es ici."], None)
    assert lex.method == lx.METHOD_SURFACE
    assert "spelling" in lex.describe()
    assert lex.label == "surface"


def test_cjk_keeps_per_character_counting():
    lex = lx.build(["我是学生。", "你是老师。"], "Chinese")
    assert lex.method == lx.METHOD_CHARACTER
    assert lex.words("我是") == ["我", "是"]


@pytest.mark.skipif(not _has("simplemma"), reason="simplemma not installed")
def test_inflected_forms_count_as_one_word():
    lex = lx.build(["Je suis ici.", "Nous étions là."], "French")
    assert lex.method == lx.METHOD_LEMMA
    assert lex.words("étaient") == ["être"]
    assert lex.words("suis") == ["être"]
    assert "dictionary form" in lex.describe()
    assert lex.label.startswith("lemma:simplemma")


@pytest.mark.skipif(not _has("simplemma"), reason="simplemma not installed")
def test_elided_articles_are_split_in_dictionary_mode():
    lex = lx.build(["L'homme est là."], "French")
    assert lex.words("l'homme") == ["le", "homme"]


@pytest.mark.skipif(not _has("simplemma"), reason="simplemma not installed")
def test_dictionary_forms_lower_the_new_word_rate():
    """The same deck, counted two ways: folding inflections can only reduce
    how much looks new."""
    from anki_deck_analyzer.apkg import Deck, Note, NoteType, Template

    nt = NoteType(1, "S", ["Text"], [Template("c", "{{Text}}", "")], "", False)
    forms = ["suis", "es", "est", "sommes", "êtes", "sont", "étais", "était"]
    notes = [
        Note(i, 1, [f"je {forms[i % len(forms)]} {_word(i)} ici"], [], position=i)
        for i in range(40)
    ]
    deck = Deck("d", "d.apkg", notes, {1: nt}, card_count=40)
    surface = metrics.compute(deck, {1: 0})
    lemma = metrics.compute(deck, {1: 0}, language="French")
    assert lemma.total_vocabulary < surface.total_vocabulary
    assert lemma.new_word_rate_overall < surface.new_word_rate_overall


@pytest.mark.skipif(not _has("wordfreq"), reason="wordfreq not installed")
def test_frequency_profile_reads_the_list():
    lex = lx.build(["le chat"], "French")
    texts = ["le chat est ici", "la maison est grande", "nous parlons français"] * 10
    bounds = [(i + 1, i * 3, (i + 1) * 3) for i in range(10)]
    prof = fq.compute(lex, texts, bounds, "French", frequency_list="wordfreq")
    assert prof.available
    assert "wordfreq" in prof.source and "French" in prof.source
    taught, size = prof.coverage[1000]
    assert 0 < taught <= size
    assert "top 1,000" in fq.table(prof)


def test_no_list_is_said_plainly():
    lex = lx.build(["blorp zib"], None)
    prof = fq.compute(lex, ["blorp zib"], [], "")
    assert not prof.available
    assert fq.table(prof).startswith("No frequency list was used:")


@pytest.mark.skipif(not _has("wordfreq"), reason="wordfreq not installed")
def test_explanation_language_words_are_kept_out_of_the_comparison():
    lex = lx.build(["x"], "French")
    texts = ["the subject agrees with the verb", "le chat mange"]
    prof = fq.compute(lex, texts, [(1, 0, 2)], "French", "English")
    assert prof.explanation_words >= 3


@pytest.mark.skipif(not _has("wordfreq"), reason="wordfreq not installed")
def test_only_listed_words_are_named_as_rare():
    lex = lx.build(["x"], "French")
    # "blorptastique" is on no list; it must be counted, not named.
    texts = ["le chat", "blorptastique"] + ["la maison"] * 8
    bounds = [(i + 1, i, i + 1) for i in range(10)]
    prof = fq.compute(lex, texts, bounds, "French")
    assert all(word != "blorptastique" for word, *_ in prof.rare_early)
    assert prof.unlisted >= 1


def test_a_line_break_inside_a_list_item_stays_a_line_break():
    text = tx.readable_field("<ul><li>tu – you<br>là – there</li><li>oui – yes</li></ul>")
    assert text.split("\n") == ["• tu – you", "là – there", "• oui – yes"]


def test_collapsed_content_is_marked_as_hidden():
    text = tx.readable_field(
        "Voilà<details><summary>Show conjugation</summary>je suis<br>tu es</details>"
    )
    assert "[collapsed: Show conjugation]" in text
    assert "[/collapsed]" in text


def test_table_cells_stay_side_by_side():
    text = tx.readable_field("<table><tr><td>je</td><td>suis</td></tr></table>")
    assert text == "je | suis"


def test_a_multi_line_field_is_indented_under_its_name():
    from anki_deck_analyzer.apkg import Deck, Note, NoteType, Template
    from anki_deck_analyzer.evidence import render_note

    nt = NoteType(1, "V", ["Words"], [Template("c", "{{Words}}", "")], "", False)
    deck = Deck("d", "d.apkg", [], {1: nt}, card_count=0)
    out = render_note(deck, Note(1, 1, ["tu – you<br>là – there"], []), position=1)
    assert "  Words:\n    | tu – you\n    | là – there" in out


def test_every_note_types_templates_reach_the_pack():
    from anki_deck_analyzer.apkg import Deck, Note, NoteType, Template

    types = {
        i: NoteType(
            i,
            f"Type{i}",
            ["Text"],
            [Template(f"card{i}", "<div class=t%d>{{Text}}</div>" % i, "back")],
            "",
            False,
        )
        for i in range(1, 8)
    }
    notes = [Note(n, 1 + n % 7, [f"frase {_word(n)}"], [], position=n) for n in range(70)]
    deck = Deck("d", "d.apkg", notes, types, card_count=70)
    pack = build_pack(deck, metrics.compute(deck), DeckProfile())
    for i in range(1, 8):
        assert f"Note type: Type{i}" in pack
        assert f"class=t{i}" in pack


def test_long_scripts_are_left_out_of_templates():
    out = tx.compact_template("<div>{{Front}}</div><script>" + "x();" * 200 + "</script>")
    assert "{{Front}}" in out
    assert "characters of script omitted" in out


def test_scripts_that_render_the_card_stay_in_templates():
    # A back built as {{FrontSide}} plus a script that inserts the back's markup.
    builder = ("const t = `<p class=\"sentence-translation\">{{Translation}}</p>"
               "{{#Comments}}<div class=\"callout\">{{Comments}}</div>{{/Comments}}`;"
               + " // layout" * 30 + " document.getElementById('back-content').innerHTML = t;")
    out = tx.compact_template("{{FrontSide}}<script>" + builder + "</script>")
    assert "{{Translation}}" in out and "callout" in out
    assert "omitted" not in out
    # A long script without markup or fields is still left out.
    logo = "var links = document.querySelectorAll('a'); " * 20
    assert "characters of script omitted" in tx.compact_template("<div>x</div><script>" + logo + "</script>")


def _metrics_with_rates(rates):
    m = metrics.Metrics(
        note_count=0, card_count=0, notetype_counts={}, deck_tree=[], subdeck_count=0,
        audio_coverage=0, image_coverage=0, notes_with_audio=0, notes_with_images=0,
        uses_tts_directive=False, distinct_audio_files=0, distinct_image_files=0,
        has_cloze=False, has_reverse_cards=False, templates_per_notetype={},
        cards_per_note=0, duplicate_note_groups=0, duplicate_note_share=0,
        empty_field_share=0, analysed_notes=0, total_vocabulary=0,
        mean_words_per_note=0, type_token_ratio=0,
    )
    m.buckets = [
        metrics.Bucket(i + 1, 0, 0, 10, r, 10, 10, 1, 0.8, 5, 100)
        for i, r in enumerate(rates)
    ]
    return m


def test_a_plateau_after_the_opening_is_described_as_a_plateau():
    shape = _metrics_with_rates(
        [0.27, 0.14, 0.088, 0.092, 0.081, 0.085, 0.09, 0.087, 0.08, 0.08]
    ).curve_shape
    assert "then holds between" in shape
    assert "Spikes" not in shape
    assert "rises again" not in shape


def test_a_spike_and_a_late_rise_are_named():
    shape = _metrics_with_rates(
        [0.25, 0.10, 0.08, 0.08, 0.20, 0.08, 0.09, 0.12, 0.13, 0.14]
    ).curve_shape
    assert "Spikes" in shape and "part 5" in shape
    assert "rises again" in shape


def test_the_rubric_does_not_ask_for_an_ever_falling_rate():
    rubric = criteria.BY_KEY["progression"].rubric
    assert "falls as the deck goes on" not in rubric
    assert "not a plateau" in rubric


def test_counting_method_reaches_every_report(deck_path):
    deck = read_package(deck_path)
    m = metrics.compute(deck, {1: 0})
    result = scoring.combine({}, m, deck, DeckProfile())
    for text in (
        report.render_markdown(deck, DeckProfile(), m, result, "x"),
        report.render_html(deck, DeckProfile(), m, result, "x"),
    ):
        assert "Words are counted" in text
    data = json.loads(report.render_json(deck, DeckProfile(), m, result, "x", None))
    assert "Words are counted" in data["measurements"]["word_counting"]
    assert "frequency" in data["measurements"]


# --------------------------------------------------------------------------
# 1.2: choosing the frequency list, and matching it fairly
# --------------------------------------------------------------------------


def _lexique_tsv(tmp_path, rows):
    header = "ortho\tphon\tlemme\tcgram\tfreqfilms2\tfreqlivres\n"
    path = tmp_path / "Lexique383.tsv"
    path.write_text(
        header + "".join(f"{o}\t-\t{l}\t{c}\t{f}\t{b}\n" for o, l, c, f, b in rows),
        encoding="utf-8",
    )
    return str(path)


def test_lexique_ranks_lemmas_by_the_mean_of_summed_film_and_book_frequency(tmp_path):
    path = _lexique_tsv(
        tmp_path,
        [
            ("chat", "chat", "NOM", 10, 10),     # chat: (10+30)/2 = 20
            ("chats", "chat", "NOM", 0, 20),
            ("est", "être", "VER", 50, 0),       # être: (50+0)/2 = 25
            ("b", "b", "NOM", 999, 999),         # a letter: dropped
            ("à", "à", "PRE", 1, 1),             # a word: kept
            ("12", "12", "ADJ:num", 999, 999),   # digits: dropped
        ],
    )
    lex = lx.Lexicon(language="fr")
    flist, reason = fq.load_list(path, lex)
    assert reason == ""
    heads = [h for h, _ in flist.entries]
    assert "b" not in heads and "à" in heads
    assert flist.source.startswith("Lexique 3.83 (New & Pallier, CC BY-SA 4.0)")
    kept, dropped = fq._clean(flist, frozenset())
    assert [h for h, _ in kept][:2] == ["être", "chat"]
    assert dropped == {"digits or symbols": 1}


def test_a_json_list_is_read_in_rank_order_and_marked_custom(tmp_path):
    path = tmp_path / "course.json"
    path.write_text(
        json.dumps({"frequency_list": [
            {"word": "avoir", "rank": 2, "translation": ""},
            {"word": "être", "rank": 1, "translation": ""},
        ]}),
        encoding="utf-8",
    )
    flist, _ = fq.load_list(str(path), lx.Lexicon(language="fr"))
    assert [h for h, _ in flist.entries] == ["être", "avoir"]
    assert flist.custom
    prof = fq.compute(lx.Lexicon(language="fr"), ["je suis là"], [], "French", "", str(path))
    assert "custom list" in fq.table(prof)


def test_a_missing_list_file_is_reported_not_raised():
    prof = fq.compute(lx.Lexicon(language="fr"), ["x"], [], "French", "", "nope.tsv")
    assert not prof.available and "not found" in prof.reason


def test_list_entries_match_whole_elided_compounds_and_phrases(tmp_path):
    path = tmp_path / "l.json"
    path.write_text(
        json.dumps(["jusqu'à", "d'accord", "parce que", "homme", "absent"]),
        encoding="utf-8",
    )
    texts = ["Jusqu’à demain.", "D'accord, parce que l'homme est là."]
    prof = fq.compute(lx.Lexicon(language="fr"), texts, [], "French", "", str(path))
    assert [w for _, w in prof.missing] == ["absent"]


def test_names_are_dropped_from_the_list_side_too(tmp_path):
    path = tmp_path / "l.json"
    path.write_text(json.dumps(["paris", "maison"]), encoding="utf-8")
    lex = lx.Lexicon(language="fr", names=frozenset({"paris"}))
    prof = fq.compute(lex, ["la maison"], [], "French", "", str(path))
    assert prof.dropped == {"names": 1}
    assert prof.coverage[1000] == (1, 1)


def test_ligatures_and_curly_apostrophes_do_not_block_a_match(tmp_path):
    path = tmp_path / "l.json"
    path.write_text(json.dumps(["coeur", "aujourd'hui"]), encoding="utf-8")
    prof = fq.compute(
        lx.Lexicon(language="fr"), ["Mon cœur, aujourd’hui."], [], "French", "", str(path)
    )
    assert prof.coverage[1000] == (2, 2)


@pytest.mark.skipif(not _has("simplemma"), reason="simplemma not installed")
def test_a_spelling_match_survives_a_lemmatizer_slip(tmp_path):
    # Whatever the lemmatizer makes of "moyenne", the spelling itself matches.
    path = tmp_path / "l.json"
    path.write_text(json.dumps(["moyenne"]), encoding="utf-8")
    lex = lx.build(["La moyenne est bonne."], "French")
    prof = fq.compute(lex, ["La moyenne est bonne."], [], "French", "", str(path))
    assert prof.coverage[1000] == (1, 1)


def test_a_non_default_list_reaches_the_reproduce_command():
    from anki_deck_analyzer.analyze import Provenance

    p = Provenance("1.2.0", "claude-sonnet-5", "high", 1, 7, "0" * 64, [], True,
                   frequency_option="Lexique383.tsv")
    assert '--frequency-list "Lexique383.tsv"' in p.command("d.apkg")


def test_dry_run_field_map_measures_the_named_field(deck_path):
    from anki_deck_analyzer.analyze import preview

    _, m, pack = preview(deck_path, None, {"Sentence": "English"})
    assert "Sentence -> English" in pack
    with pytest.raises(ValueError):
        preview(deck_path, None, {"Sentence": "Nope"})


# --------------------------------------------------------------------------
# 1.3: <audio> players, explanation note types, stated dictionary forms
# --------------------------------------------------------------------------


def test_an_html_audio_player_counts_as_audio():
    assert tx.sounds('<audio class="x" src="a.mp3" preload="none"></audio>') == ["a.mp3"]
    assert tx.sounds('<audio controls><source src="b.ogg" type="audio/ogg"></audio>') == ["b.ogg"]
    assert tx.sounds("[sound:c.mp3]") == ["c.mp3"]
    assert tx.sounds('<video><source src="v.mp4"></video>') == []


def test_a_note_type_voiced_only_by_audio_tags_has_full_coverage():
    from anki_deck_analyzer.apkg import Deck, Note, NoteType, Template

    nt = NoteType(1, "Dialogue", ["Text", "Audio"], [Template("c", "{{Text}}", "")], "", False)
    notes = [
        Note(i, 1, [f"phrase {_word(i)}", f'<audio src="d{i}.mp3"></audio>'], [], position=i)
        for i in range(30)
    ]
    m = metrics.compute(Deck("d", "d.apkg", notes, {1: nt}, card_count=30), {1: 0})
    assert m.audio_coverage == 1.0
    assert m.audio_by_notetype["Dialogue"] == (30, 30)


def _material_and_rules_deck():
    from anki_deck_analyzer.apkg import Deck, Note, NoteType, Template

    t = [Template("c", "{{Text}}", "")]
    types = {
        1: NoteType(1, "Sentence", ["Text", "Verb"], t, "", False),
        2: NoteType(2, "Rules", ["Text"], t, "", False),
        3: NoteType(3, "Quiz", ["Prompt"], t, "", False),
    }
    notes = []
    for i in range(40):
        notes.append(Note(i, 1, [f"je vois la maison {_word(i)}", "voir"], [], position=i))
    notes.append(Note(100, 2, ["le partitif et la copule expliqués ici"], [], position=100))
    notes.append(Note(101, 3, ["How do you say the cat in French?"], [], position=101))
    return Deck("d", "d.apkg", notes, types, card_count=len(notes))


def test_explanation_types_stay_out_of_the_curve_and_are_reported_apart():
    deck = _material_and_rules_deck()
    m = metrics.compute(deck, {1: 0, 2: 0}, explanation_types={2})
    assert m.explanation_types == {"Rules": 1}
    assert "partitif" in m.explanation_only_words
    # The rules note's words never reach the material's vocabulary.
    with_rules = metrics.compute(deck, {1: 0, 2: 0})
    assert m.total_vocabulary < with_rules.total_vocabulary


def test_a_type_with_no_target_field_is_left_unmeasured_once_others_are_mapped():
    deck = _material_and_rules_deck()
    m = metrics.compute(deck, {1: 0}, explanation_types={2})
    assert m.unmeasured_types == {"Quiz": 1}
    pack = build_pack(deck, m, DeckProfile(target_fields={1: 0}))
    assert "Not measured, because no target-language field was identified: Quiz" in pack


@pytest.mark.skipif(not _has("wordfreq"), reason="wordfreq not installed")
def test_english_stage_directions_are_dropped_from_a_french_field():
    filt = fq.explanation_filter(lx.Lexicon(language="fr"), "English")
    text = "Bernard: Je pose ma tasse sur la table.\nBernard puts sugar in his coffee.\nOui, merci."
    kept, dropped = fq.drop_explanation_lines(text, filt)
    assert dropped == 1
    assert "sugar" not in kept and "tasse" in kept and "Oui" in kept


@pytest.mark.skipif(not _has("wordfreq"), reason="wordfreq not installed")
def test_a_stated_dictionary_form_counts_as_taught(tmp_path):
    path = tmp_path / "l.json"
    path.write_text(json.dumps(["offrir", "absent"]), encoding="utf-8")
    lex = lx.Lexicon(language="fr")
    texts = ["Je vous offre quelque chose ?"]
    without = fq.compute(lex, texts, [], "French", "", str(path))
    assert [w for _, w in without.missing] == ["offrir", "absent"]
    with_field = fq.compute(lex, texts, [], "French", "", str(path), [(0, "offrir")])
    assert [w for _, w in with_field.missing] == ["absent"]
    assert with_field.stated_only == 1


def test_lemma_fields_reach_the_frequency_comparison_through_compute(tmp_path):
    path = tmp_path / "l.json"
    path.write_text(json.dumps(["voir"]), encoding="utf-8")
    deck = _material_and_rules_deck()
    m = metrics.compute(deck, {1: 0}, frequency_list=str(path), lemma_fields={1: [1]})
    assert m.frequency.coverage[1000] == (1, 1)


def _ranked_profile(tmp_path, entries, texts, forms=None):
    path = tmp_path / "Lexique383.tsv"
    rows = ["ortho\tphon\tlemme\tcgram\tfreqfilms2\tfreqlivres"]
    n = len(entries)
    for i, lemma in enumerate(entries):
        for form in (forms or {}).get(lemma, [lemma]):
            rows.append(f"{form}\t-\t{lemma}\tNOM\t{n - i}\t{n - i}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    lex = lx.Lexicon(language="fr")
    bounds = [(1, 0, len(texts))]
    return fq.compute(lex, texts, bounds, "French", "", str(path))


def test_a_spelling_takes_the_rank_of_the_headword_it_belongs_to(tmp_path, monkeypatch):
    monkeypatch.setattr(fq, "RARE_FLOOR", 2)
    monkeypatch.setattr(fq, "RARE_MULTIPLE", 0)
    # "toilettes" is a form of the common "toilette"; a lemmatizer that turns
    # it into a rare verb must not get it flagged.
    entries = ["toilette", "a", "b", "c", "toiletter"]
    prof = _ranked_profile(
        tmp_path, entries, ["toilettes"], {"toilette": ["toilette", "toilettes"]}
    )
    assert prof.rare_count == 0


def test_hyphenated_compounds_and_phrase_words_are_not_rare(tmp_path, monkeypatch):
    monkeypatch.setattr(fq, "RARE_FLOOR", 3)
    monkeypatch.setattr(fq, "RARE_MULTIPLE", 0)
    entries = ["quatre", "vingt", "parce que", "x1", "x2", "x3", "quatre-vingt"]
    prof = _ranked_profile(tmp_path, entries, ["quatre-vingt parce que"])
    assert prof.rare_count == 0


def _cloze_deck(notes):
    from anki_deck_analyzer.apkg import Deck, Note, NoteType, Template

    front = '{{cloze:Text}}<hr/><div class="cloze_translation">{{Translation}}</div>'
    nt = NoteType(1, "Cloze", ["Text", "Translation"], [Template("Cloze", front, "")], "", True)
    rows = [Note(i, 1, list(fields), [], position=i) for i, fields in enumerate(notes)]
    return Deck("d", "d.apkg", rows, {1: nt}, card_count=len(rows))


def test_a_translation_beside_a_cloze_is_not_a_leak():
    deck = _cloze_deck([
        ("{{c1::En rangeant}}, j'ai retrouvé une photo.",
         '<span class="cloze-hint">While tidying up</span>, I found a photo.'),
    ])
    checked, shown, _ = metrics._cloze_leaks(deck)
    assert (checked, shown) == (1, 0)


def test_a_cloze_answer_visible_on_the_front_is_counted():
    deck = _cloze_deck([
        ("Me agrada este {{c1::perfume}}.", "I like this perfume."),
        ("Je {{c1::suis}} là, oui je suis là.", "I am here."),
        ("Il {{c1::mange::mange}} une pomme.", "He eats an apple."),
        ("Elle {{c1::a}} un chat.", "She has a cat."),
    ])
    checked, shown, examples = metrics._cloze_leaks(deck)
    assert (checked, shown) == (3, 3)  # «a» is too short to check
    assert [where for _, _, where in examples] == [
        "field Translation", "the rest of Text", "its own hint",
    ]
    pack = build_pack(deck, metrics.compute(deck), DeckProfile())
    assert "Cloze answers visible on their own front: 3 of 3" in pack
    assert "[#1] «perfume» also shows in field Translation" in pack


def test_other_deletions_show_as_text_on_each_card():
    deck = _cloze_deck([("{{c1::Bonjour}}, {{c2::bonjour}} !", "Hello, hello!")])
    checked, shown, _ = metrics._cloze_leaks(deck)
    assert (checked, shown) == (2, 2)


def test_coverage_is_read_against_the_decks_own_size():
    prof = fq.FrequencyProfile(
        available=True, source="test", taught=1586,
        coverage={1000: (955, 1000), 2000: (1234, 2000)},

        arrival=[((501, 1000), 463, 0.67, 0.14), ((1001, 2000), 279, 0.54, 0.22)],
    )
    text = fq.table(prof)
    assert "with 1,586 words in all, it could cover at most 79%" in text
    assert "top 1,000: the deck teaches 955 of 1,000 (96%)\n" in text
    assert "463 of 500 taught (93%)" in text
    assert "not comparable" in text


def test_bands_of_similar_coverage_get_no_caution():
    prof = fq.FrequencyProfile(
        available=True, source="test", taught=5000,
        coverage={1000: (990, 1000)},
        arrival=[((1, 100), 100, 0.01, 1.0), ((101, 500), 395, 0.1, 0.9)],
    )
    assert "not comparable" not in fq.table(prof)


# --------------------------------------------------------------------------
# Arabic and Persian: marks, tatweel, the ZWNJ, and matching keys
# --------------------------------------------------------------------------

ZWNJ = "\u200c"


def test_vowelled_arabic_is_read_as_words_not_letters():
    assert tx.words("ذَهَبْتُ إِلَى الْمَدْرَسَةِ", "ar") == ["ذهبت", "إلى", "المدرسة"]
    assert tx.words("جـــميل", "ar") == ["جميل"]


def test_directional_marks_from_rtl_html_are_dropped():
    assert tx.words("\u200fمرحبا\u200e بك\u061c", "ar") == ["مرحبا", "بك"]


def test_a_zwnj_inside_a_persian_word_keeps_it_whole():
    assert tx.words(f"کتاب{ZWNJ}ها را می{ZWNJ}خواهم", "fa") == [
        f"کتاب{ZWNJ}ها", "را", f"می{ZWNJ}خواهم",
    ]
    assert tx.words(f"کتاب{ZWNJ}", "fa") == ["کتاب"]


def test_persian_spelling_variants_share_one_key():
    spellings = [f"می{ZWNJ}خوام", "میخوام", f"مي{ZWNJ}خوام"]
    assert len({tx.match_key(w, "fa") for w in spellings}) == 1
    # Arabic keeps ي and ى apart.
    assert tx.match_key("في", "ar") != tx.match_key("فى", "ar")


def test_persian_variants_count_as_one_word():
    lex = lx.Lexicon(language="fa")
    assert len(set(lex.words(f"می{ZWNJ}خوام میخوام مي{ZWNJ}خوام"))) == 1
    assert ZWNJ in lex.shown(f"می{ZWNJ}خوام")


def test_other_languages_tokenize_as_before():
    import unicodedata

    assert tx.words("aujourd'hui jusqu'à", "fr") == ["aujourd'hui", "jusqu'à"]
    assert tx.words("Die Straße", "de") == ["die", "straße"]
    nfd = unicodedata.normalize("NFD", "Tiếng Việt rất hay")
    toks = tx.words(nfd, "vi")
    assert [unicodedata.normalize("NFC", t) for t in toks] == ["tiếng", "việt", "rất", "hay"]
    assert toks[0] == unicodedata.normalize("NFD", "tiếng")  # marks kept, not stripped


@pytest.mark.skipif(not _has("simplemma"), reason="simplemma not installed")
def test_persian_lemmatizer_sees_the_zwnj_form():
    lex = lx.build(["کتاب"], "Persian")
    assert lex.method == lx.METHOD_LEMMA
    assert lex.words(f"کتاب{ZWNJ}ها") == ["کتاب"]


def test_arabic_and_persian_reports_state_their_limits():
    ar = lx.build(["ذهبت إلى المدرسة"], "Arabic").describe()
    assert "no capital letters" in ar
    assert "والكتاب" in ar and "spelled form" in ar
    fa = lx.build(["کتاب"], "Persian").describe()
    assert "no capital letters" in fa
    prof = fq.FrequencyProfile(available=True, source="test", language="ar")
    assert "Modern Standard Arabic" in fq.table(prof)


@pytest.mark.skipif(not _has("wordfreq"), reason="wordfreq not installed")
def test_deck_tokens_agree_with_wordfreq():
    import wordfreq

    for text, lang in (
        ("ذَهَبْتُ إِلَى الْمَدْرَسَةِ وَالْكِتَابُ جـــميل", "ar"),
        (f"کتاب{ZWNJ}ها را می{ZWNJ}خواهم و ميخواهم", "fa"),
    ):
        ours = [tx.match_key(w, lang) for w in tx.words(text, lang)]
        theirs = [tx.match_key(w, lang) for w in wordfreq.tokenize(text, lang)]
        assert ours == theirs


@pytest.mark.skipif(not _has("wordfreq"), reason="wordfreq not installed")
def test_a_persian_word_matches_the_list_with_or_without_zwnj():
    lex = lx.Lexicon(language="fa")
    texts = ["میخواهم", f"می{ZWNJ}خواهم"] + ["را"] * 8
    bounds = [(i + 1, i, i + 1) for i in range(10)]
    prof = fq.compute(lex, texts, bounds, "Persian")
    assert prof.available
    # One word, not two, and it is on the list either way.
    assert prof.taught == 2
    assert prof.unlisted == 0
    # The list's headwords keep their ZWNJ for display; the keys drop it.
    flist, _ = fq.load_list("wordfreq", lex)
    head, keys = next(e for e in flist.entries if ZWNJ in e[0])
    assert head.replace(ZWNJ, "") in keys


def test_an_inline_tag_the_stylesheet_makes_a_block_keeps_its_line():
    css = "/* titles */ .title .subtitle { display: block; font-size: 1rem; }\n.x b { color: red }"
    classes = tx.block_classes(css)
    assert classes == {"subtitle"}
    raw = 'Unit 4: Au marché<span class="subtitle">At the market</span>'
    assert tx.strip_html(tx.mark_inline_blocks(raw, classes)) == (
        "Unit 4: Au marché\nAt the market"
    )
    # Ordinary inline spans are untouched, so the comma stays put.
    hint = '<span class="cloze-hint">While tidying up</span>, I found'
    assert tx.strip_html(tx.mark_inline_blocks(hint, classes)) == "While tidying up, I found"
    # An inline style counts as well, and nested spans close at the right place.
    styled = 'a<span style="display:block">b <span>c</span> d</span>e'
    assert tx.strip_html(tx.mark_inline_blocks(styled, set())) == "a\nb c d\ne"


def test_a_mark_inside_a_mark_reads_as_one_example():
    raw = "<em>j'é<b>t</b>ait fatigué</em> ✗ → <em>j'étai<b>s</b> fatigué</em> ✓"
    assert tx.readable_field(raw) == "«j'é‹t›ait fatigué» ✗ → «j'étai‹s› fatigué» ✓"
    # Formatting wrapped around the whole mark is one mark, not two.
    assert tx.readable_field("<b><i>bonjour</i></b> !") == "«bonjour» !"


def test_a_pronoun_hyphenated_onto_a_verb_is_split_off():
    lex = lx.Lexicon(language="fr", method=lx.METHOD_LEMMA)
    lex._lemma = lambda w: {"tournez": "tourner", "dis": "dire", "donne": "donner"}.get(w, w)
    assert lex.words("Tournez-vous un peu.") == ["tourner", "vous", "un", "peu"]
    assert lex.words("Donne-le-moi, a-t-il dit") == ["donner", "le", "moi", "a", "il", "dit"]
    # A hyphenated word in its own right stays whole, elided or not.
    assert lex.words("un rendez-vous") == ["un", "rendez-vous"]
    assert "peut-être" in lex.words("peut-être")


def test_a_name_that_only_opens_sentences_is_found_in_the_translation():
    texts = ["Léa a beaucoup de choses à porter.", "Bonjour, ça va ?", "Super ! Merci."]
    beside = ["Léa has lots of things to carry.", "Hello, how are you?", "Great! Thanks."]
    names = lx.find_names(texts, "fr", beside)
    assert "léa" in names
    assert "bonjour" not in names and "super" not in names


def test_a_headword_counts_as_stated_once_the_example_is_measured():
    assert metrics.with_headwords({1: [5]}, {1: 0, 2: 0}, {1: 3, 2: 0}) == {1: [5, 0]}


def test_a_capital_quoted_in_commentary_is_not_a_name():
    texts = ["Inutile, monsieur.", "Dis-moi, Véronique.", "Celle-ci est grande."]
    beside = ["On its own, Inutile is short for c'est inutile.", "Dis-moi means 'tell me'.",
              "Celle-ci means 'this one'."]
    elsewhere = ["inutile", "je dis", "celle de Marie", "a nice day"]
    names = lx.find_names(texts, "fr", beside, elsewhere)
    assert not names & {"inutile", "dis-moi", "celle-ci"}


def test_subdecks_are_read_in_the_order_anki_studies_them():
    from anki_deck_analyzer.apkg import _deck_order

    names = [
        "Italian::01 Italian to English::02 Core 501-1500",
        "Italian::00 Start Here",
        "Italian::01 Italian to English::01 Core 1-500",
        "italian::01 italian to English::03 Core 1501-3000",
    ]
    assert sorted(names, key=_deck_order) == [
        "Italian::00 Start Here",
        "Italian::01 Italian to English::01 Core 1-500",
        "Italian::01 Italian to English::02 Core 501-1500",
        "italian::01 italian to English::03 Core 1501-3000",
    ]


# --------------------------------------------------------------------------
# Cleaned wordfreq lists
# --------------------------------------------------------------------------

from anki_deck_analyzer import wordfreq_patch as wp  # noqa: E402


def _patch(lang: str) -> dict:
    path, _ = wp.patch_path(lang)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.mark.skipif(not _has("wordfreq"), reason="wordfreq not installed")
def test_applying_a_patch_is_deterministic():
    patch = _patch("fr")
    assert wp.apply_patch(patch, top_n=3000) == wp.apply_patch(patch, top_n=3000)


@pytest.mark.skipif(not _has("wordfreq"), reason="wordfreq not installed")
def test_the_french_patch_repairs_and_removes():
    words = {w for w, _ in wp.apply_patch(_patch("fr"), top_n=5000)}
    assert "ça" in words
    assert "ca" not in words
    assert "the" not in words


@pytest.mark.skipif(not _has("wordfreq"), reason="wordfreq not installed")
def test_names_are_left_out_unless_kept():
    patch = _patch("fr")
    import wordfreq

    top = set(wordfreq.top_n_list("fr", 5000))
    name = next(n for n in patch["names"] if n in top and n not in patch["drop"])
    assert name not in {w for w, _ in wp.apply_patch(patch, top_n=5000)}
    assert name in {w for w, _ in wp.apply_patch(patch, top_n=5000, keep_names=True)}


@pytest.mark.skipif(not _has("wordfreq"), reason="wordfreq not installed")
def test_the_default_list_is_cleaned_and_raw_is_still_available():
    lex = lx.Lexicon(language="fr")
    cleaned, _ = fq.load_list("wordfreq", lex, "French")
    raw, _ = fq.load_list("wordfreq-raw", lex, "French")
    assert "cleaned (patch fr rev" in cleaned.source
    assert ", raw," in raw.source
    heads = {h for h, _ in cleaned.entries}
    assert "ça" in heads and "ca" not in heads
    assert "ca" in {h for h, _ in raw.entries}


@pytest.mark.skipif(not _has("wordfreq"), reason="wordfreq not installed")
def test_a_language_without_a_patch_falls_back_to_raw_with_a_note(monkeypatch):
    monkeypatch.setattr(wp, "patch_path", lambda lang: None)
    wp.cleaned.cache_clear()
    try:
        flist, _ = fq.load_list("wordfreq", lx.Lexicon(language="fr"), "French")
    finally:
        wp.cleaned.cache_clear()
    assert ", raw," in flist.source
    assert any("no cleaning patch for 'fr'" in n for n in flist.notes)
    prof = fq.FrequencyProfile(available=True, source=flist.source, notes=flist.notes)
    assert "no cleaning patch" in fq.table(prof)


def test_partial_and_weak_patches_say_so():
    assert "partial: no dictionary" in wp._coverage_note("fi", "partial (no dictionary)")
    assert "no usable dictionary" in wp._coverage_note("ta", "partial (dictionary unreadable: x)")
    assert "weak" in wp._coverage_note("hu", "full")
    assert wp._coverage_note("fr", "full") == ""


def test_every_shipped_patch_is_found():
    langs = {m.group(1) for n in os.listdir(wp.PATCH_DIR) if (m := wp._PATCH_RE.match(n))}
    assert {"fr", "de", "ar", "fa", "ko", "fi"} <= langs
    assert all(wp.patch_path(lang) for lang in langs)


# --------------------------------------------------------------------------
# Default lists per language
# --------------------------------------------------------------------------


def test_french_defaults_to_the_shipped_lexique_list():
    assert fq.default_list("fr") == "lexique"
    assert fq.default_list("es") == "wordfreq"
    assert fq.default_list(None) == "wordfreq"
    lex = lx.Lexicon(language="fr")
    flist, _ = fq.load_list(None, lex, "French")
    assert flist.source.startswith("Lexique 3.83")
    assert not flist.custom
    heads = [h for h, _ in flist.entries[:20]]
    assert "être" in heads and "avoir" in heads
    # The form table travels with the shipped list: "est" finds "être".
    assert "est" in flist.forms["être"]
    assert fq.load_list("lexique", lex, "French")[0].entries == flist.entries


def test_the_shipped_lexique_list_matches_the_tsv_reader(tmp_path):
    rows = [
        ("chat", "chat", "10", "20"), ("chats", "chat", "5", "5"),
        ("est", "être", "100", "90"), ("suis", "être", "40", "10"),
        ("est", "est", "1", "1"),
    ]
    tsv = tmp_path / "Lexique383.tsv"
    tsv.write_text(
        "ortho\tlemme\tfreqfilms2\tfreqlivres\n"
        + "".join("\t".join(r) + "\n" for r in rows),
        encoding="utf-8",
    )
    ranked = fq.read_lexique(str(tsv))
    assert ranked == [("être", ["est", "suis"]), ("chat", ["chat", "chats"]), ("est", ["est"])]
    with open(fq.LEXIQUE_DATA, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["version"] == "3.83" and "CC BY-SA" in data["license"]
    assert all(isinstance(forms, list) for _, forms in data["lemmas"][:100])


def test_lexique_is_refused_for_other_languages():
    flist, why = fq.load_list("lexique", lx.Lexicon(language="es"), "Spanish")
    assert flist is None and "French" in why


@pytest.mark.skipif(not _has("wordfreq"), reason="wordfreq not installed")
def test_wordfreq_can_still_be_asked_for_in_french():
    flist, _ = fq.load_list("wordfreq", lx.Lexicon(language="fr"), "French")
    assert flist.source.startswith("wordfreq")


def test_explanation_filter_without_a_target_list():
    """Chinese without jieba (Thai always): the target side cannot be looked
    up. English lines must still be set aside, and Chinese lines must not be
    mistaken for English through CJK noise on the English list."""
    pytest.importorskip("wordfreq")
    from anki_deck_analyzer import frequency as fq, lexicon as lx

    filt = fq.explanation_filter(lx.Lexicon(language="zh"), "English")
    filt.unavailable.add("zh")
    filt._zcache.clear()
    filt._explain_script = fq._script("the")
    assert filt.line_is_explanation(tx.words("She said she would come tomorrow", "zh"))
    assert not filt.line_is_explanation(tx.words("不得不", "zh"))
    assert not filt.line_is_explanation(tx.words("大不了", "zh"))


@pytest.mark.skipif(not (_has("simplemma") and _has("wordfreq")), reason="simplemma/wordfreq missing")
def test_dictionary_forms_keep_their_spelling():
    """casefold() turned ß into ss and final ς into σ in every report."""
    from anki_deck_analyzer import lexicon as lx

    de = lx.build(["Die Straße ist groß."], "German")
    assert "straße" in de.words("Die Straße ist groß.")
    assert "groß" in de.words("Die Straße ist groß.")
    el = lx.build(["Ο δρόμος είναι καλός."], "Greek")
    assert "δρόμος" in el.words("Ο δρόμος είναι καλός.")


@pytest.mark.skipif(not (_has("simplemma") and _has("wordfreq")), reason="simplemma/wordfreq missing")
def test_common_words_are_not_sent_to_a_rare_homograph():
    from anki_deck_analyzer import lexicon as lx

    sv = lx.build(["Jag är hemma."], "Swedish").words("Jag är hemma.")
    assert "jaga" not in sv and "ära" not in sv
    ro = lx.build(["Ca el, care e aici."], "Romanian").words("Ca el, care e aici.")
    assert "calciu" not in ro and "car" not in ro


@pytest.mark.skipif(not (_has("simplemma") and _has("wordfreq")), reason="simplemma/wordfreq missing")
def test_romanian_hyphenated_clitics_are_split_like_the_list():
    from anki_deck_analyzer import lexicon as lx

    text = "Ce s-a întâmplat? N-am timp, scuzați-mă."
    got = lx.build([text], "Romanian").words(text)
    assert "nu" in got and "sud" not in got and "nord" not in got
    assert not any("-" in w for w in got)


def test_korean_without_mecab_skips_the_frequency_list():
    from anki_deck_analyzer import frequency as fq, lexicon as lx

    if tx.korean_morphemes():
        pytest.skip("a Korean splitter is installed")
    flist, why = fq.load_list(None, lx.Lexicon(language="ko"), "Korean")
    assert flist is None and "kiwipiepy" in why


def test_korean_uses_morphemes_when_mecab_is_there(monkeypatch):
    fake = {"ok": True, "tool": "Kiwi", "fn": lambda text: ["학교", "에", "가", "ᆯ", "요", "."]}
    monkeypatch.setattr(tx, "_KO_STATE", fake)
    assert tx.words("학교에 가요.", "ko") == ["학교", "에", "가", "요"]


def test_pointed_hebrew_is_read_without_niqqud():
    assert tx.words("שְׁלוֹשֶׁת אֲלָפִים", "he") == ["שלושת", "אלפים"]


def test_greek_enclitic_accent_is_dropped():
    assert tx.words("τα μαθήματά μου", "el") == ["τα", "μαθήματα", "μου"]


@pytest.mark.skipif(not (_has("simplemma") and _has("wordfreq")), reason="simplemma/wordfreq missing")
def test_a_top_word_never_gets_an_unlisted_dictionary_form():
    from anki_deck_analyzer import lexicon as lx

    pl = lx.build(["Daj mnie spokój."], "Polish").words("Daj mnie spokój.")
    assert "miąć" not in pl and "mnie" in pl


def test_thai_text_is_marked_unsegmented():
    from anki_deck_analyzer import lexicon as lx

    lex = lx.build(["ผมไปโรงเรียนทุกวัน", "ฉันชอบกินข้าวผัด"], "Thai")
    assert lex.unsegmented
    assert "without spaces between words" in lex.describe()
    assert not lx.build(["Je vais à l'école."], "French").unsegmented


def test_a_raw_list_keeps_only_its_own_script():
    from anki_deck_analyzer import frequency as fq

    words = ["학교"] * 150 + ["the", "a", "친구", "of"]
    kept, n = fq._own_script_only(words)
    assert n == 3 and "the" not in kept and "친구" in kept
    latin = ["casa", "the", "perro"] * 70
    assert fq._own_script_only(latin) == (latin, 0)


def test_web_fragments_and_roman_numerals_leave_the_list():
    from anki_deck_analyzer import frequency as fq

    flist = fq.FrequencyList(
        source="t", unit="word forms",
        entries=[(w, frozenset({w})) for w in ["http", "xix", "vi", "mi", "casa"]],
    )
    kept, dropped = fq._clean(flist, frozenset())
    assert [h for h, _ in kept] == ["vi", "mi", "casa"]
    assert dropped == {"web addresses and Roman numerals": 2}


def test_a_typed_answer_card_is_production():
    """Front: the translation and a box to type the target in (Anki's reverse
    card with {{type:Target}}). The box is the answer, not text to read."""
    share, detail = scoring.production_share(
        *_recall_deck([["Target"], ["English", "type:Target"]])
    )
    assert share == pytest.approx(1.0)


def test_an_unused_note_type_does_not_count():
    """A deck carrying Anki's stock Cloze type with no notes has no cloze."""
    from anki_deck_analyzer.apkg import NoteType, Template

    deck, _, _ = _recall_deck([["Target"]])
    deck.notetypes[2] = NoteType(2, "Cloze", ["Text"], [Template("C", "{{cloze:Text}}", "")], "", True)
    m = metrics.compute(deck, {1: 0})
    assert not m.has_cloze


def test_html_report_explains_the_fingerprint_and_cleans_quotes(deck_path):
    from anki_deck_analyzer.analyze import Provenance

    deck, profile, m, result = _rendered(deck_path)
    judged = next(s for s in result.scores if s.assessed and not s.criterion.measured)
    judged.evidence = [{"quote": "Io sono Sara. <first seen here: 2 of 3>", "position": 4, "shows": "x"}]
    prov = Provenance("1.7.0", "claude-sonnet-5-5", "high", 1, 7, "ab" * 32, ["a"], True)
    out = report.render_html(deck, profile, m, result, "m", prov)
    assert "fingerprint" in out and "abababababababab…" in out
    assert "judged once" in out
    assert "first seen here" not in out.split("Criterion by criterion")[-1]


def test_a_layout_table_is_not_a_grammar_table():
    paradigm = "<table>" + "".join(
        f"<tr><td>{a}</td><td>{b}</td></tr>" for a, b in [("eu", "sunt"), ("tu", "ești"), ("el", "este")]
    ) + "</table>"
    layout = "<TABLE><TR><TD><IMG src=a.png></TD><TD>a long definition with many words in it</TD></TR></TABLE>"
    assert tx.has_paradigm_table(paradigm)
    assert not tx.has_paradigm_table(layout)


def test_text_to_speech_counts_only_in_the_target_language():
    assert not tx.has_target_tts("{{tts en_US voices=Apple_Samantha:Front}}", "ar")
    assert tx.has_target_tts("{{tts ar_SA:Back}}", "ar")
    assert tx.has_target_tts("{{tts en_US:Front}}", None)


def test_no_source_file_holds_a_stray_backspace():
    """An escaped \b written through a non-raw string becomes a backspace
    character, which silently breaks a regex."""
    import glob

    root = os.path.join(os.path.dirname(__file__), "..", "src", "anki_deck_analyzer")
    for path in glob.glob(os.path.join(root, "*.py")):
        with open(path, encoding="utf-8") as fh:
            assert "\x08" not in fh.read(), path
