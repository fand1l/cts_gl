#!/usr/bin/env bash
#
# Circle to Search — installer.
#
# Everything lands in the user's home directory; root is only ever used to let
# the package manager install the runtime dependencies, and even that step is
# optional (--no-deps) if you have them already.  dnf, apt and pacman are
# recognised; on anything else the missing pieces are named and left to you.
#
#   ./install.sh                     normal install
#   ./install.sh update              fetch the deploy branch, then reinstall
#   ./install.sh update --dev        ...fetch 'dev' instead: the newest work,
#                                    before anybody has decided it is fit to run
#   ./install.sh reinstall           remove what was installed, then install it again
#   ./install.sh reinstall --config  ...and erase the settings as well (asks first)
#
#   -y, --yes       assume yes for the package-manager step
#   --no-deps       never call the package manager
#   --force         install even if the session checks fail, update over a
#                   checkout with local changes, and reinstall a version that
#                   is already the one installed
#   --dev           update from 'dev' rather than 'deploy'
#   --branch NAME   update from some other branch entirely
#   --downgrade     allow update to install an older build — which erases the
#                   settings, because a newer version wrote them
#   --debug         print everything each step does, instead of a tick
#
# "reinstall" has nothing to do with git: it installs *this* checkout, whatever
# state it is in.  "update" brings the deploy branch first, in the order that
# cannot leave you worse off.
#
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

APP_NAME="circle-to-search"
APP_ID="io.github.fand1l.CircleToSearch"
SCRIPT_ID="circletosearch"
SERVICE="circle-to-search.service"

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
APPDIR="$DATA_HOME/$APP_NAME"
BINDIR="$HOME/.local/bin"
UNITDIR="$CONFIG_HOME/systemd/user"
DESKTOPDIR="$DATA_HOME/applications"
ICONDIR="$DATA_HOME/icons/hicolor/scalable/apps"
KWINSCRIPTDIR="$DATA_HOME/kwin/scripts"

ASSUME_YES=0
SKIP_DEPS=0
FORCE=0
COMMAND="install"
CLEAN_CONFIG=0
DEBUG=0
ALLOW_DOWNGRADE=0

#: Where "update" takes its code from.  A branch of its own rather than
#: whatever happens to be checked out: the machine running this is not the
#: machine the work is done on, and "the code I have decided is fit to run" is
#: a different question from "the code I was last editing".
DEPLOY_BRANCH="deploy"
#: And where the work lands on its way there.  --dev follows this one instead:
#: same repository, same command, but nothing has been decided about it yet —
#: it is "the code I was last editing", which is the other question.
DEV_BRANCH="dev"
UPDATE_BRANCH="$DEPLOY_BRANCH"
REMOTE="origin"

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
# Colour is for a terminal.  Piped into a file or a log it is noise wrapped
# around every line, and this output is now short enough to be worth reading
# there.
if [[ ! -t 1 ]]; then
    RED=""; GREEN=""; YELLOW=""; BOLD=""; RESET=""
fi

# Three levels, because they are three different questions.
#
#   say   what you asked to know — which version, which branch, what arrived
#   info  narration of how it is going about it, which is --debug material
#   warn  something you have to know whether you asked or not
#
# The split is the whole of the quiet mode: forty lines of narration nobody
# reads is not more transparent than five, it is only louder, and it buries the
# two lines that did matter.
say()   { printf '%s==>%s %s\n' "$GREEN$BOLD" "$RESET" "$*"; }
info()  { (( DEBUG )) && printf '%s  ·%s %s\n' "$BOLD" "$RESET" "$*"; return 0; }
warn()  { printf '%s[!]%s %s\n' "$YELLOW$BOLD" "$RESET" "$*" >&2; }
die()   { printf '%s[x]%s %s\n' "$RED$BOLD" "$RESET" "$*" >&2; exit 1; }

#: The flags to hand on when "update" re-execs the installer it just pulled.
PASSTHROUGH=()

# --------------------------------------------------------------------------- #
# The steps, and how they report.
#
# Quiet by default: a numbered label and a tick.  Everything the commands inside
# a step print goes to a buffer, and the buffer is shown only if the step failed
# — with the exception of its warnings, which are shown either way, because a
# step can succeed and still have something you need to know about.
#
# Each step runs in a subshell so that a die() inside one exits the step rather
# than the installer, and the buffer it was writing to still gets printed.  No
# step may set a variable the rest of the script reads; none needs to.
# --------------------------------------------------------------------------- #
STEP=0
STEP_TOTAL=0
#: Set when a --soft step (only 'verify') came back unhappy.  Not fatal — it
#: reports on the session rather than on the install — but it decides whether
#: the long "if nothing happens" list is worth printing at the end.
SOFT_FAILED=0

