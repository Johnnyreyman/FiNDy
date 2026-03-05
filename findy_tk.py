#!/usr/bin/env python3
"""
FiNDy Package Manager
A modern Tkinter-based GUI for managing DNF packages, Flatpaks, and AppImages
Fast, Intelligent package management for OpenMandriva Lx
Supports: Cooker, ROME, Rock (auto-detected via enabled repos)
"""

import sys
import subprocess
import os
import threading
import re
import json
import time
from tkinter import *
from tkinter import ttk, messagebox, filedialog
import ttkbootstrap as ttk
from ttkbootstrap.constants import *

# ---------------------------------------------------------------------------
# Optional tray-icon dependencies (graceful fallback if absent)
# ---------------------------------------------------------------------------
try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False

# ---------------------------------------------------------------------------
# GearLever helpers
# ---------------------------------------------------------------------------
GEARLEVER_ID = "it.mijorus.gearlever"

def _gl(*args) -> list:
    """Build a GearLever flatpak command."""
    return ["flatpak", "run", GEARLEVER_ID] + list(args)

def _gl_run(*args, timeout=300) -> subprocess.CompletedProcess:
    """
    Run a GearLever command, automatically confirming any y/n prompts by
    piping 'y' to stdin. This avoids EOFError when GearLever calls input().
    """
    return subprocess.run(
        _gl(*args),
        input="y\n",
        capture_output=True,
        text=True,
        timeout=timeout,
    )

def gearlever_available() -> bool:
    """Check for GearLever in both scopes reliably."""
    for scope_flag in ["--system", "--user"]:
        try:
            result = subprocess.run(
                ["flatpak", "info", scope_flag, GEARLEVER_ID],
                capture_output=True,
                timeout=10,
                check=False
            )
            if result.returncode == 0:
                return True
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass
    return False

def _parse_gearlever_line(line: str) -> dict | None:
    """
    Parse one line of gearlever --list-installed / --list-updates output.
    Format (fixed-width, space-padded):
      Name                     [version]                 [UpdaterType]     /path/to/file
    """
    line = line.rstrip()
    if not line:
        return None
    path_match = re.search(r'(/\S+)\s*$', line)
    if not path_match:
        return None
    path = path_match.group(1)
    brackets = re.findall(r'\[([^\]]+)\]', line)
    version = brackets[0].strip() if len(brackets) > 0 else ""
    name_part = line[:line.index('[')].strip() if '[' in line else ""
    name = name_part if name_part else os.path.basename(path)
    return {"name": name, "path": path, "version": version}

def gearlever_list_installed() -> list[dict]:
    try:
        r = subprocess.run(
            _gl("--list-installed"),
            capture_output=True, text=True, timeout=30
        )
        apps = []
        for line in r.stdout.strip().splitlines():
            parsed = _parse_gearlever_line(line)
            if parsed:
                apps.append(parsed)
        return apps
    except Exception:
        return []

def gearlever_list_updates() -> list[dict]:
    try:
        r = subprocess.run(
            _gl("--list-updates"),
            capture_output=True, text=True, timeout=60
        )
        apps = []
        for line in r.stdout.strip().splitlines():
            parsed = _parse_gearlever_line(line)
            if parsed:
                apps.append(parsed)
        return apps
    except Exception:
        return []

# ---------------------------------------------------------------------------
# Settings persistence (~/.config/findy/settings.json)
# ---------------------------------------------------------------------------
_SETTINGS_DIR = os.path.expanduser("~/.config/findy")
_SETTINGS_FILE = os.path.join(_SETTINGS_DIR, "settings.json")
_FLATPAK_TRACK_FILE = os.path.join(_SETTINGS_DIR, "flatpak_installs.json")

_DEFAULT_SETTINGS = {
    "notifications": True,
    "interval_minutes": 15,
    "minimize_to_tray": True,
    "theme": "darkly",
}

def load_settings() -> dict:
    try:
        with open(_SETTINGS_FILE, "r") as fh:
            stored = json.load(fh)
            merged = dict(_DEFAULT_SETTINGS)
            merged.update(stored)
            return merged
    except Exception:
        return dict(_DEFAULT_SETTINGS)

def save_settings(settings: dict) -> None:
    os.makedirs(_SETTINGS_DIR, exist_ok=True)
    with open(_SETTINGS_FILE, "w") as fh:
        json.dump(settings, fh, indent=2)

