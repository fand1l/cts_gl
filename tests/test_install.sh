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
# Nothing arrived, but nothing is installed either, so there is still work.
output="$(attempt "$WORKSPACE/work")"
check "an unchanged checkout is installed anyway when nothing is installed" \
      "$(says "$output" "Nothing new arrived")" "$output"

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

# --- going backwards --------------------------------------------------------
# Fetching the same branch twice only ever moves forwards, so a downgrade takes
# a deliberate turn: back from dev to deploy, or --branch at something old.  The
# first is the way out of a dev build that broke, so this can only ever be a
# stop, never a wall — and the cost of going back is the settings, because the
# newer build wrote settings the older one has never heard of.
APPDIR="$WORKSPACE/installed"
mkdir -p "$APPDIR/circle_to_search"

installed_as() {
    if [[ -n "$1" ]]; then
        printf '__version__ = "9.9.9"\n\nBUILD = %s\n' "$1" \
            > "$APPDIR/circle_to_search/__init__.py"
    else
        printf '__version__ = "9.9.9"\n' > "$APPDIR/circle_to_search/__init__.py"
    fi
}

# A committed source tree on dev, which is what build_at_ref reads.
dev_build_is() {
    mkdir -p "$WORKSPACE/seed/src/circle_to_search"
    printf '__version__ = "9.9.9"\n\nBUILD = %s\n' "$1" \
        > "$WORKSPACE/seed/src/circle_to_search/__init__.py"
    git -C "$WORKSPACE/seed" add -A
    git -C "$WORKSPACE/seed" commit -qm "build $1"
    git -C "$WORKSPACE/seed" push -q origin dev
    git -C "$WORKSPACE/work" fetch -q origin dev
}

# BRANCH_FROM is what choose_branch sets, and it is how the refusal knows which
# command to hand back.
going() {
    ( SOURCE_DIR="$WORKSPACE/work" UPDATE_BRANCH=dev BRANCH_FROM="${1---dev}" \
        refuse_downgrade 2>&1 )
}

git -C "$WORKSPACE/seed" checkout -q dev
dev_build_is 10005

installed_as 10001
output="$(going)"
check "a newer build is simply reported" "$(says "$output" "Build 10001 → 10005")" "$output"

installed_as 10005
output="$(going)"
check "the same build says so too" "$(says "$output" "the same one")" "$output"

installed_as 10009
output="$(going)"
check "an older build is refused" "$(says "$output" "an older one")" "$output"
check "and both numbers are named" \
      "$([[ "$output" == *10009* && "$output" == *10005* ]] && echo 1 || echo 0)" "$output"
check "and it says nothing has been touched" \
      "$(says "$output" "Nothing has been touched")" "$output"
check "it warns that the settings go with it" "$(says "$output" "takes the settings")" "$output"
check "and says the captures do not" "$(says "$output" "captures are kept")" "$output"
check "and hands over the exact command" \
      "$(says "$output" "./install.sh update --dev --downgrade")" "$output"
check "which follows how the branch was chosen" \
      "$(says "$(going --branch)" "./install.sh update --branch dev --downgrade")" \
      "$(going --branch)"
check "and is bare when nothing chose it" \
      "$(says "$(going "")" "./install.sh update --downgrade")" "$(going "")"

# It permits, it does not instruct.
PASSTHROUGH=()
installed_as 10001
SOURCE_DIR="$WORKSPACE/work" UPDATE_BRANCH=dev ALLOW_DOWNGRADE=1 refuse_downgrade > /dev/null 2>&1
check "--downgrade on an upgrade erases nothing" \
      "$(yes_no "${PASSTHROUGH[*]-}" "")" "${PASSTHROUGH[*]-}"

PASSTHROUGH=()
installed_as 10009
output="$(SOURCE_DIR="$WORKSPACE/work" UPDATE_BRANCH=dev ALLOW_DOWNGRADE=1 refuse_downgrade 2>&1)"
SOURCE_DIR="$WORKSPACE/work" UPDATE_BRANCH=dev ALLOW_DOWNGRADE=1 refuse_downgrade > /dev/null 2>&1
check "--downgrade lets it through" "$(says "$output" "Going back from build 10009")" "$output"
check "and the reinstall it hands over to is the one that erases" \
      "$(yes_no "${PASSTHROUGH[*]-}" "--config")" "${PASSTHROUGH[*]-}"
PASSTHROUGH=()

# An installation from before any of this had a build number at all.  Unknown
# is not "older", and refusing on a number that does not exist would block
# every update from the version that shipped before it.
installed_as ""
output="$(going)"
check "no build number installed is not a downgrade" \
      "$(yes_no "$(says "$output" "older")" "0")" "$output"
installed_as 10001
output="$( ( SOURCE_DIR="$WORKSPACE/work" UPDATE_BRANCH=main refuse_downgrade 2>&1 ) )"
check "nor is a branch with no build number on it" \
      "$(yes_no "$(says "$output" "older")" "0")" "$output"

# And what the version line says now.
check "the version line carries the build" \
      "$(says "$(SOURCE_DIR="$WORKSPACE/seed" packaged_version)" "9.9.9 (build 10005)")" \
      "$(SOURCE_DIR="$WORKSPACE/seed" packaged_version)"

# --- which branch the flags reach -------------------------------------------
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

# --- nothing to do ----------------------------------------------------------
# Reported from a real machine: "update" on an up-to-date install stopped the
# daemon, took the installation out and put the identical thing back.  A minute
# of churn to arrive exactly where it started.  Nothing fetched *and* the same
# version installed means there is no work, and the command has to say so
# rather than invent some.
git clone -q "$WORKSPACE/remote.git" "$WORKSPACE/current" 2>/dev/null
git -C "$WORKSPACE/current" config user.email test@example.com
git -C "$WORKSPACE/current" config user.name "Test"
git -C "$WORKSPACE/current" checkout -q dev

