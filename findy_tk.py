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
# Optional tray-icon dependencies  (graceful fallback if absent)
# ---------------------------------------------------------------------------
try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False

# ---------------------------------------------------------------------------
# Settings persistence  (~/.config/findy/settings.json)
# ---------------------------------------------------------------------------
_SETTINGS_DIR  = os.path.expanduser("~/.config/findy")
_SETTINGS_FILE = os.path.join(_SETTINGS_DIR, "settings.json")

_DEFAULT_SETTINGS = {
    "notifications":     True,   # show pop-up when updates found
    "interval_minutes":  15,     # how often the tray icon re-checks
}


def load_settings() -> dict:
    """Load user settings from disk; return defaults on any failure."""
    try:
        with open(_SETTINGS_FILE, "r") as fh:
            stored = json.load(fh)
        # Merge so that new keys added later still get defaults
        merged = dict(_DEFAULT_SETTINGS)
        merged.update(stored)
        return merged
    except Exception:
        return dict(_DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    """Persist settings to disk."""
    os.makedirs(_SETTINGS_DIR, exist_ok=True)
    with open(_SETTINGS_FILE, "w") as fh:
        json.dump(settings, fh, indent=2)


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
        for edition in ("cooker", "rome", "rock"):
            if edition in output:
                return edition.capitalize()
    except Exception:
        pass

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


def _display_edition(edition: str) -> str:
    return "ROME" if edition.lower() == "rome" else edition


# ---------------------------------------------------------------------------
# Helper – launch the OMV repo-selector / release-switcher
# ---------------------------------------------------------------------------

def launch_repo_selector():
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
# Background update checker  (runs once on startup, and periodically from tray)
# ---------------------------------------------------------------------------

class UpdateChecker(threading.Thread):
    """
    Collects pending updates for DNF, Flatpak, and (optionally) Gear Lever
    AppImages.  Results are passed to *callback* as:

        callback(updates_dict)

    updates_dict = {
        "dnf":      [{"name": ..., "version_installed": ..., "version_available": ..., "repo": ...}, ...],
        "flatpak":  [{"id": ..., "name": ..., "version_installed": ..., "version_available": ..., "branch": ...}, ...],
        "appimage": [{"name": ..., "path": ..., "note": ...}, ...]   # Gear Lever only
    }
    """

    def __init__(self, callback):
        super().__init__(daemon=True)
        self.callback = callback

    # ------------------------------------------------------------------
    def run(self):
        updates = {"dnf": [], "flatpak": [], "appimage": []}
        try:
            updates["dnf"]      = self._check_dnf()
        except Exception:
            pass
        try:
            updates["flatpak"]  = self._check_flatpak()
        except Exception:
            pass
        try:
            updates["appimage"] = self._check_appimage()
        except Exception:
            pass
        self.callback(updates)

    # ------------------------------------------------------------------
    def _check_dnf(self):
        """
        `dnf list --upgrades` prints lines like:
            package-name.arch   new-version   repo-id
        We also grab the currently-installed version via a second pass.
        """
        result = subprocess.run(
            ["dnf", "list", "--upgrades"],
            capture_output=True, text=True, timeout=60
        )
        packages = []
        for line in result.stdout.split("\n"):
            line = line.strip()
            if (not line
                    or line.startswith("Upgrades")
                    or line.startswith("Last")):
                continue
            parts = re.split(r'\s+', line)
            if len(parts) >= 3:
                pkg_full  = parts[0]
                version   = parts[1]
                repo      = parts[2]
                # Strip arch suffix for installed-version lookup
                pkg_name  = pkg_full.rsplit('.', 1)[0] if '.' in pkg_full else pkg_full
                installed = self._get_installed_dnf_version(pkg_name)
                packages.append({
                    "name":               pkg_full,
                    "version_installed":  installed,
                    "version_available":  version,
                    "repo":               repo,
                })
        return packages

    @staticmethod
    def _get_installed_dnf_version(pkg_name):
        try:
            res = subprocess.run(
                ["dnf", "list", "--installed", pkg_name],
                capture_output=True, text=True, timeout=10
            )
            for line in res.stdout.split("\n"):
                line = line.strip()
                if line.startswith("Installed") or not line:
                    continue
                parts = re.split(r'\s+', line)
                if len(parts) >= 2:
                    return parts[1]
        except Exception:
            pass
        return "unknown"

    # ------------------------------------------------------------------
    def _check_flatpak(self):
        """
        `flatpak update --check` (or plain `flatpak update -y --dry-run`)
        isn't universally available.  We compare installed vs remote instead.
        """
        # Get installed
        installed_map = {}
        res = subprocess.run(
            ["flatpak", "list", "--app",
             "--columns=application,name,version,branch"],
            capture_output=True, text=True, timeout=30
        )
        for line in res.stdout.strip().split("\n"):
            parts = line.split("\t")
            if len(parts) >= 4:
                installed_map[parts[0]] = {
                    "name":    parts[1],
                    "version": parts[2],
                    "branch":  parts[3],
                }

        # Get remote (available) – only apps that are installed remotely
        updates = []
        for app_id, info in installed_map.items():
            try:
                remote_res = subprocess.run(
                    ["flatpak", "remote-ls", "flathub", "--app",
                     "--columns=application,version,branch"],
                    capture_output=True, text=True, timeout=30
                )
                for rline in remote_res.stdout.strip().split("\n"):
                    rparts = rline.split("\t")
                    if len(rparts) >= 3 and rparts[0] == app_id:
                        if rparts[1] != info["version"]:
                            updates.append({
                                "id":                app_id,
                                "name":              info["name"],
                                "version_installed": info["version"],
                                "version_available": rparts[1],
                                "branch":            info["branch"],
                            })
                        break   # found this app in remote, move on
            except Exception:
                continue
        return updates

    # ------------------------------------------------------------------
    def _check_appimage(self):
        """
        If Gear Lever is installed we can ask it to fetch-updates.
        We capture stdout to see if it reports anything; otherwise return
        a single synthetic entry so the user knows a check was attempted.
        """
        try:
            res = subprocess.run(
                ["flatpak", "info", "it.mijorus.gearlever"],
                capture_output=True, timeout=5
            )
            if res.returncode != 0:
                return []   # Gear Lever not installed – nothing to do

            upd = subprocess.run(
                ["flatpak", "run", "it.mijorus.gearlever", "--fetch-updates"],
                capture_output=True, text=True, timeout=30
            )
            # Gear Lever prints update info to stdout; if non-empty treat as
            # "updates may be available" (exact parsing is fragile across GL
            # versions, so we surface the raw output for the user).
            if upd.stdout.strip():
                return [{"name": "Gear Lever Updates",
                         "path": "",
                         "note": upd.stdout.strip()}]
        except Exception:
            pass
        return []


# ---------------------------------------------------------------------------
# Update-selection window  (opened when user wants to see / act on updates)
# ---------------------------------------------------------------------------

class UpdateWindow(Toplevel):
    """
    Separate top-level window that lists every pending update with a
    per-row checkbox.  Buttons: Update Selected / Update All / Cancel.
    """

    def __init__(self, master, updates: dict):
        super().__init__(master)
        self.title("FiNDy – Available Updates")
        self.geometry("820x560")
        self.resizable(True, True)
        self.grab_set()          # modal-ish
        self.transient(master)

        self.updates   = updates
        self.check_vars = []     # list of (BooleanVar, category, index)
        self.master_app = master # FiNDyApp instance

        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self):
        # ---- header label ----
        ttk.Label(self, text="Available Updates",
                  font=("", 15, "bold")).pack(pady=(12, 2), anchor=W, padx=16)
        ttk.Separator(self, orient=HORIZONTAL).pack(fill=X, padx=16, pady=(0, 8))

        # ---- scrollable checklist area ----
        container = ttk.Frame(self)
        container.pack(fill=BOTH, expand=True, padx=16, pady=(0, 4))

        canvas = Canvas(container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient=VERTICAL, command=canvas.yview)
        self.scroll_frame = ttk.Frame(canvas)

        self.scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill=Y)

        # Mouse-wheel scroll support
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # ---- populate rows ----
        row_idx = 0

        # --- DNF ---
        if self.updates.get("dnf"):
            ttk.Label(self.scroll_frame, text="  DNF Packages",
                      font=("", 11, "bold"), bootstyle="success-inverse"
                      ).grid(row=row_idx, column=0, columnspan=5,
                             sticky=W, pady=(6, 2), padx=4)
            row_idx += 1
            # sub-header
            for col_idx, header in enumerate(
                    ["", "Package", "Installed", "Available", "Repository"]):
                ttk.Label(self.scroll_frame, text=header,
                          font=("", 9, "bold")
                          ).grid(row=row_idx, column=col_idx,
                                 sticky=W, padx=4, pady=1)
            row_idx += 1

            for i, pkg in enumerate(self.updates["dnf"]):
                var = BooleanVar(value=True)
                self.check_vars.append((var, "dnf", i))
                ttk.Checkbutton(self.scroll_frame, variable=var
                                ).grid(row=row_idx, column=0, sticky=W, padx=4)
                ttk.Label(self.scroll_frame, text=pkg["name"]
                          ).grid(row=row_idx, column=1, sticky=W, padx=4)
                ttk.Label(self.scroll_frame, text=pkg["version_installed"]
                          ).grid(row=row_idx, column=2, sticky=W, padx=4)
                ttk.Label(self.scroll_frame, text=pkg["version_available"],
                          bootstyle="success"
                          ).grid(row=row_idx, column=3, sticky=W, padx=4)
                ttk.Label(self.scroll_frame, text=pkg["repo"]
                          ).grid(row=row_idx, column=4, sticky=W, padx=4)
                row_idx += 1

        # --- Flatpak ---
        if self.updates.get("flatpak"):
            ttk.Label(self.scroll_frame, text="  Flatpak Apps",
                      font=("", 11, "bold"), bootstyle="info-inverse"
                      ).grid(row=row_idx, column=0, columnspan=5,
                             sticky=W, pady=(10, 2), padx=4)
            row_idx += 1
            for col_idx, header in enumerate(
                    ["", "App Name", "Installed", "Available", "Branch"]):
                ttk.Label(self.scroll_frame, text=header,
                          font=("", 9, "bold")
                          ).grid(row=row_idx, column=col_idx,
                                 sticky=W, padx=4, pady=1)
            row_idx += 1

            for i, app in enumerate(self.updates["flatpak"]):
                var = BooleanVar(value=True)
                self.check_vars.append((var, "flatpak", i))
                ttk.Checkbutton(self.scroll_frame, variable=var
                                ).grid(row=row_idx, column=0, sticky=W, padx=4)
                ttk.Label(self.scroll_frame, text=app["name"]
                          ).grid(row=row_idx, column=1, sticky=W, padx=4)
                ttk.Label(self.scroll_frame, text=app["version_installed"]
                          ).grid(row=row_idx, column=2, sticky=W, padx=4)
                ttk.Label(self.scroll_frame, text=app["version_available"],
                          bootstyle="success"
                          ).grid(row=row_idx, column=3, sticky=W, padx=4)
                ttk.Label(self.scroll_frame, text=app["branch"]
                          ).grid(row=row_idx, column=4, sticky=W, padx=4)
                row_idx += 1

        # --- AppImage / Gear Lever ---
        if self.updates.get("appimage"):
            ttk.Label(self.scroll_frame, text="  AppImage Updates (Gear Lever)",
                      font=("", 11, "bold"), bootstyle="warning-inverse"
                      ).grid(row=row_idx, column=0, columnspan=5,
                             sticky=W, pady=(10, 2), padx=4)
            row_idx += 1
            for col_idx, header in enumerate(["", "Source", "Details", "", ""]):
                ttk.Label(self.scroll_frame, text=header,
                          font=("", 9, "bold")
                          ).grid(row=row_idx, column=col_idx,
                                 sticky=W, padx=4, pady=1)
            row_idx += 1

            for i, app in enumerate(self.updates["appimage"]):
                var = BooleanVar(value=True)
                self.check_vars.append((var, "appimage", i))
                ttk.Checkbutton(self.scroll_frame, variable=var
                                ).grid(row=row_idx, column=0, sticky=W, padx=4)
                ttk.Label(self.scroll_frame, text=app["name"]
                          ).grid(row=row_idx, column=1, sticky=W, padx=4)
                ttk.Label(self.scroll_frame, text=app.get("note", "Update available"),
                          wraplength=400
                          ).grid(row=row_idx, column=2, columnspan=3,
                                 sticky=W, padx=4)
                row_idx += 1

        # ---- select-all / deselect-all row ----
        sep_row = row_idx
        ttk.Separator(self.scroll_frame, orient=HORIZONTAL
                      ).grid(row=sep_row, column=0, columnspan=5,
                             sticky="ew", pady=(8, 2), padx=2)
        row_idx += 1
        ttk.Button(self.scroll_frame, text="Select All",
                   command=lambda: self._set_all(True),
                   bootstyle="outline-secondary"
                   ).grid(row=row_idx, column=1, sticky=W, padx=4, pady=4)
        ttk.Button(self.scroll_frame, text="Deselect All",
                   command=lambda: self._set_all(False),
                   bootstyle="outline-secondary"
                   ).grid(row=row_idx, column=2, sticky=W, padx=4, pady=4)

        # ---- action buttons at bottom (outside scroll area) ----
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=X, padx=16, pady=(4, 12))

        ttk.Button(btn_frame, text="Update Selected",
                   command=self._update_selected,
                   bootstyle=SUCCESS).pack(side=LEFT, padx=(0, 6))
        ttk.Button(btn_frame, text="Update All",
                   command=self._update_all,
                   bootstyle=WARNING).pack(side=LEFT, padx=(0, 6))
        ttk.Button(btn_frame, text="Cancel",
                   command=self.destroy,
                   bootstyle="outline-danger").pack(side=RIGHT)

        # ---- progress / status label ----
        self.status_var = StringVar(value="")
        ttk.Label(btn_frame, textvariable=self.status_var,
                  bootstyle="info").pack(side=LEFT, padx=(20, 0))

    # ------------------------------------------------------------------
    def _set_all(self, value: bool):
        for var, _cat, _idx in self.check_vars:
            var.set(value)

    # ------------------------------------------------------------------
    def _update_all(self):
        self._set_all(True)
        self._update_selected()

    # ------------------------------------------------------------------
    def _update_selected(self):
        """
        Collect checked items, then run the appropriate package-manager
        commands in a background thread.
        """
        selected = {"dnf": [], "flatpak": [], "appimage": []}
        for var, cat, idx in self.check_vars:
            if var.get():
                selected[cat].append(idx)

        if not any(selected.values()):
            messagebox.showinfo("Nothing Selected",
                                "Please select at least one update.")
            return

        self.status_var.set("Updating…")
        # Disable buttons during update
        for widget in self.winfo_children():
            if isinstance(widget, ttk.Frame):
                for child in widget.winfo_children():
                    if isinstance(child, tttk.Button):
                        child.config(state=DISABLED)

        threading.Thread(
            target=self._run_updates,
            args=(selected,),
            daemon=True
        ).start()

    # ------------------------------------------------------------------
    def _run_updates(self, selected):
        errors   = []
        success  = []

        # --- DNF: one pkexec call per package (or single distro-sync) ---
        if selected["dnf"]:
            dnf_pkgs = [self.updates["dnf"][i]["name"] for i in selected["dnf"]]
            cmd = ["pkexec", "dnf", "install", "-y"] + dnf_pkgs
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                success.append(f"DNF: updated {len(dnf_pkgs)} package(s)")
            else:
                errors.append(f"DNF error:\n{res.stderr or res.stdout}")

        # --- Flatpak: update each selected app individually -----------
        for idx in selected["flatpak"]:
            app = self.updates["flatpak"][idx]
            cmd = ["flatpak", "update", "-y", app["id"]]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                success.append(f"Flatpak: updated {app['name']}")
            else:
                errors.append(f"Flatpak {app['name']}:\n{res.stderr or res.stdout}")

        # --- AppImage (Gear Lever fetch-updates) ----------------------
        if selected["appimage"]:
            cmd = ["flatpak", "run", "it.mijorus.gearlever", "--fetch-updates"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if res.returncode == 0:
                success.append("AppImage: Gear Lever update check / apply completed")
            else:
                errors.append(f"Gear Lever:\n{res.stderr or res.stdout}")

        # --- report back on the Tk main thread ------------------------
        self.after(0, lambda: self._finish(success, errors))

    # ------------------------------------------------------------------
    def _finish(self, success, errors):
        msg_parts = []
        if success:
            msg_parts.append("✓ Completed:\n  " + "\n  ".join(success))
        if errors:
            msg_parts.append("✗ Errors:\n  " + "\n  ".join(errors))

        if errors:
            messagebox.showwarning("Updates – Partial Failure", "\n\n".join(msg_parts))
        else:
            messagebox.showinfo("Updates Complete", "\n".join(msg_parts))

        self.destroy()


# ---------------------------------------------------------------------------
# Worker thread  (install / remove single packages – unchanged logic)
# ---------------------------------------------------------------------------

class PackageWorker(threading.Thread):
    """Worker thread for package operations"""

    def __init__(self, operation, package_type, package_name, callback):
        super().__init__(daemon=True)
        self.operation    = operation
        self.package_type = package_name if False else package_type   # keep original
        self.package_type = package_type
        self.package_name = package_name
        self.callback     = callback

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
        cmd_map = {
            "install": ["pkexec", "dnf", "install", "-y", self.package_name],
            "remove":  ["pkexec", "dnf", "remove",  "-y", self.package_name],
            "update":  ["pkexec", "dnf", "distro-sync", "-y"],
        }
        cmd = cmd_map.get(self.operation)
        if not cmd:
            self.callback(False, "Unknown operation")
            return
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            self.callback(True,  f"Successfully {self.operation}ed {self.package_name}")
        else:
            self.callback(False, result.stderr or result.stdout)

    # ------------------------------------------------------------------
    def _run_flatpak(self):
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
            self.callback(True,  f"Successfully {self.operation}ed {self.package_name}")
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
        appimage_dir = os.path.expanduser("~/.local/share/applications/appimages")
        if self.operation == "install":
            if not os.path.exists(appimage_dir):
                os.makedirs(appimage_dir, exist_ok=True)
            dest = os.path.join(appimage_dir, os.path.basename(self.package_name))
            subprocess.run(["cp", self.package_name, dest])
            subprocess.run(["chmod", "+x", dest])
            self.callback(True, f"AppImage installed to {dest}")
        elif self.operation == "remove":
            if os.path.exists(self.package_name):
                os.remove(self.package_name)
                self.callback(True, f"Removed {os.path.basename(self.package_name)}")
            else:
                self.callback(False, f"AppImage not found: {self.package_name}")

    # ------------------------------------------------------------------
    def _run_gearlever(self):
        base_cmd = ["flatpak", "run", "it.mijorus.gearlever"]
        if self.operation == "integrate":
            cmd    = base_cmd + ["--integrate", self.package_name]
            result = subprocess.run(cmd, capture_output=True, text=True)
            success = result.returncode == 0
            msg = (f"Integrated {os.path.basename(self.package_name)}"
                   if success else result.stderr)
            self.callback(success, msg)

        elif self.operation == "remove":
            cmd    = base_cmd + ["--remove", self.package_name]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                self.callback(True, f"Removed {os.path.basename(self.package_name)} via Gear Lever")
            else:
                self._manual_remove_fallback(result.stderr)

        elif self.operation == "update":
            cmd    = base_cmd + ["--fetch-updates"]
            result = subprocess.run(cmd, capture_output=True, text=True)
            self.callback(result.returncode == 0, "Gear Lever: Update check completed.")

    # ------------------------------------------------------------------
    def _manual_remove_fallback(self, gearlever_stderr):
        basename = os.path.basename(self.package_name)
        stem     = re.sub(r'\.appimage$', '', basename, flags=re.IGNORECASE)

        removed_files = []
        errors        = []

        try:
            if os.path.exists(self.package_name):
                os.remove(self.package_name)
                removed_files.append(basename)
            else:
                errors.append(f"AppImage not found: {self.package_name}")
        except OSError as e:
            errors.append(f"Could not remove AppImage: {e}")

        desktop_candidates = [
            os.path.join(os.path.dirname(self.package_name), f"{stem}.desktop"),
            os.path.expanduser(f"~/.local/share/applications/{stem}.desktop"),
        ]
        for desktop_path in desktop_candidates:
            try:
                if os.path.exists(desktop_path):
                    os.remove(desktop_path)
                    removed_files.append(os.path.basename(desktop_path))
            except OSError as e:
                errors.append(f"Could not remove {desktop_path}: {e}")

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
        launch_repo_selector()
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
        desc_map       = {}
        current_pkg    = ""
        current_desc   = ""
        in_description = False

        for line in info_output.split('\n'):
            line = line.strip()
            if line.startswith("Name"):
                if current_pkg and current_desc:
                    desc_map[current_pkg] = current_desc
                parts = line.split(":", 1)
                if len(parts) > 1:
                    current_pkg    = parts[1].strip()
                    current_desc   = ""
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
        app_name    = ""
        description = ""

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
# Tray icon manager
# ---------------------------------------------------------------------------

class TrayManager:
    """
    Owns the pystray.Icon and the periodic-check timer.
    All icon-image and menu operations happen on a background thread
    (pystray requirement).  Callbacks into Tk are marshalled via
    root.after().
    """

    # Badge colours
    _COLOUR_IDLE    = (80, 180, 120)   # green-ish
    _COLOUR_UPDATE  = (230, 160, 40)   # amber
    _COLOUR_BG      = (40,  40,  50)   # dark body

    def __init__(self, root: "FiNDyApp", settings: dict):
        self.root     = root
        self.settings = settings
        self.icon     = None
        self._stop    = threading.Event()
        self._has_updates = False

        # Start the icon on its own thread (pystray runs its own mainloop)
        self._icon_thread = threading.Thread(target=self._run_icon, daemon=True)
        self._icon_thread.start()

        # Start the periodic checker
        self._checker_thread = threading.Thread(target=self._periodic_loop, daemon=True)
        self._checker_thread.start()

    # ------------------------------------------------------------------
    # Icon image helpers
    # ------------------------------------------------------------------
    @classmethod
    def _make_image(cls, has_updates: bool) -> "Image.Image":
        """Draw a 32x32 tray icon: FiNDy circle + optional update badge."""
        size = 32
        img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Main circle
        draw.ellipse([2, 2, size - 3, size - 3], fill=cls._COLOUR_BG,
                     outline=(120, 200, 140), width=2)

        # "F" letter (simple lines)
        draw.line([(9, 8), (9, 24)], fill=(200, 240, 200), width=3)   # vertical
        draw.line([(9, 8), (20, 8)], fill=(200, 240, 200), width=2)   # top bar
        draw.line([(9, 15),(17, 15)], fill=(200, 240, 200), width=2)  # mid bar

        # Update badge (small orange dot in top-right)
        if has_updates:
            draw.ellipse([22, 2, 30, 10], fill=cls._COLOUR_UPDATE,
                         outline=(255, 200, 80), width=1)

        return img

    # ------------------------------------------------------------------
    # pystray icon + menu
    # ------------------------------------------------------------------
    def _run_icon(self):
        """Blocking – runs on its own thread."""
        menu = pystray.Menu(
            pystray.MenuItem("Show / Restore FiNDy", self._on_show),
            pystray.MenuItem("Check Now",            self._on_check_now),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda: f"Notifications: {'ON' if self.settings['notifications'] else 'OFF'}",
                self._on_toggle_notifications
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Check interval:",      None, enabled=False),
            pystray.MenuItem(" 5 minutes",  lambda: self._set_interval(5)),
            pystray.MenuItem("15 minutes",  lambda: self._set_interval(15),
                             checked=lambda item: self.settings["interval_minutes"] == 15),
            pystray.MenuItem("30 minutes",  lambda: self._set_interval(30),
                             checked=lambda item: self.settings["interval_minutes"] == 30),
            pystray.MenuItem("60 minutes",  lambda: self._set_interval(60),
                             checked=lambda item: self.settings["interval_minutes"] == 60),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._on_quit),
        )

        self.icon = pystray.Icon(
            "FiNDy",
            self._make_image(False),
            "FiNDy Package Manager",
            menu
        )
        self.icon.run()   # blocks until icon.stop()

    # ------------------------------------------------------------------
    # Menu callbacks  (run on pystray's thread – marshal to Tk carefully)
    # ------------------------------------------------------------------
    def _on_show(self, icon, item):
        self.root.after(0, self._restore_window)

    def _on_check_now(self, icon, item):
        # Kick off a one-shot check; result handled by _on_updates_found
        UpdateChecker(self._on_updates_found).start()

    def _on_toggle_notifications(self, icon, item):
        self.settings["notifications"] = not self.settings["notifications"]
        save_settings(self.settings)
        # Rebuild menu so the label text refreshes
        if self.icon:
            self.icon.update_menu()

    def _set_interval(self, minutes):
        self.settings["interval_minutes"] = minutes
        save_settings(self.settings)
        if self.icon:
            self.icon.update_menu()

    def _on_quit(self, icon, item):
        self._stop.set()
        if self.icon:
            self.icon.stop()
        self.root.after(0, self.root.destroy)

    # ------------------------------------------------------------------
    # Periodic update loop
    # ------------------------------------------------------------------
    def _periodic_loop(self):
        """Sleep in small increments so we can react to _stop quickly."""
        while not self._stop.is_set():
            interval_secs = self.settings.get("interval_minutes", 15) * 60
            # Sleep in 5-second chunks
            elapsed = 0
            while elapsed < interval_secs and not self._stop.is_set():
                self._stop.wait(5)
                elapsed += 5
            if self._stop.is_set():
                break
            # Time to check
            UpdateChecker(self._on_updates_found).start()

    # ------------------------------------------------------------------
    # Shared callback when an update check finishes
    # ------------------------------------------------------------------
    def _on_updates_found(self, updates: dict):
        total = (len(updates.get("dnf", []))
               + len(updates.get("flatpak", []))
               + len(updates.get("appimage", [])))

        self._has_updates = total > 0

        # Update the tray icon badge
        if self.icon:
            self.icon.icon = self._make_image(self._has_updates)
            self.icon.update_menu()

        # If notifications are on and there are updates, show pop-up on Tk thread
        if self._has_updates and self.settings.get("notifications", True):
            self.root.after(0, lambda: self.root.show_update_dialog(updates))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _restore_window(self):
        """Bring the main window back to focus."""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def stop(self):
        self._stop.set()
        if self.icon:
            self.icon.stop()


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

        # Load persistent settings
        self.settings = load_settings()

        # Progress bar (top)
        self.progress = ttk.Progressbar(self, mode='indeterminate', bootstyle="success")
        self.progress.pack(fill=X, side=TOP)

        # Header with edition badge
        header = ttk.Frame(self)
        header.pack(fill=X, padx=20, pady=10)

        ttk.Label(header, text="FiNDy Package Manager",
                  font=("", 18, "bold")).pack(side=LEFT, anchor=W)

        badge_style = {
            "Cooker": "success",
            "ROME":   "warning",
            "Rock":   "info",
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

        # --------------- tray icon + initial update check -----------------
        self.tray_manager = None
        if TRAY_AVAILABLE:
            try:
                self.tray_manager = TrayManager(self, self.settings)
            except Exception as e:
                print(f"Tray icon failed to start: {e}")

        # Kick off the very first update check (result → show_update_dialog)
        UpdateChecker(self._on_startup_check).start()

        # Graceful shutdown hook
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Startup check callback  (runs on the checker thread – marshal to Tk)
    # ------------------------------------------------------------------
    def _on_startup_check(self, updates: dict):
        self.after(0, lambda: self._handle_startup_updates(updates))

    def _handle_startup_updates(self, updates: dict):
        total = (len(updates.get("dnf", []))
               + len(updates.get("flatpak", []))
               + len(updates.get("appimage", [])))
        if total > 0:
            self.show_update_dialog(updates)

    # ------------------------------------------------------------------
    # The "updates available" prompt dialog
    # ------------------------------------------------------------------
    def show_update_dialog(self, updates: dict):
        """
        Show a small modal dialog asking the user whether they want to
        view/act on the available updates.  Only one dialog at a time.
        """
        # Guard: don't stack multiple dialogs
        if getattr(self, "_update_dialog_open", False):
            return
        self._update_dialog_open = True

        total = (len(updates.get("dnf", []))
               + len(updates.get("flatpak", []))
               + len(updates.get("appimage", [])))

        # Build a small summary string
        parts = []
        if updates.get("dnf"):
            parts.append(f"{len(updates['dnf'])} DNF package(s)")
        if updates.get("flatpak"):
            parts.append(f"{len(updates['flatpak'])} Flatpak app(s)")
        if updates.get("appimage"):
            parts.append(f"{len(updates['appimage'])} AppImage update(s)")
        summary = ", ".join(parts)

        # Use a Toplevel for full control over buttons
        dlg = Toplevel(self)
        dlg.title("FiNDy – Updates Available")
        dlg.geometry("380x160")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()
        dlg.lift()

        ttk.Label(dlg, text="Updates Available",
                  font=("", 13, "bold")).pack(pady=(16, 4))
        ttk.Label(dlg, text=f"{total} update(s) available:\n{summary}",
                  justify=CENTER).pack(pady=4)

        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(pady=12)

        def _view():
            dlg.destroy()
            self._update_dialog_open = False
            UpdateWindow(self, updates)

        def _dismiss():
            dlg.destroy()
            self._update_dialog_open = False

        ttk.Button(btn_frame, text="View Updates",
                   command=_view,
                   bootstyle=SUCCESS).pack(side=LEFT, padx=6)
        ttk.Button(btn_frame, text="Dismiss",
                   command=_dismiss,
                   bootstyle="outline-secondary").pack(side=LEFT, padx=6)

        # Release the guard if the dialog is closed via the X button
        dlg.protocol("WM_DELETE_WINDOW", _dismiss)

    # ------------------------------------------------------------------
    def start_progress(self):
        self.progress.start(10)
        self.status.config(text="Processing…")

    def stop_progress(self):
        self.progress.stop()
        self.status.config(text="Ready")

    # ------------------------------------------------------------------
    def _on_close(self):
        """Shut everything down cleanly."""
        if self.tray_manager:
            self.tray_manager.stop()
        self.destroy()


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = FiNDyApp()
    app.mainloop()