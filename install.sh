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
#   ./install.sh reinstall           remove what was installed, then install it again
#   ./install.sh reinstall --config  ...and erase the settings as well (asks first)
#
#   -y, --yes     assume yes for the package-manager step
#   --no-deps     never call the package manager
#   --force       install even if the session checks fail
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

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
info()  { printf '%s==>%s %s\n' "$GREEN$BOLD" "$RESET" "$*"; }
warn()  { printf '%s[!]%s %s\n' "$YELLOW$BOLD" "$RESET" "$*" >&2; }
die()   { printf '%s[x]%s %s\n' "$RED$BOLD" "$RESET" "$*" >&2; exit 1; }

for arg in "$@"; do
    case "$arg" in
        install|reinstall) COMMAND="$arg" ;;
        --config)      CLEAN_CONFIG=1 ;;
        -y|--yes)      ASSUME_YES=1 ;;
        --no-deps)     SKIP_DEPS=1 ;;
        --force)       FORCE=1 ;;
        -h|--help)
            sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) die "unknown option: $arg (try --help)" ;;
    esac
done

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

        apt:qt)            echo "python3-pyqt6" ;;
        apt:pillow)        echo "python3-pil" ;;
        apt:requests)      echo "python3-requests" ;;
        apt:kconfig)       echo "libkf6config-bin" ;;
        apt:kpackage)      echo "libkf6package-bin" ;;

        pacman:qt)         echo "python-pyqt6" ;;
        pacman:pillow)     echo "python-pillow" ;;
        pacman:requests)   echo "python-requests" ;;
        pacman:kconfig)    echo "kconfig" ;;
        pacman:kpackage)   echo "kpackage" ;;

        zypper:qt)         echo "python3-qt6" ;;
        zypper:pillow)     echo "python3-Pillow" ;;
        zypper:requests)   echo "python3-requests" ;;
        zypper:kconfig)    echo "kconfig-tools" ;;
        zypper:kpackage)   echo "kpackage-tools" ;;

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
        *)        echo "$1" ;;
    esac
}

missing_requirements() {
    has_module "PyQt6.QtWidgets, PyQt6.QtDBus" || echo qt
    has_module "PIL" || echo pillow
    has_module "requests" || echo requests
    command -v kwriteconfig6 >/dev/null 2>&1 || echo kconfig
    command -v kpackagetool6 >/dev/null 2>&1 || echo kpackage
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
# 3. Reinstalling
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
    info "Removing the previous installation"

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
    info "Erasing the configuration"
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
# 4. Install
# --------------------------------------------------------------------------- #
install_python_package() {
    info "Installing the Python package into $APPDIR"
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
    info "Installing the desktop entry and the icon"
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
    info "Installing the KWin script"
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
    info "Installing the systemd --user unit"
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
    info "Verifying"
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
    check_session
    pick_python
    info "Using interpreter: $PYTHON"

    if [[ "$COMMAND" == "reinstall" ]]; then
        # Asked before anything is touched, so saying no leaves the working
        # installation exactly as it was.
        if (( CLEAN_CONFIG )); then
            confirm_config_wipe
        fi
        remove_installation
        if (( CLEAN_CONFIG )); then
            wipe_config
        fi
    fi

    install_deps
    install_python_package
    install_data_files
    install_kwin_script
    install_service
    verify || true

    cat <<EOF

${GREEN}${BOLD}Done.${RESET}

  Try it:   shake the pointer diagonally (down-up-down-up, twice), or press
            ${BOLD}Meta+Shift+L${RESET}, then drag a rectangle. Esc or right-click cancels.

  Tray:     the "Circle to Search" icon has "Capture now" and "Settings".

  If nothing happens:
    0. journalctl --user -u plasma-kwin_wayland | grep "script started"
       — the version there must match $(packaged_script_version). If it does
       not, KWin is still running the old code: toggle the script off and on in
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
}

main "$@"