def load_flatpak_scopes() -> dict:
    try:
        with open(_FLATPAK_TRACK_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_flatpak_scope(app_id: str, scope: str):
    scopes = load_flatpak_scopes()
    scopes[app_id] = scope
    os.makedirs(_SETTINGS_DIR, exist_ok=True)
    with open(_FLATPAK_TRACK_FILE, "w") as f:
        json.dump(scopes, f, indent=2)

def remove_flatpak_scope(app_id: str):
    scopes = load_flatpak_scopes()
    if app_id in scopes:
        del scopes[app_id]
    os.makedirs(_SETTINGS_DIR, exist_ok=True)
    with open(_FLATPAK_TRACK_FILE, "w") as f:
        json.dump(scopes, f, indent=2)

# ---------------------------------------------------------------------------
# OpenMandriva edition detection
# ---------------------------------------------------------------------------
def detect_omv_edition():
    try:
        with open("/etc/os-release") as fh:
            for line in fh:
                if line.startswith("VARIANT_ID="):
                    val = line.split("=", 1)[1].strip().strip('"').lower()
                    for edition in ("cooker", "rome", "rock"):
                        if edition in val:
                            return edition.capitalize()
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["dnf", "repolist", "--enabled"],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout.lower()
        for edition in ("cooker", "rome", "rock"):
            if edition in output:
                return edition.capitalize()
    except Exception:
        pass
    return "Unknown"

def _display_edition(edition: str) -> str:
    return "ROME" if edition.lower() == "rome" else edition

# ---------------------------------------------------------------------------
# Helper – launch the OMV repo-selector / release-switcher
# ---------------------------------------------------------------------------
def launch_repo_selector():
    try:
        subprocess.Popen(["pkexec", "om-repo-picker"], start_new_session=True)
        return True
    except FileNotFoundError:
        candidates = [
            ["pkexec", "om-release-switcher"],
            ["pkexec", "om-release-selector"],
            ["pkexec", "om-repoman"],
        ]
        for cmd in candidates:
            try:
                subprocess.Popen(cmd, start_new_session=True)
                return True
            except FileNotFoundError:
                continue
    except Exception:
        pass
    messagebox.showwarning(
        "Repo Selector Not Found",
        "Could not find the OpenMandriva repo selector utility.\n\n"
        "Try installing one of these packages:\n"
        " • om-repo-picker\n"
        " • om-release-switcher\n"
        " • om-release-selector\n"
        " • om-repoman\n\n"
        "You can install them via:\n"
        " pkexec dnf install -y om-repo-picker"
    )
    return False

# ---------------------------------------------------------------------------
# Refresh repos / mirrors
# ---------------------------------------------------------------------------
def refresh_repos():
    try:
        subprocess.run(["dnf", "makecache"], capture_output=True, timeout=60)
    except Exception:
        pass
    try:
        subprocess.run(["flatpak", "update", "--appstream"], capture_output=True, timeout=60)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Background update checker
# ---------------------------------------------------------------------------
class UpdateChecker(threading.Thread):
    def __init__(self, callback):
        super().__init__(daemon=True)
        self.callback = callback

    def run(self):
        updates = {"dnf": [], "flatpak": [], "appimage": []}
        try:
            updates["dnf"] = self._check_dnf()
        except Exception:
            pass
        try:
            updates["flatpak"] = self._check_flatpak()
        except Exception:
            pass
        try:
            updates["appimage"] = self._check_appimage()
        except Exception:
            pass
        self.callback(updates)

    def _check_dnf(self):
        result = subprocess.run(
            ["dnf", "check-update", "--quiet"],
            capture_output=True, text=True, timeout=120
        )
        updates = []
        for line in result.stdout.strip().split('\n'):
            line = line.strip()
            if line and not line.startswith('Last metadata') and not line.startswith('Obsoleting'):
                parts = line.split()
                if len(parts) >= 2:
                    updates.append(parts[0])
        return updates

    def _check_flatpak(self):
        result = subprocess.run(
            ["flatpak", "remote-ls", "--updates"],
            capture_output=True, text=True, timeout=60
        )
        updates = []
        for line in result.stdout.strip().split('\n'):
            if line.strip():
                updates.append(line.strip())
        return updates

    def _check_appimage(self):
        if not gearlever_available():
            return []
        return gearlever_list_updates()

# ---------------------------------------------------------------------------
# PackageWorker – with Flatpak scope awareness
# ---------------------------------------------------------------------------
class PackageWorker(threading.Thread):
    def __init__(self, operation, package_type, package_name, callback):
        super().__init__(daemon=True)
        self.operation    = operation
        self.package_type = package_type
        self.package_name = package_name
        self.callback     = callback
        self.scope        = None
        self.main_app     = None

    def run(self):
        try:
            if self.package_type == "dnf":
                self._run_dnf()
            elif self.package_type == "flatpak":
                self._run_flatpak()
            elif self.package_type == "appimage":
                self._run_appimage_via_gearlever()
        except Exception as e:
            self.callback(False, str(e))

    def _run_dnf(self):
        cmd_map = {
            "install": ["pkexec", "dnf", "install", "-y", self.package_name],
            "remove":  ["pkexec", "dnf", "remove",  "-y", self.package_name],
            "update":  ["pkexec", "dnf", "upgrade", "-y"],
        }
        cmd = cmd_map.get(self.operation)
        if not cmd:
            self.callback(False, f"Unknown DNF operation: {self.operation}")
            return
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            self.callback(True, result.stdout)
        else:
            self.callback(False, result.stderr or result.stdout)

    def _run_flatpak(self):
        app_id = self.package_name
        scopes_to_try = []

        if self.operation == "install":
            scope_flag = f"--{self.scope}" if self.scope else "--system"
            scopes_to_try = [scope_flag]
        else:
            scopes = load_flatpak_scopes()
            remembered = scopes.get(app_id)
            if remembered:
                scopes_to_try = [f"--{remembered}"]
            else:
                scopes_to_try = ["--system", "--user"]

        success  = False
        messages = []

        for scope_flag in scopes_to_try:
            try:
                if self.operation == "install":
                    cmd = ["flatpak", "install", scope_flag, "-y", app_id]
                elif self.operation == "remove":
                    cmd = ["flatpak", "uninstall", scope_flag, "-y", app_id]
                elif self.operation == "update":
                    cmd = ["flatpak", "update", scope_flag, "-y", app_id]
                else:
                    continue

                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if result.returncode == 0:
                    success = True
                    messages.append(result.stdout)
                    if self.operation == "remove":
                        remove_flatpak_scope(app_id)
                    else:
                        save_flatpak_scope(app_id, scope_flag.lstrip("--"))
                    break
                else:
                    messages.append(f"{scope_flag}: {result.stderr or result.stdout}")
            except Exception as e:
                messages.append(f"{scope_flag}: {str(e)}")

        if success:
            self.callback(True, "\n".join(messages) or "Done.")
        else:
            self.callback(False, "\n".join(messages) or "Failed in all scopes.")

    def _run_appimage_via_gearlever(self):
        if not gearlever_available():
            self.callback(False, "GearLever is not installed.\n\n"
                "Install it from Flathub:\n"
                " flatpak install flathub it.mijorus.gearlever")
            return
        op_flag = {
            "integrate": "--integrate",
            "update":    "--update",
            "remove":    "--remove",
        }.get(self.operation)
        if op_flag is None:
            self.callback(False, f"Unknown AppImage operation: {self.operation}")
            return
        result = _gl_run(op_flag, self.package_name)
        if result.returncode == 0:
            self.callback(True, result.stdout or "Done.")
        else:
            self.callback(False, result.stderr or result.stdout)

# ---------------------------------------------------------------------------
# DNFTab
# ---------------------------------------------------------------------------
class DNFTab(ttk.Frame):
    def __init__(self, parent, main_app):
        super().__init__(parent)
        self.main_app = main_app
        self.init_ui()

    def init_ui(self):
        search_frame = ttk.Frame(self)
        search_frame.pack(fill=X, padx=10, pady=5)
        ttk.Label(search_frame, text="Search:").pack(side=LEFT)
        self.search_var = StringVar()
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_var)
        self.search_entry.pack(side=LEFT, fill=X, expand=True, padx=5)
        self.search_entry.bind("<Return>", lambda e: self.search_packages())
        ttk.Button(search_frame, text="Search", command=self.search_packages, bootstyle=PRIMARY).pack(side=LEFT)
        ttk.Label(search_frame, text="View:").pack(side=LEFT, padx=5)
        self.view_var = StringVar(value="Installed")
        combo = ttk.Combobox(
            search_frame, textvariable=self.view_var,
            values=["All", "Installed", "Available"],
            state="readonly", width=12
        )
        combo.pack(side=LEFT)
        combo.bind("<<ComboboxSelected>>", lambda e: self._list_selected())

        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill=BOTH, expand=True, padx=10, pady=5)
        cols = ("Name", "Version", "Repo", "Summary")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        self.tree.heading("Name",    text="Name");    self.tree.column("Name",    width=200)
        self.tree.heading("Version", text="Version"); self.tree.column("Version", width=130)
        self.tree.heading("Repo",    text="Repo");    self.tree.column("Repo",    width=120)
        self.tree.heading("Summary", text="Summary"); self.tree.column("Summary", width=350)
        scrollbar = ttk.Scrollbar(tree_frame, orient=VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill=Y)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=X, padx=10, pady=5)
        ttk.Button(btn_frame, text="Install",    command=lambda: self.dnf_action("install"), bootstyle=SUCCESS).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Remove",     command=lambda: self.dnf_action("remove"),  bootstyle=DANGER).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Update All", command=lambda: self.dnf_action("update"),  bootstyle=WARNING).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Repo Selector", command=launch_repo_selector,            bootstyle=INFO).pack(side=RIGHT, padx=5)

        self.status_var = StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status_var, anchor=W).pack(fill=X, padx=10, pady=(0, 5))

    def on_tab_selected(self):
        if not self.tree.get_children():
            self.list_installed()

    def _list_selected(self):
        val = self.view_var.get()
        if val == "All":         self.list_all()
        elif val == "Installed": self.list_installed()
        elif val == "Available": self.list_available()

    def search_packages(self):
        query = self.search_var.get().strip()
        if not query:
            return
        self.status_var.set(f"Searching for '{query}'…")
        threading.Thread(target=self._do_search, args=(query,), daemon=True).start()

    def _do_search(self, query):
        try:
            result = subprocess.run(
                ["dnf", "search", "--quiet", query],
                capture_output=True, text=True, timeout=60
            )
            self.after(0, lambda: self._parse_search(result.stdout))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error", str(e)))

    def _parse_search(self, output):
        for item in self.tree.get_children():
            self.tree.delete(item)
        count = 0
        for line in output.strip().split('\n'):
            line = line.rstrip()
            if not line or line.startswith("===") or line.startswith("---"):
                continue
            m = re.match(r'^(\S+)\s*:\s*(.*)', line)
            if m:
                name = re.sub(r'\.(x86_64|i686|noarch|aarch64|src)$', '', m.group(1))
                self.tree.insert("", END, values=(name, "", "", m.group(2)))
                count += 1
        self.status_var.set(f"Found {count} result(s).")

    def list_all(self):
        self.status_var.set("Listing all packages…")
        threading.Thread(target=self._do_list_all, daemon=True).start()

    def _do_list_all(self):
        try:
            result = subprocess.run(
                ["dnf", "list", "--quiet"],
                capture_output=True, text=True, timeout=120
            )
            self.after(0, lambda: self._parse_dnf_list(result.stdout, label="packages"))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error", str(e)))

    def list_installed(self):
        self.status_var.set("Listing installed packages…")
        threading.Thread(target=self._do_list_installed, daemon=True).start()

    def _do_list_installed(self):
        try:
            result = subprocess.run(
                ["dnf", "list", "--installed", "--quiet"],
                capture_output=True, text=True, timeout=60
            )
            self.after(0, lambda: self._parse_dnf_list(result.stdout, label="installed packages"))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error", str(e)))

    def list_available(self):
        self.status_var.set("Listing available packages…")
        threading.Thread(target=self._do_list_available, daemon=True).start()

    def _do_list_available(self):
        try:
            result = subprocess.run(
                ["dnf", "list", "--available", "--quiet"],
                capture_output=True, text=True, timeout=120
            )
            self.after(0, lambda: self._parse_dnf_list(result.stdout, label="available packages"))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error", str(e)))

    def _parse_dnf_list(self, output, label="packages"):
        for item in self.tree.get_children():
            self.tree.delete(item)
        count = 0
        for line in output.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            if line.startswith("Last metadata") or line.startswith("Installed") \
               or line.startswith("Available") or line.startswith("Extra"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            raw_name = parts[0]
            version  = parts[1]
            repo     = parts[2] if len(parts) > 2 else ""
            name     = re.sub(r'\.(x86_64|i686|noarch|aarch64|armv7hl|src)$', '', raw_name)
            self.tree.insert("", END, values=(name, version, repo, ""))
            count += 1
        self.status_var.set(f"Showing {count} {label}.")

    def dnf_action(self, action):
        if action == "update":
            if not messagebox.askyesno("Confirm", "Update all installed packages?"):
                return
            self.main_app.start_progress()
            PackageWorker("update", "dnf", "", self.on_action_finished).start()
            return
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("No Selection", "Please select a package first.")
            return
        pkg = self.tree.item(sel[0])['values'][0]
        if not messagebox.askyesno("Confirm", f"{action.capitalize()} package '{pkg}'?"):
            return
        self.main_app.start_progress()
        PackageWorker(action, "dnf", pkg, self.on_action_finished).start()

    def on_action_finished(self, success, message):
        self.after(0, lambda: self._action_done(success, message))

    def _action_done(self, success, message):
        self.main_app.stop_progress()
        if success:
            messagebox.showinfo("Success", "Operation completed successfully.")
            self.status_var.set("Done.")
            self.list_installed()
        else:
            messagebox.showerror("Error", message)
            self.status_var.set("Error – see dialog for details.")

# ---------------------------------------------------------------------------
# FlatpakTab
# ---------------------------------------------------------------------------
class FlatpakTab(ttk.Frame):
    def __init__(self, parent, main_app):
        super().__init__(parent)
        self.main_app = main_app
        self.init_ui()

    def init_ui(self):
        search_frame = ttk.Frame(self)
        search_frame.pack(fill=X, padx=10, pady=5)
        ttk.Label(search_frame, text="Search:").pack(side=LEFT)
        self.search_var = StringVar()
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_var)
        self.search_entry.pack(side=LEFT, fill=X, expand=True, padx=5)
        self.search_entry.bind("<Return>", lambda e: self.search_flatpaks())
        ttk.Button(search_frame, text="Search", command=self.search_flatpaks, bootstyle=PRIMARY).pack(side=LEFT)
        ttk.Label(search_frame, text="View:").pack(side=LEFT, padx=5)
        self.view_var = StringVar(value="Installed")
        combo = ttk.Combobox(
            search_frame, textvariable=self.view_var,
            values=["Installed", "Available"],
            state="readonly", width=12
        )
        combo.pack(side=LEFT)
        combo.bind("<<ComboboxSelected>>", lambda e: self._list_selected())

        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill=BOTH, expand=True, padx=10, pady=5)
        cols = ("Name", "App ID", "Version", "Branch", "Description")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        self.tree.heading("Name",        text="Name");        self.tree.column("Name",        width=160)
        self.tree.heading("App ID",      text="App ID");      self.tree.column("App ID",      width=220)
        self.tree.heading("Version",     text="Version");     self.tree.column("Version",     width=100)
        self.tree.heading("Branch",      text="Branch");      self.tree.column("Branch",      width=80)
        self.tree.heading("Description", text="Description"); self.tree.column("Description", width=300)
        scrollbar = ttk.Scrollbar(tree_frame, orient=VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill=Y)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=X, padx=10, pady=5)
        ttk.Button(btn_frame, text="Install", command=lambda: self.flat_action("install"), bootstyle=SUCCESS).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Remove",  command=lambda: self.flat_action("remove"),  bootstyle=DANGER).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Update",  command=lambda: self.flat_action("update"),  bootstyle=WARNING).pack(side=LEFT, padx=5)

        self.status_var = StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status_var, anchor=W).pack(fill=X, padx=10, pady=(0, 5))

    def _list_selected(self):
        val = self.view_var.get()
        if val == "Installed":   self.list_installed()
        elif val == "Available": self.list_available()

    def search_flatpaks(self):
        query = self.search_var.get().strip()
        if not query:
            return
        self.status_var.set(f"Searching for '{query}'…")
        threading.Thread(target=self._do_search, args=(query,), daemon=True).start()

    def _do_search(self, query):
        try:
            result = subprocess.run(
                ["flatpak", "search", "--columns=name,application,version,branch,description", query],
                capture_output=True, text=True, timeout=30
            )
            self.after(0, lambda: self._parse_flatpak_output(result.stdout, is_search=True))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error", str(e)))

    def list_installed(self):
        self.status_var.set("Loading installed Flatpaks…")
        threading.Thread(target=self._do_list_installed, daemon=True).start()

    def _do_list_installed(self):
        try:
            result = subprocess.run(
                ["flatpak", "list", "--columns=name,application,version,branch,description"],
                capture_output=True, text=True, timeout=30
            )
            self.after(0, lambda: self._parse_flatpak_output(result.stdout, is_search=False))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error", str(e)))

    def list_available(self):
        self.status_var.set("Listing available Flatpaks…")
        threading.Thread(target=self._do_list_available, daemon=True).start()

    def _do_list_available(self):
        try:
            result = subprocess.run(
                ["flatpak", "remote-ls", "--columns=name,application,version,branch,description"],
                capture_output=True, text=True, timeout=120
            )
            self.after(0, lambda: self._parse_flatpak_output(result.stdout, is_search=True))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error", str(e)))

    def _parse_flatpak_output(self, output, is_search: bool):
        for item in self.tree.get_children():
            self.tree.delete(item)
        count = 0
        for line in output.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            if line.lower().startswith("name\t") or line.lower().startswith("name "):
                continue
            parts = line.split('\t')
            if len(parts) < 2:
                continue
            name    = parts[0].strip()
            app_id  = parts[1].strip()
            version = parts[2].strip() if len(parts) > 2 else ""
            branch  = parts[3].strip() if len(parts) > 3 else ""
            desc    = parts[4].strip() if len(parts) > 4 else ""
            if not app_id:
                continue
            if not is_search:
                scopes = load_flatpak_scopes()
                if app_id not in scopes:
                    for flag in ["--system", "--user"]:
                        try:
                            res = subprocess.run(
                                ["flatpak", "info", flag, app_id],
                                capture_output=True, timeout=5
                            )
                            if res.returncode == 0:
                                save_flatpak_scope(app_id, flag.lstrip("--"))
                                break
                        except Exception:
                            pass
            self.tree.insert("", END, values=(name, app_id, version, branch, desc))
            count += 1
        label = "search result(s)" if is_search else "Flatpak(s)"
        self.status_var.set(f"{count} {label}.")

    def flat_action(self, action):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("No Selection", "Please select a Flatpak first.")
            return
        app_id = self.tree.item(sel[0])['values'][1]
        if action == "install":
            dlg = Toplevel(self)
            dlg.title("Choose Install Scope")
            dlg.geometry("400x200")
            dlg.transient(self.main_app)
            dlg.grab_set()
            dlg.resizable(False, False)
            ttk.Label(dlg, text=f"How would you like to install\n{app_id}?", font=("", 12)).pack(pady=16)
            choice_var = StringVar(value="system")
            f = ttk.Frame(dlg)
            f.pack(pady=10)
            ttk.Radiobutton(f, text="For all users (system-wide) — recommended",
                            variable=choice_var, value="system", bootstyle="info-toolbutton").pack(anchor=W, padx=40, pady=6)
            ttk.Radiobutton(f, text="Only for current user",
                            variable=choice_var, value="user", bootstyle="info-toolbutton").pack(anchor=W, padx=40, pady=6)
            def proceed():
                scope = choice_var.get()
                dlg.destroy()
                self.main_app.start_progress()
                worker = PackageWorker(action, "flatpak", app_id, self.on_action_finished)
                worker.scope    = scope
                worker.main_app = self.main_app
                worker.start()
            ttk.Button(dlg, text="Install", command=proceed,     bootstyle=SUCCESS).pack(pady=12)
            ttk.Button(dlg, text="Cancel",  command=dlg.destroy, bootstyle="outline-secondary").pack()
            return
        self.main_app.start_progress()
        worker = PackageWorker(action, "flatpak", app_id, self.on_action_finished)
        worker.main_app = self.main_app
        worker.start()

    def on_action_finished(self, success, message):
        self.after(0, lambda: self._action_done(success, message))

    def _action_done(self, success, message):
        self.main_app.stop_progress()
        if success:
            messagebox.showinfo("Success", "Operation completed successfully.")
            self.list_installed()
        else:
            messagebox.showerror("Error", message)
        self.status_var.set("Ready.")

