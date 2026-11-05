# Changes that affect scores

Every report prints the analyzer version it was made with. Compare two decks only on reports from the same version: the changes below move scores or measurements.

## 1.8.0

Word lists and tagged decks are scored for how they're meant to be studied.

- A word list is no longer marked down for working the way word lists work. On a card that teaches one word, the word is new by construction, which is i+1 at card level. The progression rubric dropped "frequency-list order" from its lowest band and judges word cards on their order instead: basics first, rare words late. A deck of bare words with no sentences tops out at 8 for progression.
- Example sentences on word cards are held to i+1. The headword is the card's one new item; an example that brings in any other unknown word fails, even when the headword is inflected differently in the example.
- The curve gets a column for the new-word rate in sentences alone. When single words make up a fifth of the deck or more, the curve's shape is read over the sentences. The i+1 share counts distinct new words, so a new word used twice in one sentence counts once.
- A headword or dictionary-form field now counts as taught for the cards after it, which lowers later new-word rates slightly on decks that have one.
- Level tags (d1–d4, CEFR A1–C2, JLPT N5–N1, HSK, beginner to advanced) are detected. When the default order ignores them, the deck is measured a second time, lowest level first; both curves are in the report and the judge sees the first notes of that path. Progression is scored on the path the deck's tags support, with at most a point off for the setup it takes.
- Frequency bands with three digits (Level001 to Level169) count as level tags when the tag says "level". With more than eight levels, the report summarizes them instead of listing each one.
- Sentence quality defines "level-appropriate": pitched for the deck's stated level and for the word the sentence teaches. A natural sentence far harder than its word is a weakness, and a 5–6 problem when the sample shows many of them. Where a sentence sits in the order is left to progression, so a bad order isn't penalized twice.
- A deck ordered by an exam or level list (Goethe, CEFR, JLPT, HSK) is not put in the bottom band for its order, and scores at most 8 for vocabulary.
- A supplement (phrase book, conjugation drill, alphabet deck, exam word list) gets a line under its total saying it was scored as a main course, and which criteria matter less for it. Weights and scores are unchanged by this.
- Character-counted decks (Chinese, Japanese) and Korean without Kiwi say next to the curve that its figures can't be compared with word-counted decks.
- The curve's description no longer says the rate "falls" when it rose.

## 1.7.0

How words are counted changed in several languages.

- Words keep their exact spelling in reports. 1.6 printed German *straße* as "strasse" and dropped the Greek final ς.
- A very common word is no longer reduced to a rare, unrelated dictionary form (Romanian *ca* to *calciu*, "calcium"; Swedish *jag* to *jaga*, "to hunt"; Polish *mnie* to *miąć*).
- Romanian clitics (*s-a*, *n-am*, *într-o*) are split the way the frequency list splits them.
- Hebrew vowel points are removed before matching, so a deck written with niqqud matches the list.
- Korean is split into morphemes when Kiwi is installed (`pip install ".[korean]"`), the way its frequency list is; without it, the Korean frequency comparison is skipped.
- Thai, Lao, Khmer, Burmese and Tibetan decks get no progression curve, since words can't be counted in a script without spaces.
- Web fragments (http, www) and Roman numerals (ii, xix) are dropped from every frequency list.
- Text-to-speech counts as audio only when it speaks the language being learned. A deck that reads its English side aloud no longer gets full audio coverage for it.
- A reverse card with a type-in box (`{{type:Field}}`) counts as production.
- Note types no note uses (Anki's stock Cloze or Basic) no longer count toward card variety or cloze.
- "Short notes and explanations" needs an explanation field (now including Remarks and Tip) that the notes fill in, on a note type the deck uses. "Subdecks" needs notes in at least two decks; one nested deck doesn't count.
- "Grammar tables" needs a table that reads like a conjugation or declension table; a table used only for layout doesn't count.
- An HTML report is written by default, and the deck's SHA-256 is explained as a fingerprint of the file.
- The five criteria that carry 80% of the score are called the fundamentals (they were the non-negotiables), and the JSON field `score.non_negotiables` is now `score.fundamentals`. Weights and scores are unchanged.
- `--publish` is gone from the options; `--model claude-opus-5-5` selects Opus. Old reproduce commands that use `--publish` still run.

## 1.6.0

French vocabulary is measured against Lexique 3.83 instead of wordfreq, and the other languages against a cleaned wordfreq list. A report from an earlier version reproduces with `--frequency-list wordfreq-raw`.

## 1.5.0

The scoring scale changed. Earlier versions told the judge that a 10 meant it "could not find a way to improve" the deck, which no deck can meet, so no judged criterion ever got one. From 1.5.0 a 10 means the criterion is fully met for the learner the deck is aimed at, and a point is only taken off for a weakness that changes what the learner learns or how easily they study. On seven decks of different kinds, scored both ways, that lifted totals by about five points and left their order the same.
