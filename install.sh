#!/usr/bin/env bash
#
# Circle to Search — installer.
#
# Everything lands in the user's home directory; root is only ever used to let
# dnf install the runtime dependencies, and even that step is optional
# (--no-deps) if you have them already.
#
#   ./install.sh              normal install (asks before touching dnf)
#   ./install.sh -y           assume yes for the dnf step
#   ./install.sh --no-deps    never call dnf
#   ./install.sh --force      install even if the session checks fail
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

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
info()  { printf '%s==>%s %s\n' "$GREEN$BOLD" "$RESET" "$*"; }
warn()  { printf '%s[!]%s %s\n' "$YELLOW$BOLD" "$RESET" "$*" >&2; }
die()   { printf '%s[x]%s %s\n' "$RED$BOLD" "$RESET" "$*" >&2; exit 1; }

for arg in "$@"; do
    case "$arg" in
        -y|--yes)      ASSUME_YES=1 ;;
        --no-deps)     SKIP_DEPS=1 ;;
        --force)       FORCE=1 ;;
        -h|--help)
            sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) die "unknown option: $arg (try --help)" ;;
    esac
done

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

python_deps_ok() {
    "$PYTHON" - <<'PY' >/dev/null 2>&1
import PyQt6.QtWidgets
import PyQt6.QtDBus
import PIL
import requests
PY
}

install_deps() {
    local missing=()
    python_deps_ok || missing+=(python3-pyqt6 python3-pillow python3-requests)
    command -v kwriteconfig6 >/dev/null 2>&1 || missing+=(kf6-kconfig-core)
    command -v kpackagetool6 >/dev/null 2>&1 || missing+=(kf6-kpackage)

    if (( ${#missing[@]} == 0 )); then
        info "All dependencies are present."
        return 0
    fi

    if (( SKIP_DEPS )); then
        warn "Missing packages (--no-deps given, not installing): ${missing[*]}"
        return 0
    fi

    if ! command -v dnf >/dev/null 2>&1; then
        warn "dnf not found. Install these yourself: ${missing[*]}"
        return 0
    fi

    echo
    warn "The following packages are missing: ${missing[*]}"
    echo "    sudo dnf install ${missing[*]}"
    if (( ! ASSUME_YES )); then
        read -r -p "Run it now? [Y/n] " answer
        case "$answer" in [nN]*) warn "Skipping; install them manually."; return 0 ;; esac
    fi
    sudo dnf install -y "${missing[@]}" || warn "dnf failed — continuing, but the daemon may not start."
}

# --------------------------------------------------------------------------- #
# 3. Install
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
        if kpackagetool6 --type=KWin/Script --list 2>/dev/null | grep -qx "$SCRIPT_ID"; then
            kpackagetool6 --type=KWin/Script --upgrade "$SOURCE_DIR/kwinscript" >/dev/null
        else
            kpackagetool6 --type=KWin/Script --install "$SOURCE_DIR/kwinscript" >/dev/null
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
    reconfigure_kwin
}

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
    done <<'EOF'
enabled=true
reversals=2
windowMs=600
minAmplitudePx=150
angleTolerance=30
pollMs=50
cooldownMs=1500
minStepPx=6
shortcut=Meta+Shift+L
EOF
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
    return $((1 - ok))
}

main() {
    check_session
    pick_python
    info "Using interpreter: $PYTHON"
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
    1. journalctl --user -u plasma-kwin_wayland -f | grep -i circle
       (no "KWin script started" line → the script is not loaded: open
        System Settings → Window Management → KWin Scripts and tick
        "Circle to Search")
    2. journalctl --user -u $SERVICE -f
    3. busctl --user call $APP_ID \\
           /io/github/fand1l/CircleToSearch $APP_ID Trigger iis 100 100 ""
       should open the overlay straight away.
    4. See "What can break" in README.md.
EOF
}

main "$@"
