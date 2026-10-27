#!/usr/bin/env bash
# Dev-style install for Fedora: venv with system site packages, editable install,
# `filehouse` on PATH and a launcher entry. Safe to run again.
#
#   ./install.sh               install or update the app
#   ./install.sh --portal      and make it the Open and Save dialog of other apps
#   ./install.sh --no-portal   and give that back to the desktop's own dialog
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

portal=keep
for arg in "$@"; do
    case "$arg" in
        --portal) portal=on ;;
        --no-portal) portal=off ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

bin_dir="$HOME/.local/bin"
apps_dir="$HOME/.local/share/applications"
icons_dir="$HOME/.local/share/icons/hicolor/scalable/apps"
services_dir="$HOME/.local/share/dbus-1/services"
portals_dir="$HOME/.local/share/xdg-desktop-portal/portals"
portal_conf_dir="${XDG_CONFIG_HOME:-$HOME/.config}/xdg-desktop-portal"

# PySide6 and PyGObject must come from the distribution (see CLAUDE.md). The libarchive
# binding is optional to the app, which leaves archives out of its menu without it, but
# not to an install: nothing in the app says it is missing.
missing=()
for pkg in python3-pyside6 python3-gobject python3-libarchive-c; do
    rpm -q --quiet "$pkg" || missing+=("$pkg")
