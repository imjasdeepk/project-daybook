#!/usr/bin/env bash
#
# project-daybook installer.
#
#   curl -fsSL https://raw.githubusercontent.com/imjasdeepk/project-daybook/main/install.sh | bash
#
# Prefer to read it first? That is the right instinct with any piped installer:
#
#   curl -fsSLO https://raw.githubusercontent.com/imjasdeepk/project-daybook/main/install.sh
#   less install.sh && bash install.sh
#
# Answer a couple of questions and you are recording. Everything can also be set
# up front, which skips all prompts:
#
#   DAYBOOK_DIR=~/Documents/daybook DAYBOOK_CURRENCIES=INR,USD DAYBOOK_BACKUP=git \
#     bash install.sh
#
set -euo pipefail

REPO_URL="${DAYBOOK_REPO:-https://github.com/imjasdeepk/project-daybook.git}"
INSTALL_DIR="${DAYBOOK_INSTALL_DIR:-$HOME/project-daybook}"
DAYBOOK_DIR="${DAYBOOK_DIR:-}"
DAYBOOK_CURRENCIES="${DAYBOOK_CURRENCIES:-}"
DAYBOOK_BACKUP="${DAYBOOK_BACKUP:-}"          # git | synced
DAYBOOK_GLOBAL_SKILL="${DAYBOOK_GLOBAL_SKILL:-}"
DAYBOOK_KEEP="${DAYBOOK_KEEP:-}"              # diary | ledger | both

bold=$(tput bold 2>/dev/null || printf '')
dim=$(tput dim 2>/dev/null || printf '')
red=$(tput setaf 1 2>/dev/null || printf '')
green=$(tput setaf 2 2>/dev/null || printf '')
reset=$(tput sgr0 2>/dev/null || printf '')

say()  { printf '%s\n' "$*"; }
step() { printf '\n%s==>%s %s%s\n' "$green" "$reset" "$bold" "$*$reset"; }
note() { printf '    %s%s%s\n' "$dim" "$*" "$reset"; }
die()  { printf '\n%serror:%s %s\n' "$red" "$reset" "$*" >&2; exit 1; }

# When this script is piped into bash, stdin is the script itself, so prompts
# have to come from the terminal directly. If there is no terminal, run
# unattended using the defaults and any variables that were set.
if [ -t 0 ]; then TTY=/dev/stdin
elif [ -e /dev/tty ] && (exec 3<>/dev/tty) 2>/dev/null; then TTY=/dev/tty
else TTY=""; fi

ask() { # ask <prompt> <default>
  local prompt="$1" default="$2" answer=""
  if [ -z "$TTY" ]; then printf '%s' "$default"; return; fi
  printf '%s [%s]: ' "$prompt" "$default" > /dev/tty
  IFS= read -r answer < "$TTY" || answer=""
  printf '%s' "${answer:-$default}"
}