# ---------------------------------------------------------------------------
# AppImageTab
# ---------------------------------------------------------------------------
class AppImageTab(ttk.Frame):
    def __init__(self, parent, main_app):
        super().__init__(parent)
        self.main_app = main_app
        self._gl_ok   = False
        self.init_ui()
        self.check_gearlever_then_load()

    def init_ui(self):
        self.warn_frame = ttk.Frame(self, bootstyle=WARNING)
        self.warn_lbl   = ttk.Label(
            self.warn_frame,
            text="⚠ GearLever is not installed. Install it from Flathub: "
                 "flatpak install flathub it.mijorus.gearlever",
            bootstyle=WARNING, wraplength=700, justify=LEFT
        )
        self.warn_lbl.pack(padx=10, pady=6, anchor=W)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=X, padx=10, pady=8)
        ttk.Button(btn_frame, text="Add / Integrate…", command=self.integrate_appimage, bootstyle=SUCCESS).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Remove",           command=self.remove_appimage,    bootstyle=DANGER).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Run",              command=self.run_appimage,       bootstyle=PRIMARY).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Refresh List",     command=self.load_installed,     bootstyle=SECONDARY).pack(side=RIGHT, padx=5)

        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill=BOTH, expand=True, padx=10, pady=5)
        cols = ("Name", "Version", "Path")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        self.tree.heading("Name",    text="Name");    self.tree.column("Name",    width=200)
        self.tree.heading("Version", text="Version"); self.tree.column("Version", width=100)
        self.tree.heading("Path",    text="Path");    self.tree.column("Path",    width=500)
        scrollbar = ttk.Scrollbar(tree_frame, orient=VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill=Y)

        self.status_var = StringVar(value="Checking for GearLever…")
        ttk.Label(self, textvariable=self.status_var, anchor=W).pack(fill=X, padx=10, pady=(0, 5))

    def check_gearlever_then_load(self):
        def _check():
            ok = gearlever_available()
            self.after(0, lambda: self._on_gl_check(ok))
        threading.Thread(target=_check, daemon=True).start()

    def _on_gl_check(self, ok):
        self._gl_ok = ok
        if ok:
            self.warn_frame.pack_forget()
            self.load_installed()
        else:
            self.warn_frame.pack(fill=X, padx=10, pady=(8, 0))
            self.status_var.set("GearLever not found – install it to manage AppImages.")

    def load_installed(self):
        if not self._gl_ok:
            return
        self.status_var.set("Loading installed AppImages from GearLever…")
        threading.Thread(target=self._do_load, daemon=True).start()

    def _do_load(self):
        apps = gearlever_list_installed()
        self.after(0, lambda: self._populate(apps))

    def _populate(self, apps: list[dict]):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for app in apps:
            self.tree.insert("", END, values=(app["name"], app.get("version", ""), app["path"]))
        self.status_var.set(f"{len(apps)} AppImage(s) managed by GearLever.")

    def _selected_path(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("No Selection", "Please select an AppImage first.")
            return None
        return self.tree.item(sel[0])['values'][2]

    def integrate_appimage(self):
        if not self._gl_ok:
            return
        path = filedialog.askopenfilename(
            title="Select AppImage to integrate",
            filetypes=[("AppImage files", "*.AppImage *.appimage"), ("All files", "*")]
        )
        if not path:
            return
        self.main_app.start_progress("Integrating with GearLever…")
        PackageWorker("integrate", "appimage", path, self._on_done).start()

    def remove_appimage(self):
        path = self._selected_path()
        if not path:
            return
        name = self.tree.item(self.tree.selection()[0])['values'][0]
        if not messagebox.askyesno("Confirm", f"Remove '{name}' via GearLever?\n\n"
                                              "This will trash the AppImage, its .desktop entry, and icons."):
            return
        self.main_app.start_progress("Removing AppImage via GearLever…")
        PackageWorker("remove", "appimage", path, self._on_done).start()

    def run_appimage(self):
        path = self._selected_path()
        if not path:
            return
        try:
            if not os.access(path, os.X_OK):
                os.chmod(path, 0o755)
            subprocess.Popen([path], start_new_session=True)
            self.status_var.set(f"Launched: {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("Launch Error", str(e))

    def _on_done(self, success, message):
        self.after(0, lambda: self._done(success, message))

    def _done(self, success, message):
        self.main_app.stop_progress()
        if success:
            messagebox.showinfo("Success", message or "Operation completed.")
            self.load_installed()
        else:
            messagebox.showerror("Error", message)

# ---------------------------------------------------------------------------
# UpdatesTab
# ---------------------------------------------------------------------------
class UpdatesTab(ttk.Frame):
    def __init__(self, parent, main_app):
        super().__init__(parent)
        self.main_app = main_app
        self._last_appimage_updates: list[dict] = []
        self.init_ui()

    def init_ui(self):
        top = ttk.Frame(self)
        top.pack(fill=X, padx=10, pady=8)
        ttk.Label(top, text="Available Updates", font=("", 13, "bold")).pack(side=LEFT)
        ttk.Button(top, text="Check Now",            command=self.check_updates,       bootstyle=PRIMARY).pack(side=RIGHT, padx=5)
        ttk.Button(top, text="Update All DNF",       command=self.update_all_dnf,      bootstyle=WARNING).pack(side=RIGHT, padx=5)
        ttk.Button(top, text="Update All Flatpak",   command=self.update_all_flatpak,  bootstyle=WARNING).pack(side=RIGHT, padx=5)
        ttk.Button(top, text="Update All AppImages", command=self.update_all_appimage, bootstyle=WARNING).pack(side=RIGHT, padx=5)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill=BOTH, expand=True, padx=10, pady=5)

        dnf_frame = ttk.Frame(self.nb)
        self.nb.add(dnf_frame, text="DNF")
        self.dnf_tree = self._make_tree(dnf_frame, ("Package", "Available Version", "Repo"))

        flat_frame = ttk.Frame(self.nb)
        self.nb.add(flat_frame, text="Flatpak")
        self.flat_tree = self._make_tree(flat_frame, ("App ID", "Name", "Branch"))

        ai_frame = ttk.Frame(self.nb)
        self.nb.add(ai_frame, text="AppImages")
        self.ai_tree = self._make_tree(ai_frame, ("Name", "Version", "Path"))

        self.status_var = StringVar(value="Press 'Check Now' to scan for updates.")
        ttk.Label(self, textvariable=self.status_var, anchor=W).pack(fill=X, padx=10, pady=(0, 5))

    def _make_tree(self, parent, cols):
        f = ttk.Frame(parent)
        f.pack(fill=BOTH, expand=True)
        tree = ttk.Treeview(f, columns=cols, show="headings")
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=200)
        sb = ttk.Scrollbar(f, orient=VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side=LEFT, fill=BOTH, expand=True)
        sb.pack(side=RIGHT, fill=Y)
        return tree

    def check_updates(self):
        self.status_var.set("Checking for updates…")
        self.main_app.start_progress()
        UpdateChecker(self._on_updates).start()

    def _on_updates(self, updates):
        self.after(0, lambda: self._populate(updates))

    def _populate(self, updates):
        self.main_app.stop_progress()
        for item in self.dnf_tree.get_children():  self.dnf_tree.delete(item)
        for item in self.flat_tree.get_children(): self.flat_tree.delete(item)
        for item in self.ai_tree.get_children():   self.ai_tree.delete(item)

        for pkg in updates.get("dnf", []):
            self.dnf_tree.insert("", END, values=(pkg, "", ""))

        for entry in updates.get("flatpak", []):
            parts = entry.split('\t') if '\t' in entry else [entry, "", ""]
            self.flat_tree.insert("", END, values=(
                parts[0],
                parts[1] if len(parts) > 1 else "",
                parts[2] if len(parts) > 2 else ""
            ))

        self._last_appimage_updates = updates.get("appimage", [])
        for app in self._last_appimage_updates:
            self.ai_tree.insert("", END, values=(
                app.get("name", ""),
                app.get("version", ""),
                app.get("path", ""),
            ))

        total = sum(len(v) for v in updates.values())
        self.status_var.set(f"{total} update(s) available.")

    def update_all_dnf(self):
        if not messagebox.askyesno("Confirm", "Update all DNF packages now?"):
            return
        self.main_app.start_progress("Updating DNF packages…")
        PackageWorker("update", "dnf", "", self._on_done).start()

    def update_all_flatpak(self):
        if not messagebox.askyesno("Confirm", "Update all Flatpaks now?"):
            return
        self.main_app.start_progress("Updating Flatpaks…")
        def _do():
            r = subprocess.run(["flatpak", "update", "-y"],
                               capture_output=True, text=True, timeout=300)
            ok = r.returncode == 0
            self.after(0, lambda: self._on_done(ok, r.stdout if ok else r.stderr or r.stdout))
        threading.Thread(target=_do, daemon=True).start()

    def update_all_appimage(self):
        if not gearlever_available():
            messagebox.showwarning("GearLever Missing", "GearLever is not installed.")
            return

        # Always do a fresh check so the list is never stale or empty
        self.main_app.start_progress("Checking for AppImage updates…")
        self.status_var.set("Checking for AppImage updates…")

        def _fetch_then_confirm():
            to_update = gearlever_list_updates()
            self.after(0, lambda: _confirm(to_update))

        def _confirm(to_update):
            self.main_app.stop_progress()
            if not to_update:
                messagebox.showinfo("No Updates", "All AppImages are already up to date.")
                self.status_var.set("All AppImages are up to date.")
                return
            names = "\n".join(f" • {a['name']}" for a in to_update)
            if not messagebox.askyesno(
                "Update AppImages",
                f"{len(to_update)} update(s) available:\n\n{names}\n\nUpdate all now?"
            ):
                self.status_var.set("Update cancelled.")
                return
            # User confirmed — run updates one by one
            self.main_app.start_progress("Updating AppImages…")
            def _do_updates():
                errors = []
                for app in to_update:
                    self.after(0, lambda n=app["name"]:
                               self.main_app.start_progress(f"Updating {n}…"))
                    r = _gl_run("--update", app["path"])
                    if r.returncode != 0:
                        errors.append(f"{app['name']}: {r.stderr or r.stdout}")
                ok  = len(errors) == 0
                msg = (f"Updated {len(to_update) - len(errors)} of {len(to_update)} AppImage(s)."
                       if ok else "\n\n".join(errors))
                self.after(0, lambda: self._on_done(ok, msg))
            threading.Thread(target=_do_updates, daemon=True).start()

        threading.Thread(target=_fetch_then_confirm, daemon=True).start()

    def _on_done(self, success, message):
        self.main_app.stop_progress()
        if success:
            messagebox.showinfo("Success", message or "Update completed.")
            self.check_updates()
        else:
            messagebox.showerror("Error", message)