done
if ((${#missing[@]})); then
    echo "Installing ${missing[*]} with dnf"
    sudo dnf install -y "${missing[@]}"
fi

# The system interpreter, because the distro packages are built for it.
if command -v uv >/dev/null; then
    uv venv --python /usr/bin/python3 --system-site-packages --allow-existing .venv
    uv pip install --python .venv/bin/python -r requirements.txt
else
    /usr/bin/python3 -m venv --system-site-packages .venv
    .venv/bin/python -m pip install -r requirements.txt
fi

.venv/bin/python -c "import gi, PySide6" ||
    { echo "gi or PySide6 is not importable from the venv" >&2; exit 1; }

mkdir -p "$bin_dir"
ln -sf "$PWD/.venv/bin/filehouse" "$bin_dir/filehouse"
install -Dm644 data/filehouse.desktop "$apps_dir/filehouse.desktop"
# The app's icon, under the app's name: the launcher entry, the windows and the tray ask
# the icon theme for it, and fall back to the theme's file manager without it
install -Dm644 icons/filehouse-d.svg "$icons_dir/filehouse.svg"
command -v gtk-update-icon-cache >/dev/null &&
    gtk-update-icon-cache -q -t -f "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
command -v update-desktop-database >/dev/null && update-desktop-database "$apps_dir"

# "Show in folder" of other apps: the session bus starts the app for it. A service file
# of the user's comes before the system's, which is Dolphin's where that is installed.
mkdir -p "$services_dir"
sed "s|@BIN@|$bin_dir/filehouse|" data/org.freedesktop.FileManager1.service.in \
    >"$services_dir/org.freedesktop.FileManager1.service"
# The Open and Save dialog of other apps: the backend the file chooser portal can be told
# to ask, and the service file the bus starts the app for it with. Being there changes
# nothing: the portal asks this backend only when its configuration names it (below).
install -Dm644 data/filehouse.portal "$portals_dir/filehouse.portal"
sed "s|@BIN@|$bin_dir/filehouse|" data/org.freedesktop.impl.portal.desktop.filehouse.service.in \
    >"$services_dir/org.freedesktop.impl.portal.desktop.filehouse.service"
# The session bus reads its service files when it starts and when it is told to, and it
# outlives a logout: without this the new file is not seen until a reboot.
if command -v busctl >/dev/null; then
    busctl --user call org.freedesktop.DBus /org/freedesktop/DBus \
        org.freedesktop.DBus ReloadConfig >/dev/null 2>&1 || true
fi

# A process of the version before may be staying in the background: the new one runs
# only after it has ended, which it does now, or when its windows are closed
"$bin_dir/filehouse" --quit >/dev/null 2>&1 || true

# Which backend the portal asks for a file is in its configuration, and the first file
# found is the whole of it: the user's own takes the place of the desktop's, so it is
# the desktop's with one line more. Without that file the desktop's is used again.
desktop="$(printf '%s' "${XDG_CURRENT_DESKTOP:-}" | cut -d: -f1 | tr '[:upper:]' '[:lower:]')"
portal_conf="$portal_conf_dir/${desktop:+$desktop-}portals.conf"
chooser_key="org.freedesktop.impl.portal.FileChooser"
marker="# Written by filehouse's install.sh"
restart_portal() {
    # The portal reads its backends and its configuration when it starts
    systemctl --user try-restart xdg-desktop-portal.service >/dev/null 2>&1 || true
}
has_owner() {
    busctl --user call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus \
        NameHasOwner s "$1" 2>/dev/null | grep -q true
}
backend_can_answer() {
    # The portal asks for its backend the moment it starts, waits half a minute for one
    # that does not come, and then offers no file dialog at all. So the backend is there
    # before the portal is restarted: started here when nothing of the app runs (the bus
    # would start it too, but not reliably while it reads the service files again). A
    # Filehouse from before this install, kept running by its open windows, is not the
    # backend, and stands in the way of one being started
    command -v busctl >/dev/null || return 0
    backend=org.freedesktop.impl.portal.desktop.filehouse
    for _ in $(seq 20); do
        has_owner "$backend" && return 0
        if ! has_owner io.github.joonaskit.filehouse && [[ -z ${started:-} ]]; then
            started=yes
            setsid -f "$bin_dir/filehouse" --service >/dev/null 2>&1 </dev/null || true
        fi
        sleep 0.5
    done
    return 1
}
if [[ $portal == on ]]; then
    if [[ -e $portal_conf ]] && ! grep -qF "$marker" "$portal_conf"; then
        echo "$portal_conf is your own: to use Filehouse's dialog, add under [preferred]:"
        echo "  $chooser_key=filehouse;${desktop:-gtk}"
    else
        mkdir -p "$portal_conf_dir"
        system_conf="/usr/share/xdg-desktop-portal/${desktop:+$desktop-}portals.conf"
        {
            echo "$marker --portal; removed by --no-portal"
            if [[ -r $system_conf ]] && grep -q '^\[preferred\]' "$system_conf"; then
                grep -v "^$chooser_key=" "$system_conf"
            else
                printf '[preferred]\ndefault=%s\n' "${desktop:-gtk}"
            fi
            # The desktop's own dialog after it, for a desktop where this app is gone
            echo "$chooser_key=filehouse;${desktop:-gtk}"
        } >"$portal_conf"
        if backend_can_answer; then
            restart_portal
            echo "Filehouse is the Open and Save dialog of apps that use the portal."
        else
            echo "Filehouse is set as the Open and Save dialog, but the one that is running"
            echo "is from before this install. Close its windows, then run this again:"
            echo "  ./install.sh --portal"
        fi
        echo "To go back to the desktop's own: ./install.sh --no-portal"
    fi
elif [[ $portal == off ]]; then
    if [[ -e $portal_conf ]] && grep -qF "$marker" "$portal_conf"; then
        rm -f "$portal_conf"
        restart_portal
        echo "The desktop's own Open and Save dialog is used again."
    elif [[ -e $portal_conf ]]; then
        echo "$portal_conf is your own: remove its $chooser_key line yourself."
    fi
fi

echo "Installed. Run: filehouse ~/Downloads"
echo "To open folders with it by default: gio mime inode/directory filehouse.desktop"
if [[ $portal == keep ]] && ! grep -qsF "$marker" "$portal_conf"; then
    echo "To open and save files with it in other apps: ./install.sh --portal"
fi
if pgrep -x dolphin >/dev/null; then
    echo "Dolphin is running and answers other apps' \"Show in folder\" until it is closed:"
    echo "  systemctl --user stop plasma-dolphin.service"
fi
case ":$PATH:" in
    *":$bin_dir:"*) ;;
    *) echo "Note: $bin_dir is not on your PATH" ;;
esac
