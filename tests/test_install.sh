#!/usr/bin/env bash
#
# The installer's "update" command, against throwaway repositories.
#
# This is the one thing in the project that reaches into somebody else's git
# checkout, so the interesting cases are the refusals: every one of them has to
# stop *before* anything is fetched, moved or removed, leaving the machine
# exactly as it was found.  Each check below asserts the message and the state.
#
# install.sh is sourced rather than run, so update_checkout can be called on its
# own without the install steps that follow it.
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASS=0
FAIL=0

check() {
    local name="$1" ok="$2" detail="${3:-}"
    if [[ "$ok" == "1" ]]; then
        printf 'PASS  %s\n' "$name"
        PASS=$((PASS + 1))
    else
        printf 'FAIL  %s\n' "$name"
        printf '%s\n' "$detail" | sed 's/^/          /'
        FAIL=$((FAIL + 1))
    fi
}

yes_no() { [[ "$1" == "$2" ]] && echo 1 || echo 0; }
says()   { [[ "$1" == *"$2"* ]] && echo 1 || echo 0; }

# Sourcing runs the argument parsing with no arguments, which leaves the
# defaults, and defines every function.  main is guarded, so nothing installs.
# shellcheck source=/dev/null
source "$HERE/../install.sh"
# The installer sets -e, and every refusal below is a die() inside a command
# substitution — which is exactly the thing -e aborts on.  Half the point of
# this file is to reach the check after the one that failed.
set +e

check "update follows a branch of its own by default" \
      "$(yes_no "$UPDATE_BRANCH" "deploy")" "$UPDATE_BRANCH"
check "and --dev has one to switch to" "$(yes_no "$DEV_BRANCH" "dev")" "$DEV_BRANCH"

WORKSPACE="$(mktemp -d)"
trap 'rm -rf "$WORKSPACE"' EXIT

git init -q --bare "$WORKSPACE/remote.git"
git -C "$WORKSPACE/remote.git" symbolic-ref HEAD refs/heads/main
git clone -q "$WORKSPACE/remote.git" "$WORKSPACE/seed" 2>/dev/null
git -C "$WORKSPACE/seed" config user.email test@example.com
git -C "$WORKSPACE/seed" config user.name "Test"
echo one > "$WORKSPACE/seed/marker"
git -C "$WORKSPACE/seed" add -A
git -C "$WORKSPACE/seed" commit -qm "the first one"
git -C "$WORKSPACE/seed" branch -qM main
git -C "$WORKSPACE/seed" push -q origin main

git clone -q "$WORKSPACE/remote.git" "$WORKSPACE/work" 2>/dev/null
git -C "$WORKSPACE/work" config user.email test@example.com
git -C "$WORKSPACE/work" config user.name "Test"

# die() exits, so every refusal is run in a subshell and judged by its output.
attempt() {
    SOURCE_DIR="$1" update_checkout 2>&1
}
branch_of() {
    git -C "$WORKSPACE/work" symbolic-ref --quiet --short HEAD || echo "(detached)"
}

# --- the branch does not exist yet ------------------------------------------
# The first thing anybody will hit, so the message has to carry the fix.
output="$(attempt "$WORKSPACE/work")"
check "a missing deploy branch is refused" "$(says "$output" "Could not fetch 'deploy'")" "$output"
check "and the message says how to make it" \
      "$(says "$output" "git push origin HEAD:refs/heads/deploy")" "$output"
check "nothing moved" "$(yes_no "$(branch_of)" "main")" "$(branch_of)"

# --- it switches onto the deploy branch -------------------------------------
git -C "$WORKSPACE/seed" checkout -q -b deploy
echo two > "$WORKSPACE/seed/marker"
git -C "$WORKSPACE/seed" commit -qam "fit to run"
git -C "$WORKSPACE/seed" push -q origin deploy

output="$(attempt "$WORKSPACE/work")"
check "it moves the checkout onto deploy" "$(yes_no "$(branch_of)" "deploy")" "$output"
check "and says it is doing so" "$(says "$output" "Switching from main to deploy")" "$output"
check "the code arrives" "$(yes_no "$(cat "$WORKSPACE/work/marker")" "two")" \
      "$(cat "$WORKSPACE/work/marker")"