on_current() {
    ( SOURCE_DIR="$WORKSPACE/current" UPDATE_BRANCH=dev FORCE="${1:-0}" \
        update_checkout 2>&1 )
}

installed_as 10005          # the same as the tip of dev
output="$(on_current)"
check "an up-to-date install is left alone" "$(says "$output" "Nothing to do")" "$output"
check "and it names what you are on" "$(says "$output" "9.9.9 (build 10005)")" "$output"
check "and it does not install anything" \
      "$(yes_no "$(says "$output" "About to install")" "0")" "$output"
check "it offers the two ways to do it anyway" \
      "$([[ "$output" == *"install.sh reinstall"* && "$output" == *"update --force"* ]] \
        && echo 1 || echo 0)" "$output"

output="$(on_current 1)"
check "--force reinstalls it regardless" "$(says "$output" "Nothing new arrived")" "$output"
check "and goes on to install" "$(says "$output" "Staying on")" "$output"

# A different version installed is exactly when reinstalling *is* the answer:
# another branch, a half-finished install, a file that stopped shipping.
installed_as 10001
output="$(on_current)"
check "a different version installed is reinstalled" \
      "$(says "$output" "Nothing new arrived")" "$output"
installed_as ""
printf 'not a version file\n' > "$APPDIR/circle_to_search/__init__.py"
output="$(on_current)"
check "and so is an installation it cannot read" \
      "$(says "$output" "Nothing new arrived")" "$output"

# Code changed but the version did not — a doc commit, a fix nobody renumbered.
# Something arrived, so it goes in.
installed_as 10005
echo "six" > "$WORKSPACE/seed/marker"
git -C "$WORKSPACE/seed" commit -qam "a commit that changes no version"
git -C "$WORKSPACE/seed" push -q origin dev
output="$(on_current)"
check "new commits win over a matching version" "$(says "$output" "New commits")" "$output"
check "and it says the version is not moving" "$(says "$output" "Staying on")" "$output"

# --- argument handling ------------------------------------------------------
passthrough_for() {
    ( source "$HERE/../install.sh" "$@" > /dev/null 2>&1; printf '%s' "${PASSTHROUGH[*]-}" )
}
check "--debug is handed on to the installer update pulls" \
      "$(says "$(passthrough_for update --debug)" "--debug")" "$(passthrough_for update --debug)"
check "and --verbose is the same flag" \
      "$(says "$(passthrough_for update --verbose)" "--debug")" \
      "$(passthrough_for update --verbose)"
check "nothing is handed on by default" "$(yes_no "$(passthrough_for update)" "")" \
      "$(passthrough_for update)"

output="$("$HERE/../install.sh" reinstall --downgrade 2>&1)"
check "--downgrade is refused without 'update'" \
      "$(says "$output" "only means anything with it")" "$output"

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

# --- what the installer says while it works ---------------------------------
# Quiet by default, and the whole of that is what happens to a step's output:
# kept back until it is needed, rather than printed at somebody who is not
# reading it.  What must survive being quiet is a warning, and a failure.
STEP_TOTAL=3
STEP=0
DEBUG=0
quiet_chatter() { echo "cp: some file"; info "narration nobody asked for"; }
quiet_warn()    { echo "more chatter"; warn "but this one you need"; }
quiet_fail()    { echo "the context of it"; die "it did not work"; }

output="$(run_step "Python package" quiet_chatter 2>&1)"
check "a step that works is one line" "$(yes_no "$(printf '%s' "$output" | wc -l)" "0")" "$output"
check "with its label" "$(says "$output" "Python package")" "$output"
check "and a tick" "$(says "$output" "✓")" "$output"
check "the commands it ran are not printed" \
      "$(yes_no "$(says "$output" "cp: some file")" "0")" "$output"
check "and neither is the narration" \
      "$(yes_no "$(says "$output" "narration")" "0")" "$output"

output="$(run_step "KWin script" quiet_warn 2>&1)"
check "but a warning survives being quiet" "$(says "$output" "but this one you need")" "$output"
check "and the step still counts as done" "$(says "$output" "✓")" "$output"

output="$( ( run_step "systemd unit" quiet_fail ) 2>&1 )"
check "a step that fails prints everything it had" \
      "$(says "$output" "the context of it")" "$output"
check "and says which step it was" "$(says "$output" "systemd unit failed")" "$output"
check "and points at --debug for the rest" "$(says "$output" "--debug")" "$output"

# Not a subshell: the flag it sets is read by the caller, so it has to be the
# caller's shell that runs it.
quiet_soft() { warn "  KWin is running the old script"; return 1; }
run_step --soft "Checking it came up" quiet_soft > "$WORKSPACE/soft.txt" 2>&1
output="$(cat "$WORKSPACE/soft.txt")"
check "a soft step that is unhappy does not stop the install" \
      "$(yes_no "$(says "$output" "failed")" "0")" "$output"
check "and it is marked as needing a look" "$(says "$output" "⚠")" "$output"
check "with everything it had to say" "$(says "$output" "running the old script")" "$output"
check "which the ending knows about" "$(yes_no "$SOFT_FAILED" "1")" "$SOFT_FAILED"

SOFT_FAILED=0
STEP=0
output="$(DEBUG=1 run_step "Python package" quiet_chatter 2>&1)"
check "--debug prints what the commands said" "$(says "$output" "cp: some file")" "$output"
check "and the narration with it" "$(says "$output" "narration")" "$output"

echo
if (( FAIL )); then
    echo "$FAIL FAILED, $PASS passed"
    exit 1
fi
echo "all good"
