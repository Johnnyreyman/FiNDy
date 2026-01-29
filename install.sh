#!/bin/bash
# Installation script for FiNDy Package Manager (Tkinter version)

echo "==================================="
echo "FiNDy Package Manager Setup"
echo "==================================="
echo ""

# Check for Python 3
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is not installed"
    echo "Install with: sudo dnf install python3"
    exit 1
fi

# Check for tkinter (usually comes with Python)
echo "Checking for tkinter..."
if ! python3 -c "import tkinter" 2>/dev/null; then
    echo "Tkinter not found. Installing..."
    sudo dnf install -y python3-tkinter
fi

# Install ttkbootstrap for modern themes
echo ""
echo "Installing ttkbootstrap for modern themes..."
pip3 install ttkbootstrap --user --break-system-packages

# Check if it worked
if python3 -c "import ttkbootstrap" 2>/dev/null; then
    echo "✓ ttkbootstrap installed successfully!"
else
    echo "⚠ ttkbootstrap installation failed"
    echo "Try: pip3 install ttkbootstrap --user"
fi

# Check for required tools
echo ""
echo "Checking for package management tools..."

if ! command -v dnf &> /dev/null; then
    echo "Warning: DNF not found (should be available on OpenMandriva)"
fi

if ! command -v flatpak &> /dev/null; then
    echo "Flatpak not found. Installing..."
    sudo dnf install -y flatpak
fi

if ! command -v pkexec &> /dev/null; then
    echo "pkexec not found. Installing polkit..."
    sudo dnf install -y polkit
fi

# Add Flathub repository
echo ""
echo "Setting up Flathub repository..."
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo 2>/dev/null

# Make script executable
echo ""
echo "Making package manager executable..."
chmod +x findy_tk.py

# Create AppImage directory
echo "Creating AppImage directory..."
mkdir -p ~/.local/share/applications/appimages

# Create desktop entry
echo ""
echo "Creating desktop entry..."
mkdir -p ~/.local/share/applications

cat > ~/.local/share/applications/findy-package-manager.desktop << EOF
[Desktop Entry]
Type=Application
Name=FiNDy Package Manager
Comment=Fast, Intelligent package management for DNF, Flatpak & AppImages
Exec=python3 $(pwd)/findy_tk.py
Icon=system-software-install
Terminal=false
Categories=System;PackageManager;
EOF

chmod +x ~/.local/share/applications/findy-package-manager.desktop

echo ""
echo "==================================="
echo "Installation complete!"
echo "==================================="
echo ""
echo "You can now run FiNDy with:"
echo "  python3 findy_tk.py"
echo ""
echo "Or find it in your application menu as:"
echo "  'FiNDy Package Manager'"
echo ""