check "and it says what arrived" "$(says "$output" "fit to run")" "$output"
check "the branch tracks the remote afterwards" \
      "$(yes_no "$(git -C "$WORKSPACE/work" rev-parse --abbrev-ref '@{upstream}')" \
                "origin/deploy")" \
      "$(git -C "$WORKSPACE/work" rev-parse --abbrev-ref '@{upstream}' 2>&1)"

# --- nothing new ------------------------------------------------------------
output="$(attempt "$WORKSPACE/work")"
check "an unchanged checkout is still reinstalled" "$(says "$output" "Already up to date")" \
      "$output"

# --- it follows deploy, not the branch you were on --------------------------
git -C "$WORKSPACE/work" checkout -q main
echo three > "$WORKSPACE/seed/marker"
git -C "$WORKSPACE/seed" commit -qam "the third one"
git -C "$WORKSPACE/seed" push -q origin deploy

output="$(attempt "$WORKSPACE/work")"
check "wandering back to main does not change what update follows" \
      "$(yes_no "$(branch_of)" "deploy")" "$output"
check "and the commits on the branch left behind are still there" \
      "$(yes_no "$(git -C "$WORKSPACE/work" rev-parse --verify --quiet main > /dev/null \
                   && echo kept)" "kept")" \
      "main is gone"

# --- local changes ----------------------------------------------------------
echo four > "$WORKSPACE/seed/marker"
git -C "$WORKSPACE/seed" commit -qam "the fourth one"
git -C "$WORKSPACE/seed" push -q origin deploy
echo "mine" >> "$WORKSPACE/work/marker"

output="$(attempt "$WORKSPACE/work")"
check "a dirty checkout is refused" "$(says "$output" "local changes")" "$output"
check "and nothing was fetched over it" \
      "$(yes_no "$(head -1 "$WORKSPACE/work/marker")" "three")" \
      "$(head -1 "$WORKSPACE/work/marker")"
git -C "$WORKSPACE/work" checkout -q -- marker

# --- diverged ---------------------------------------------------------------
echo local > "$WORKSPACE/work/marker"
git -C "$WORKSPACE/work" commit -qam "a commit only this checkout has"
output="$(attempt "$WORKSPACE/work")"
check "a deploy branch with commits of its own is refused" \
      "$(says "$output" "Could not fast-forward deploy")" "$output"
check "and it says the installed copy is untouched" \
      "$(says "$output" "still the one running")" "$output"
check "and how to throw the local commits away" "$(says "$output" "reset --hard")" "$output"
git -C "$WORKSPACE/work" reset -q --hard HEAD~1

# --- a detached HEAD is not a problem any more ------------------------------
git -C "$WORKSPACE/work" checkout -q --detach HEAD
output="$(attempt "$WORKSPACE/work")"
check "a detached HEAD is simply moved onto deploy" "$(yes_no "$(branch_of)" "deploy")" "$output"

# --- not a checkout at all --------------------------------------------------
mkdir -p "$WORKSPACE/tarball"
output="$(attempt "$WORKSPACE/tarball")"
check "an unpacked tarball is told to use reinstall" \
      "$([[ "$output" == *"not a git checkout"* && "$output" == *"reinstall"* ]] \
        && echo 1 || echo 0)" "$output"

# --- --branch overrides it --------------------------------------------------
output="$(UPDATE_BRANCH=main attempt "$WORKSPACE/work")"
check "--branch picks a different one" "$(yes_no "$(branch_of)" "main")" "$output"

# --- --dev is the same command pointed one branch earlier -------------------
# 'dev' is where the work is pushed as it happens; 'deploy' is what somebody
# has decided is fit to run.  One flag between them, so it has to say which one
# it took — installing the unreviewed branch by accident is not a thing you
# notice until it breaks.
git -C "$WORKSPACE/seed" checkout -q -b dev
echo five > "$WORKSPACE/seed/marker"
git -C "$WORKSPACE/seed" commit -qam "nobody has decided about this one yet"
git -C "$WORKSPACE/seed" push -q origin dev

output="$(UPDATE_BRANCH=dev attempt "$WORKSPACE/work")"
check "--dev moves the checkout onto dev" "$(yes_no "$(branch_of)" "dev")" "$output"
check "and the code on it arrives" "$(yes_no "$(cat "$WORKSPACE/work/marker")" "five")" \
      "$(cat "$WORKSPACE/work/marker")"
