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
from tkinter import *
from tkinter import ttk, messagebox, filedialog
import ttkbootstrap as ttk
from ttkbootstrap.constants import *


# ---------------------------------------------------------------------------
# OpenMandriva edition detection
# ---------------------------------------------------------------------------

def detect_omv_edition():
    """
    Detect the running OpenMandriva edition by inspecting enabled DNF repos.
    Returns one of: "Cooker", "ROME", "Rock", or "Unknown".
    """
    try:
        result = subprocess.run(
            ["dnf", "repolist", "--enabled"],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout.lower()

        # The repo IDs contain the edition name, e.g.
        #   openmandriva_cooker_main_release
        #   openmandriva_rome_main_release
        #   openmandriva_rock_main_release
        # We scan for the *first* match so priority order doesn't matter –
        # only one edition's repos should be enabled at a time.
        for edition in ("cooker", "rome", "rock"):
            if edition in output:
                return edition.capitalize()   # "Cooker" / "ROME" / "Rock"

    except Exception:
        pass

    # Second chance: read /etc/os-release VARIANT_ID
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

    return "Unknown"


# Capitalise ROME consistently
def _display_edition(edition: str) -> str:
    return "ROME" if edition.lower() == "rome" else edition


# ---------------------------------------------------------------------------
# Helper – launch the OMV repo-selector / release-switcher
# ---------------------------------------------------------------------------

def launch_repo_selector():
    """
    Try to open the OpenMandriva repo-selector application.
    Checks (in order):
      1. om-release-switcher   (newer name)
      2. om-release-selector   (older name)
      3. om-repoman            (alternative name)
    All are launched with pkexec because they modify repo configuration.
    """
    candidates = [
        ["pkexec", "om-release-switcher"],
        ["pkexec", "om-release-selector"],
        ["pkexec", "om-repoman"],
        ["pkexec", "om-repo-picker"],
    ]
    for cmd in candidates:
        try:
            subprocess.Popen(cmd, start_new_session=True)
            return True
        except FileNotFoundError:
            continue
        except Exception:
            continue

    # Nothing found – inform user
    messagebox.showwarning(
        "Repo Selector Not Found",
        "Could not find the OpenMandriva repo selector utility.\n\n"
        "Try installing one of these packages:\n"
        "  • om-release-switcher\n"
        "  • om-release-selector\n"
        "  • om-repoman\n\n"
        "You can install them via:\n"
        "  pkexec dnf install -y om-release-switcher"
    )
    return False


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------

class PackageWorker(threading.Thread):
    """Worker thread for package operations"""

    def __init__(self, operation, package_type, package_name, callback):
        super().__init__(daemon=True)
        self.operation = operation
        self.package_type = package_type
        self.package_name = package_name   # full path for appimage/gearlever ops
        self.callback = callback

    def run(self):
        try:
            if self.package_type == "dnf":
                self._run_dnf()
            elif self.package_type == "flatpak":
                self._run_flatpak()
            elif self.package_type == "appimage":
                self._run_appimage()
            elif self.package_type == "gearlever":
                self._run_gearlever()
        except Exception as e:
            self.callback(False, str(e))

    # ------------------------------------------------------------------
    def _run_dnf(self):
        """Execute DNF operations – works identically across all OMV editions."""
        cmd_map = {
            "install": ["pkexec", "dnf", "install", "-y", self.package_name],
            "remove":  ["pkexec", "dnf", "remove",  "-y", self.package_name],
            # distro-sync is the correct update verb for all OMV editions
            "update":  ["pkexec", "dnf", "distro-sync", "-y"],
        }

        cmd = cmd_map.get(self.operation)
        if not cmd:
            self.callback(False, "Unknown operation")
            return

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            self.callback(True, f"Successfully {self.operation}ed {self.package_name}")
        else:
            self.callback(False, result.stderr or result.stdout)

    # ------------------------------------------------------------------
    def _run_flatpak(self):
        """Execute Flatpak operations"""
        cmd_map = {
            "install": ["flatpak", "install", "-y", "flathub", self.package_name],
            "remove":  ["flatpak", "uninstall", "-y", self.package_name],
            "update":  ["flatpak", "update", "-y"],
        }

        cmd = cmd_map.get(self.operation)
        if not cmd:
            self.callback(False, "Unknown operation")
            return

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            self.callback(True, f"Successfully {self.operation}ed {self.package_name}")
        else:
            error_msg = result.stderr or result.stdout
            if "No remote chosen" in error_msg or "not installed" in error_msg.lower():
                error_msg += (
                    "\n\nTip: Make sure Flathub is added as a remote:\n"
                    "flatpak remote-add --if-not-exists flathub "
                    "https://flathub.org/repo/flathub.flatpakrepo"
                )
            self.callback(False, error_msg)

    # ------------------------------------------------------------------
    def _run_appimage(self):
        """Execute manual AppImage operations (fallback when Gear Lever is absent)"""
        appimage_dir = os.path.expanduser("~/.local/share/applications/appimages")

        if self.operation == "install":
            if not os.path.exists(appimage_dir):
                os.makedirs(appimage_dir, exist_ok=True)

            dest = os.path.join(appimage_dir, os.path.basename(self.package_name))
            subprocess.run(["cp", self.package_name, dest])
            subprocess.run(["chmod", "+x", dest])
            self.callback(True, f"AppImage installed to {dest}")

        elif self.operation == "remove":
            # self.package_name is already the full path from remove_app()
            if os.path.exists(self.package_name):
                os.remove(self.package_name)
                self.callback(True, f"Removed {os.path.basename(self.package_name)}")
            else:
                self.callback(False, f"AppImage not found: {self.package_name}")

    # ------------------------------------------------------------------
    def _run_gearlever(self):
        """Execute Gear Lever operations via Flatpak CLI.

        self.package_name must be the absolute path to the .appimage file
        for both --integrate and --remove – Gear Lever requires the full path.
        """
        base_cmd = ["flatpak", "run", "it.mijorus.gearlever"]

        if self.operation == "integrate":
            cmd = base_cmd + ["--integrate", self.package_name]
            result = subprocess.run(cmd, capture_output=True, text=True)
            success = result.returncode == 0
            msg = (f"Integrated {os.path.basename(self.package_name)}"
                   if success else result.stderr)
            self.callback(success, msg)

        elif self.operation == "remove":
            # --remove trashes the AppImage, its .desktop file and icons.
            # It requires the full path to the AppImage.
            cmd = base_cmd + ["--remove", self.package_name]
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode == 0:
                self.callback(True, f"Removed {os.path.basename(self.package_name)} via Gear Lever")
            else:
                # Gear Lever failed – attempt manual cleanup as a last resort.
                # self.package_name is already the full path; no need to join.
                self._manual_remove_fallback(result.stderr)

        elif self.operation == "update":
            # --fetch-updates checks for and applies updates
            cmd = base_cmd + ["--fetch-updates"]
            result = subprocess.run(cmd, capture_output=True, text=True)
            self.callback(result.returncode == 0, "Gear Lever: Update check completed.")

    # ------------------------------------------------------------------
    def _manual_remove_fallback(self, gearlever_stderr):
        """Last-resort cleanup when Gear Lever --remove fails.

        Removes:
          • The AppImage file itself (self.package_name – full path)
          • ~/.local/share/applications/<stem>.desktop
          • Any <stem>.desktop sitting next to the AppImage
        """
        basename = os.path.basename(self.package_name)                # e.g. MyApp-1.0.AppImage
        stem     = re.sub(r'\.appimage$', '', basename, flags=re.IGNORECASE)  # e.g. MyApp-1.0

        removed_files = []
        errors        = []

        # 1. Remove the AppImage itself
        try:
            if os.path.exists(self.package_name):
                os.remove(self.package_name)
                removed_files.append(basename)
            else:
                errors.append(f"AppImage not found: {self.package_name}")
        except OSError as e:
            errors.append(f"Could not remove AppImage: {e}")

        # 2. Candidate .desktop files to clean up
        desktop_candidates = [
            os.path.join(os.path.dirname(self.package_name), f"{stem}.desktop"),   # next to the AppImage
            os.path.expanduser(f"~/.local/share/applications/{stem}.desktop"),     # standard user apps dir
        ]

        for desktop_path in desktop_candidates:
            try:
                if os.path.exists(desktop_path):
                    os.remove(desktop_path)
                    removed_files.append(os.path.basename(desktop_path))
            except OSError as e:
                errors.append(f"Could not remove {desktop_path}: {e}")

        # 3. Report outcome
        if removed_files:
            msg = f"Removed (manual fallback): {', '.join(removed_files)}"
            if errors:
                msg += f"\nWarnings: {'; '.join(errors)}"
            self.callback(True, msg)
        else:
            self.callback(
                False,
                f"Gear Lever failed: {gearlever_stderr}\n"
                f"Manual fallback also failed: {'; '.join(errors)}"
            )


# ---------------------------------------------------------------------------
# DNF Tab
# ---------------------------------------------------------------------------

class DNFTab(ttk.Frame):
    """Tab for DNF package management"""

    def __init__(self, parent, main_app):
        super().__init__(parent)
        self.main_app = main_app
        self.init_ui()

    def init_ui(self):
        search_frame = ttk.Frame(self)
        search_frame.pack(fill=X, padx=10, pady=10)

        self.search_var = StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=40)
        search_entry.pack(side=LEFT, padx=(0, 5))
        search_entry.bind('<Return>', lambda e: self.search_packages())

        self.filter_var = StringVar(value="All Packages")
        filter_combo = ttk.Combobox(
            search_frame, textvariable=self.filter_var,
            values=["All Packages", "Installed Only", "Available Only"],
            state="readonly", width=15
        )
        filter_combo.pack(side=LEFT, padx=5)

        ttk.Button(search_frame, text="Search", command=self.search_packages,
                   bootstyle=PRIMARY).pack(side=LEFT, padx=5)

        # OMV Repo Selector button – lives in the DNF tab toolbar
        ttk.Button(search_frame, text="OMV Repo Selector",
                   command=self._open_repo_selector,
                   bootstyle="outline-info").pack(side=RIGHT, padx=(10, 0))

        # --- package list --------------------------------------------------
        list_frame = ttk.Frame(self)
        list_frame.pack(fill=BOTH, expand=True, padx=10, pady=(0, 10))

        columns = ("Name", "Description", "Version", "Repository", "Status")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=15)

        for col in columns:
            self.tree.heading(col, text=col)
            if col == "Description":
                self.tree.column(col, width=250)
            elif col == "Name":
                self.tree.column(col, width=150)
            else:
                self.tree.column(col, width=100)

        scrollbar = ttk.Scrollbar(list_frame, orient=VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill=Y)

        self.tree.bind('<Double-Button-1>', self.show_package_details)

        # --- action buttons ------------------------------------------------
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=X, padx=10, pady=(0, 10))

        ttk.Button(btn_frame, text="Install",
                   command=lambda: self.package_action("install"),
                   bootstyle=SUCCESS).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Remove",
                   command=lambda: self.package_action("remove"),
                   bootstyle=DANGER).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Update All",
                   command=self.update_all,
                   bootstyle=WARNING).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Refresh List",
                   command=self.load_installed_packages,
                   bootstyle=INFO).pack(side=LEFT, padx=5)

        # --- log area ------------------------------------------------------
        self.output_text = Text(self, height=5, wrap=WORD)
        self.output_text.pack(fill=X, padx=10, pady=(0, 10))

        self.after(500, self.load_installed_packages)

    # ------------------------------------------------------------------
    def _open_repo_selector(self):
        """Launch the OMV repo selector and refresh the package list after."""
        launch_repo_selector()
        # Give the tool a moment, then refresh so any repo changes are visible
        self.after(2000, self.load_installed_packages)

    # ------------------------------------------------------------------
    def log(self, message):
        self.output_text.insert(END, message + "\n")
        self.output_text.see(END)

    # ------------------------------------------------------------------
    def search_packages(self):
        query       = self.search_var.get().strip()
        filter_type = self.filter_var.get()
        self.main_app.start_progress()

        def run_search():
            try:
                if filter_type == "Installed Only":
                    cmd = ["dnf", "list", "--installed"]
                elif filter_type == "Available Only":
                    cmd = ["dnf", "list", "--available"]
                else:
                    cmd = ["dnf", "list"]

                if query:
                    cmd.append(f"*{query}*")

                res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

                # Collect package names for bulk description fetch
                package_names = []
                lines = res.stdout.split('\n')
                for line in lines:
                    line = line.strip()
                    if (line
                            and not line.startswith("Installed")
                            and not line.startswith("Available")
                            and not line.startswith("Last")):
                        parts = re.split(r'\s+', line)
                        if len(parts) >= 3:
                            pkg_full_name = parts[0]
                            pkg_name = (pkg_full_name.rsplit('.', 1)[0]
                                        if '.' in pkg_full_name else pkg_full_name)
                            package_names.append(pkg_name)

                # Bulk-fetch descriptions (first 50 to keep it fast)
                desc_map = {}
                if package_names:
                    packages_to_query = package_names[:50]
                    cmd_desc = ["dnf", "info"] + packages_to_query
                    desc_res = subprocess.run(cmd_desc, capture_output=True, text=True, timeout=30)
                    desc_map = self.parse_dnf_info_output(desc_res.stdout)

                self.after(0, lambda: self.parse_dnf_list_output(res.stdout, desc_map))

            except subprocess.TimeoutExpired:
                self.after(0, lambda: self.log("Timeout: DNF command took too long"))
            except Exception as e:
                self.after(0, lambda: self.log(f"Error: {e}"))
            finally:
                self.after(0, self.main_app.stop_progress)

        threading.Thread(target=run_search, daemon=True).start()

    # ------------------------------------------------------------------
    def parse_dnf_info_output(self, info_output):
        """Parse DNF info output to extract package descriptions"""
        desc_map        = {}
        current_pkg     = ""
        current_desc    = ""
        in_description  = False

        for line in info_output.split('\n'):
            line = line.strip()

            if line.startswith("Name"):
                if current_pkg and current_desc:
                    desc_map[current_pkg] = current_desc
                parts = line.split(":", 1)
                if len(parts) > 1:
                    current_pkg  = parts[1].strip()
                    current_desc = ""
                    in_description = False

            elif line.startswith("Summary"):
                parts = line.split(":", 1)
                if len(parts) > 1 and current_pkg:
                    current_desc = parts[1].strip()

            elif in_description and line and not line.startswith(":"):
                current_desc += " " + line
            elif line.startswith("Description"):
                in_description = True
            elif in_description and (line.startswith(":") or not line):
                in_description = False

        if current_pkg and current_desc:
            desc_map[current_pkg] = current_desc

        return desc_map

    # ------------------------------------------------------------------
    def parse_dnf_list_output(self, list_output, desc_map):
        """Parse DNF list output and populate the tree"""
        for item in self.tree.get_children():
            self.tree.delete(item)

        for line in list_output.split('\n'):
            line = line.strip()
            if (not line
                    or line.startswith("Installed")
                    or line.startswith("Available")
                    or line.startswith("Last")):
                continue

            parts = re.split(r'\s+', line)
            if len(parts) >= 3:
                pkg_full_name = parts[0]
                pkg_name = (pkg_full_name.rsplit('.', 1)[0]
                            if '.' in pkg_full_name else pkg_full_name)

                description = desc_map.get(pkg_name,
                              desc_map.get(pkg_full_name, "No description available"))
                version = parts[1]
                repo    = parts[2]
                status  = "installed" if repo.startswith('@') else "available"

                self.tree.insert("", END,
                                 values=(pkg_full_name, description, version, repo, status))

    # ------------------------------------------------------------------
    def load_installed_packages(self):
        self.search_packages()

    def package_action(self, action):
        sel = self.tree.selection()
        if not sel:
            return
        pkg = self.tree.item(sel[0])['values'][0]
        self.main_app.start_progress()
        PackageWorker(action, "dnf", pkg, self.on_finished).start()

    def update_all(self):
        self.main_app.start_progress()
        PackageWorker("update", "dnf", "", self.on_finished).start()

    def on_finished(self, success, msg):
        self.main_app.stop_progress()
        self.log(msg)
        if success:
            self.search_packages()

    # ------------------------------------------------------------------
    def show_package_details(self, event):
        sel = self.tree.selection()
        if not sel:
            return

        values  = self.tree.item(sel[0])['values']
        details = (
            f"Package Details:\n"
            f"────────────────\n"
            f"Name        : {values[0]}\n"
            f"Description : {values[1]}\n"
            f"Version     : {values[2]}\n"
            f"Repository  : {values[3]}\n"
            f"Status      : {values[4]}\n\n"
            f"Full Information:\n"
            f"────────────────"
        )

        try:
            result = subprocess.run(
                ["dnf", "info", values[0]],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                details += f"\n{result.stdout}"
        except Exception as e:
            details += f"\nCould not fetch additional info: {e}"

        self.output_text.delete(1.0, END)
        self.output_text.insert(END, details)
        self.output_text.see(END)


# ---------------------------------------------------------------------------
# Flatpak Tab
# ---------------------------------------------------------------------------

class FlatpakTab(ttk.Frame):
    """Tab for Flatpak management"""

    def __init__(self, parent, main_app):
        super().__init__(parent)
        self.main_app = main_app
        self.init_ui()

    def init_ui(self):
        search_frame = ttk.Frame(self)
        search_frame.pack(fill=X, padx=10, pady=10)

        self.search_var = StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=40)
        search_entry.pack(side=LEFT, padx=(0, 5))
        search_entry.bind('<Return>', lambda e: self.search_flatpaks())

        self.filter_var = StringVar(value="Installed Apps")
        ttk.Combobox(
            search_frame, textvariable=self.filter_var,
            values=["Installed Apps", "Search Flathub"],
            state="readonly"
        ).pack(side=LEFT, padx=5)

        ttk.Button(search_frame, text="Search",
                   command=self.search_flatpaks).pack(side=LEFT, padx=5)

        # --- list ----------------------------------------------------------
        list_frame = ttk.Frame(self)
        list_frame.pack(fill=BOTH, expand=True, padx=10, pady=(0, 10))

        columns = ("Name", "Description", "ID", "Version", "Branch")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings")

        for col in columns:
            self.tree.heading(col, text=col)
            if col == "Description":
                self.tree.column(col, width=250)
            elif col in ("Name", "ID"):
                self.tree.column(col, width=150)
            else:
                self.tree.column(col, width=100)

        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        self.tree.bind('<Double-Button-1>', self.show_flatpak_details)

        # --- buttons -------------------------------------------------------
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=X, padx=10, pady=(0, 10))

        ttk.Button(btn_frame, text="Install",
                   command=lambda: self.flat_action("install"),
                   bootstyle=SUCCESS).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Remove",
                   command=lambda: self.flat_action("remove"),
                   bootstyle=DANGER).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Update All",
                   command=self.update_all,
                   bootstyle=WARNING).pack(side=LEFT, padx=5)

        # --- log -----------------------------------------------------------
        self.output_text = Text(self, height=5, wrap=WORD)
        self.output_text.pack(fill=X, padx=10, pady=(0, 10))

        self.after(500, self.search_flatpaks)

    # ------------------------------------------------------------------
    def log(self, message):
        self.output_text.insert(END, message + "\n")
        self.output_text.see(END)

    # ------------------------------------------------------------------
    def search_flatpaks(self):
        query = self.search_var.get().strip().lower()
        self.main_app.start_progress()

        def run_search():
            try:
                if self.filter_var.get() == "Installed Apps":
                    cmd = ["flatpak", "list", "--app",
                           "--columns=application,name,version,branch,description"]
                    res = subprocess.run(cmd, capture_output=True, text=True)
                    self.after(0, lambda: self.parse_flat_output(
                        res.stdout, is_search=False, query_filter=query))
                else:
                    if query:
                        cmd = ["flatpak", "search", query,
                               "--columns=application,name,version,branch,description"]
                        res = subprocess.run(cmd, capture_output=True, text=True)
                        self.after(0, lambda: self.parse_flat_output(
                            res.stdout, is_search=True, query_filter=None))
                    else:
                        cmd = ["flatpak", "list", "--app",
                               "--columns=application,name,version,branch,description"]
                        res = subprocess.run(cmd, capture_output=True, text=True)
                        self.after(0, lambda: self.parse_flat_output(
                            res.stdout, is_search=False, query_filter=None))
            finally:
                self.after(0, self.main_app.stop_progress)

        threading.Thread(target=run_search, daemon=True).start()

    # ------------------------------------------------------------------
    def parse_flat_output(self, output, is_search, query_filter=None):
        for item in self.tree.get_children():
            self.tree.delete(item)

        for line in output.strip().split('\n'):
            if "Application ID" in line or line.startswith("Name"):
                continue

            parts = line.split('\t')
            if len(parts) >= 5:
                app_id, app_name, version, branch, description = (
                    parts[0], parts[1], parts[2], parts[3], parts[4]
                )
            elif len(parts) == 4:
                app_id, app_name, version, branch = parts
                description = "No description available"
            else:
                continue

            if query_filter:
                if (query_filter not in app_name.lower()
                        and query_filter not in app_id.lower()
                        and query_filter not in description.lower()):
                    continue

            self.tree.insert("", END, values=(app_name, description, app_id, version, branch))

    # ------------------------------------------------------------------
    def flat_action(self, action):
        sel = self.tree.selection()
        if not sel:
            return
        app_id = self.tree.item(sel[0])['values'][2]
        self.main_app.start_progress()
        PackageWorker(action, "flatpak", app_id, self.on_finished).start()

    def update_all(self):
        self.main_app.start_progress()
        PackageWorker("update", "flatpak", "", self.on_finished).start()

    def on_finished(self, success, msg):
        self.main_app.stop_progress()
        self.log(msg)
        self.search_flatpaks()

    # ------------------------------------------------------------------
    def show_flatpak_details(self, event):
        sel = self.tree.selection()
        if not sel:
            return

        values  = self.tree.item(sel[0])['values']
        details = (
            f"Flatpak Details:\n"
            f"────────────────\n"
            f"Name           : {values[0]}\n"
            f"Description    : {values[1]}\n"
            f"Application ID : {values[2]}\n"
            f"Version        : {values[3]}\n"
            f"Branch         : {values[4]}\n\n"
            f"Full Information:\n"
            f"────────────────"
        )

        try:
            result = subprocess.run(
                ["flatpak", "info", values[2]],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                details += f"\n{result.stdout}"
        except Exception as e:
            details += f"\nCould not fetch additional info: {e}"

        self.output_text.delete(1.0, END)
        self.output_text.insert(END, details)
        self.output_text.see(END)


# ---------------------------------------------------------------------------
# AppImage Tab
# ---------------------------------------------------------------------------

class AppImageTab(ttk.Frame):
    """Tab for AppImage management with Gear Lever automation"""

    def __init__(self, parent, main_app):
        super().__init__(parent)
        self.main_app = main_app
        self.appimage_dir = os.path.expanduser("~/AppImages")

        if not os.path.exists(self.appimage_dir):
            self.appimage_dir = os.path.expanduser("~/.local/share/applications/appimages")

        self.gear_lever_active = self._check_gear_lever()
        self.init_ui()

    # ------------------------------------------------------------------
    def _check_gear_lever(self):
        try:
            res = subprocess.run(
                ["flatpak", "info", "it.mijorus.gearlever"],
                capture_output=True
            )
            return res.returncode == 0
        except Exception:
            return False

    # ------------------------------------------------------------------
    def init_ui(self):
        status_text = "Gear Lever Mode: Active" if self.gear_lever_active else "Manual Mode"

        search_frame = ttk.Frame(self)
        search_frame.pack(fill=X, padx=10, pady=10)

        self.search_var = StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=40)
        search_entry.pack(side=LEFT, padx=(0, 5))
        search_entry.bind('<Return>', lambda e: self.load_apps())

        ttk.Button(search_frame, text="Search Local",
                   command=self.load_apps, bootstyle=PRIMARY).pack(side=LEFT, padx=5)
        ttk.Label(search_frame, text=f"({status_text})",
                  bootstyle=INFO).pack(side=RIGHT, padx=10)

        # --- list ----------------------------------------------------------
        list_frame = ttk.Frame(self)
        list_frame.pack(fill=BOTH, expand=True, padx=10, pady=(0, 10))

        columns = ("App Name", "Filename", "Description", "Size", "Last Modified")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings")

        for col in columns:
            self.tree.heading(col, text=col)
            if col == "Description":
                self.tree.column(col, width=200)
            elif col == "App Name":
                self.tree.column(col, width=150)
            elif col == "Filename":
                self.tree.column(col, width=120)
            else:
                self.tree.column(col, width=80)

        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        self.tree.bind('<Double-Button-1>', self.show_appimage_details)

        # --- buttons -------------------------------------------------------
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=X, padx=10, pady=(0, 10))

        add_text = "Add (Integrate)" if self.gear_lever_active else "Add Manual"
        ttk.Button(btn_frame, text=add_text,
                   command=self.add_app, bootstyle=SUCCESS).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Run",
                   command=self.run_app, bootstyle=PRIMARY).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Remove",
                   command=self.remove_app, bootstyle=DANGER).pack(side=LEFT, padx=5)

        if self.gear_lever_active:
            ttk.Button(btn_frame, text="Check Updates",
                       command=self.check_updates, bootstyle=WARNING).pack(side=LEFT, padx=5)

        ttk.Button(btn_frame, text="Refresh",
                   command=self.load_apps).pack(side=RIGHT, padx=5)

        # --- log -----------------------------------------------------------
        self.output_text = Text(self, height=5, wrap=WORD)
        self.output_text.pack(fill=X, padx=10, pady=(0, 10))

        self.after(500, self.load_apps)

    # ------------------------------------------------------------------
    def load_apps(self):
        query = self.search_var.get().lower().strip()
        for item in self.tree.get_children():
            self.tree.delete(item)

        if not os.path.exists(self.appimage_dir):
            return

        from datetime import datetime

        for f in os.listdir(self.appimage_dir):
            if f.lower().endswith('.appimage'):
                fpath = os.path.join(self.appimage_dir, f)
                app_name, description = self.get_appimage_metadata(f, fpath)

                if (query
                        and query not in app_name.lower()
                        and query not in f.lower()
                        and query not in description.lower()):
                    continue

                size  = os.path.getsize(fpath)
                mtime = os.path.getmtime(fpath)
                mtime_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')

                self.tree.insert("", END, values=(
                    app_name, f, description,
                    f"{size // 1048576} MB", mtime_str
                ))

    # ------------------------------------------------------------------
    def get_appimage_metadata(self, filename, filepath):
        """Extract actual app name and description from AppImage"""
        app_name    = ""
        description = ""

        # Try .desktop file created by Gear Lever or similar
        desktop_file = filepath + '.desktop'
        if os.path.exists(desktop_file):
            try:
                with open(desktop_file, 'r') as fh:
                    for line in fh:
                        line = line.strip()
                        if line.startswith('Name=') and not app_name:
                            app_name = line.split('=', 1)[1].strip()
                        elif line.startswith('Comment=') and not description:
                            description = line.split('=', 1)[1].strip()
            except Exception:
                pass

        # Fallback: derive from filename
        if not app_name:
            name = os.path.splitext(filename)[0]
            name = name.replace('.AppImage', '').replace('.appimage', '')
            name = name.replace('_', ' ').replace('-', ' ')
            name = re.sub(r'[0-9]+(\.[0-9]+)*', '', name).strip()
            app_name = ' '.join(word.capitalize() for word in name.split())

        if not description:
            description = f"AppImage: {app_name}"

        return app_name, description

    # ------------------------------------------------------------------
    def add_app(self):
        f = filedialog.askopenfilename(
            filetypes=[("AppImage", "*.AppImage *.appimage")]
        )
        if f:
            self.main_app.start_progress()
            if self.gear_lever_active:
                PackageWorker("integrate", "gearlever", f, self.on_finished).start()
            else:
                PackageWorker("install", "appimage", f, self.on_finished).start()

    def check_updates(self):
        if self.gear_lever_active:
            self.main_app.start_progress()
            PackageWorker("update", "gearlever", "", self.on_finished).start()

    def run_app(self):
        sel = self.tree.selection()
        if not sel:
            return
        filename = self.tree.item(sel[0])['values'][1]
        path = os.path.join(self.appimage_dir, filename)
        subprocess.Popen([path], start_new_session=True)

    def remove_app(self):
        sel = self.tree.selection()
        if not sel:
            return
        filename  = self.tree.item(sel[0])['values'][1]
        full_path = os.path.join(self.appimage_dir, filename)

        if messagebox.askyesno("Confirm", f"Remove {filename}?"):
            self.main_app.start_progress()
            ptype = "gearlever" if self.gear_lever_active else "appimage"
            PackageWorker("remove", ptype, full_path, self.on_finished).start()

    def on_finished(self, success, msg):
        self.main_app.stop_progress()
        messagebox.showinfo("FiNDy", msg)
        self.load_apps()

    # ------------------------------------------------------------------
    def show_appimage_details(self, event):
        sel = self.tree.selection()
        if not sel:
            return

        values   = self.tree.item(sel[0])['values']
        app_name = values[0]
        filename = values[1]
        filepath = os.path.join(self.appimage_dir, filename)

        details = (
            f"AppImage Details:\n"
            f"────────────────\n"
            f"App Name      : {app_name}\n"
            f"Filename      : {filename}\n"
            f"Description   : {values[2]}\n"
            f"Size          : {values[3]}\n"
            f"Last Modified : {values[4]}\n"
            f"Path          : {filepath}\n\n"
            f"File Information:\n"
            f"────────────────"
        )

        try:
            import stat
            import datetime
            st = os.stat(filepath)
            details += f"\nPermissions : {oct(stat.S_IMODE(st.st_mode))}"
            details += (f"\nCreated     : "
                        f"{datetime.datetime.fromtimestamp(st.st_ctime).strftime('%Y-%m-%d %H:%M:%S')}")
            details += f"\nExecutable  : {'Yes' if os.access(filepath, os.X_OK) else 'No'}"

            try:
                result = subprocess.run(
                    [filepath, "--appimage-version"],
                    capture_output=True, text=True, timeout=2
                )
                if result.returncode == 0:
                    details += f"\nAppImage Ver: {result.stdout.strip()}"
            except Exception:
                pass

        except Exception as e:
            details += f"\nCould not fetch file info: {e}"

        self.output_text.delete(1.0, END)
        self.output_text.insert(END, details)
        self.output_text.see(END)


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------