confirm() { # confirm <prompt> <default y|n>
  local answer
  answer=$(ask "$1 (y/n)" "$2")
  case "$answer" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

say ""
say "${bold}project-daybook${reset}"
say "A daybook you talk to: a diary and a ledger, built so it cannot invent facts."

# --------------------------------------------------------------- prerequisites
step "Checking what you already have"

command -v git >/dev/null 2>&1 || die \
  "git is not installed. Install it from https://git-scm.com/downloads and run this again."
note "git $(git --version | awk '{print $3}')"

if ! command -v uv >/dev/null 2>&1; then
  # uv installs its own Python, which is why nothing here depends on the
  # Python you may or may not already have.
  say ""
  say "This needs ${bold}uv${reset}, which manages Python for you."
  if [ -n "$TTY" ] && ! confirm "    Install uv now from astral.sh?" "y"; then
    die "uv is required. See https://docs.astral.sh/uv/getting-started/installation/"
  fi
  curl -fsSL https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 \
    || die "Could not install uv. Install it by hand, then run this again."
  for candidate in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
    [ -x "$candidate/uv" ] && export PATH="$candidate:$PATH"
  done
  command -v uv >/dev/null 2>&1 \
    || die "uv installed but is not on your PATH. Open a new terminal and run this again."
fi
note "uv $(uv --version | awk '{print $2}')"

# ------------------------------------------------------------------- the tool
step "Getting the tool"
if [ -d "$INSTALL_DIR/.git" ]; then
  note "Already at $INSTALL_DIR, updating it"
  git -C "$INSTALL_DIR" pull --ff-only --quiet \
    || note "Could not fast-forward; leaving your copy as it is."
elif [ -e "$INSTALL_DIR" ]; then
  die "$INSTALL_DIR already exists and is not a git checkout. Move it, or set DAYBOOK_INSTALL_DIR."
else
  git clone --quiet "$REPO_URL" "$INSTALL_DIR" \
    || die "Could not clone $REPO_URL. If the repository is private, check your access."
  note "Cloned into $INSTALL_DIR"
fi

cd "$INSTALL_DIR"
note "Installing dependencies, which takes a minute the first time"
uv sync --quiet || die "uv sync failed. Run 'uv sync' in $INSTALL_DIR to see why."

step "Putting daybook on your PATH"
# The skills call bare `daybook`, so it has to resolve from any folder, not
# just $INSTALL_DIR. `uv tool install` puts a real entry point on PATH,
# independent of $INSTALL_DIR/.venv -- --force so re-running this script
# after an update replaces the old shim rather than refusing.
uv tool install --quiet --force "$INSTALL_DIR" \
  || die "Could not install the daybook command. Run 'uv tool install $INSTALL_DIR' to see why."
bin_dir=$(uv tool dir --bin 2>/dev/null || printf '%s' "$HOME/.local/bin")
DAYBOOK="$bin_dir/daybook"
[ -x "$DAYBOOK" ] || die "uv tool install said it succeeded, but $DAYBOOK is not there."
if command -v daybook >/dev/null 2>&1; then
  note "daybook -> $(command -v daybook)"
else
  # A brand new shim directory is not on PATH until the next shell starts, even
  # after `uv tool update-shell` -- so the rest of this script uses the shim's
  # full path directly rather than waiting on a PATH that will not exist yet.
  uv tool update-shell >/dev/null 2>&1 || true
  say ""
  say "    ${red}daybook was installed but is not on your PATH yet.${reset}"
  say "    Add it, then open a new terminal:"
  say ""
  say "        ${bold}export PATH=\"$bin_dir:\$PATH\"${reset}"
  say ""
fi

# ---------------------------------------------------------------- your records
step "What do you want to keep?"
say "    ${bold}diary${reset}   a journal and knowledge base, in plain Markdown"
say "    ${bold}ledger${reset}  money lent, interest, birthdays -- Beancount underneath"
say "    ${bold}both${reset}    the default: one folder, either kind of entry"
[ -z "$DAYBOOK_KEEP" ] && DAYBOOK_KEEP=$(ask "    Choice" "both")
case "$DAYBOOK_KEEP" in diary|ledger|both) ;; *) die "DAYBOOK_KEEP must be diary, ledger or both, not '$DAYBOOK_KEEP'." ;; esac
WANT_LEDGER="yes"; WANT_NOTES="yes"
[ "$DAYBOOK_KEEP" = "diary" ] && WANT_LEDGER="no"
[ "$DAYBOOK_KEEP" = "ledger" ] && WANT_NOTES="no"

step "Where should your records live?"
say "    Any folder. It is yours, and nothing you record is ever stored with the tool."
[ -z "$DAYBOOK_DIR" ] && DAYBOOK_DIR=$(ask "    Folder" "$HOME/Documents/daybook")
DAYBOOK_DIR="${DAYBOOK_DIR/#\~/$HOME}"

step "How do you want them backed up?"
say "    ${bold}synced${reset}  put the folder in Google Drive, Dropbox or iCloud"
say "    ${bold}git${reset}     make it a private git repository, with every entry committed"
[ -z "$DAYBOOK_BACKUP" ] && DAYBOOK_BACKUP=$(ask "    Choice" "synced")