check "it says this is not the deployed branch" \
      "$(says "$output" "Updating from 'dev', not 'deploy'")" "$output"
check "and what that costs you" "$(says "$output" "expected to be broken")" "$output"
check "the plain deploy update says none of that" \
      "$(yes_no "$(UPDATE_BRANCH=deploy attempt "$WORKSPACE/work" | grep -c "not 'deploy'")" \
                "0")" \
      "$(UPDATE_BRANCH=deploy attempt "$WORKSPACE/work")"

# --- it says which release you are getting ----------------------------------
# The whole reason to name a release: "am I updating to the one I actually
# need" is answerable from a word and not from three digits.
mkdir -p "$WORKSPACE/work/src/circle_to_search"
printf '__version__ = "9.9.9"\n\nRELEASE_NAME = "a-named-one"\n' \
    > "$WORKSPACE/work/src/circle_to_search/__init__.py"
check "the packaged version is read straight out of the source" \
      "$(yes_no "$(SOURCE_DIR="$WORKSPACE/work" packaged_version)" '9.9.9 “a-named-one”')" \
      "$(SOURCE_DIR="$WORKSPACE/work" packaged_version)"
printf '__version__ = "9.9.9"\n' > "$WORKSPACE/work/src/circle_to_search/__init__.py"
check "a release with no name is just its number" \
      "$(yes_no "$(SOURCE_DIR="$WORKSPACE/work" packaged_version)" "9.9.9")" \
      "$(SOURCE_DIR="$WORKSPACE/work" packaged_version)"
rm -rf "$WORKSPACE/work/src"
check "and nothing at all where there is no source to read" \
      "$(yes_no "$(SOURCE_DIR="$WORKSPACE/work" packaged_version)" "")" \
      "$(SOURCE_DIR="$WORKSPACE/work" packaged_version)"
check "the real tree names its own release" \
      "$(says "$(SOURCE_DIR="$HERE/.." packaged_version)" '“')" \
      "$(SOURCE_DIR="$HERE/.." packaged_version)"

# --- argument handling ------------------------------------------------------
# The flags really do reach UPDATE_BRANCH.  Sourcing with arguments runs the
# parsing and nothing else, since main is guarded.
branch_for() {
    ( source "$HERE/../install.sh" "$@" > /dev/null 2>&1; printf '%s' "$UPDATE_BRANCH" )
}
check "plain update follows deploy" "$(yes_no "$(branch_for update)" "deploy")" \
      "$(branch_for update)"
check "--dev follows dev" "$(yes_no "$(branch_for update --dev)" "dev")" \
      "$(branch_for update --dev)"
check "--branch still reaches anywhere else" \
      "$(yes_no "$(branch_for update --branch wip)" "wip")" "$(branch_for update --branch wip)"
check "--branch=NAME too" "$(yes_no "$(branch_for update --branch=wip)" "wip")" \
      "$(branch_for update --branch=wip)"

output="$("$HERE/../install.sh" update --config 2>&1)"
check "update --config is refused before anything happens" \
      "$(says "$output" "only means anything with 'reinstall'")" "$output"
output="$("$HERE/../install.sh" update --branch 2>&1)"
check "--branch with nothing after it is refused" \
      "$(says "$output" "needs a branch name")" "$output"
output="$("$HERE/../install.sh" update --branch= 2>&1)"
check "and an empty one too" "$(says "$output" "needs a branch name")" "$output"

# --dev and --branch answer the same question, so two different answers is a
# typo, and the wrong one installs silently.
output="$("$HERE/../install.sh" update --dev --branch other 2>&1)"
check "two branches at once is refused" "$(says "$output" "Pick one")" "$output"
check "but saying the same thing twice is not" \
      "$(yes_no "$(branch_for update --dev --branch dev)" "dev")" \
      "$(branch_for update --dev --branch dev)"

# It chooses what 'update' fetches, so it means nothing anywhere else — and
# "./install.sh --dev" quietly installing the checkout you are standing in is
# exactly the surprise this is for.
for command in "" "reinstall"; do
    output="$("$HERE/../install.sh" $command --dev 2>&1)"
    check "--dev with '${command:-no command}' is refused" \
          "$(says "$output" "only means anything with 'update'")" "$output"
done

echo
if (( FAIL )); then
    echo "$FAIL FAILED, $PASS passed"
    exit 1
fi
echo "all good"