run_step() {
    local soft=0
    if [[ "${1:-}" == "--soft" ]]; then soft=1; shift; fi
    local label="$1"; shift
    local buffer status=0
    STEP=$((STEP + 1))

    if (( DEBUG )); then
        printf '%s[%d/%d]%s %s\n' "$GREEN$BOLD" "$STEP" "$STEP_TOTAL" "$RESET" "$label"
        ( "$@" ) || status=$?
        (( status )) && (( ! soft )) && die "$label failed."
        (( status )) && SOFT_FAILED=1
        return 0
    fi

    buffer="$(mktemp)"
    printf '%s[%d/%d]%s %-40s' "$GREEN$BOLD" "$STEP" "$STEP_TOTAL" "$RESET" "$label"
    ( "$@" ) > "$buffer" 2>&1 || status=$?

    if (( status == 0 )); then
        printf '%s✓%s\n' "$GREEN" "$RESET"
        # Warnings only.  They are marked, so they can be picked out of whatever
        # else the commands had to say for themselves.
        grep -F '[!]' "$buffer" || true
    elif (( soft )); then
        printf '%s⚠%s\n' "$YELLOW" "$RESET"
        cat "$buffer"
        SOFT_FAILED=1
    else
        printf '%s✗%s\n' "$RED" "$RESET"
        echo
        cat "$buffer"
        rm -f "$buffer"
        die "$label failed. Everything it printed is above; --debug shows the rest."
    fi
    rm -f "$buffer"
    return 0
}

#: Which flag chose the branch, so a second one can be caught.  --dev and
#: --branch answer the same question, and asking it twice with two different
#: answers is a typo — one that installs the wrong code without saying so.
BRANCH_FROM=""
choose_branch() {
    local wanted="$1" flag="$2"
    if [[ -n "$BRANCH_FROM" && "$wanted" != "$UPDATE_BRANCH" ]]; then
        die "$BRANCH_FROM asks for '$UPDATE_BRANCH' and $flag asks for '$wanted'. Pick one."
    fi
    UPDATE_BRANCH="$wanted"
    BRANCH_FROM="$flag"
}

while (( $# )); do
    case "$1" in
        install|reinstall|update) COMMAND="$1" ;;
        --config)      CLEAN_CONFIG=1 ;;
        -y|--yes)      ASSUME_YES=1; PASSTHROUGH+=("$1") ;;
        --no-deps)     SKIP_DEPS=1;  PASSTHROUGH+=("$1") ;;
        --force)       FORCE=1;      PASSTHROUGH+=("$1") ;;
        --dev)         choose_branch "$DEV_BRANCH" "--dev" ;;
        --downgrade)   ALLOW_DOWNGRADE=1 ;;
        --debug|--verbose) DEBUG=1; PASSTHROUGH+=("--debug") ;;
        --branch)
            [[ -n "${2:-}" ]] || die "--branch needs a branch name."
            choose_branch "$2" "--branch"; shift ;;
        --branch=*)    choose_branch "${1#--branch=}" "--branch" ;;
        -h|--help)
            # Every comment line of the header, however long it grows.
            awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' \
                "${BASH_SOURCE[0]}"
            exit 0 ;;
        *) die "unknown option: $1 (try --help)" ;;
    esac
    shift
done

if [[ -z "$UPDATE_BRANCH" ]]; then
    die "--branch needs a branch name."
fi

if [[ -n "$BRANCH_FROM" && "$COMMAND" != "update" ]]; then
    die "$BRANCH_FROM chooses what 'update' fetches, so it only means anything with 'update'."
fi

if (( ALLOW_DOWNGRADE )) && [[ "$COMMAND" != "update" ]]; then
    die "--downgrade answers a question only 'update' asks, so it only means anything with it."
fi

if (( CLEAN_CONFIG )) && [[ "$COMMAND" != "reinstall" ]]; then
    die "--config erases settings, so it only means anything with 'reinstall'."
fi

# --------------------------------------------------------------------------- #
# 1. Session checks
# --------------------------------------------------------------------------- #
check_session() {
    local problems=0

    if [[ "${XDG_SESSION_TYPE:-}" != "wayland" && -z "${WAYLAND_DISPLAY:-}" ]]; then
        warn "This is not a Wayland session (XDG_SESSION_TYPE='${XDG_SESSION_TYPE:-unset}')."
        warn "  The KWin script reads the cursor position from the compositor; on X11 KWin"
        warn "  still runs the script, but the ScreenShot2 capture path and the overlay"
        warn "  stacking were only tested on Wayland. Log into 'Plasma (Wayland)'."
        problems=1
    fi

    if command -v plasmashell >/dev/null 2>&1; then
        local version
        version="$(plasmashell --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1)"
        if [[ -z "$version" || "${version%%.*}" -lt 6 ]]; then
            warn "Plasma ${version:-unknown} detected — this project targets Plasma 6."
            warn "  On Plasma 5 the tools are named kwriteconfig5/kpackagetool5 and the KWin"
            warn "  scripting API differs (clientList/clientAdded); it will not work as is."
            problems=1
        else
            info "Plasma $version detected."
        fi
    else
        warn "plasmashell not found — is this a Plasma session?"
        problems=1
    fi

    if ! pgrep -x kwin_wayland >/dev/null 2>&1 && ! pgrep -x kwin_x11 >/dev/null 2>&1; then
        warn "KWin does not seem to be running; the KWin script cannot be loaded."
        problems=1
    fi

    if (( problems && ! FORCE )); then
        die "Session checks failed. Fix the above, or re-run with --force to install anyway."
    fi
}

# --------------------------------------------------------------------------- #
# 2. Dependencies
# --------------------------------------------------------------------------- #
PYTHON=""

pick_python() {
    local candidate
    for candidate in python3.13 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            # KWin resolves the caller through /proc/<pid>/exe, so the *real*
            # interpreter path is what has to end up in the .desktop file.
            PYTHON="$(readlink -f "$(command -v "$candidate")")"
            return 0
        fi
    done
    die "No python3 interpreter found."
}

has_module() {
    "$PYTHON" -c "import $1" >/dev/null 2>&1
}

