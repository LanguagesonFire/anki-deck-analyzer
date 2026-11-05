"""The scoring framework.

Weights and descriptions follow "How to choose an Anki deck you'll actually
use" (Languages on Fire). Five fundamentals carry 80% of the score because
they concern the learning material itself; the four nice-to-haves carry 20%
because they improve the experience without rescuing weak fundamentals.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Criterion:
    key: str
    title: str
    weight: float
    group: str
    #: What the criterion is asking, in the article's terms.
    definition: str
    #: What a low, middling and high score look like.
    rubric: str
    #: Which parts of the evidence pack answer it.
    evidence: str
    #: True when the tool measures this itself instead of asking the model.
    measured: bool = False


FUNDAMENTAL = "fundamental"
NICE_TO_HAVE = "nice-to-have"


CRITERIA: tuple[Criterion, ...] = (
    Criterion(
        key="progression",
        title="Gradual progression and comprehensibility",
        weight=0.20,
        group=FUNDAMENTAL,
        definition=(
            "New sentences should mostly consist of language the learner already "
            "understands, with a small amount of new material at a time — Krashen's "
            "i+1. Difficulty should rise gradually rather than jumping."
        ),
        rubric=(
            "1-3: no ordering principle: cards in no discernible order of difficulty "
            "or usefulness; sentences routinely drop several unknown words and "
            "unintroduced structures at once; difficulty does not build.\n"
            "4-6: a broad sense of order, but frequent jumps, or long stretches where "
            "difficulty is flat and then spikes.\n"
            "7-8: a clear build, most cards introduce one new element, occasional jumps.\n"
            "9-10: consistent i+1 — new material is nearly always a single step beyond "
            "what earlier cards taught; the new-word rate falls over the opening and "
            "then holds at a stable, low level.\n"
            "Word cards: a card that teaches one word introduces one new item, which "
            "is i+1 at card level, not a flaw. A word list's high new-word rate is "
            "its format, so never score it down for that. Judge its word cards on "
            "their order: basics first, a sensible frequency or difficulty order, no "
            "rare words early. Frequency order is a sound ordering principle. Judge "
            "every sentence, example sentences on word cards included, on what it "
            "brings in: on a word card the headword is the card's one new item, so "
            "an example that adds further unknown words is a progression fault. "
            "Where the pack reads the curve over sentences, use that reading and "
            "the i+1 share, not the all-notes rate. A deck of bare words with no "
            "sentences shows order but not comprehensibility, so it tops out at 8.\n"
            "Level tags: where the pack shows a study path by level tags that the "
            "default order ignores, score the path the deck's own tags support and "
            "say which one you scored. A learner has to filter or suspend by tag to "
            "follow it; note that, and take off at most one point for the setup.\n"
            "Reading the curve: any deck that keeps teaching levels off at a non-zero "
            "new-word rate, so a falling rate early followed by a steady low rate is "
            "the shape to expect, not a flaw. Penalize spikes and late rises, not a "
            "plateau."
        ),
        evidence=(
            "the format line, the new-word curve and the sentence describing its "
            "shape, the i+1 share over sentences, the study path by level tags where "
            "there is one, the opening sequence, the spread samples, and whether "
            "sentences in later parts reuse earlier vocabulary"
        ),
    ),
    Criterion(
        key="sentences",
        title="Sentence quality and usefulness",
        weight=0.20,
        group=FUNDAMENTAL,
        definition=(
            "Sentences should be natural, level-appropriate, useful for understanding "
            "the target word or structure, and representative of how the language is "
            "really used. A sentence can be grammatically correct and still be a poor "
            "learning sentence. Sentences that are a meaningful part of the learning "
            "count for more than sentences used as decoration under isolated vocabulary."
        ),
        rubric=(
            "1-3: machine-sounding, absurd or corpus-scraped sentences; obscure "
            "vocabulary; situations no learner will meet; or no sentences at all where "
            "the deck claims to teach them.\n"
            "4-6: serviceable but stilted, repetitive, or wildly uneven in usefulness.\n"
            "7-8: natural and mostly useful, with some filler.\n"
            "9-10: sentences a native speaker would actually say, pitched at the right "
            "level, each one earning its place in the learner's memory.\n"
            "Level-appropriate means pitched for the level the deck says it teaches "
            "and for the word or structure the sentence illustrates. A natural "
            "sentence far harder than the word it teaches (a bureaucratic, "
            "specialized sentence to illustrate an everyday word), or than the "
            "deck's stated level, is a weakness here; when such sentences are "
            "common in the sample, that is a 5-6 problem however natural they are. "
            "Where a sentence sits in the deck's order is the progression "
            "criterion's job: do not score its position here.\n"
            "Judge the sentences the deck teaches as correct. The wrong options on a "
            "multiple-choice card and the mistaken version on an error-correction "
            "card are wrong on purpose; they say nothing about naturalness, so leave "
            "them out of this score."
        ),
        evidence="the random sample and the spread samples, read as sentences",
    ),
    Criterion(
        key="vocabulary",
        title="Vocabulary selection and frequency",
        weight=0.15,
        group=FUNDAMENTAL,
        definition=(
            "High-frequency vocabulary gives more value early on, but frequency should "
            "not be followed mechanically: a pedagogically important word can deserve to "
            "come earlier than its ranking. The question is whether the deck teaches "
            "useful vocabulary in a sensible order."
        ),
        rubric=(
            "1-3: obscure or specialized words before basic everyday vocabulary; no "
            "discernible ordering principle.\n"
            "4-6: roughly frequency-driven but with odd early entries, or sensible words "
            "in an order that ignores what a beginner needs first.\n"
            "7-8: useful words early, sensible order, a few questionable choices.\n"
            "9-10: the first few hundred words are the ones a learner will meet most, "
            "with pedagogical exceptions that clearly earn their place.\n"
            "Where the pack gives a frequency list, judge frequency from it, not from "
            "your own sense of which words are common: quote its coverage figures and "
            "ranks. A word is only rare for its position if the list says so. Where "
            "the pack says no list was used, say that your frequency judgments are "
            "unchecked and let it lower your confidence.\n"
            "Level and exam lists: a deck ordered by a recognized level list (CEFR "
            "levels, the Goethe or DELE word lists, JLPT, HSK) has an ordering "
            "principle, so its order alone does not put it in the 1-3 band. Its "
            "score is still read off the general frequency list, which these lists "
            "track closely, so a good one measures well on its own. Frequency order "
            "gets a general learner further sooner, so a deck ordered by a level "
            "list rather than by frequency scores at most 8. Say in the verdict "
            "that the deck follows its list, and that for that exam the order is "
            "the right one. A topic or travel deck with no level structure is "
            "judged like any other deck.\n"
            "Where the pack shows a study path by level tags, the frequency "
            "comparison along that path counts as the deck's order."
        ),
        evidence=(
            "the frequency-list comparison (coverage, arrival of the most common "
            "words, new words per tenth by rank, and words rare for their position), "
            "the same comparison along a level-tag study path where there is one, "
            "the opening sequence and the cumulative vocabulary counts"
        ),
    ),
    Criterion(
        key="accuracy",
        title="Accuracy",
        weight=0.15,
        group=FUNDAMENTAL,
        definition=(
            "Target-language sentences, translations, gender and plural information, "
            "conjugations and declensions, grammar explanations, spelling and "
            "punctuation all need to be trustworthy. No large deck is perfect; frequent "
            "errors are a serious problem."
        ),
        rubric=(
            "1-3: errors in a large share of the sample — wrong translations, broken "
            "grammar, wrong gender, garbled encoding.\n"
            "4-6: a handful of real errors across the sample, or pervasive small "
            "sloppiness in punctuation and capitalization.\n"
            "7-8: occasional slips, nothing that would mislead a learner.\n"
            "9-10: nothing wrong found in the sample.\n"
            "Report errors you can actually point to. Do not invent an error rate."
        ),
        evidence="every sampled note, checked field by field",
    ),
    Criterion(
        key="grammar",
        title="Grammar handling",
        weight=0.10,
        group=FUNDAMENTAL,
        definition=(
            "As soon as a learner studies sentences they are learning grammar. New "
            "structures should build on ones already seen, and nearby sentences may "
            "reinforce the same pattern. Dedicated explanations, grammar cards or tables "
            "help but are not essential if the sentence sequence handles grammar well."
        ),
        rubric=(
            "1-3: structures appear at random; tenses and cases arrive with no "
            "preparation and no reinforcement.\n"
            "4-6: a loose progression, or good explanations undermined by a sentence "
            "order that ignores them.\n"
            "7-8: structures build in a recognizable order and recur enough to stick.\n"
            "9-10: a deliberate grammar syllabus, visible in the sequence itself, "
            "whether or not the deck has dedicated grammar cards."
        ),
        evidence=(
            "the opening sequence, the spread samples, grammar-flavored note types, "
            "tags and any explanation fields"
        ),
    ),
    Criterion(
        key="context",
        title="Context-based learning",
        weight=0.06,
        group=NICE_TO_HAVE,
        definition=(
            "Language is easier to hold on to when it belongs to something larger: a "
            "dialogue, a story, a lesson, a recurring situation, or an external source "
            "such as a textbook, video course or TV series. An isolated sentence beats "
            "an isolated word; a dialogue beats an isolated sentence."
        ),
        rubric=(
            "1-3: unconnected words or sentences with nothing tying them together.\n"
            "4-6: sentences grouped loosely by topic, or context asserted but not "
            "actually present on the cards.\n"
            "7-8: real recurring context — themed lessons, a source course, situations "
            "that return.\n"
            "9-10: a dialogue, story or course running through the deck and broken down "
            "into the cards, so each card sits inside something the learner follows."
        ),
        evidence="subdeck names, tags, note types, and whether samples connect to each other",
    ),
    Criterion(
        key="audio",
        title="High-quality audio",
        weight=0.06,
        group=NICE_TO_HAVE,
        definition=(
            "Clear audio on the cards is highly useful. Native-speaker recordings are "
            "excellent and good text-to-speech works well. This is a nice-to-have "
            "because audio can be added later with an add-on such as HyperTTS, "
            "while poor progression cannot be repaired that way."
        ),
        rubric=(
            "Scored from measured coverage. This tool reads the cards; it cannot listen "
            "to them, so it scores whether audio is there, not how good it sounds."
        ),
        evidence=(
            "the share of notes carrying audio: a [sound:] tag, an <audio> player, "
            "or a {{tts}} directive"
        ),
        measured=True,
    ),
    Criterion(
        key="design",
        title="Card design and study usability",
        weight=0.05,
        group=NICE_TO_HAVE,
        definition=(
            "A learner may see these cards every day for years. The card should make it "
            "immediately clear what is to be recalled, the answer should be easy to "
            "grade, and important information should be separated from optional detail. "
            "Templates are relatively fixable without touching the notes, so this counts "
            "less than the material."
        ),
        rubric=(
            "1-3: the prompt is ambiguous, the answer is already on the front, or the "
            "back is an undifferentiated wall of fields.\n"
            "4-6: it works, but the hierarchy is unclear or the card is cluttered.\n"
            "7-8: a clear prompt, a readable answer, sensible use of styling.\n"
            "9-10: every element earns its place; the recall task is unmistakable and "
            "the answer is easy to grade at a glance.\n"
            "The answer is the target-language text the card asks for. A meaning "
            "in the learner's language on the front is a cue to produce it, not "
            "the answer: a translation beside a cloze blank is no more a give-away "
            "than the English prompt on a reverse card. Count it as a give-away "
            "only where the hidden text itself is visible; the measurements say "
            "how often that happens across every cloze note.\n"
            "Judge the card the learner sees and grades. How the template source is "
            "written — its length, repeated markup, script size, fields it leaves "
            "unused — counts only where it changes what renders on the card or how "
            "it behaves. Scripts too long for this pack are left out of it, so their "
            "absence is not evidence that a card is hard to use."
        ),
        evidence="the card templates and stylesheet, read as HTML",
    ),
    Criterion(
        key="images",
        title="Images",
        weight=0.03,
        group=NICE_TO_HAVE,
        definition=(
            "Images make reviews more enjoyable and add a second route to meaning, "
            "especially for concrete vocabulary. They matter less for abstract words, "
            "grammar, and longer sentences, so they are a bonus rather than a "
            "requirement."
        ),
        rubric=(
            "Scored from measured coverage. This tool reads the cards; it cannot see "
            "the pictures, so it scores whether images are there, not what they show."
        ),
        evidence="the share of notes carrying an <img> reference",
        measured=True,
    ),
)

BY_KEY = {c.key: c for c in CRITERIA}

#: Features the article lists as helpful but deliberately leaves out of the
#: weighted score. Reported, never scored.
EXTRA_FEATURES = (
    ("subdecks", "Subdecks, so sections can be studied or skipped separately"),
    ("grammar_tables", "Grammar tables for conjugation or declension"),
    ("card_variety", "Card variety — cloze, vocabulary and grammar cards"),
    ("active_recall", "Production is selective, not asked of every card"),
    ("notes", "Short notes and explanations alongside the main card"),
)


def total_weight(keys: set[str] | None = None) -> float:
    if keys is None:
        return sum(c.weight for c in CRITERIA)
    return sum(c.weight for c in CRITERIA if c.key in keys)
