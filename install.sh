#!/usr/bin/env bash
# Dev-style install for Fedora: venv with system site packages, editable install,
# `filehouse` on PATH and a launcher entry. Safe to run again.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

bin_dir="$HOME/.local/bin"
apps_dir="$HOME/.local/share/applications"
services_dir="$HOME/.local/share/dbus-1/services"

# PySide6 and PyGObject must come from the distribution (see CLAUDE.md).
missing=()
for pkg in python3-pyside6 python3-gobject; do
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
command -v update-desktop-database >/dev/null && update-desktop-database "$apps_dir"

# "Show in folder" of other apps: the session bus starts the app for it. A service file
# of the user's comes before the system's, which is Dolphin's where that is installed.
mkdir -p "$services_dir"
sed "s|@BIN@|$bin_dir/filehouse|" data/org.freedesktop.FileManager1.service.in \
    >"$services_dir/org.freedesktop.FileManager1.service"
# The session bus reads its service files when it starts and when it is told to, and it
# outlives a logout: without this the new file is not seen until a reboot.
if command -v busctl >/dev/null; then
    busctl --user call org.freedesktop.DBus /org/freedesktop/DBus \
        org.freedesktop.DBus ReloadConfig >/dev/null 2>&1 || true
fi

# A process of the version before may be staying in the background: the new one runs
# only after it has ended, which it does now, or when its windows are closed
"$bin_dir/filehouse" --quit >/dev/null 2>&1 || true

echo "Installed. Run: filehouse ~/Downloads"
echo "To open folders with it by default: gio mime inode/directory filehouse.desktop"
if pgrep -x dolphin >/dev/null; then
    echo "Dolphin is running and answers other apps' \"Show in folder\" until it is closed:"
    echo "  systemctl --user stop plasma-dolphin.service"
fi
case ":$PATH:" in
    *":$bin_dir:"*) ;;
    *) echo "Note: $bin_dir is not on your PATH" ;;
esac
