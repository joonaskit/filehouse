#!/usr/bin/env bash
# Dev-style install for Fedora: venv with system site packages, editable install,
# `filehouse` on PATH and a launcher entry. Safe to run again.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

bin_dir="$HOME/.local/bin"
apps_dir="$HOME/.local/share/applications"

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

echo "Installed. Run: filehouse ~/Downloads"
case ":$PATH:" in
    *":$bin_dir:"*) ;;
    *) echo "Note: $bin_dir is not on your PATH" ;;
esac