class FiNDyApp(ttk.Window):
    def __init__(self):
        super().__init__(themename="darkly")

        # Detect edition once at startup
        self.omv_edition = detect_omv_edition()
        display_ed       = _display_edition(self.omv_edition)

        self.title(f"FiNDy Package Manager – OpenMandriva {display_ed}")
        self.geometry("1000x750")

        # Progress bar (top)
        self.progress = ttk.Progressbar(self, mode='indeterminate', bootstyle="success")
        self.progress.pack(fill=X, side=TOP)

        # Header with edition badge
        header = ttk.Frame(self)
        header.pack(fill=X, padx=20, pady=10)

        ttk.Label(header, text="FiNDy Package Manager",
                  font=("", 18, "bold")).pack(side=LEFT, anchor=W)

        # Edition badge on the right of the header
        badge_style = {
            "Cooker":  "success",   # green  – bleeding edge
            "ROME":    "warning",   # orange – rolling release
            "Rock":    "info",      # blue   – stable
        }
        badge_color = badge_style.get(self.omv_edition, "secondary")
        ttk.Label(header,
                  text=f"  Edition: {display_ed}  ",
                  bootstyle=f"{badge_color}-inverse",
                  font=("", 11, "bold")
                  ).pack(side=RIGHT, anchor=E, padx=(10, 0))

        # Notebook / tabs
        self.notebook = ttk.Notebook(self, bootstyle="primary")
        self.notebook.pack(fill=BOTH, expand=True, padx=10, pady=10)

        self.notebook.add(DNFTab(self.notebook, self),       text=" DNF Packages ")
        self.notebook.add(FlatpakTab(self.notebook, self),   text=" Flatpak Apps ")
        self.notebook.add(AppImageTab(self.notebook, self),  text=" AppImages    ")

        # Status bar (bottom)
        self.status = ttk.Label(self, text="Ready", relief=SUNKEN, anchor=W)
        self.status.pack(fill=X, side=BOTTOM, padx=5, pady=2)

    # ------------------------------------------------------------------
    def start_progress(self):
        self.progress.start(10)
        self.status.config(text="Processing…")

    def stop_progress(self):
        self.progress.stop()
        self.status.config(text="Ready")


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    FiNDyApp().mainloop()