# ---------------------------------------------------------------------------
# SettingsDialog
# ---------------------------------------------------------------------------
class SettingsDialog(Toplevel):
    def __init__(self, parent, settings: dict, on_save):
        super().__init__(parent)
        self.title("FiNDy Settings")
        self.geometry("420x320")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.settings = dict(settings)
        self.on_save  = on_save
        self._build()

    def _build(self):
        pad = {"padx": 20, "pady": 8}
        ttk.Label(self, text="Settings", font=("", 14, "bold")).pack(pady=(16, 4))
        ttk.Separator(self).pack(fill=X, padx=20)
        self.notif_var = BooleanVar(value=self.settings.get("notifications", True))
        ttk.Checkbutton(self, text="Enable desktop notifications",
                        variable=self.notif_var, bootstyle="round-toggle").pack(anchor=W, **pad)
        self.tray_var = BooleanVar(value=self.settings.get("minimize_to_tray", True))
        ttk.Checkbutton(self, text="Minimize to system tray (requires pystray + Pillow)",
                        variable=self.tray_var, bootstyle="round-toggle",
                        state=NORMAL if TRAY_AVAILABLE else DISABLED).pack(anchor=W, **pad)
        interval_frame = ttk.Frame(self)
        interval_frame.pack(fill=X, **pad)
        ttk.Label(interval_frame, text="Check interval (minutes):").pack(side=LEFT)
        self.interval_var = IntVar(value=self.settings.get("interval_minutes", 15))
        ttk.Spinbox(interval_frame, from_=1, to=1440, textvariable=self.interval_var, width=6).pack(side=LEFT, padx=8)
        theme_frame = ttk.Frame(self)
        theme_frame.pack(fill=X, **pad)
        ttk.Label(theme_frame, text="Theme:").pack(side=LEFT)
        self.theme_var = StringVar(value=self.settings.get("theme", "darkly"))
        themes = ["darkly", "cyborg", "vapor", "solar", "superhero", "flatly", "litera", "pulse"]
        ttk.Combobox(theme_frame, textvariable=self.theme_var, values=themes,
                     width=14, state="readonly").pack(side=LEFT, padx=8)
        ttk.Separator(self).pack(fill=X, padx=20, pady=8)
        btn_row = ttk.Frame(self)
        btn_row.pack(pady=4)
        ttk.Button(btn_row, text="Save",   command=self._save,   bootstyle=SUCCESS,   width=10).pack(side=LEFT, padx=8)
        ttk.Button(btn_row, text="Cancel", command=self.destroy, bootstyle=SECONDARY, width=10).pack(side=LEFT)

    def _save(self):
        self.settings["notifications"]    = self.notif_var.get()
        self.settings["minimize_to_tray"] = self.tray_var.get()
        self.settings["interval_minutes"] = self.interval_var.get()
        self.settings["theme"]            = self.theme_var.get()
        save_settings(self.settings)
        self.on_save(self.settings)
        self.destroy()

