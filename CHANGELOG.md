# Changes that affect scores

Every report prints the analyzer version it was made with. Compare two decks only on reports from the same version: the changes below move scores or measurements.

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
- "Grammar tables" needs a table that reads like a conjugation or declension table; a table used only for layout doesn't count.
- An HTML report is written by default, and the deck's SHA-256 is explained as a fingerprint of the file.
- `--publish` is gone from the options; `--model claude-opus-5-5` selects Opus. Old reproduce commands that use `--publish` still run.

## 1.6.0

French vocabulary is measured against Lexique 3.83 instead of wordfreq, and the other languages against a cleaned wordfreq list. A report from an earlier version reproduces with `--frequency-list wordfreq-raw`.

## 1.5.0

The scoring scale changed. Earlier versions told the judge that a 10 meant it "could not find a way to improve" the deck, which no deck can meet, so no judged criterion ever got one. From 1.5.0 a 10 means the criterion is fully met for the learner the deck is aimed at, and a point is only taken off for a weakness that changes what the learner learns or how easily they study. On seven decks of different kinds, scored both ways, that lifted totals by about five points and left their order the same.
