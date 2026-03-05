#!/bin/bash
# Installation script for FiNDy Package Manager (Tkinter version)
# Targeted at fresh OpenMandriva Lx systems

set -euo pipefail

echo "======================================="
echo "      FiNDy Package Manager Setup      "
echo "======================================="
echo ""

# ─────────────────────────────────────────────
#  1. Basic dependencies
# ─────────────────────────────────────────────

echo "Checking for Python 3..."
if ! command -v python3 &>/dev/null; then
    echo "Error: Python 3 is not installed."
    echo "Please install it first:"
    echo "    sudo dnf install python3"
    exit 1
fi

echo "Checking for Tkinter..."
if ! python3 -c "import tkinter" 2>/dev/null; then
    echo "Tkinter not found → installing package 'tkinter'..."
    sudo dnf install -y tkinter || {
        echo "Failed to install the 'tkinter' package."
        echo "Please install it manually:"
        echo "    sudo dnf install tkinter"
        echo "(or search with: dnf search tkinter)"
        exit 1
    }
fi

# ─────────────────────────────────────────────
#  2. Modern theme library (ttkbootstrap)
# ─────────────────────────────────────────────

echo ""
echo "Installing ttkbootstrap (modern Tkinter themes)..."
pip3 install --user --break-system-packages ttkbootstrap || {
    echo "⚠  ttkbootstrap installation failed"
    echo "   You can try again manually:"
    echo "       pip3 install --user ttkbootstrap"
}

if python3 -c "import ttkbootstrap" 2>/dev/null; then
    echo "✓ ttkbootstrap installed successfully"
else
    echo "Warning: ttkbootstrap not detected after install — GUI might look plain"
fi

# ─────────────────────────────────────────────
#  3. Flatpak + Flathub (needed for GearLever & many apps)
# ─────────────────────────────────────────────

echo ""
echo "Checking for Flatpak..."
if ! command -v flatpak &>/dev/null; then
    echo "Flatpak not found → installing..."
    sudo dnf install -y flatpak || {
        echo "Failed to install flatpak. Please install it manually."
        exit 1
    }
fi

echo "Adding Flathub repository (if not already present)..."
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo

echo "Updating flatpak appstream metadata..."
flatpak update --appstream --assumeyes >/dev/null 2>&1 || true

# ─────────────────────────────────────────────
#  4. Ask about GearLever (recommended for AppImages)
# ─────────────────────────────────────────────

echo ""
echo "GearLever is an excellent tool for managing AppImages"
echo "(integrate, update, remove, desktop integration, etc.)"
echo "FiNDy works best with it — highly recommended."
echo ""

while true; do
    read -r -p "Install GearLever from Flathub now? [Y/n] " answer
    case "${answer:-Y}" in
        [Yy]*|"")
            echo "Installing GearLever (flatpak)..."
            flatpak install -y flathub it.mijorus.gearlever && {
                echo "✓ GearLever installed successfully"
            } || {
                echo "⚠ GearLever installation failed — install later with:"
                echo "   flatpak install flathub it.mijorus.gearlever"
            }
            break
            ;;
        [Nn]*)
            echo "Skipping GearLever."
            echo "You can install it anytime for full AppImage support:"
            echo "   flatpak install flathub it.mijorus.gearlever"
            break
            ;;
        *)
            echo "Please answer y or n."
            ;;
    esac
done

# ─────────────────────────────────────────────
#  5. Polkit (for pkexec — used by FiNDy)
# ─────────────────────────────────────────────

if ! command -v pkexec &>/dev/null; then
    echo ""
    echo "pkexec not found → installing polkit..."
    sudo dnf install -y polkit || echo "Warning: could not install polkit"
fi

# ─────────────────────────────────────────────
#  6. Make program executable + desktop integration
# ─────────────────────────────────────────────

SCRIPT_DIR="$(pwd)"
MAIN_SCRIPT="$SCRIPT_DIR/findy_tk.py"

if [[ ! -f "$MAIN_SCRIPT" ]]; then
    echo "Error: findy_tk.py not found in current directory!"
    echo "Please cd into the folder containing findy_tk.py and run this script again."
    exit 1
fi

# Ensure absolute path for reliability
ABS_SCRIPT="$(realpath "$MAIN_SCRIPT")"

echo ""
echo "Making findy_tk.py executable..."
chmod +x "$MAIN_SCRIPT"

echo "Creating desktop entry..."
mkdir -p ~/.local/share/applications

DESKTOP_FILE=~/.local/share/applications/findy-package-manager.desktop

cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Type=Application
Name=FiNDy Package Manager
Comment=Fast package management for DNF • Flatpak • AppImages (OpenMandriva)
Exec=python3 $ABS_SCRIPT
Icon=system-software-install
Terminal=false
Categories=System;Settings;PackageManager;
StartupNotify=true
EOF

chmod +x "$DESKTOP_FILE"

# Refresh desktop database (helps KDE Plasma, LXQt, etc. see the new entry)
echo "Refreshing desktop menu cache..."
update-desktop-database ~/.local/share/applications 2>/dev/null || true

# Optional: try to restart KDE plasmashell if running Plasma (harmless if not)
if command -v plasmashell &>/dev/null; then
    kquitapp5 plasmashell && kstart5 plasmashell >/dev/null 2>&1 || true
fi

# ─────────────────────────────────────────────
#  Final message
# ─────────────────────────────────────────────

echo ""
echo "======================================="
echo "         Installation complete!        "
echo "======================================="
echo ""
echo "FiNDy should now appear in your application menu as 'FiNDy Package Manager'."
echo ""
echo "Quick start options:"
echo "  • Menu / launcher → search for 'FiNDy' or 'Package Manager'"
echo "  • Terminal:         python3 \"$ABS_SCRIPT\""
echo ""
echo "If it doesn't show up right away:"
echo "  • Run: update-desktop-database ~/.local/share/applications"
echo "  • Or log out and back in (or restart your session)"
echo "  • In KDE Plasma: sometimes right-click menu → 'Refresh Desktop' or restart plasmashell helps"
echo ""
echo "GearLever (if installed) will give the best AppImage experience."
echo ""
echo "Enjoy FiNDy on OpenMandriva!"
echo ""