# ---------------------------------------------------------------------------
# TrayManager
# ---------------------------------------------------------------------------
class TrayManager:
    def __init__(self, app):
        self.app  = app
        self.icon = None

    def _create_icon_image(self):
        img  = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse((4, 4, 60, 60), fill=(52, 152, 219))
        draw.text((20, 20), "F", fill="white")
        return img

    def show(self):
        if not TRAY_AVAILABLE or self.icon:
            return
        menu = pystray.Menu(
            pystray.MenuItem("Show FiNDy", self._restore),
            pystray.MenuItem("Quit",       self._quit),
        )
        self.icon = pystray.Icon("findy", self._create_icon_image(), "FiNDy", menu)
        threading.Thread(target=self.icon.run, daemon=True).start()

    def hide(self):
        if self.icon:
            self.icon.stop()
            self.icon = None

    def _restore(self, icon=None, item=None):
        self.hide()
        self.app.after(0, self.app.deiconify)

    def _quit(self, icon=None, item=None):
        self.hide()
        self.app.after(0, self.app.quit)

# ---------------------------------------------------------------------------
# FiNDyApp – Main window
# ---------------------------------------------------------------------------
class FiNDyApp(ttk.Window):
    def __init__(self):
        self.settings = load_settings()
        super().__init__(themename=self.settings.get("theme", "darkly"))
        self.edition  = detect_omv_edition()
        display_ed    = _display_edition(self.edition)
        self.title(f"FiNDy — OpenMandriva {display_ed}")
        self.geometry("1000x680")
        self.minsize(800, 500)
        self.tray = TrayManager(self)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_menu()
        self._build_ui()
        self._schedule_update_check()

    def _build_menu(self):
        menubar   = Menu(self)
        self.configure(menu=menubar)
        file_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Refresh Repos/Mirrors", command=self._refresh_repos)
        file_menu.add_command(label="Repo Selector…",        command=launch_repo_selector)
        file_menu.add_separator()
        file_menu.add_command(label="Settings…", command=self._open_settings)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self.quit)
        help_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About FiNDy", command=self._about)

    def _build_ui(self):
        header = ttk.Frame(self, bootstyle=DARK)
        header.pack(fill=X)
        display_ed = _display_edition(self.edition)
        ttk.Label(header, text=f" FiNDy | OpenMandriva {display_ed}",
                  font=("", 13, "bold"), bootstyle=INVERSE+DARK).pack(side=LEFT, pady=6, padx=6)
        self.progress = ttk.Progressbar(header, mode="indeterminate", length=180, bootstyle=INFO)
        self.progress.pack(side=RIGHT, padx=10, pady=8)
        self.progress_label = ttk.Label(header, text="", bootstyle=INVERSE+DARK)
        self.progress_label.pack(side=RIGHT, padx=4)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill=BOTH, expand=True, padx=6, pady=6)
        self.dnf_tab      = DNFTab(self.nb, self)
        self.flatpak_tab  = FlatpakTab(self.nb, self)
        self.appimage_tab = AppImageTab(self.nb, self)
        self.updates_tab  = UpdatesTab(self.nb, self)
        self.nb.add(self.dnf_tab,      text=" DNF ")
        self.nb.add(self.flatpak_tab,  text=" Flatpak ")
        self.nb.add(self.appimage_tab, text=" AppImages ")
        self.nb.add(self.updates_tab,  text=" Updates ")
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self.statusbar_var = StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.statusbar_var, relief=SUNKEN,
                  anchor=W, padding=(6, 2)).pack(fill=X, side=BOTTOM)

    def _on_tab_changed(self, event):
        selected_tab = self.nb.select()
        if selected_tab == str(self.dnf_tab):
            self.dnf_tab.on_tab_selected()

    def start_progress(self, label="Working…"):
        self.progress_label.configure(text=label)
        self.progress.start(10)
        self.statusbar_var.set(label)

    def stop_progress(self):
        self.progress.stop()
        self.progress_label.configure(text="")
        self.statusbar_var.set("Ready.")

    def _refresh_repos(self):
        self.start_progress("Refreshing repos…")
        threading.Thread(
            target=lambda: (refresh_repos(), self.after(0, self.stop_progress)),
            daemon=True
        ).start()

    def _open_settings(self):
        SettingsDialog(self, self.settings, self._apply_settings)

    def _apply_settings(self, new_settings):
        self.settings = new_settings
        try:
            self.style.theme_use(new_settings.get("theme", "darkly"))
        except Exception:
            pass
        self._schedule_update_check()

    def _schedule_update_check(self):
        interval_ms = self.settings.get("interval_minutes", 15) * 60 * 1000
        self.after(interval_ms, self._auto_check_updates)

    def _auto_check_updates(self):
        UpdateChecker(self._notify_updates).start()
        self._schedule_update_check()

    def _notify_updates(self, updates):
        total = sum(len(v) for v in updates.values())
        if total > 0 and self.settings.get("notifications", True):
            self.after(0, lambda: self.statusbar_var.set(
                f"{total} update(s) available — check the Updates tab."
            ))

    def _about(self):
        messagebox.showinfo(
            "About FiNDy",
            "FiNDy Package Manager\n"
            "Fast, Intelligent package management for OpenMandriva Lx\n\n"
            "• DNF packages\n"
            "• Flatpak apps\n"
            "• AppImages (via GearLever)\n\n"
            "Detects edition automatically (Cooker / ROME / Rock)."
        )

    def _on_close(self):
        if TRAY_AVAILABLE and self.settings.get("minimize_to_tray", True):
            self.withdraw()
            self.tray.show()
        else:
            self.quit()

# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = FiNDyApp()
    app.mainloop()