# --------------------------------------------------------------------------- #
# Package names differ per distribution, so the script works in terms of what it
# *needs* and translates that at the last moment.  An unknown package manager is
# not an error: the requirements are printed in words and the install carries
# on, because someone on a distribution nobody thought of still knows their own
# package names better than this script does.
# --------------------------------------------------------------------------- #
PACKAGE_MANAGER=""
DISTRO_NAME=""

detect_package_manager() {
    if [[ -r /etc/os-release ]]; then
        DISTRO_NAME="$( . /etc/os-release 2>/dev/null && echo "${PRETTY_NAME:-${NAME:-}}" )"
    fi
    if command -v dnf >/dev/null 2>&1; then
        PACKAGE_MANAGER="dnf"
    elif command -v apt-get >/dev/null 2>&1; then
        PACKAGE_MANAGER="apt"
    elif command -v pacman >/dev/null 2>&1; then
        PACKAGE_MANAGER="pacman"
    elif command -v zypper >/dev/null 2>&1; then
        PACKAGE_MANAGER="zypper"
    fi
    if [[ -n "$PACKAGE_MANAGER" ]]; then
        info "Package manager: $PACKAGE_MANAGER${DISTRO_NAME:+ ($DISTRO_NAME)}"
    else
        warn "No known package manager found${DISTRO_NAME:+ on $DISTRO_NAME}."
    fi
}

# What each requirement is called, per family.  "-" means this script has no
# name worth guessing at, and the human description is printed instead.
package_for() {
    case "$PACKAGE_MANAGER:$1" in
        dnf:qt)            echo "python3-pyqt6" ;;
        dnf:pillow)        echo "python3-pillow" ;;
        dnf:requests)      echo "python3-requests" ;;
        dnf:kconfig)       echo "kf6-kconfig-core" ;;
        dnf:kpackage)      echo "kf6-kpackage" ;;
        dnf:zbar)          echo "zbar" ;;

        apt:qt)            echo "python3-pyqt6" ;;
        apt:pillow)        echo "python3-pil" ;;
        apt:requests)      echo "python3-requests" ;;
        apt:kconfig)       echo "libkf6config-bin" ;;
        apt:kpackage)      echo "libkf6package-bin" ;;
        apt:zbar)          echo "zbar-tools" ;;

        pacman:qt)         echo "python-pyqt6" ;;
        pacman:pillow)     echo "python-pillow" ;;
        pacman:requests)   echo "python-requests" ;;
        pacman:kconfig)    echo "kconfig" ;;
        pacman:kpackage)   echo "kpackage" ;;
        pacman:zbar)       echo "zbar" ;;

        zypper:qt)         echo "python3-qt6" ;;
        zypper:pillow)     echo "python3-Pillow" ;;
        zypper:requests)   echo "python3-requests" ;;
        zypper:kconfig)    echo "kconfig-tools" ;;
        zypper:kpackage)   echo "kpackage-tools" ;;
        zypper:zbar)       echo "zbar" ;;

        *) echo "-" ;;
    esac
}

describe_requirement() {
    case "$1" in
        qt)       echo "PyQt6, including its QtDBus module" ;;
        pillow)   echo "Pillow, the Python imaging library" ;;
        requests) echo "python-requests" ;;
        kconfig)  echo "the KConfig command line tools (kreadconfig6, kwriteconfig6)" ;;
        kpackage) echo "the KPackage command line tool (kpackagetool6)" ;;
        zbar)     echo "zbar, for reading a QR code instead of uploading it" ;;
        *)        echo "$1" ;;
    esac
}

missing_requirements() {
    has_module "PyQt6.QtWidgets, PyQt6.QtDBus" || echo qt
    has_module "PIL" || echo pillow
    has_module "requests" || echo requests
    command -v kwriteconfig6 >/dev/null 2>&1 || echo kconfig
    command -v kpackagetool6 >/dev/null 2>&1 || echo kpackage
    # Optional, and named anyway: reading a QR code out of the selection is on
    # by default, so shipping it with the decoder missing means the feature
    # silently does nothing and the only place that says why is --doctor.  A
    # feature that is on by default should arrive with what it needs.
    command -v zbarimg >/dev/null 2>&1 || echo zbar
    return 0
}

install_command() {
    case "$PACKAGE_MANAGER" in
        dnf)    echo "sudo dnf install -y" ;;
        apt)    echo "sudo apt-get install -y" ;;
        pacman) echo "sudo pacman -S --needed --noconfirm" ;;
        zypper) echo "sudo zypper install -y" ;;
    esac
}

