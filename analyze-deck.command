#!/bin/bash
# Double-click this file in Finder, or drop an .apkg onto it in a terminal.
cd "$(dirname "$0")" || exit 1

pause_close() { read -r -p "Press return to close. "; }

if ! command -v python3 >/dev/null 2>&1 \
   || ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
  echo "Python 3.9 or newer was not found."
  echo
  echo "Install it from https://www.python.org/downloads/ and run this again."
  pause_close
  exit 1
fi

# Install into a private environment the first time, and again whenever this
# folder holds a new version, so nothing else on the machine is touched.
WANT=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
HAVE=$(cat .venv/installed-version.txt 2>/dev/null)
[ -x ".venv/bin/anki-deck-analyzer" ] || HAVE=""
if [ "$HAVE" != "$WANT" ]; then
  echo "Setting up version $WANT. This happens once and takes a few minutes."
  [ -x .venv/bin/python ] || python3 -m venv .venv \
    || { echo "Could not create the environment."; pause_close; exit 1; }
  ./.venv/bin/python -m pip install --quiet --upgrade pip
  if ! ./.venv/bin/python -m pip install --quiet ".[lemma,chinese,japanese]"; then
    echo
    echo "The install failed. Check the internet connection and run this again."
    echo "If it keeps failing, delete the .venv folder here and try once more."
    pause_close
    exit 1
  fi
  printf '%s' "$WANT" > .venv/installed-version.txt
  echo "Done."
  echo
fi

if [ -z "$ANTHROPIC_API_KEY" ]; then
  if [ -f api-key.txt ]; then
    ANTHROPIC_API_KEY=$(tr -d '[:space:]' < api-key.txt)
  else
    echo "An Anthropic API key is needed. Create one at"
    echo "https://console.anthropic.com/settings/keys after adding credit under Billing."
    echo
    while :; do
      read -r -p "Paste your key here and press return: " ANTHROPIC_API_KEY
      ANTHROPIC_API_KEY=$(printf '%s' "$ANTHROPIC_API_KEY" | tr -d '[:space:]')
      [ -z "$ANTHROPIC_API_KEY" ] && { echo "No key given."; pause_close; exit 1; }
      case "$ANTHROPIC_API_KEY" in
        sk-ant-*) break ;;
        *) echo "That doesn't look like an Anthropic key: they start with sk-ant-. Try again." ;;
      esac
    done
    printf '%s' "$ANTHROPIC_API_KEY" > api-key.txt
    chmod 600 api-key.txt
    echo
    echo "Saved to api-key.txt in this folder. Delete that file to change it."
    echo
  fi
  export ANTHROPIC_API_KEY
fi

DECK="$1"
if [ -z "$DECK" ]; then
  read -r -p "Drag the .apkg file into this window and press return: " DECK
  # Finder and Terminal quote or escape a dragged path.
  DECK="${DECK%\'}"; DECK="${DECK#\'}"; DECK="${DECK%\"}"; DECK="${DECK#\"}"
  DECK="${DECK%"${DECK##*[![:space:]]}"}"
  DECK="${DECK//\\ / }"
fi
[ -z "$DECK" ] && { echo "No deck given."; pause_close; exit 1; }
[ -f "$DECK" ] || { echo "File not found: $DECK"; pause_close; exit 1; }

NAME=$(basename "$DECK"); NAME="${NAME%.*}"
REPORT="$(pwd)/$NAME - report.html"
./.venv/bin/anki-deck-analyzer "$DECK" -o "$REPORT"

echo
[ -f "$REPORT" ] && { echo "Opening the report..."; open "$REPORT"; }
pause_close
