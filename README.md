# Anki Deck Analyzer

Point it at an `.apkg` file and it tells you whether the deck is worth your review time.

There are thousands of shared decks on AnkiWeb and the quality varies enormously. Some are real courses. Others are a frequency list poured into flashcards, with sentences nobody would say and no grammar progression at all. You usually find out three weeks in.

This tool reads the deck file, measures what can be measured, and has Claude read a structured sample of the cards. It scores the deck out of 100 against the nine criteria from [How to choose an Anki deck you'll actually use](https://languages-on-fire.com), weighted the way that article weights them.

**You need your own Anthropic API key.** The deck is read on your computer, but the assessment is done by Claude, so there is an API call and it costs money: **$0.40 to $0.70 per deck**, billed by Anthropic to your account. Nothing is charged by this tool.

```
Italian course
Italian · dialogue-based course · 4,029 notes · 5,110 cards

  81/100   Good
  Solid material with fixable gaps.

  The five non-negotiables   (80% of the score)
     8/10 ████████░░  Gradual progression and comprehensibility (20%)
     8/10 ████████░░  Sentence quality and usefulness (20%)
     8/10 ████████░░  Vocabulary selection and frequency (15%)
     6/10 ██████░░░░  Accuracy (15%)
     8/10 ████████░░  Grammar handling (10%)

  The four nice-to-haves   (20% of the score)
     9/10 █████████░  Context-based learning (6%)
     9/10 █████████░  High-quality audio (6%)
     8/10 ████████░░  Card design and study usability (5%)
    10/10 ██████████  Images (3%)
```

Every finding in the full report points at a specific card, so you can check it yourself.

## Quick start

You need three things:

1. **Python 3.9 or newer**, from [python.org/downloads](https://www.python.org/downloads/). On Windows, tick **"Add Python to PATH"** on the first screen of the installer.
2. **An Anthropic API key with credit on it.** Create an account at [console.anthropic.com](https://console.anthropic.com), add credit under **Billing** (the API is prepaid; a few dollars covers several decks), then create a key under **API keys**. It starts with `sk-ant-`.
3. **The deck as an `.apkg` file.** On [AnkiWeb's shared decks](https://ankiweb.net/shared/decks), sign in, open the deck and press **Download**. A deck you already have in Anki: **File → Export**, format **Anki Deck Package (.apkg)**.

Then unzip this folder somewhere with a short path, such as `Documents\anki-deck-analyzer`.

- **Windows:** drag the `.apkg` file onto **`analyze-deck.bat`**.
- **macOS:** double-click **`analyze-deck.command`**, then drag the `.apkg` file into the window that opens. The first time, macOS may refuse to open a file downloaded from the internet: right-click it and choose **Open**, or allow it under **System Settings → Privacy & Security**.

The first run sets itself up, which takes a few minutes, and asks for your key once. It saves the key in `api-key.txt` in the same folder; delete that file to change it. The report opens in your browser when the analysis is done (two to four minutes) and is saved next to the launcher as `<deck name> - report.html`.

On Linux, or if you prefer a terminal, see [Installing from the command line](#installing-from-the-command-line).

## What it scores

The five non-negotiables carry 80% because they concern the learning material itself, the part you cannot fix without rewriting the deck. The nice-to-haves carry 20% because they improve the experience and can mostly be added later.

| Criterion | Weight | How it's judged |
|---|---|---|
| Gradual progression and comprehensibility | 20% | measured curve + Claude |
| Sentence quality and usefulness | 20% | Claude |
| Vocabulary selection and frequency | 15% | measured against a frequency list + Claude |
| Accuracy | 15% | Claude |
| Grammar handling | 10% | Claude |
| Context-based learning | 6% | Claude |
| High-quality audio | 6% | measured coverage |
| Card design and study usability | 5% | Claude reads the templates |
| Images | 3% | measured coverage |

The grade (Excellent, Good, Mixed, Weak, Poor) is the solid part of the result. Each criterion is judged once, and that one reading is the score you see. Treat the number as accurate to about a point: the judged criteria are a model's reading, and running the same command again can move one of them by a point.

## What it cannot do

- **It cannot hear the audio or see the images.** Those two criteria are scored on whether the files are there, not on whether the recordings are any good. A deck with robotic text-to-speech and a deck with a studio-recorded native speaker score the same. Card types that never carry audio (announcements, grammar tables) don't count against the deck.
- **It reads a sample, not the deck.** About 90 notes out of however many the deck has. That is enough to catch a deck with no progression and to find recurring errors; it is not enough to certify that a 10,000-card deck is error-free.
- **The frequency list has a register.** The French list leans toward film subtitles and books, the Arabic one toward Modern Standard Arabic (so a dialect deck's everyday words show up as rare), and the Portuguese one toward Brazilian Portuguese (so *autocarro* and *comboio* rank lower than a learner in Lisbon hears them).
- **Some languages are measured less precisely.** Arabic, Persian, Hebrew, Korean and the Indian scripts have no capital letters, so names can't be told apart from vocabulary. Arabic and Hebrew are counted by spelled form: *والكتاب* and *الكتاب* are two words, and so are *ולא* and *לא*. Korean needs Kiwi installed to be compared with its frequency list (the launchers don't install it; see below). Thai, Lao, Khmer and Burmese are written without spaces between words, so their progression curve isn't measured and the model judges progression from the cards alone. Chinese and Japanese are counted per character and have no frequency comparison. The report states every one of these limits for the deck it covers.
- **The weighting is one opinion.** It reflects a particular approach to language learning, not a validated formula. A deck that scores 62 may be exactly right for you if the thing it's weak at is a thing you don't care about.

Use the number to compare decks, not to settle an argument.

## Installing from the command line

Open a terminal in the unzipped folder and create a private environment for the tool, so nothing else on your computer is affected:

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
pip install ".[lemma]"
```

```powershell
# Windows PowerShell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install ".[lemma]"
```

If PowerShell refuses to run `Activate.ps1`, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once and try again.

That gives you an `anki-deck-analyzer` command whenever the environment is active. The `[lemma]` part counts words by dictionary form, so *suis*, *es* and *sommes* are one verb rather than three new words; without it every spelling counts separately. For Korean decks, install `".[lemma,korean]"` instead, which adds Kiwi (about 150 MB). To add Kiwi to the launchers' setup, run `.venv\Scripts\pip install kiwipiepy` (Windows) or `.venv/bin/pip install kiwipiepy` (macOS) in this folder once.

Then set your key for the session:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."         # macOS / Linux
```

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."         # Windows PowerShell
```

To keep the key on Windows, run `setx ANTHROPIC_API_KEY "sk-ant-..."` once and open a new terminal.

## Using it

```bash
anki-deck-analyzer deck.apkg
```

That prints a summary in the terminal and writes the full report as a web page, `deck - report.html`, in the current folder. Open it in any browser; it's a single file you can keep or send to someone. For another format, name the file:

```bash
anki-deck-analyzer deck.apkg -o report.md       # Markdown
anki-deck-analyzer deck.apkg -o report.json     # every number, for scripting
```

Compare several decks in one go, with one report each:

```bash
anki-deck-analyzer german-a.apkg german-b.apkg german-c.apkg -o reports/
```

See every measurement, and exactly what would be sent to the API, without sending anything or spending money:

```bash
anki-deck-analyzer deck.apkg --dry-run
```

### Options

| Option | What it does |
|---|---|
| `-o PATH` | where to write the report; the format follows the extension (`.html`, `.md`, `.json`) |
| `--no-report` | terminal summary only, no report file |
| `--dry-run` | measure and print the evidence pack; no API call, no cost |
| `--draft` | a cheaper model at low effort, about $0.15 a deck. Fine for a first look, not for a score you'll quote |
| `--model`, `--effort` | choose the model (default `claude-sonnet-5-5`) and effort (`low` to `max`, default `high`). `--model claude-opus-5-5` is sharper at naming specific errors and costs about $0.70 to $1.10 a deck |
| `--repeat N` | judge each criterion N times instead of once and report the median and its range |
| `--only KEYS` | judge only these criteria (`progression,accuracy`). The run is marked partial and its total can't be compared with a full run |
| `--no-summary` | skip the closing verdict, saving one call |
| `--frequency-list LIST` | measure vocabulary against another list: `wordfreq`, `wordfreq-raw`, `lexique`, a Lexique 3 `.tsv`, or a `.json` list of headwords in rank order (marked custom in the report) |
| `--api-key KEY` | use this key instead of `ANTHROPIC_API_KEY` |
| `--workers N` | how many criteria to judge at once (default 4); use 1 if you hit rate limits |
| `-q` | no progress output |

A dry run doesn't know which field holds the target language, which the real run asks Claude to identify. These options supply it, so a dry run measures what a real run would: `--language French`, `--fields "Sentence=Front,Vocab=Word"` (the target-language field per note type), `--explanation-language Vietnamese` (the language of the translations, default English), `--explanation-types "Grammar,Info"` (note types that explain rather than teach) and `--lemma-fields "Sentence=Infinitive"` (fields that state a word's dictionary form).

### What a run costs

Nine API calls: one to identify the deck, seven to judge, one to write the verdict. The evidence pack is sent once at full price and read from cache by the other eight, so deck size barely moves the number. Bigger decks with wider note types cost a little more.

| | Roughly |
|---|---|
| `--dry-run` | free |
| `--draft` | $0.10 – $0.20 |
| default (`claude-sonnet-5-5`) | $0.40 – $0.70 |
| `--model claude-opus-5-5` | $0.70 – $1.10 |

The exact cost of a run is printed at the end of the terminal output and stored in the JSON report.

### Reproducing a score

Every report ends with the exact command that produced it, plus the analyzer version, the model, the sampling seed and the deck's SHA-256:

```
To reproduce this score
  anki-deck-analyzer "deck.apkg"
  analyzer 1.7.0 · sample seed 7 · words counted lemma:simplemma 1.2.0 · deck sha256 272314f187741604…
```

The sample is seeded, so the same deck always puts the same cards in front of the model. If the SHA-256 matches, you have the same file. Compare decks only on reports from the same analyzer version; [CHANGELOG.md](CHANGELOG.md) lists the changes that move scores.

## Where your deck goes

The deck file never leaves your computer. What gets sent to the Anthropic API is the evidence pack: the sampled card text, the card templates, and the measurements. No audio, no images, no media files. `--dry-run` prints the pack in full so you can read it before you send it.

## Troubleshooting

| What you see | What to do |
|---|---|
| `Python was not found` (Windows) | Reinstall Python and tick "Add Python to PATH", or open a new window after installing |
| `pip` or `anki-deck-analyzer` is not recognized | Activate the environment first (see [Installing](#installing-from-the-command-line)), or run `python -m pip` and `python -m anki_deck_analyzer` |
| `externally-managed-environment` | Your system Python won't take packages directly: use the environment steps above |
| `no Anthropic API key was found` | Set `ANTHROPIC_API_KEY`, or pass `--api-key` |
| `Anthropic rejected the API key` | Copy the key again from the console. With a launcher, delete `api-key.txt` and run it again |
| `credit balance is too low` | Add credit under Billing in the Anthropic console |
| A path or DLL error during install on Windows | Move the folder somewhere with a shorter path, delete `.venv`, and run again |
| The launcher's setup failed halfway | Delete the `.venv` folder next to it and run it again |
| The report says the deck was read in creation order | The deck has been studied, so its original new-card order is gone. Analyze the file as you downloaded it if you can |

## How the measurements work

**It measures the deck first.** Before any model sees anything, the tool walks the deck in the order you'd study it and counts how much unfamiliar language each card introduces. That produces a progression curve: the deck in ten equal parts, with the share of words in each part that had not appeared earlier. A deck that builds properly trends downward. A frequency list dressed up as a course does not.

```
part |  notes | new-word rate | sentences | <=1 new word | mean words | vocab so far
-----+--------+---------------+-----------+--------------+------------+-------------
   1 |    403 |         41.2% |       330 |        18.5% |        7.1 |        1,204
   5 |    403 |         14.8% |       351 |        52.4% |       10.3 |        4,890
  10 |    402 |          9.6% |       366 |        61.2% |       11.8 |        7,331
```

Decks that offer the same sentences in several recall directions (read it, hear it, produce it) are giving you a menu, not teaching the material three times, so the curve follows each piece of material the first time it appears. On a deck whose notes carry a headword and an example sentence, the curve reads the example, because progression is a property of the sentences you meet.

Names are left out of the count: a word only ever written with a capital, and capitalized at least once mid-sentence, is a character or a place. Grammar and announcement cards are kept out too, so grammar terminology is never read as vocabulary, and English lines inside a target-language field, such as a dialogue's stage directions, are dropped. When a card states a word's dictionary form in its own field, that word counts as taught.

**It holds the vocabulary against a frequency list**, one general-purpose list per language and the same for every deck: [wordfreq](https://pypi.org/project/wordfreq/)'s blend of subtitles, web text, Wikipedia and books, or for French, Lexique 3.83 (New & Pallier). wordfreq's French leans toward news and Wikipedia: *gouvernement*, *ministre* and *société* rank in its top 320, ahead of words a learner hears every day. Lexique ranks by film-subtitle and book frequency and assigns its own dictionary forms, so *mise* is never counted as the verb *miser*. The Languages on Fire French course was built with a Lexique-based list, so it starts with an advantage here; `--frequency-list wordfreq` measures any French deck, ours included, against the general list instead. The report says how many of the 1,000 and 2,000 most common words the deck teaches, how early they arrive, and which words are unusually rare for where they appear, so the model judges vocabulary from ranks instead of its own sense of which words are common. The wordfreq lists are cleaned first: foreign words from web pages, misspellings and junk are taken out by a patch built against a spelling dictionary, applied to your own wordfreq install. The report names the list and any limits of its cleaning.

**It counts what can be counted:** audio and image coverage, duplicate notes, cards per note, cloze types, subdecks, vocabulary size and mean sentence length. On a cloze deck it checks every deletion for a give-away, the hidden text showing up word for word elsewhere on the front.

**Then it samples the cards the way you would.** The article recommends looking at the beginning, the middle and the later parts of a deck. The tool takes the first 30 notes in study order, three consecutive runs from the 25%, 50% and 75% marks, the last 15, and a random spread from everywhere else, about 90 notes, with every field shown the way Anki lays it out, plus the templates and stylesheets of every note type.

**Then it judges one criterion at a time.** Seven separate calls, each looking at the same evidence with one question in front of it. Every finding has to point at a card.

## Using it from Python

```python
from anki_deck_analyzer import Analyzer, analyze

analysis = analyze("deck.apkg", Analyzer(model="claude-sonnet-5-5"))

print(analysis.result.total)          # 81.4
print(analysis.result.grade)          # "Good"
for score in analysis.result.scores:
    print(score.criterion.title, score.score, score.verdict)
```

Everything before the API calls works on its own, if all you want are the numbers:

```python
from anki_deck_analyzer import preview
from anki_deck_analyzer.metrics import curve_table

deck, metrics, pack = preview("deck.apkg")
print(curve_table(metrics))
print(metrics.audio_coverage, metrics.total_vocabulary)
```

## Deck formats

Reads `.apkg` and `.colpkg`, old and new: the plain `collection.anki2` layout that most AnkiWeb decks still use, and the zstd-compressed `collection.anki21b` layout modern Anki writes. Anki itself does not need to be installed.

## License

MIT, for the code. Two data sets ship under their sources' CC BY-SA 4.0 license: the French frequency list in `data/lexique383_lemmas.json`, derived from Lexique 3.83 (notice in `data/LICENSE-Lexique.txt`), and the cleaning patches in `data/wordfreq_patches/`, derived from wordfreq's word lists (notice in `data/wordfreq_patches/LICENSE.txt`). The framework it scores against is from "How to choose an Anki deck you'll actually use" by [Languages on Fire](https://languages-on-fire.com).
