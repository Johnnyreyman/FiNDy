#!/bin/bash
# Installation script for FiNDy Package Manager (Tkinter version)
# Targeted at fresh OpenMandriva Lx systems (KDE Plasma default)

set -euo pipefail

echo "======================================="
echo "      FiNDy Package Manager Setup      "
echo "======================================="
echo ""

# ─────────────────────────────────────────────
# 1. Basic dependencies
# ─────────────────────────────────────────────

echo "Checking for Python 3..."
if ! command -v python3 &>/dev/null; then
    echo "Error: Python 3 is not installed."
    echo "Please install it first: sudo dnf install python3"
    exit 1
fi

echo "Checking for Tkinter..."
if ! python3 -c "import tkinter" 2>/dev/null; then
    echo "Tkinter not found → installing package 'tkinter'..."
    sudo dnf install -y tkinter || {
        echo "Failed to install 'tkinter'. Try manually: sudo dnf install tkinter"
        echo "Or search: dnf search tkinter"
        exit 1
    }
fi

# ─────────────────────────────────────────────
# 2. ttkbootstrap
# ─────────────────────────────────────────────

echo ""
echo "Installing ttkbootstrap..."
pip3 install --user --break-system-packages ttkbootstrap || {
    echo "⚠ ttkbootstrap failed — try: pip3 install --user ttkbootstrap"
}

if python3 -c "import ttkbootstrap" 2>/dev/null; then
    echo "✓ ttkbootstrap installed"
else
    echo "Warning: ttkbootstrap not detected — GUI may look basic"
fi

# ─────────────────────────────────────────────
# 3. Flatpak + Flathub
# ─────────────────────────────────────────────

echo ""
echo "Checking for Flatpak..."
if ! command -v flatpak &>/dev/null; then
    echo "Installing flatpak..."
    sudo dnf install -y flatpak || exit 1
fi

echo "Adding Flathub..."
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo

echo "Updating flatpak metadata..."
flatpak update --appstream --assumeyes >/dev/null 2>&1 || true

# ─────────────────────────────────────────────
# 4. GearLever prompt
# ─────────────────────────────────────────────

echo ""
echo "GearLever greatly improves AppImage support in FiNDy."
echo "Install it from Flathub? (recommended)"
echo ""

while true; do
    read -r -p "Install GearLever now? [Y/n] " answer
    case "${answer:-Y}" in
        [Yy]*|"") 
            flatpak install -y flathub it.mijorus.gearlever && echo "✓ GearLever installed" || echo "⚠ Failed — try later: flatpak install flathub it.mijorus.gearlever"
            break ;;
        [Nn]*) 
            echo "Skipping GearLever. Install later if needed."
            break ;;
        *) echo "Please answer y/n." ;;
    esac
done

# ─────────────────────────────────────────────
# 5. Polkit
# ─────────────────────────────────────────────

if ! command -v pkexec &>/dev/null; then
    echo "Installing polkit..."
    sudo dnf install -y polkit || echo "Warning: polkit install failed"
fi

# ─────────────────────────────────────────────
# 6. Executable + Desktop entry
# ─────────────────────────────────────────────

SCRIPT_DIR="$(pwd)"
MAIN_SCRIPT="$SCRIPT_DIR/findy_tk.py"

if [[ ! -f "$MAIN_SCRIPT" ]]; then
    echo "Error: findy_tk.py not found here! cd to its folder first."
    exit 1
fi

ABS_SCRIPT="$(realpath "$MAIN_SCRIPT")"

echo ""
echo "Making executable..."
chmod +x "$MAIN_SCRIPT"

echo "Creating desktop entry..."
mkdir -p ~/.local/share/applications

DESKTOP_FILE=~/.local/share/applications/findy-package-manager.desktop

cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Type=Application
Name=FiNDy Package Manager
Comment=Fast package management: DNF • Flatpak • AppImages (OpenMandriva)
Exec=python3 $ABS_SCRIPT
Icon=system-software-install
Terminal=false
Categories=System;Settings;PackageManager;
StartupNotify=true
EOF

chmod +x "$DESKTOP_FILE"

# ─────────────────────────────────────────────
# 7. Force menu refresh (KDE Plasma focused)
# ─────────────────────────────────────────────

echo "Refreshing application menu cache..."

update-desktop-database ~/.local/share/applications 2>/dev/null || true

# Plasma 6 preferred (OpenMandriva recent uses Plasma 6)
if command -v kbuildsycoca6 &>/dev/null; then
    kbuildsycoca6 --noincremental && echo "✓ Plasma menu cache rebuilt (kbuildsycoca6)"
else
    # Fallback for Plasma 5
    if command -v kbuildsycoca5 &>/dev/null; then
        kbuildsycoca5 --noincremental && echo "✓ Plasma menu cache rebuilt (kbuildsycoca5)"
    fi
fi

# Offer to restart Plasma shell (non-destructive)
echo ""
read -r -p "Restart Plasma now to ensure menu shows? (recommended) [Y/n] " restart_ans
case "${restart_ans:-Y}" in
    [Yy]*|"")
        if command -v plasmashell &>/dev/null; then
            kquitapp6 plasmashell 2>/dev/null && kstart6 plasmashell 2>/dev/null && echo "Plasma restarted."
        else
            echo "plasmashell not found — skipping restart."
        fi
        ;;
    *) echo "Skipping Plasma restart." ;;
esac

# ─────────────────────────────────────────────
# Final instructions
# ─────────────────────────────────────────────

echo ""
echo "======================================="
echo "         Installation complete!        "
echo "======================================="
echo ""
echo "FiNDy should now appear in your KDE menu (search 'FiNDy' or 'Package Manager')."
echo ""
echo "Quick launch:"
echo "  • Menu → search 'FiNDy'"
echo "  • Terminal: python3 \"$ABS_SCRIPT\""
echo ""
echo "If STILL not showing:"
echo "  1. Run: kbuildsycoca6 --noincremental   (or kbuildsycoca5 if on older Plasma)"
echo "  2. Or restart Plasma: kquitapp6 plasmashell && kstart6 plasmashell"
echo "  3. Worst case: log out / log back in"
echo ""
echo "GearLever (if installed) unlocks full AppImage features."
echo ""
echo "Let me know if it works now — or share what happens when you run the commands above!"
echo ""
