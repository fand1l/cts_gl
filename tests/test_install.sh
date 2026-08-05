#!/usr/bin/env bash
#
# The installer's "update" command, against throwaway repositories.
#
# This is the one thing in the project that reaches into somebody else's git
# checkout, so the interesting cases are the refusals: every one of them has to
# stop *before* anything is pulled or removed, leaving the machine exactly as it
# was found.  Each check below asserts the message and the state.
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

# Sourcing runs the argument parsing with no arguments, which leaves the
# defaults, and defines every function.  main is guarded, so nothing installs.
# shellcheck source=/dev/null
source "$HERE/../install.sh"
# The installer sets -e, and every refusal below is a die() inside a command
# substitution — which is exactly the thing -e aborts on.  Half the point of
# this file is to reach the check after the one that failed.
set +e

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

# die() exits, so every refusal is run in a subshell and judged by its output
# and its exit code.
attempt() {
    SOURCE_DIR="$1" update_checkout 2>&1
}

# --- nothing new ------------------------------------------------------------
SOURCE_DIR="$WORKSPACE/work"
output="$(attempt "$WORKSPACE/work")"
check "an unchanged checkout is still reinstalled" \
      "$([[ "$output" == *"Already up to date"* ]] && echo 1 || echo 0)" "$output"

# --- a real update ----------------------------------------------------------
echo two > "$WORKSPACE/seed/marker"
git -C "$WORKSPACE/seed" commit -qam "the second one"
git -C "$WORKSPACE/seed" push -q origin main

output="$(attempt "$WORKSPACE/work")"
check "the pull happens" \
      "$([[ "$(cat "$WORKSPACE/work/marker")" == "two" ]] && echo 1 || echo 0)" \
      "$(cat "$WORKSPACE/work/marker")"
check "and it says what arrived" \
      "$([[ "$output" == *"the second one"* ]] && echo 1 || echo 0)" "$output"

# --- local changes ----------------------------------------------------------
echo three > "$WORKSPACE/seed/marker"
git -C "$WORKSPACE/seed" commit -qam "the third one"
git -C "$WORKSPACE/seed" push -q origin main
echo "mine" >> "$WORKSPACE/work/marker"

output="$(attempt "$WORKSPACE/work")"
check "a dirty checkout is refused" \
      "$([[ "$output" == *"local changes"* ]] && echo 1 || echo 0)" "$output"
check "and nothing was pulled over it" \
      "$([[ "$(head -1 "$WORKSPACE/work/marker")" == "two" ]] && echo 1 || echo 0)" \
      "$(head -1 "$WORKSPACE/work/marker")"

git -C "$WORKSPACE/work" checkout -q -- marker

# --- diverged ---------------------------------------------------------------
echo local > "$WORKSPACE/work/marker"
git -C "$WORKSPACE/work" commit -qam "a commit only this checkout has"
output="$(attempt "$WORKSPACE/work")"
check "a checkout that cannot fast-forward is refused" \
      "$([[ "$output" == *"Could not fast-forward"* ]] && echo 1 || echo 0)" "$output"
check "and it says the installed copy is untouched" \
      "$([[ "$output" == *"still the one that was working"* ]] && echo 1 || echo 0)" "$output"
git -C "$WORKSPACE/work" reset -q --hard HEAD~1

# --- detached HEAD ----------------------------------------------------------
git -C "$WORKSPACE/work" checkout -q --detach HEAD
output="$(attempt "$WORKSPACE/work")"
check "a detached HEAD is refused, with the fix" \
      "$([[ "$output" == *"detached"* && "$output" == *"git switch"* ]] && echo 1 || echo 0)" \
      "$output"
git -C "$WORKSPACE/work" checkout -q main

# --- no upstream ------------------------------------------------------------
git -C "$WORKSPACE/work" checkout -q -b nowhere
output="$(attempt "$WORKSPACE/work")"
check "a branch tracking nothing is refused, with the fix" \
      "$([[ "$output" == *"not tracking anything"* && "$output" == *"branch -u"* ]] \
        && echo 1 || echo 0)" "$output"
git -C "$WORKSPACE/work" checkout -q main

# --- not a checkout at all --------------------------------------------------
mkdir -p "$WORKSPACE/tarball"
output="$(attempt "$WORKSPACE/tarball")"
check "an unpacked tarball is told to use reinstall" \
      "$([[ "$output" == *"not a git checkout"* && "$output" == *"reinstall"* ]] \
        && echo 1 || echo 0)" "$output"

# --- --config cannot be smuggled into an update -----------------------------
output="$("$HERE/../install.sh" update --config 2>&1)"
check "update --config is refused before anything happens" \
      "$([[ "$output" == *"only means anything with 'reinstall'"* ]] && echo 1 || echo 0)" \
      "$output"

echo
if (( FAIL )); then
    echo "$FAIL FAILED, $PASS passed"
    exit 1
fi
echo "all good"