if [ "$WANT_LEDGER" = "yes" ]; then
  [ -z "$DAYBOOK_CURRENCIES" ] && DAYBOOK_CURRENCIES=$(ask "
$(printf '%s' "    Which currencies? Comma separated")" "USD")

  INIT_ARGS=("$DAYBOOK_DIR" "--currencies" "$DAYBOOK_CURRENCIES")
  [ "$DAYBOOK_BACKUP" = "git" ] && INIT_ARGS+=("--git")

  step "Creating your ledger"
  if [ -f "$DAYBOOK_DIR/main.beancount" ]; then
    note "A ledger already exists at $DAYBOOK_DIR, keeping it"
    printf '%s\n' "$DAYBOOK_DIR" > "$INSTALL_DIR/.daybook-root"
  else
    "$DAYBOOK" init "${INIT_ARGS[@]}" >/dev/null || die "Could not create the ledger."
    note "Created $DAYBOOK_DIR"
  fi
  "$DAYBOOK" check >/dev/null || die "The new ledger did not validate."
  note "Validated"
elif [ "$DAYBOOK_BACKUP" = "git" ] && [ ! -d "$DAYBOOK_DIR/.git" ]; then
  mkdir -p "$DAYBOOK_DIR"
  (cd "$DAYBOOK_DIR" && git init -b main --quiet)
  note "Made $DAYBOOK_DIR a private git repository"
fi

if [ "$WANT_NOTES" = "yes" ]; then
  step "Setting up your diary"
  if [ -f "$DAYBOOK_DIR/notes/notes.toml" ]; then
    note "Notes already set up at $DAYBOOK_DIR/notes, keeping them"
  else
    "$DAYBOOK" note init "$DAYBOOK_DIR/notes" --period week >/dev/null \
      || die "Could not create the notes folder."
    note "Created $DAYBOOK_DIR/notes for your diary and knowledge base"
  fi
fi

# ------------------------------------------------------- use it from anywhere
# The skill's reach and the root pointer's reach are one choice, not two: a
# skill that can trigger somewhere the pointer can't see just fails with "no
# ledger found" there, and a pointer nothing can trigger from is dead weight.
# "Any folder" ties both to $HOME; "just this one" ties both to $DAYBOOK_DIR,
# which is the folder you actually open in Claude Code -- not $INSTALL_DIR,
# unless the two happen to be the same folder.
if [ -z "$DAYBOOK_GLOBAL_SKILL" ]; then
  if [ -n "$TTY" ] && confirm "
    Use daybook from any folder, not just this one?" "y"; then
    DAYBOOK_GLOBAL_SKILL=yes
  else
    DAYBOOK_GLOBAL_SKILL=no
  fi
fi
WANTED_SKILLS=()
[ "$WANT_LEDGER" = "yes" ] && WANTED_SKILLS+=("ledger")
[ "$WANT_NOTES" = "yes" ] && WANTED_SKILLS+=("notes")

link_skills() { # link_skills <skills-dir>
  local skills_dir="$1"
  mkdir -p "$skills_dir"
  for skill in "${WANTED_SKILLS[@]}"; do
    rm -rf "$skills_dir/$skill"
    ln -s "$INSTALL_DIR/.claude/skills/$skill" "$skills_dir/$skill"
  done
}

# A record of where the tool itself lives, for the fallback the skills name
# when `daybook` is somehow still not on PATH: `uv run --project <this> daybook`.
write_tool_pointer() { # write_tool_pointer <dir>
  printf '%s\n' "$INSTALL_DIR" > "$1/.daybook-tool"
}

if [ "$DAYBOOK_GLOBAL_SKILL" = "yes" ]; then
  link_skills "$HOME/.claude/skills"
  # Found by walking up from any folder inside your home directory.
  [ "$WANT_LEDGER" = "yes" ] && printf '%s\n' "$DAYBOOK_DIR" > "$HOME/.daybook-root"
  [ "$WANT_NOTES" = "yes" ] && printf '%s\n' "$DAYBOOK_DIR/notes" > "$HOME/.daybook-notes-root"
  write_tool_pointer "$HOME"
  note "Linked ${WANTED_SKILLS[*]} into ~/.claude/skills and pointed them at your records"
elif [ "$(cd "$DAYBOOK_DIR" && pwd)" != "$(cd "$INSTALL_DIR" && pwd)" ]; then
  # DAYBOOK_DIR is the folder you'll actually open -- give it its own skills
  # rather than leaving them stranded in $INSTALL_DIR, which nothing points at
  # from there. project_root()'s cwd fallback needs no pointer file for this:
  # DAYBOOK_DIR already holds main.beancount directly.
  link_skills "$DAYBOOK_DIR/.claude/skills"
  [ "$WANT_NOTES" = "yes" ] && printf '%s\n' "$DAYBOOK_DIR/notes" > "$DAYBOOK_DIR/.daybook-notes-root"
  write_tool_pointer "$DAYBOOK_DIR"
  note "Linked ${WANTED_SKILLS[*]} into $DAYBOOK_DIR/.claude/skills -- only that folder"
fi

# ------------------------------------------------------------------ finish up
step "Done"
say ""
say "    Records   ${bold}$DAYBOOK_DIR${reset}"
say "    Tool      ${bold}$INSTALL_DIR${reset}"
say "    Command   ${bold}$(command -v daybook || printf 'not on PATH yet, see above')${reset}"
say ""
say "  Open ${bold}$DAYBOOK_DIR${reset} in Claude Code or Claude Cowork and just talk to it:"
say ""
if [ "$WANT_NOTES" = "yes" ]; then
say "      ${dim}note that we cut over the ingest pipeline this morning${reset}"
say "      ${dim}what did I say about the rollback?${reset}"
say "      ${dim}what did I do last week?${reset}"
fi
if [ "$WANT_LEDGER" = "yes" ]; then
say "      ${dim}lent dad 5000 rupees for the car last tuesday${reset}"
say "      ${dim}how much does dad owe me?${reset}"
say "      ${dim}dad's birthday is 14 March 1962${reset}"
say "      ${dim}whose birthdays are coming up?${reset}"
fi
say ""
say "  Or use it directly:  ${bold}daybook --help${reset}"
if [ "$DAYBOOK_BACKUP" = "git" ]; then
say ""
say "  Your records are a git repository. To keep an off-machine copy, add a"
say "  ${bold}private${reset} remote and then run ${bold}daybook sync${reset}:"
say ""
say "      ${dim}cd $DAYBOOK_DIR${reset}"
say "      ${dim}gh repo create <you>/my-daybook --private --source . --remote origin --push${reset}"
fi
say ""
