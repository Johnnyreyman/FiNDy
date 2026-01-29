# FiNDy Package Manager

**F**ast, **i**ntelligent package management for **N**DF, Flatpak, an**D** AppImages

A modern Tkinter-based GUI package manager for OpenMandriva Lx with support for DNF packages, Flatpak applications, and AppImages.

Built with ttkbootstrap for beautiful themes that automatically match your system's light or dark mode!

## Features

- **DNF Package Management**: Browse, search, install, remove, and update DNF packages
- **Flatpak Support**: Manage Flatpak applications from Flathub
- **AppImage Manager**: Add, run, and remove AppImages from a central location
- **Beautiful Modern Themes**: Uses ttkbootstrap with automatic dark/light mode detection
- **System Theme Integration**: Automatically detects KDE Plasma and GNOME theme preferences
- **Automatic Update Checks**: Hourly checks for available updates
- **No Heavy Dependencies**: Just Python, tkinter, and ttkbootstrap!

## Quick Start (Automated Install)

The easiest way to get FiNDy running is to use the provided installation script, which handles dependencies, permissions, and desktop integration automatically.

1. **Download the project files** to your local machine.
2. **Open a terminal** in the project directory.
3. **Run the installer**:
   ```bash
   chmod +x install_tk.sh
   ./install_tk.sh

```

## Manual Installation

If you prefer to set up the environment manually, follow these steps:

### 1. Install System Dependencies

```bash
# Install Python and tkinter
sudo dnf install python3 python3-tkinter python3-pip

# Install package management tools
sudo dnf install dnf flatpak

# Add Flathub repository (if not already added)
flatpak remote-add --if-not-exists flathub [https://flathub.org/repo/flathub.flatpakrepo](https://flathub.org/repo/flathub.flatpakrepo)

```

### 2. Install Python Packages

```bash
# Install ttkbootstrap for modern themes
pip3 install ttkbootstrap --user --break-system-packages

```

### 3. Setup Permissions & Execution

```bash
# Make the script executable
chmod +x findy_tk.py

```

### 4. Create Desktop Entry (Optional)

```bash
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

```

## Usage

### Running the Application

Launch FiNDy from your application menu (look for "FiNDy Package Manager") or run it directly via terminal:

```bash
python3 findy_tk.py

```

### Theme Selection

FiNDy automatically detects your system theme:

* **KDE Plasma**: Reads your ColorScheme setting (supports Breeze Dark/Light)
* **GNOME**: Checks GTK theme preference
* **Fallback**: Uses a dark theme by default

### Tab Overviews

* **DNF Packages**: Search and manage system-level packages. Requires `pkexec` (root) for changes.
* **Flatpak Apps**: Manage user-space applications from Flathub.
* **AppImages**: Manage portable Linux apps. These are stored in `~/.local/share/applications/appimages/`.

## Troubleshooting

### "Command not found" errors

Ensure the core tools are present:

```bash
which dnf flatpak

```

### Permission errors

DNF operations require root access. FiNDy uses `pkexec` to prompt for your password securely. Ensure Polkit is installed:

```bash
sudo dnf install polkit

```

### Theme looks wrong

If the auto-detection fails to match your preference, you can modify the `detect_system_theme()` function in `findy_tk.py` to hardcode a theme from the `ttkbootstrap` library (e.g., `flatly` for light or `darkly` for dark).

## Why Tkinter?

* ✅ **No heavy Qt dependencies** - Small footprint.
* ✅ **Automatic theme matching** - Adapts to KDE or GNOME.
* ✅ **Cross-platform** - Portable across Linux distributions.
* ✅ **Modern look** - High-quality themes via `ttkbootstrap`.

## Contributing

FiNDy is designed specifically for OpenMandriva Lx. Contributions, bug reports, and feature requests are welcome!

## License

This software is provided as-is for use with OpenMandriva Lx.
