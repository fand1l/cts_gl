#!/usr/bin/env bash
#
# Circle to Search — uninstaller.  Removes everything install.sh created.
#
#   ./uninstall.sh              remove files, keep the settings
#   ./uninstall.sh --purge      also drop [Script-circletosearch] from kwinrc
#                               and the application's own INI file
#
set -euo pipefail

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

PURGE=0
GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
info() { printf '%s==>%s %s\n' "$GREEN$BOLD" "$RESET" "$*"; }
warn() { printf '%s[!]%s %s\n' "$YELLOW$BOLD" "$RESET" "$*" >&2; }

for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=1 ;;
        -h|--help) sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) warn "unknown option: $arg"; exit 1 ;;
    esac
done

info "Stopping the service"
systemctl --user disable --now "$SERVICE" >/dev/null 2>&1 || true
rm -f "$UNITDIR/$SERVICE"
systemctl --user daemon-reload || true

info "Disabling the KWin script"
kwriteconfig6 --file kwinrc --group Plugins --key "${SCRIPT_ID}Enabled" --type bool false \
    >/dev/null 2>&1 || true
if command -v kpackagetool6 >/dev/null 2>&1; then
    kpackagetool6 --type=KWin/Script --remove "$SCRIPT_ID" >/dev/null 2>&1 || true
fi
rm -rf "$DATA_HOME/kwin/scripts/$SCRIPT_ID"

info "Removing files"
rm -rf "$APPDIR"
rm -f "$BINDIR/$APP_NAME"
rm -f "$DESKTOPDIR/$APP_ID.desktop"
rm -f "$ICONDIR/$APP_ID.svg"

command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "$DESKTOPDIR" >/dev/null 2>&1 || true
command -v kbuildsycoca6 >/dev/null 2>&1 && kbuildsycoca6 >/dev/null 2>&1 || true

if (( PURGE )); then
    info "Purging settings"
    kwriteconfig6 --file kwinrc --group "Script-$SCRIPT_ID" --key enabled --delete \
        >/dev/null 2>&1 || true
    for key in reversals windowMs minAmplitudePx angleTolerance pollMs cooldownMs \
               minStepPx minSpeedPxPerSec maxCurvaturePct reversalTolerance \
               debug trace glow disableInFullscreen shortcut; do
        kwriteconfig6 --file kwinrc --group "Script-$SCRIPT_ID" --key "$key" --delete \
            >/dev/null 2>&1 || true
    done
    rm -rf "$CONFIG_HOME/$APP_NAME"
    kwriteconfig6 --file kwinrc --group Plugins --key "${SCRIPT_ID}Enabled" --delete \
        >/dev/null 2>&1 || true
fi

info "Asking KWin to reload"
if command -v qdbus6 >/dev/null 2>&1; then
    qdbus6 org.kde.KWin /KWin reconfigure >/dev/null 2>&1 || true
elif command -v busctl >/dev/null 2>&1; then
    busctl --user call org.kde.KWin /KWin org.kde.KWin reconfigure >/dev/null 2>&1 || true
fi

info "Uninstalled."
if (( ! PURGE )); then
    echo "    Settings were kept; re-run with --purge to remove them too."
fi