install_deps() {
    local requirement package answer
    local -a needed=() packages=() unnamed=() still=()

    mapfile -t needed < <(missing_requirements)
    if (( ${#needed[@]} == 0 )); then
        info "All dependencies are present."
        return 0
    fi

    detect_package_manager
    for requirement in "${needed[@]}"; do
        package="$(package_for "$requirement")"
        if [[ "$package" == "-" ]]; then
            unnamed+=("$(describe_requirement "$requirement")")
        else
            packages+=("$package")
        fi
    done

    echo
    warn "Missing: ${needed[*]}"
    if (( ${#unnamed[@]} )); then
        warn "Install these however your distribution names them:"
        printf '      %s\n' "${unnamed[@]}"
    fi
    if (( ${#packages[@]} == 0 )); then
        return 0
    fi

    local -a command
    read -r -a command <<< "$(install_command)"
    echo "    ${command[*]} ${packages[*]}"

    if (( SKIP_DEPS )); then
        warn "--no-deps was given, so that was only a suggestion."
        return 0
    fi
    if (( ! ASSUME_YES )); then
        read -r -p "Run it now? [Y/n] " answer
        case "$answer" in [nN]*) warn "Skipping; install them manually."; return 0 ;; esac
    fi

    if ! "${command[@]}" "${packages[@]}"; then
        warn "$PACKAGE_MANAGER did not finish."
        [[ "$PACKAGE_MANAGER" == "apt" ]] && \
            warn "  Try 'sudo apt-get update' first, then run this again."
        warn "  Continuing anyway; the daemon may not start."
        return 0
    fi

    # The names are a guess everywhere but the distribution this was written on,
    # so say plainly whether the guess worked rather than assuming it did.
    mapfile -t still < <(missing_requirements)
    if (( ${#still[@]} )); then
        warn "Still missing: ${still[*]}"
        for requirement in "${still[@]}"; do
            warn "  $(describe_requirement "$requirement")"
        done
    else
        info "All dependencies are present."
    fi
}

# --------------------------------------------------------------------------- #
# 3. Updating
#
# Keeping up with the project was always two commands — `git pull` and then
# `./install.sh reinstall` — and remembering the second one.  This is both, in
# the order that cannot leave you worse off: **the fetch happens first**, and a
# fetch that fails has removed nothing, so the working installation is exactly
# as it was.
#
# It follows one named branch (`deploy`), not whatever is checked out.  The
# machine running this is not the machine the work is done on, and "the code I
# have decided is fit to run" is a different question from "the code I was last
# editing".  So if the checkout is somewhere else, this moves it — which is safe
# because the tree has to be clean to get this far, and any commits on the
# branch being left are still on it afterwards.
#
# Fast-forward only.  An update is not the moment to find out that a merge
# wanted a decision from you, and refusing is a better answer than a conflicted
# checkout half installed over the top of a working one.
# --------------------------------------------------------------------------- #
update_checkout() {
    command -v git >/dev/null 2>&1 \
        || die "'update' needs git. Update the checkout yourself, then run 'reinstall'."
    git -C "$SOURCE_DIR" rev-parse --git-dir >/dev/null 2>&1 \
        || die "$SOURCE_DIR is not a git checkout, so there is nothing to fetch. Use 'reinstall'."

    local here before after
    if [[ -n "$(git -C "$SOURCE_DIR" status --porcelain)" ]]; then
        if (( FORCE )); then
            warn "The checkout has local changes; --force says carry on."
            warn "  git will still refuse if they are in the way."
        else
            die "The checkout has local changes — commit, stash or discard them first
      (git -C $SOURCE_DIR status), or pass --force to try anyway."
        fi
    fi

    if [[ "$UPDATE_BRANCH" != "$DEPLOY_BRANCH" ]]; then
        warn "Updating from '$UPDATE_BRANCH', not '$DEPLOY_BRANCH'."
        if [[ "$UPDATE_BRANCH" == "$DEV_BRANCH" ]]; then
            warn "  That is where the work is pushed as it happens. Nothing on it has"
            warn "  been decided to be fit to run, and it is expected to be broken"
            warn "  sometimes. './install.sh update' goes back to $DEPLOY_BRANCH."
        fi
    fi

    say "Fetching $UPDATE_BRANCH from $REMOTE"
    git -C "$SOURCE_DIR" fetch --quiet "$REMOTE" "$UPDATE_BRANCH" 2>/dev/null \
        || die "Could not fetch '$UPDATE_BRANCH' from $REMOTE. Nothing has been touched.
      If the branch does not exist yet, make it:
          git push $REMOTE HEAD:refs/heads/$UPDATE_BRANCH
      If it is there but you cannot read it, this checkout needs credentials
      that can — an SSH remote, or a git credential helper holding a token.
      Otherwise check the network and the remote, and try again."

    local had
    had="$(installed_version)"
    [[ -n "$had" ]] && say "Installed now: $had"

    refuse_downgrade

    before="$(git -C "$SOURCE_DIR" rev-parse HEAD)"
    here="$(git -C "$SOURCE_DIR" symbolic-ref --quiet --short HEAD || echo "a detached HEAD")"
    if [[ "$here" != "$UPDATE_BRANCH" ]]; then
        # Moving off whatever was checked out.  Nothing is lost: the tree is
        # clean by now and the commits on the old branch stay on it.
        say "Switching from $here to $UPDATE_BRANCH"
        if git -C "$SOURCE_DIR" show-ref --verify --quiet "refs/heads/$UPDATE_BRANCH"; then
            git -C "$SOURCE_DIR" checkout --quiet "$UPDATE_BRANCH"
        else
            git -C "$SOURCE_DIR" checkout --quiet -b "$UPDATE_BRANCH" \
                --track "$REMOTE/$UPDATE_BRANCH"
        fi || die "Could not switch to $UPDATE_BRANCH. Nothing has been installed;
      the copy that was running is still the one running."
    fi

    git -C "$SOURCE_DIR" merge --ff-only --quiet "$REMOTE/$UPDATE_BRANCH" \
        || die "Could not fast-forward $UPDATE_BRANCH onto $REMOTE/$UPDATE_BRANCH —
      the local branch has commits of its own. Nothing has been installed; the
      copy that was running is still the one running. Reset it with
          git -C $SOURCE_DIR reset --hard $REMOTE/$UPDATE_BRANCH
      if those commits are not wanted."
    after="$(git -C "$SOURCE_DIR" rev-parse HEAD)"

    # The name, not just the number: "am I updating to the one I actually
    # need" is answerable from a word and not from three digits.
    local coming
    coming="$(packaged_version)"

    if [[ "$before" == "$after" ]]; then
        # Nothing was fetched.  If what is installed is also what is here, then
        # there is no work left for this command to do, and doing it anyway —
        # stopping the daemon, taking the installation out, putting it back —
        # is a minute of churn to arrive exactly where it started.
        #
        # Only when they *differ* is a reinstall the answer: an installation
        # from another branch, a half-finished one, or one from before a file
        # stopped shipping.
        if [[ -n "$had" && "$coming" == "$had" ]] && (( ! FORCE )); then
            say "Nothing to do — $coming is already installed."
            printf '      %s\n' \
                "Nothing arrived, and what is installed is what is here." \
                "To put it in again regardless:  ./install.sh reinstall" \
                "                          or:  ./install.sh update --force"
            exit 0
        fi
        say "Nothing new arrived — installing this copy anyway."
    else
        say "New commits:"
        git -C "$SOURCE_DIR" --no-pager log --oneline --no-decorate "$before..$after" \
            | sed 's/^/      /'
    fi

    if [[ -n "$coming" ]]; then
        if [[ "$coming" == "$had" ]]; then
            say "Staying on $coming."
        else
            say "About to install: $coming"
        fi
    fi
}

# --------------------------------------------------------------------------- #
# Going backwards.
#
# Fetching the same branch twice can only move forwards — it is fast-forward
# only — so a downgrade takes one of two deliberate turns: back from 'dev' to
# 'deploy' after trying something, or --branch at something old.  Both are
# legitimate, and the first one is the way out of a dev build that broke, so
# this must never be a wall.  It is a stop.
#
# The cost is that the settings go with it.  A newer version writes settings an
# older one has never heard of, and reads them back through code that was
# written before they existed; the failure that comes of that looks like a bug
# in the older version and is not one.  So --downgrade means "and erase the
# configuration", and says so before it does anything.
#
# It permits, it does not instruct: --downgrade on an update that turns out to
# move forwards erases nothing.
# --------------------------------------------------------------------------- #
refuse_downgrade() {
    local have coming
    have="$(installed_build)"
    coming="$(build_at_ref "$REMOTE/$UPDATE_BRANCH")"

    if [[ -z "$have" || -z "$coming" ]]; then
        # One of them predates the build number.  Unknown is not "older", and
        # refusing on a number that does not exist would block every update
        # from a version installed before this one shipped.
        info "No build number to compare (installed '${have:-none}', incoming '${coming:-none}')."
        return 0
    fi

    if (( coming > have )); then
        say "Build $have → $coming."
        return 0
    fi
    if (( coming == have )); then
        say "Build $have, the same one."
        return 0
    fi

    if (( ! ALLOW_DOWNGRADE )); then
        local how=""
        if [[ "$BRANCH_FROM" == "--dev" ]]; then
            how=" --dev"
        elif [[ -n "$BRANCH_FROM" ]]; then
            how=" --branch $UPDATE_BRANCH"
        fi
        die "That would install build $coming over build $have — an older one.
      Nothing has been touched.

      Going back is allowed, but it takes the settings with it: build $have
      wrote settings that build $coming has never heard of, and reading them
      back through older code fails in ways that look like a bug and are not.

      To go back anyway:
          ./install.sh update$how --downgrade
      It asks again before erasing anything, and your captures are kept."
    fi

    warn "Going back from build $have to build $coming."
    warn "  --downgrade was given, so the settings will be erased with it:"
    warn "  every application setting, the gesture thresholds and anything the"
    warn "  calibration measured. Captures and saved traces are kept."
    # The wipe is done by the reinstall this hands over to, which already asks
    # twice before erasing a configuration and has done since before any of
    # this existed.  Nothing here erases anything.
    PASSTHROUGH+=("--config")
}

# --------------------------------------------------------------------------- #
# 4. Reinstalling
#
# A plain install already overwrites everything it owns, so "reinstall" exists
# for the case that does not cover: a file the project used to ship and no
# longer does, left behind and still being loaded.  Taking the old installation
# out first is the only way to be sure of what is running afterwards.
#
# It removes what the installer put there and nothing else.  Settings, the kept
# captures and the saved traces all survive unless --config is given.
# --------------------------------------------------------------------------- #
remove_installation() {
    systemctl --user disable --now "$SERVICE" >/dev/null 2>&1 || true
    rm -f "$UNITDIR/$SERVICE"
    systemctl --user daemon-reload >/dev/null 2>&1 || true

    if command -v kpackagetool6 >/dev/null 2>&1; then
        kpackagetool6 --type=KWin/Script --remove "$SCRIPT_ID" >/dev/null 2>&1 || true
    fi
    rm -rf "${KWINSCRIPTDIR:?}/$SCRIPT_ID"

    # The package directory only.  Its parent also holds the recent captures and
    # the traces saved from misfire reports, which are the user's, not ours.
    rm -rf "${APPDIR:?}/circle_to_search"
    rm -f "$BINDIR/$APP_NAME"
    rm -f "$DESKTOPDIR/$APP_ID.desktop"
    rm -f "$ICONDIR/$APP_ID.svg"
}

confirm_config_wipe() {
    local group="Script-$SCRIPT_ID"
    local answer
    echo
    warn "--config will erase:"
    echo "      $CONFIG_HOME/$APP_NAME/"
    echo "          every application setting — language, selection mode, the"
    echo "          Lens back end, whether text recognition is on, and the"
    echo "          answers to the questions it only asks once"
    echo "      [$group] in kwinrc"
    echo "          the gesture thresholds, including anything the calibration"
    echo "          measured, and the global shortcut"
    echo
    echo "  Kept:   recent captures and saved traces in $APPDIR"
    echo
    if [[ ! -t 0 ]]; then
        die "--config needs a terminal to confirm on. Run it by hand."
    fi
    read -r -p "  Type 'yes' to erase the configuration: " answer
    if [[ "$answer" != "yes" ]]; then
        die "Not confirmed. Nothing was erased and nothing was installed."
    fi
}

wipe_config() {
    local group="Script-$SCRIPT_ID"
    local key value

    if command -v kwriteconfig6 >/dev/null 2>&1; then
        while IFS='=' read -r key value; do
            [[ -z "$key" ]] && continue
            kwriteconfig6 --file kwinrc --group "$group" --key "$key" --delete \
                >/dev/null 2>&1 || true
        done < <(detection_defaults)
        for key in "${TRANSIENT_KEYS[@]}"; do
            kwriteconfig6 --file kwinrc --group "$group" --key "$key" --delete \
                >/dev/null 2>&1 || true
        done
        kwriteconfig6 --file kwinrc --group Plugins --key "${SCRIPT_ID}Enabled" --delete \
            >/dev/null 2>&1 || true
    fi

    rm -rf "${CONFIG_HOME:?}/$APP_NAME"
    reconfigure_kwin
}

# --------------------------------------------------------------------------- #
# 5. Install
# --------------------------------------------------------------------------- #
install_python_package() {
    info "into $APPDIR"
    rm -rf "${APPDIR:?}/circle_to_search"
    mkdir -p "$APPDIR"
    cp -r "$SOURCE_DIR/src/circle_to_search" "$APPDIR/"
    find "$APPDIR" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

    mkdir -p "$BINDIR"
    cat > "$BINDIR/$APP_NAME" <<EOF
#!/bin/sh
# Generated by install.sh
exec "$PYTHON" "$APPDIR/circle_to_search/__main__.py" "\$@"
EOF
    chmod +x "$BINDIR/$APP_NAME"

    case ":$PATH:" in
        *":$BINDIR:"*) ;;
        *) warn "$BINDIR is not in your PATH; run the app as $BINDIR/$APP_NAME" ;;
    esac
}

install_data_files() {
    mkdir -p "$DESKTOPDIR" "$ICONDIR"
    sed -e "s|@PYTHON@|$PYTHON|g" -e "s|@APPDIR@|$APPDIR|g" \
        "$SOURCE_DIR/data/$APP_ID.desktop.in" > "$DESKTOPDIR/$APP_ID.desktop"
    install -m 0644 \
        "$SOURCE_DIR/data/icons/hicolor/scalable/apps/$APP_ID.svg" \
        "$ICONDIR/$APP_ID.svg"

    # KWin looks the caller up through KService, which reads the sycoca cache.
    command -v update-desktop-database >/dev/null 2>&1 && \
        update-desktop-database "$DESKTOPDIR" >/dev/null 2>&1 || true
    command -v kbuildsycoca6 >/dev/null 2>&1 && kbuildsycoca6 >/dev/null 2>&1 || true
    command -v gtk-update-icon-cache >/dev/null 2>&1 && \
        gtk-update-icon-cache -qtf "$DATA_HOME/icons/hicolor" >/dev/null 2>&1 || true
}

install_kwin_script() {
    if command -v kpackagetool6 >/dev/null 2>&1; then
        local action="--install"
        if kpackagetool6 --type=KWin/Script --list 2>/dev/null | grep -qx "$SCRIPT_ID"; then
            action="--upgrade"
        fi
        if ! kpackagetool6 --type=KWin/Script "$action" "$SOURCE_DIR/kwinscript"; then
            warn "kpackagetool6 $action failed — falling back to a plain copy."
            rm -rf "${KWINSCRIPTDIR:?}/$SCRIPT_ID"
            mkdir -p "$KWINSCRIPTDIR"
            cp -r "$SOURCE_DIR/kwinscript" "$KWINSCRIPTDIR/$SCRIPT_ID"
        fi
    else
        warn "kpackagetool6 is missing — copying the package by hand instead."
        rm -rf "${KWINSCRIPTDIR:?}/$SCRIPT_ID"
        mkdir -p "$KWINSCRIPTDIR"
        cp -r "$SOURCE_DIR/kwinscript" "$KWINSCRIPTDIR/$SCRIPT_ID"
    fi

    info "Enabling it in kwinrc"
    kwriteconfig6 --file kwinrc --group Plugins --key "${SCRIPT_ID}Enabled" --type bool true
    seed_detection_defaults
    reload_kwin_script
}

# --------------------------------------------------------------------------- #
# Getting the *new* code into a running KWin.
#
# "reconfigure" only makes KWin re-read settings: a script that is already
# loaded keeps running its old code until it is unloaded, so upgrading the
# package on disk changed nothing until the next login.  Unload it explicitly,
# then toggle the plugin so KWin loads it again from disk.
# --------------------------------------------------------------------------- #
reload_kwin_script() {
    info "Reloading the script inside KWin"

    if command -v busctl >/dev/null 2>&1; then
        busctl --user call org.kde.KWin /Scripting org.kde.kwin.Scripting \
            unloadScript s "$SCRIPT_ID" >/dev/null 2>&1 || true
    fi

    kwriteconfig6 --file kwinrc --group Plugins --key "${SCRIPT_ID}Enabled" --type bool false
    reconfigure_kwin
    sleep 1
    kwriteconfig6 --file kwinrc --group Plugins --key "${SCRIPT_ID}Enabled" --type bool true
    reconfigure_kwin
    sleep 2
}

# Ask the journal which version KWin actually has in memory.
running_script_version() {
    journalctl --user -u plasma-kwin_wayland --since "-60s" -o cat 2>/dev/null \
        | grep -o "KWin script started (v[^)]*)" | tail -1 \
        | sed -e 's/.*(v//' -e 's/)//'
}

packaged_script_version() {
    grep -o 'var SCRIPT_VERSION = "[^"]*"' "$SOURCE_DIR/kwinscript/contents/code/main.js" \
        | head -1 | sed -e 's/.*"\(.*\)"/\1/'
}

# --------------------------------------------------------------------------- #
# The application's own version, and what that release is called.
#
# Read out of the source rather than kept here, and read the same way out of the
# *installed* copy — so an update can say what you were on and what you are on
# now, which is the question somebody running it is actually asking.  Nothing
# imports anything for this: the daemon may not be running, and the installed
# copy may be a version whose imports this interpreter cannot satisfy.
# --------------------------------------------------------------------------- #
version_in() {
    local file="$1" key="$2"
    [[ -r "$file" ]] || return 0
    grep -o "^$key = \"[^\"]*\"" "$file" | head -1 | sed -e 's/.*"\(.*\)"/\1/'
}

#: The build number is not quoted in the source, so it needs its own reader.
build_in() {
    local file="$1"
    [[ -r "$file" ]] || return 0
    grep -o '^BUILD = [0-9]\+' "$file" | head -1 | grep -o '[0-9]\+' || true
}

describe_version() {
    local init="$1" number name build
    number="$(version_in "$init" "__version__")"
    [[ -n "$number" ]] || return 0
    name="$(version_in "$init" "RELEASE_NAME")"
    build="$(build_in "$init")"
    printf '%s' "$number${name:+ “$name”}${build:+ (build $build)}"
}

PACKAGED_INIT="src/circle_to_search/__init__.py"
INSTALLED_INIT="circle_to_search/__init__.py"

packaged_version()  { describe_version "$SOURCE_DIR/$PACKAGED_INIT"; }
installed_version() { describe_version "$APPDIR/$INSTALLED_INIT"; }
packaged_build()    { build_in "$SOURCE_DIR/$PACKAGED_INIT"; }
installed_build()   { build_in "$APPDIR/$INSTALLED_INIT"; }

#: The build number on a ref, without checking it out.  This is what makes the
#: downgrade refusal safe: the comparison happens after the fetch and before
#: anything in the working tree has been touched, so refusing leaves the machine
#: exactly as it was found, like every other refusal in 'update'.
build_at_ref() {
    git -C "$SOURCE_DIR" show "$1:$PACKAGED_INIT" 2>/dev/null \
        | grep -o '^BUILD = [0-9]\+' | head -1 | grep -o '[0-9]\+' || true
}

# One list, used both to seed the defaults and to erase them again, so a key
# added to the script cannot end up seeded but never cleaned up.
detection_defaults() {
    cat <<'EOF'
enabled=true
reversals=2
windowMs=600
minAmplitudePx=150
angleTolerance=30
pollMs=50
cooldownMs=1500
minStepPx=6
minSpeedPxPerSec=700
maxCurvaturePct=140
reversalTolerance=40
disableInFullscreen=true
restoreFocus=true
shortcut=Meta+Shift+L
EOF
}

#: Written by the running program rather than configured by anyone.
TRANSIENT_KEYS=(calibrating collectTraces)

seed_detection_defaults() {
    # Only write the keys that are not set yet, so re-running the installer never
    # clobbers tuned values.
    local group="Script-$SCRIPT_ID"
    local key value
    while IFS='=' read -r key value; do
        [[ -z "$key" ]] && continue
        if [[ -z "$(kreadconfig6 --file kwinrc --group "$group" --key "$key" 2>/dev/null)" ]]; then
            kwriteconfig6 --file kwinrc --group "$group" --key "$key" "$value"
        fi
    done < <(detection_defaults)

    # Not settings but transient state: the calibration window sets one and the
    # daemon the other, and a crash while either was on would otherwise leave
    # the gesture measuring instead of firing, or recording for nobody.
    for key in "${TRANSIENT_KEYS[@]}"; do
        kwriteconfig6 --file kwinrc --group "$group" --key "$key" --type bool false
    done
}

reconfigure_kwin() {
    if command -v qdbus6 >/dev/null 2>&1; then
        qdbus6 org.kde.KWin /KWin reconfigure >/dev/null 2>&1 || true
    elif command -v qdbus >/dev/null 2>&1; then
        qdbus org.kde.KWin /KWin reconfigure >/dev/null 2>&1 || true
    elif command -v busctl >/dev/null 2>&1; then
        busctl --user call org.kde.KWin /KWin org.kde.KWin reconfigure >/dev/null 2>&1 || true
    else
        warn "Could not ask KWin to reload — log out and back in."
    fi
}

install_service() {
    mkdir -p "$UNITDIR"
    sed -e "s|@PYTHON@|$PYTHON|g" -e "s|@APPDIR@|$APPDIR|g" \
        "$SOURCE_DIR/data/$SERVICE.in" > "$UNITDIR/$SERVICE"
    systemctl --user daemon-reload
    systemctl --user enable --now "$SERVICE"
    sleep 1
    if systemctl --user is-active --quiet "$SERVICE"; then
        info "$SERVICE is running."
    else
        warn "$SERVICE did not start. Look at:  journalctl --user -u $SERVICE -n 50"
    fi
}

verify() {
    local ok=1
    if busctl --user list 2>/dev/null | grep -q "$APP_ID"; then
        info "  D-Bus name $APP_ID is claimed."
    else
        warn "  D-Bus name $APP_ID is NOT on the bus."
        ok=0
    fi
    if [[ -f "$DATA_HOME/kwin/scripts/$SCRIPT_ID/metadata.json" || \
          -f "/usr/share/kwin/scripts/$SCRIPT_ID/metadata.json" ]]; then
        info "  KWin script package is installed."
    else
        warn "  KWin script package was not found."
        ok=0
    fi

    local packaged running
    packaged="$(packaged_script_version)"
    running="$(running_script_version)"
    if [[ -n "$running" && "$running" == "$packaged" ]]; then
        info "  KWin is running script v$running."
    elif [[ -n "$running" ]]; then
        warn "  KWin is still running script v$running, but v$packaged was installed."
        warn "    Toggle it off and on in System Settings → Window Management →"
        warn "    KWin Scripts, or log out and back in."
        ok=0
    else
        warn "  Could not tell which script version KWin is running."
        warn "    Check with: journalctl --user -u plasma-kwin_wayland -n 100 | grep circle"
    fi
    return $((1 - ok))
}

main() {
    if [[ "$COMMAND" == "update" ]]; then
        # First, and before the session checks: the pull is worth doing from any
        # terminal, and the installer this hands over to runs them itself.
        update_checkout
        # Hand over to the installer that was just pulled, for two reasons.  The
        # new code is what knows where the new code goes — an old installer
        # would not place a file this version has only just started shipping.
        # And bash reads a script as it runs it, so carrying on inside a file
        # that has changed underneath is a way to execute something nobody
        # wrote.
        say "Handing over to the installer that was just pulled."
        echo
        exec "$SOURCE_DIR/install.sh" reinstall "${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}"
    fi

    check_session
    pick_python
    info "Using interpreter: $PYTHON"

    # Dependencies first and outside the count: this one is about the machine
    # rather than about installing anything, it may have to ask a question, and
    # a package manager asking for a password from behind a tick is the one
    # thing a quiet installer must never do.
    install_deps

    # Five things get installed; a reinstall takes the old one out first, and
    # --config erases the settings between the two.
    STEP_TOTAL=5
    [[ "$COMMAND" == "reinstall" ]] && STEP_TOTAL=$((STEP_TOTAL + 1))
    (( CLEAN_CONFIG )) && STEP_TOTAL=$((STEP_TOTAL + 1))

    if [[ "$COMMAND" == "reinstall" ]]; then
        # Asked before anything is touched, so saying no leaves the working
        # installation exactly as it was.
        if (( CLEAN_CONFIG )); then
            confirm_config_wipe
        fi
        run_step "Removing the previous install" remove_installation
        if (( CLEAN_CONFIG )); then
            run_step "Erasing the configuration" wipe_config
        fi
    fi

    run_step "Python package" install_python_package
    run_step "Desktop entry and icon" install_data_files
    run_step "KWin script" install_kwin_script
    run_step "systemd --user service" install_service
    run_step --soft "Checking it came up" verify

    printf '\n%s✔  %s is installed.%s\n\n' "$GREEN$BOLD" "$(packaged_version)" "$RESET"
    cat <<EOF
  Shake the pointer diagonally (down-up-down-up, twice), or press
  ${BOLD}Meta+Shift+L${RESET}, then drag round something. Esc or right-click cancels.

  The "Circle to Search" tray icon has "Capture now" and Settings.
EOF

    if (( SOFT_FAILED )); then
        cat <<EOF

${YELLOW}${BOLD}Something above is not right yet.${RESET} Usually it is KWin: it loads a script
once, at login, and keeps running that copy. Logging out and back in fixes
most of it. If it does not:

  0. journalctl --user -u plasma-kwin_wayland | grep "script started"
     — the version there must match $(packaged_script_version). If it does not,
     KWin is still running the old code: toggle the script off and on in
     System Settings → Window Management → KWin Scripts.
  1. journalctl --user -u plasma-kwin_wayland -f | grep -i circle
     (no "KWin script started" line → the script is not loaded: open
      System Settings → Window Management → KWin Scripts and tick
      "Circle to Search")
  2. journalctl --user -u $SERVICE -f
  3. busctl --user call $APP_ID \\
         /io/github/fand1l/CircleToSearch $APP_ID Trigger iis 100 100 ""
     should open the overlay straight away.
  4. See "What can break" in docs/TECHNICAL.md.
EOF
    elif (( ! DEBUG )); then
        printf '  %sRun it again with --debug to see everything it did.%s\n' "$BOLD" "$RESET"
    fi
}

# Run when executed, stay quiet when sourced — tests/test_install.sh sources this
# to exercise update_checkout against a throwaway repository, which is worth
# being able to do for the one command here that touches somebody's checkout.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
