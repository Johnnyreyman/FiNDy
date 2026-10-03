#!/usr/bin/env python3
""" FiNDy Package Manager
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
import shutil
from tkinter import *
from tkinter import messagebox, filedialog
import ttkbootstrap as ttk
from ttkbootstrap.constants import *

# ---------------------------------------------------------------------------
# Optional tray-icon dependencies
# ---------------------------------------------------------------------------
try:
    import pystray
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = ImageDraw = None

# ---------------------------------------------------------------------------
# GearLever helpers
# ---------------------------------------------------------------------------
GEARLEVER_ID = "it.mijorus.gearlever"

def _gl(*args) -> list:
    return ["flatpak", "run", GEARLEVER_ID] + list(args)

def _gl_run(*args, timeout=300) -> subprocess.CompletedProcess:
    return subprocess.run(
        _gl(*args), input="y\n", capture_output=True, text=True, timeout=timeout,
    )

def gearlever_available() -> bool:
    for scope_flag in ["--system", "--user"]:
        try:
            result = subprocess.run(
                ["flatpak", "info", scope_flag, GEARLEVER_ID],
                capture_output=True, timeout=10, check=False
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass
    return False

def _parse_gearlever_line(line: str) -> dict | None:
    line = line.rstrip()
    if not line: return None
    path_match = re.search(r'(/\S+)\s*$', line)
    if not path_match: return None
    path = path_match.group(1)
    brackets = re.findall(r'\[([^\]]+)\]', line)
    version = brackets[0].strip() if brackets else ""
    name_part = line[:line.index('[')].strip() if '[' in line else ""
    name = name_part if name_part else os.path.basename(path)
    return {"name": name, "path": path, "version": version}

def gearlever_list_installed() -> list[dict]:
    try:
        r = subprocess.run(_gl("--list-installed"), capture_output=True, text=True, timeout=30)
        return [p for p in (_parse_gearlever_line(l) for l in r.stdout.strip().splitlines()) if p]
    except Exception:
        return []

def gearlever_list_updates() -> list[dict]:
    try:
        r = subprocess.run(_gl("--list-updates"), capture_output=True, text=True, timeout=60)
        return [p for p in (_parse_gearlever_line(l) for l in r.stdout.strip().splitlines()) if p]
    except Exception:
        return []

# ---------------------------------------------------------------------------
# Settings persistence
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
        with open(_SETTINGS_FILE) as fh:
            return {**_DEFAULT_SETTINGS, **json.load(fh)}
    except Exception:
        return dict(_DEFAULT_SETTINGS)

def save_settings(settings: dict) -> None:
    os.makedirs(_SETTINGS_DIR, exist_ok=True)
    with open(_SETTINGS_FILE, "w") as fh:
        json.dump(settings, fh, indent=2)

def load_flatpak_scopes() -> dict:
    try:
        with open(_FLATPAK_TRACK_FILE) as f:
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
    scopes.pop(app_id, None)
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
                    for ed in ("cooker", "rome", "rock"):
                        if ed in val:
                            return ed.capitalize()
    except Exception:
        pass
    try:
        out = subprocess.run(["dnf", "repolist", "--enabled"], capture_output=True, text=True, timeout=10).stdout.lower()
        for ed in ("cooker", "rome", "rock"):
            if ed in out:
                return ed.capitalize()
    except Exception:
        pass
    return "Unknown"

def _display_edition(edition: str) -> str:
    return "ROME" if edition.lower() == "rome" else edition

# ---------------------------------------------------------------------------
# Repo selector / mirror refresh
# ---------------------------------------------------------------------------
def launch_repo_selector():
    for cmd in [["pkexec", "om-repo-picker"], ["pkexec", "om-release-switcher"], ["pkexec", "om-release-selector"], ["pkexec", "om-repoman"]]:
        try:
            subprocess.Popen(cmd, start_new_session=True)
            return True
        except FileNotFoundError:
            continue
    messagebox.showwarning("Repo Selector Not Found", "Could not find the OpenMandriva repo selector utility.\n\nTry: pkexec dnf install -y om-repo-picker")
    return False

def refresh_repos():
    for cmd in [["dnf", "makecache"], ["flatpak", "update", "--appstream"]]:
        try:
            subprocess.run(cmd, capture_output=True, timeout=60)
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Arch suffix regex
# ---------------------------------------------------------------------------
_ARCH_RE = re.compile(r'\.(x86_64|i686|noarch|aarch64|armv7hl|src)$')

# ---------------------------------------------------------------------------
# Strong baseline system packages filter for OpenMandriva
# ---------------------------------------------------------------------------
_DNF_BASELINE_PREFIXES = (
    "glibc", "kernel", "systemd", "bash", "coreutils", "filesystem", "setup", "rpm", "dnf", "python3", "dbus",
    "omv", "openmandriva", "mandriva", "mandrivalinux", "live", "release",
    "plasma", "kde", "xfce", "lxqt", "gnome", "mate", "cinnamon", "lxde",
    "mesa", "xorg", "wayland", "libdrm", "vulkan", "nvidia", "amd", "intel",
    "pulseaudio", "pipewire", "alsa", "gstreamer",
    "dracut", "grub", "grubby", "efi", "fwupd", "bootloader", "plymouth",
    "NetworkManager", "firewalld", "avahi", "openssh",
    "shadow", "util-linux", "pam", "audit", "selinux", "polkit", "sudo", "ca-certificates",
    "curl", "wget", "openssl", "libgcc", "libstdc++", "gcc", "binutils",
    "tzdata", "hwdata", "pciutils", "usbutils", "dmidecode", "cups",
)

def _is_baseline_dnf(name: str) -> bool:
    if not name or len(name) < 2:
        return True
    name_lower = name.lower()
    
    # Direct prefix matches
    if any(name_lower == p or name_lower.startswith(p + "-") or name_lower.startswith(p + ".") for p in _DNF_BASELINE_PREFIXES):
        return True
    
    # Extra aggressive rules
    lib_app_exceptions = ("libreoffice", "librewolf", "libre", "libinput", "libqalculate", "libratom", "libnotify", "libappindicator")
    if name_lower.startswith("lib") and not any(x in name_lower for x in lib_app_exceptions):
        # Only filter lib packages that look like system libraries
        if any(name_lower.endswith(sfx) for sfx in ("-devel", "-docs", "-doc", "-lang", "-data", "-common", "-core", "-libs", "-debuginfo", "-debugsource")):
            return True
        # Also filter generic lib + number packages (e.g. libxml2, libpng16)
        if re.match(r'^lib\w+\d*$', name_lower):
            return True
    if any(x in name_lower for x in ["-devel", "-debug", "-static", "-docs", "-help", "-lang", "-data"]):
        return True
    if name_lower.endswith("-common") or name_lower.endswith("-core") or name_lower.endswith("-libs"):
        return True
    
    return False

# ---------------------------------------------------------------------------
# Polkit agent management
# ---------------------------------------------------------------------------
_POLKIT_AGENTS = [
    "lxqt-policykit", "lxqt-policykit-agent", "/usr/bin/lxqt-policykit",
    "/usr/libexec/lxqt-policykit", "/usr/lib/lxqt-policykit/lxqt-policykit",
    "polkit-kde-agent-1", "/usr/lib/polkit-kde-agent-1", "/usr/libexec/polkit-kde-agent-1",
    "polkit-gnome-authentication-agent-1", "/usr/lib/polkit-gnome/polkit-gnome-authentication-agent-1",
    "/usr/libexec/polkit-gnome-authentication-agent-1",
]

_polkit_proc: subprocess.Popen | None = None

def _polkit_agent_running() -> bool:
    agent_basenames = {"lxqt-policykit", "lxqt-policykit-agent", "polkit-kde-agent-1",
                       "polkit-gnome-authentication-agent-1", "polkit-mate-authentication-agent-1",
                       "polkit-xfce-authentication-agent-1", "xfce-polkit"}
    try:
        out = subprocess.run(["ps", "-e", "-o", "comm="], capture_output=True, text=True, timeout=5).stdout
        running = {line.strip() for line in out.splitlines()}
        return bool(running & agent_basenames)
    except Exception:
        return False

def _find_polkit_agent_exe() -> str | None:
    for agent in _POLKIT_AGENTS:
        if os.path.isabs(agent):
            if os.path.isfile(agent) and os.access(agent, os.X_OK):
                return agent
        else:
            found = shutil.which(agent)
            if found:
                return found
    return None

def ensure_polkit_agent() -> bool:
    global _polkit_proc
    if _polkit_agent_running():
        return True
    if _polkit_proc is not None and _polkit_proc.poll() is None:
        return True
    exe = _find_polkit_agent_exe()
    if not exe:
        return False
    try:
        _polkit_proc = subprocess.Popen(
            ["setsid", exe],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            close_fds=True
        )
        for _ in range(10):
            time.sleep(0.3)
            if _polkit_agent_running():
                return True
        return _polkit_proc.poll() is None
    except Exception:
        return False

def _pkexec_available() -> bool:
    return shutil.which("pkexec") is not None

def _run_privileged_dnf(dnf_args: list[str]) -> tuple[bool, str]:
    """
    Run a DNF command with root privileges.

    Strategy:
      1. Ensure a graphical polkit agent is running, then use pkexec.
      2. If pkexec is unavailable, fall back to kdesu.
      3. Last resort: xterm + sudo (opens a visible terminal).

    Returns (success: bool, output: str).
    """
    # --- Strategy 1: pkexec (preferred) ---
    if _pkexec_available():
        agent_ok = ensure_polkit_agent()
        if not agent_ok:
            pass

        cmd = ["pkexec", "--disable-internal-agent", "dnf"] + dnf_args
        try:
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                stdin=subprocess.DEVNULL,
            )
            if r.returncode == 0:
                return True, r.stdout
            stderr = r.stderr or r.stdout
            if "authentication agent" not in stderr.lower() and \
               "no such device" not in stderr.lower():
                return False, stderr
        except FileNotFoundError:
            pass
        except subprocess.TimeoutExpired:
            return False, "Operation timed out after 5 minutes."

    # --- Strategy 2: kdesu ---
    if shutil.which("kdesu"):
        cmd = ["kdesu", "--", "dnf"] + dnf_args
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                               stdin=subprocess.DEVNULL)
            if r.returncode == 0:
                return True, r.stdout
            return False, r.stderr or r.stdout
        except subprocess.TimeoutExpired:
            return False, "Operation timed out after 5 minutes."
        except Exception:
            pass

    # --- Strategy 3: xterm + sudo (last resort) ---
    if shutil.which("xterm") and shutil.which("sudo"):
        shell_cmd = "sudo dnf " + " ".join(dnf_args) + \
                    "; echo; echo '=== Press Enter to close ==='; read _"
        try:
            r = subprocess.run(
                ["xterm", "-e", "bash", "-c", shell_cmd],
                timeout=300,
            )
            return r.returncode == 0, "Operation completed via xterm."
        except subprocess.TimeoutExpired:
            return False, "Operation timed out after 5 minutes."
        except Exception:
            pass

    return False, (
        "Could not find a way to run DNF with elevated privileges.\n\n"
        "Please install a graphical Polkit authentication agent:\n"
        "  • lxqt-policykit  (OpenMandriva/LXQt)\n"
        "      sudo dnf install -y lxqt-policykit\n"
        "  • polkit-kde-agent-1  (KDE Plasma)\n"
        "      sudo dnf install -y polkit-kde-agent-1\n"
        "  • polkit-gnome  (GNOME/GTK desktops)\n"
        "      sudo dnf install -y polkit-gnome"
    )

# ---------------------------------------------------------------------------
# Package Sync helpers
# ---------------------------------------------------------------------------
_SYNC_FORMAT_VERSION = 2

def collect_sync_data(include_dnf=True, include_flatpak=True, include_appimage=True, skip_baseline=True) -> dict:
    data = {
        "format_version": _SYNC_FORMAT_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "hostname": os.uname().nodename,
        "dnf": [],
        "flatpak": [],
        "appimage": [],
    }

    if include_dnf:
        dnf_list = []
        try:
            # Primary method - repoquery
            qf = "%{name}\t%{version}-%{release}\t%{repoid}"
            r = subprocess.run(["dnf", "repoquery", "--queryformat", qf, "--installed"],
                               capture_output=True, text=True, timeout=90)
            for line in r.stdout.splitlines():
                line = line.strip()
                if not line or any(x in line for x in ("Last metadata", "Modular", "Loaded plugins", "Repo")):
                    continue
                parts = [p.strip() for p in line.split("\t") if p.strip()]
                if len(parts) < 1: continue
                name = parts[0]
                if skip_baseline and _is_baseline_dnf(name):
                    continue
                version = parts[1] if len(parts) > 1 else ""
                repo = parts[2] if len(parts) > 2 else ""
                dnf_list.append({"name": name, "version": version, "repo": repo})
        except Exception:
            pass

        # Fallback
        if len(dnf_list) < 20:
            try:
                r2 = subprocess.run(["dnf", "list", "--installed", "--quiet"],
                                    capture_output=True, text=True, timeout=60)
                for line in r2.stdout.splitlines():
                    line = line.strip()
                    if not line or any(x in line for x in ("Installed", "Last metadata", "Loaded")):
                        continue
                    parts = re.split(r'\s+', line)
                    if len(parts) < 2: continue
                    name_arch = parts[0]
                    name = _ARCH_RE.sub('', name_arch)
                    if skip_baseline and _is_baseline_dnf(name):
                        continue
                    version = parts[1] if len(parts) > 1 else ""
                    repo = parts[2] if len(parts) > 2 else ""
                    dnf_list.append({"name": name, "version": version, "repo": repo})
            except Exception:
                pass

        data["dnf"] = dnf_list

    if include_flatpak:
        try:
            r = subprocess.run(["flatpak", "list", "--columns=name,application,version,branch,installation"],
                               capture_output=True, text=True, timeout=30)
            scopes = load_flatpak_scopes()
            for line in r.stdout.splitlines():
                line = line.strip()
                if not line or line.lower().startswith("name"): continue
                parts = [p.strip() for p in line.split("\t")]
                if len(parts) < 2: continue
                app_id = parts[1]
                data["flatpak"].append({
                    "name": parts[0],
                    "app_id": app_id,
                    "version": parts[2] if len(parts) > 2 else "",
                    "branch": parts[3] if len(parts) > 3 else "",
                    "scope": scopes.get(app_id, "system"),
                })
        except Exception:
            pass

    if include_appimage and gearlever_available():
        for app in gearlever_list_installed():
            data["appimage"].append({
                "name": app.get("name", ""),
                "version": app.get("version", ""),
                "path": app.get("path", ""),
            })

    return data

# ---------------------------------------------------------------------------
# UpdateChecker
# ---------------------------------------------------------------------------
class UpdateChecker(threading.Thread):
    def __init__(self, callback):
        super().__init__(daemon=True)
        self.callback = callback

    def run(self):
        updates = {"dnf": [], "flatpak": [], "appimage": []}
        errors = []
        for key, fn in [("dnf", self._check_dnf), ("flatpak", self._check_flatpak), ("appimage", self._check_appimage)]:
            try:
                updates[key] = fn()
            except Exception as e:
                errors.append(f"{key}: {e}")
        updates["errors"] = errors
        self.callback(updates)

    def _check_dnf(self):
        r = subprocess.run(["dnf", "check-update", "--quiet"],
                           capture_output=True, text=True, timeout=120)
        return [l.split()[0] for l in r.stdout.strip().splitlines()
                if l.strip() and not l.startswith(('Last metadata', 'Obsoleting'))]

    def _check_flatpak(self):
        r = subprocess.run(["flatpak", "remote-ls", "--updates"], capture_output=True, text=True, timeout=60)
        return [l.strip() for l in r.stdout.strip().splitlines() if l.strip()]

    def _check_appimage(self):
        return gearlever_list_updates() if gearlever_available() else []

# ---------------------------------------------------------------------------
# PackageWorker
# ---------------------------------------------------------------------------
class PackageWorker(threading.Thread):
    def __init__(self, operation, package_type, package_name, callback):
        super().__init__(daemon=True)
        self.operation = operation
        self.package_type = package_type
        self.package_name = package_name
        self.callback = callback
        self.scope = None

    def run(self):
        try:
            {"dnf": self._run_dnf, "flatpak": self._run_flatpak, "appimage": self._run_appimage_via_gearlever}[self.package_type]()
        except Exception as e:
            self.callback(False, str(e))

    def _run_dnf(self):
        dnf_args_map = {
            "install": ["install", "-y", self.package_name],
            "remove": ["remove", "-y", self.package_name],
            "update": ["upgrade", "-y"],
        }
        dnf_args = dnf_args_map.get(self.operation)
        if not dnf_args:
            self.callback(False, f"Unknown DNF operation: {self.operation}")
            return
        success, output = _run_privileged_dnf(dnf_args)
        self.callback(success, output)

    def _run_flatpak(self):
        app_id = self.package_name
        if self.operation == "install":
            scopes_to_try = [f"--{self.scope}" if self.scope else "--system"]
        else:
            remembered = load_flatpak_scopes().get(app_id)
            scopes_to_try = [f"--{remembered}"] if remembered else ["--system", "--user"]

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
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if r.returncode == 0:
                    if self.operation == "remove":
                        remove_flatpak_scope(app_id)
                    else:
                        save_flatpak_scope(app_id, scope_flag.lstrip("--"))
                    self.callback(True, r.stdout or "Done.")
                    return
            except Exception:
                pass
        self.callback(False, "Operation failed.")

    def _run_appimage_via_gearlever(self):
        if not gearlever_available():
            self.callback(False, "GearLever is not installed.\nInstall: flatpak install flathub it.mijorus.gearlever")
            return
        op_flag = {"integrate": "--integrate", "update": "--update", "remove": "--remove"}.get(self.operation)
        if not op_flag:
            self.callback(False, f"Unknown AppImage operation: {self.operation}")
            return
        r = _gl_run(op_flag, self.package_name)
        self.callback(r.returncode == 0, r.stdout or "Done." if r.returncode == 0 else r.stderr or r.stdout)

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
        combo = ttk.Combobox(search_frame, textvariable=self.view_var, values=["Installed", "Available", "All"], state="readonly", width=12)
        combo.pack(side=LEFT)
        combo.bind("<<ComboboxSelected>>", lambda e: self._list_selected())

        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill=BOTH, expand=True, padx=10, pady=5)
        cols = ("Name", "Version", "Repo", "Summary")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        for col, width in zip(cols, [180, 130, 110, 400]):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=width, minwidth=80, stretch=(col == "Summary"))
        sb = ttk.Scrollbar(tree_frame, orient=VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        sb.pack(side=RIGHT, fill=Y)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=X, padx=10, pady=5)
        ttk.Button(btn_frame, text="Install", command=lambda: self.dnf_action("install"), bootstyle=SUCCESS).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Remove", command=lambda: self.dnf_action("remove"), bootstyle=DANGER).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Update All", command=lambda: self.dnf_action("update"), bootstyle=WARNING).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Repo Selector", command=launch_repo_selector, bootstyle=INFO).pack(side=RIGHT, padx=5)

        self.status_var = StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status_var, anchor=W).pack(fill=X, padx=10, pady=(0, 5))

    def on_tab_selected(self):
        if not self.tree.get_children():
            self.list_installed()

    def _list_selected(self):
        val = self.view_var.get()
        if val == "Installed": self.list_installed()
        elif val == "Available": self.list_available()
        elif val == "All": self.list_all()

    def search_packages(self):
        query = self.search_var.get().strip()
        if not query: return
        self.status_var.set(f"Searching for '{query}'…")
        self.main_app.start_progress()
        threading.Thread(target=self._do_search, args=(query,), daemon=True).start()

    def _do_search(self, query):
        try:
            qf = "%{name}\t%{version}-%{release}\t%{repoid}\t%{summary}"
            r = subprocess.run(["dnf", "repoquery", "--queryformat", qf, "--available", "--arch", "x86_64,noarch", f"*{query}*"],
                               capture_output=True, text=True, timeout=60)
            results = []
            seen = set()
            for line in r.stdout.splitlines():
                line = line.strip()
                if not line or line.startswith(("Last metadata", "Modular")): continue
                parts = line.split("\t")
                if len(parts) < 2: continue
                name = parts[0].strip()
                if name in seen: continue
                seen.add(name)
                results.append({
                    "name": name,
                    "version": parts[1] if len(parts) > 1 else "",
                    "repo": parts[2] if len(parts) > 2 else "",
                    "summary": parts[3] if len(parts) > 3 else ""
                })
            self.after(0, lambda: self._show_search_results(results, query))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Search Error", str(e)))

    def _show_search_results(self, results, query):
        self.main_app.stop_progress()
        for item in self.tree.get_children(): self.tree.delete(item)
        for pkg in results:
            self.tree.insert("", END, values=(pkg["name"], pkg.get("version", ""), pkg.get("repo", ""), pkg.get("summary", "")))
        count = len(results)
        self.status_var.set(f"Found {count} result(s) for '{query}'." if count else f"No results found for '{query}'.")

    def list_installed(self):
        self.status_var.set("Listing installed packages…")
        self.main_app.start_progress()
        threading.Thread(target=self._do_list_installed, daemon=True).start()

    def _do_list_installed(self):
        try:
            r = subprocess.run(["dnf", "list", "--installed", "--quiet"], capture_output=True, text=True, timeout=60)
            self.after(0, lambda: self._parse_and_populate(r.stdout, "installed packages"))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error", str(e)))

    def list_available(self):
        self.status_var.set("Listing available packages…")
        self.main_app.start_progress()
        threading.Thread(target=self._do_list_available, daemon=True).start()

    def _do_list_available(self):
        try:
            r = subprocess.run(["dnf", "list", "--available", "--quiet"], capture_output=True, text=True, timeout=120)
            self.after(0, lambda: self._parse_and_populate(r.stdout, "available packages"))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error", str(e)))

    def list_all(self):
        self.status_var.set("Listing all packages…")
        self.main_app.start_progress()
        threading.Thread(target=self._do_list_all, daemon=True).start()

    def _do_list_all(self):
        try:
            r = subprocess.run(["dnf", "list", "--quiet"], capture_output=True, text=True, timeout=120)
            self.after(0, lambda: self._parse_and_populate(r.stdout, "packages"))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error", str(e)))

    def _parse_and_populate(self, output: str, label: str):
        for item in self.tree.get_children(): self.tree.delete(item)
        count = 0
        for line in output.strip().splitlines():
            parsed = _parse_dnf_line(line)
            if parsed:
                name, version, repo = parsed
                self.tree.insert("", END, values=(name, version, repo, ""))
                count += 1
        self.main_app.stop_progress()
        self.status_var.set(f"Showing {count} {label}.")

    def dnf_action(self, action):
        if action == "update":
            if not messagebox.askyesno("Confirm", "Update all installed packages?"): return
            self.main_app.start_progress("Updating all packages…")
            PackageWorker("update", "dnf", "", self.on_action_finished).start()
            return
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("No Selection", "Please select a package first.")
            return
        pkg = self.tree.item(sel[0])['values'][0]
        if not messagebox.askyesno("Confirm", f"{action.capitalize()} package '{pkg}'?"): return
        self.main_app.start_progress(f"{action.capitalize()}ing {pkg}…")
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
        combo = ttk.Combobox(search_frame, textvariable=self.view_var, values=["Installed", "Available"], state="readonly", width=12)
        combo.pack(side=LEFT)
        combo.bind("<<ComboboxSelected>>", lambda e: self._list_selected())

        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill=BOTH, expand=True, padx=10, pady=5)
        cols = ("Name", "App ID", "Version", "Branch", "Description")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        for col, w in zip(cols, [160, 220, 100, 80, 350]):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, minwidth=60, stretch=(col == "Description"))
        sb = ttk.Scrollbar(tree_frame, orient=VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        sb.pack(side=RIGHT, fill=Y)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=X, padx=10, pady=5)
        ttk.Button(btn_frame, text="Install", command=lambda: self.flat_action("install"), bootstyle=SUCCESS).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Remove", command=lambda: self.flat_action("remove"), bootstyle=DANGER).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Update", command=lambda: self.flat_action("update"), bootstyle=WARNING).pack(side=LEFT, padx=5)

        self.status_var = StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status_var, anchor=W).pack(fill=X, padx=10, pady=(0, 5))

    def on_tab_selected(self):
        if not self.tree.get_children():
            self.list_installed()

    def _list_selected(self):
        if self.view_var.get() == "Installed":
            self.list_installed()
        else:
            self.list_available()

    def search_flatpaks(self):
        query = self.search_var.get().strip()
        if not query: return
        self.status_var.set(f"Searching for '{query}'…")
        self.main_app.start_progress()
        threading.Thread(target=self._do_search, args=(query,), daemon=True).start()

    def _do_search(self, query):
        try:
            r = subprocess.run(["flatpak", "search", "--columns=name,application,version,branch,description", query],
                               capture_output=True, text=True, timeout=30)
            self.after(0, lambda: (self.main_app.stop_progress(), self._parse_flatpak_output(r.stdout, is_search=True)))
        except Exception as e:
            self.after(0, lambda: (self.main_app.stop_progress(), messagebox.showerror("Error", str(e))))

    def list_installed(self):
        self.status_var.set("Loading installed Flatpaks…")
        self.main_app.start_progress()
        threading.Thread(target=self._do_list_installed, daemon=True).start()

    def _do_list_installed(self):
        try:
            r = subprocess.run(["flatpak", "list", "--columns=name,application,version,branch,description"],
                               capture_output=True, text=True, timeout=30)
            self.after(0, lambda: (self.main_app.stop_progress(), self._parse_flatpak_output(r.stdout, is_search=False)))
        except Exception as e:
            self.after(0, lambda: (self.main_app.stop_progress(), messagebox.showerror("Error", str(e))))

    def list_available(self):
        self.status_var.set("Listing available Flatpaks…")
        self.main_app.start_progress()
        threading.Thread(target=self._do_list_available, daemon=True).start()

    def _do_list_available(self):
        try:
            r = subprocess.run(["flatpak", "remote-ls", "--columns=name,application,version,branch,description"],
                               capture_output=True, text=True, timeout=120)
            self.after(0, lambda: (self.main_app.stop_progress(), self._parse_flatpak_output(r.stdout, is_search=True)))
        except Exception as e:
            self.after(0, lambda: (self.main_app.stop_progress(), messagebox.showerror("Error", str(e))))

    def _parse_flatpak_output(self, output, is_search: bool):
        for item in self.tree.get_children(): self.tree.delete(item)
        count = 0
        for line in output.strip().splitlines():
            line = line.strip()
            if not line or line.lower().startswith(("name\t", "name ")): continue
            parts = [p.strip() for p in line.split('\t')]
            if len(parts) < 2: continue
            name, app_id = parts[0], parts[1]
            version = parts[2] if len(parts) > 2 else ""
            branch = parts[3] if len(parts) > 3 else ""
            desc = parts[4] if len(parts) > 4 else ""
            if not app_id: continue
            self.tree.insert("", END, values=(name, app_id, version, branch, desc))
            count += 1
        self.status_var.set(f"{count} {'search result(s)' if is_search else 'Flatpak(s)'}.")

    def flat_action(self, action):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("No Selection", "Please select a Flatpak first.")
            return
        app_id = self.tree.item(sel[0])['values'][1]
        if action == "install":
            self._show_scope_dialog(app_id)
            return
        self.main_app.start_progress(f"{action.capitalize()}ing {app_id}…")
        w = PackageWorker(action, "flatpak", app_id, self.on_action_finished)
        w.start()

    def _show_scope_dialog(self, app_id):
        dlg = Toplevel(self)
        dlg.title("Choose Install Scope")
        dlg.resizable(False, False)
        dlg.transient(self.main_app)
        dlg.grab_set()
        ttk.Label(dlg, text=f"How would you like to install\n{app_id}?", font=("", 12), anchor=CENTER, justify=CENTER).pack(padx=24, pady=(20, 8))
        choice_var = StringVar(value="system")
        rb_frame = ttk.Frame(dlg)
        rb_frame.pack(padx=32, pady=8, anchor=W)
        ttk.Radiobutton(rb_frame, text="For all users (system-wide) — recommended", variable=choice_var, value="system", bootstyle="info-toolbutton").pack(anchor=W, pady=4)
        ttk.Radiobutton(rb_frame, text="Only for current user", variable=choice_var, value="user", bootstyle="info-toolbutton").pack(anchor=W, pady=4)
        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(pady=(8, 16))
        def proceed():
            scope = choice_var.get()
            dlg.destroy()
            self.main_app.start_progress(f"Installing {app_id}…")
            w = PackageWorker("install", "flatpak", app_id, self.on_action_finished)
            w.scope = scope
            w.start()
        ttk.Button(btn_frame, text="Install", command=proceed, bootstyle=SUCCESS, width=12).pack(side=LEFT, padx=8)
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy, bootstyle="outline-secondary", width=12).pack(side=LEFT, padx=8)

    def on_action_finished(self, success, message):
        self.after(0, lambda: self._action_done(success, message))

    def _action_done(self, success, message):
        self.main_app.stop_progress()
        if success:
            messagebox.showinfo("Success", "Operation completed successfully.")
            self.list_installed()
        else:
            messagebox.showerror("Error", message)

# ---------------------------------------------------------------------------
# AppImageTab
# ---------------------------------------------------------------------------
class AppImageTab(ttk.Frame):
    def __init__(self, parent, main_app):
        super().__init__(parent)
        self.main_app = main_app
        self._gl_ok = False
        self.init_ui()
        self.check_gearlever_then_load()

    def init_ui(self):
        self.warn_frame = ttk.Frame(self, bootstyle=WARNING)
        ttk.Label(self.warn_frame, text="GearLever is not installed. Install: flatpak install flathub it.mijorus.gearlever", bootstyle=WARNING, wraplength=700, justify=LEFT).pack(padx=10, pady=6, anchor=W)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=X, padx=10, pady=8)
        ttk.Button(btn_frame, text="Add / Integrate…", command=self.integrate_appimage, bootstyle=SUCCESS).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Remove", command=self.remove_appimage, bootstyle=DANGER).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Run", command=self.run_appimage, bootstyle=PRIMARY).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Refresh List", command=self.load_installed, bootstyle=SECONDARY).pack(side=RIGHT, padx=5)

        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill=BOTH, expand=True, padx=10, pady=5)
        cols = ("Name", "Version", "Path")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        for col, w in zip(cols, [200, 100, 500]):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, minwidth=80, stretch=(col == "Path"))
        sb = ttk.Scrollbar(tree_frame, orient=VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        sb.pack(side=RIGHT, fill=Y)

        self.status_var = StringVar(value="Checking for GearLever…")
        ttk.Label(self, textvariable=self.status_var, anchor=W).pack(fill=X, padx=10, pady=(0, 5))

    def check_gearlever_then_load(self):
        def _check():
            ok = gearlever_available()
            self.after(0, lambda: self._on_gl_check(ok))
        threading.Thread(target=_check, daemon=True).start()

    def on_tab_selected(self):
        if not self.tree.get_children() and self._gl_ok:
            self.load_installed()

    def _on_gl_check(self, ok):
        self._gl_ok = ok
        if ok:
            self.warn_frame.pack_forget()
            self.load_installed()
        else:
            self.warn_frame.pack(fill=X, padx=10, pady=(8, 0))
            self.status_var.set("GearLever not found – install it to manage AppImages.")

    def load_installed(self):
        if not self._gl_ok: return
        self.status_var.set("Loading installed AppImages from GearLever…")
        def _load():
            apps = gearlever_list_installed()
            self.after(0, lambda: self._populate(apps))
        threading.Thread(target=_load, daemon=True).start()

    def _populate(self, apps):
        for item in self.tree.get_children(): self.tree.delete(item)
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
        if not self._gl_ok: return
        path = filedialog.askopenfilename(title="Select AppImage to integrate", filetypes=[("AppImage files", "*.AppImage *.appimage"), ("All files", "*")])
        if path:
            self.main_app.start_progress("Integrating with GearLever…")
            PackageWorker("integrate", "appimage", path, self._on_done).start()

    def remove_appimage(self):
        path = self._selected_path()
        if not path: return
        name = self.tree.item(self.tree.selection()[0])['values'][0]
        if messagebox.askyesno("Confirm", f"Remove '{name}' via GearLever?"):
            self.main_app.start_progress("Removing AppImage via GearLever…")
            PackageWorker("remove", "appimage", path, self._on_done).start()

    def run_appimage(self):
        path = self._selected_path()
        if not path: return
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
        self.init_ui()

    def init_ui(self):
        top = ttk.Frame(self)
        top.pack(fill=X, padx=10, pady=8)
        ttk.Label(top, text="Available Updates", font=("", 13, "bold")).pack(side=LEFT)
        ttk.Button(top, text="Check Now", command=self.check_updates, bootstyle=PRIMARY).pack(side=RIGHT, padx=5)
        ttk.Button(top, text="Update All DNF", command=self.update_all_dnf, bootstyle=WARNING).pack(side=RIGHT, padx=5)
        ttk.Button(top, text="Update All Flatpak", command=self.update_all_flatpak, bootstyle=WARNING).pack(side=RIGHT, padx=5)
        ttk.Button(top, text="Update All AppImages", command=self.update_all_appimage, bootstyle=WARNING).pack(side=RIGHT, padx=5)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill=BOTH, expand=True, padx=10, pady=5)
        for attr, title, cols in [
            ("dnf_tree", "DNF", ("Package", "Available Version", "Repo")),
            ("flat_tree", "Flatpak", ("App ID", "Name", "Branch")),
            ("ai_tree", "AppImages", ("Name", "Version", "Path")),
        ]:
            frame = ttk.Frame(self.nb)
            self.nb.add(frame, text=title)
            setattr(self, attr, self._make_tree(frame, cols))

        self.status_var = StringVar(value="Press 'Check Now' to scan for updates.")
        ttk.Label(self, textvariable=self.status_var, anchor=W).pack(fill=X, padx=10, pady=(0, 5))

    def on_tab_selected(self):
        if not self.dnf_tree.get_children() and not self.flat_tree.get_children():
            self.check_updates()

    def _make_tree(self, parent, cols):
        f = ttk.Frame(parent); f.pack(fill=BOTH, expand=True)
        tree = ttk.Treeview(f, columns=cols, show="headings")
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=200, minwidth=80, stretch=True)
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
        for tree in (self.dnf_tree, self.flat_tree, self.ai_tree):
            for item in tree.get_children(): tree.delete(item)
        for pkg in updates.get("dnf", []):
            self.dnf_tree.insert("", END, values=(pkg, "", ""))
        for entry in updates.get("flatpak", []):
            parts = entry.split('\t') if '\t' in entry else [entry, "", ""]
            self.flat_tree.insert("", END, values=(parts[0], parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else ""))
        for app in updates.get("appimage", []):
            self.ai_tree.insert("", END, values=(app.get("name", ""), app.get("version", ""), app.get("path", "")))
        total = sum(len(v) for v in updates.values() if isinstance(v, list))
        errors = updates.get("errors", [])
        if errors:
            self.status_var.set(f"{total} update(s) available. Errors: {'; '.join(errors)}")
        else:
            self.status_var.set(f"{total} update(s) available.")

    def update_all_dnf(self):
        if messagebox.askyesno("Confirm", "Update all DNF packages now?"):
            self.main_app.start_progress("Updating DNF packages…")
            PackageWorker("update", "dnf", "", self._on_done).start()

    def update_all_flatpak(self):
        if not messagebox.askyesno("Confirm", "Update all Flatpaks now?"): return
        self.main_app.start_progress("Updating Flatpaks…")
        def _do():
            try:
                r = subprocess.run(["flatpak", "update", "-y"], capture_output=True, text=True, timeout=300)
                msg = r.stdout if r.returncode == 0 else (r.stderr or r.stdout)
                self.after(0, lambda: self._on_done(r.returncode == 0, msg or "No output from flatpak."))
            except Exception as e:
                self.after(0, lambda: self._on_done(False, str(e)))
        threading.Thread(target=_do, daemon=True).start()

    def update_all_appimage(self):
        if not gearlever_available():
            messagebox.showwarning("GearLever Missing", "GearLever is not installed.")
            return
        self.main_app.start_progress("Checking for AppImage updates…")
        def _fetch():
            to_update = gearlever_list_updates()
            self.after(0, lambda: self._confirm(to_update))
        threading.Thread(target=_fetch, daemon=True).start()

    def _confirm(self, to_update):
        self.main_app.stop_progress()
        if not to_update:
            messagebox.showinfo("No Updates", "All AppImages are already up to date.")
            return
        names = "\n".join(f" • {a['name']}" for a in to_update)
        if not messagebox.askyesno("Update AppImages", f"{len(to_update)} update(s) available:\n\n{names}\n\nUpdate all now?"): return
        self.main_app.start_progress("Updating AppImages…")
        def _do():
            for app in to_update:
                _gl_run("--update", app["path"])
            self.after(0, lambda: self._on_done(True, "AppImage updates completed."))
        threading.Thread(target=_do, daemon=True).start()

    def _on_done(self, success, message):
        self.main_app.stop_progress()
        if success:
            messagebox.showinfo("Success", message or "Update completed.")
            self.check_updates()
        else:
            messagebox.showerror("Error", message)

# ---------------------------------------------------------------------------
# SyncTab
# ---------------------------------------------------------------------------
class SyncTab(ttk.Frame):
    _TAG_DNF = "dnf"
    _TAG_FLATPAK = "flatpak"
    _TAG_APPIMAGE = "appimage"

    def __init__(self, parent, main_app):
        super().__init__(parent)
        self.main_app = main_app
        self._sync_data = None
        self.init_ui()

    def init_ui(self):
        toolbar = ttk.Frame(self)
        toolbar.pack(fill=X, padx=10, pady=(10, 4))
        ttk.Label(toolbar, text="Package Sync / Backup", font=("", 13, "bold")).pack(side=LEFT)
        ttk.Button(toolbar, text="Export Backup", command=self.export_backup, bootstyle=SUCCESS, width=18).pack(side=RIGHT, padx=4)
        ttk.Button(toolbar, text="Load Backup", command=self.load_backup, bootstyle=INFO, width=18).pack(side=RIGHT, padx=4)

        opt_lf = ttk.Labelframe(self, text="Export / Preview options", padding=8)
        opt_lf.pack(fill=X, padx=10, pady=4)
        self.inc_dnf_var = BooleanVar(value=True)
        self.inc_flatpak_var = BooleanVar(value=True)
        self.inc_appimage_var = BooleanVar(value=True)
        self.skip_base_var = BooleanVar(value=True)
        ttk.Checkbutton(opt_lf, text="Include DNF packages", variable=self.inc_dnf_var, bootstyle="round-toggle").grid(row=0, column=0, sticky=W, padx=12, pady=2)
        ttk.Checkbutton(opt_lf, text="Include Flatpaks", variable=self.inc_flatpak_var, bootstyle="round-toggle").grid(row=0, column=1, sticky=W, padx=12, pady=2)
        ttk.Checkbutton(opt_lf, text="Include AppImages", variable=self.inc_appimage_var, bootstyle="round-toggle").grid(row=0, column=2, sticky=W, padx=12, pady=2)
        ttk.Checkbutton(opt_lf, text="Skip baseline system packages", variable=self.skip_base_var, bootstyle="round-toggle").grid(row=1, column=0, columnspan=2, sticky=W, padx=12, pady=2)
        ttk.Button(opt_lf, text="Preview current install", command=self.preview_current, bootstyle="outline-secondary").grid(row=1, column=2, sticky=E, padx=12)

        tree_lf = ttk.Labelframe(self, text="Packages", padding=4)
        tree_lf.pack(fill=BOTH, expand=True, padx=10, pady=4)
        cols = ("Type", "Name", "Version / App-ID", "Extra", "Status")
        self.tree = ttk.Treeview(tree_lf, columns=cols, show="headings", selectmode="extended")
        for c, w in zip(cols, [80, 200, 200, 220, 110]):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, minwidth=60, stretch=(c == "Extra"))
        self.tree.tag_configure(self._TAG_DNF, foreground="#5bc0de")
        self.tree.tag_configure(self._TAG_FLATPAK, foreground="#9b59b6")
        self.tree.tag_configure(self._TAG_APPIMAGE, foreground="#e67e22")
        vsb = ttk.Scrollbar(tree_lf, orient=VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_lf, orient=HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_lf.rowconfigure(0, weight=1)
        tree_lf.columnconfigure(0, weight=1)

        restore_frame = ttk.Frame(self)
        restore_frame.pack(fill=X, padx=10, pady=(4, 8))
        self.restore_scope_var = StringVar(value="system")
        ttk.Label(restore_frame, text="Flatpak scope:").pack(side=LEFT)
        ttk.Radiobutton(restore_frame, text="System", variable=self.restore_scope_var, value="system", bootstyle="info-toolbutton").pack(side=LEFT, padx=4)
        ttk.Radiobutton(restore_frame, text="User", variable=self.restore_scope_var, value="user", bootstyle="info-toolbutton").pack(side=LEFT, padx=4)
        self.restore_btn = ttk.Button(restore_frame, text="Restore All", command=self.start_restore, bootstyle=WARNING, width=16, state=DISABLED)
        self.restore_btn.pack(side=RIGHT, padx=6)
        ttk.Button(restore_frame, text="Clear", command=self._clear, bootstyle="outline-secondary", width=10).pack(side=RIGHT, padx=4)

        self.status_var = StringVar(value="Export a backup from this machine, or load one to restore.")
        ttk.Label(self, textvariable=self.status_var, anchor=W).pack(fill=X, padx=10, pady=(0, 4))

    def export_backup(self):
        self.status_var.set("Collecting installed packages…")
        self.main_app.start_progress()
        def _collect():
            data = collect_sync_data(
                include_dnf=self.inc_dnf_var.get(),
                include_flatpak=self.inc_flatpak_var.get(),
                include_appimage=self.inc_appimage_var.get(),
                skip_baseline=self.skip_base_var.get(),
            )
            self.after(0, lambda: self._do_save_dialog(data))
        threading.Thread(target=_collect, daemon=True).start()

    def _do_save_dialog(self, data: dict):
        self.main_app.stop_progress()
        hostname = data.get("hostname", "unknown")
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        default = f"findy_backup_{hostname}_{timestamp}.json"
        path = filedialog.asksaveasfilename(title="Save package backup", initialfile=default,
                                            defaultextension=".json",
                                            filetypes=[("FiNDy backup", "*.json"), ("All files", "*")])
        if not path:
            self.status_var.set("Export cancelled.")
            return
        try:
            with open(path, "w") as fh:
                json.dump(data, fh, indent=2)
            self.status_var.set(f"Backup saved to {path}")
            messagebox.showinfo("Backup Saved", f"Package backup saved successfully!\n\nFile: {path}")
            self._load_data_into_tree(data, mode="preview")
        except Exception as e:
            messagebox.showerror("Save Error", str(e))

    def load_backup(self):
        path = filedialog.askopenfilename(title="Open FiNDy backup", filetypes=[("FiNDy backup", "*.json"), ("All files", "*")])
        if not path: return
        try:
            with open(path) as fh:
                data = json.load(fh)
            self._sync_data = data
            self._load_data_into_tree(data, mode="restore")
            self.restore_btn.config(state=NORMAL)
            self.status_var.set(f"Loaded backup with {len(data.get('dnf', []))} DNF, {len(data.get('flatpak', []))} Flatpak, {len(data.get('appimage', []))} AppImage packages")
        except Exception as e:
            messagebox.showerror("Load Error", str(e))

    def preview_current(self):
        self.status_var.set("Scanning installed packages…")
        self.main_app.start_progress()
        def _collect():
            data = collect_sync_data(
                include_dnf=self.inc_dnf_var.get(),
                include_flatpak=self.inc_flatpak_var.get(),
                include_appimage=self.inc_appimage_var.get(),
                skip_baseline=self.skip_base_var.get(),
            )
            self.after(0, lambda: self._after_preview(data))
        threading.Thread(target=_collect, daemon=True).start()

    def _after_preview(self, data):
        self.main_app.stop_progress()
        self._load_data_into_tree(data, mode="preview")
        dnf_count = len(data.get("dnf", []))
        if self.inc_dnf_var.get() and self.skip_base_var.get():
            self.status_var.set(f"Preview ready — {dnf_count} user DNF packages (system baseline skipped).")
        else:
            self.status_var.set(f"Preview ready — {dnf_count} DNF packages.")

    def _load_data_into_tree(self, data: dict, mode: str = "preview"):
        for item in self.tree.get_children():
            self.tree.delete(item)
        status = "" if mode == "preview" else "Pending"
        for pkg in data.get("dnf", []):
            self.tree.insert("", END, tags=(self._TAG_DNF,), values=("DNF", pkg.get("name", ""), pkg.get("version", ""), pkg.get("repo", ""), status))
        for fp in data.get("flatpak", []):
            self.tree.insert("", END, tags=(self._TAG_FLATPAK,), values=("Flatpak", fp.get("name", ""), fp.get("app_id", ""), fp.get("branch", ""), status))
        for ai in data.get("appimage", []):
            self.tree.insert("", END, tags=(self._TAG_APPIMAGE,), values=("AppImage", ai.get("name", ""), ai.get("version", ""), ai.get("path", ""), status))
        if not self.tree.get_children():
            self.status_var.set("No packages found after filtering.")

    def _clear(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._sync_data = None
        self.restore_btn.config(state=DISABLED)
        self.status_var.set("Cleared.")

    def start_restore(self):
        if not self._sync_data:
            messagebox.showwarning("No Backup Loaded", "Load a backup file first.")
            return

        dnf_pkgs = [p.get("name", "") for p in self._sync_data.get("dnf", []) if p.get("name")]
        flat_pkgs = [p for p in self._sync_data.get("flatpak", []) if p.get("app_id")]
        ai_pkgs = self._sync_data.get("appimage", [])

        parts = []
        if dnf_pkgs:
            parts.append(f"{len(dnf_pkgs)} DNF package(s)")
        if flat_pkgs:
            parts.append(f"{len(flat_pkgs)} Flatpak(s)")
        if ai_pkgs:
            parts.append(f"{len(ai_pkgs)} AppImage(s)")

        if not parts:
            messagebox.showinfo("Nothing to Restore", "The loaded backup contains no packages.")
            return

        summary = "Restore will install:\n\n" + "\n".join(f"  - {p}" for p in parts)
        summary += "\n\nDNF packages require root authentication."
        if not messagebox.askyesno("Confirm Restore", summary):
            return

        self.main_app.start_progress("Restoring packages…")

        def _restore():
            results = {"dnf_installed": 0, "dnf_failed": 0, "flatpak_installed": 0, "flatpak_failed": 0, "errors": []}

            if dnf_pkgs:
                installed = self._get_installed_dnf_names()
                to_install = [p for p in dnf_pkgs if p not in installed]
                if to_install:
                    batch_size = 50
                    for i in range(0, len(to_install), batch_size):
                        batch = to_install[i:i + batch_size]
                        success, output = _run_privileged_dnf(["install", "-y"] + batch)
                        if success:
                            results["dnf_installed"] += len(batch)
                        else:
                            results["dnf_failed"] += len(batch)
                            results["errors"].append(f"DNF batch failed: {output[:200]}")

            scope = self.restore_scope_var.get()
            for fp in flat_pkgs:
                app_id = fp["app_id"]
                fp_scope = fp.get("scope", scope)
                cmd = ["flatpak", "install", f"--{fp_scope}", "-y", app_id]
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                    if r.returncode == 0:
                        results["flatpak_installed"] += 1
                        save_flatpak_scope(app_id, fp_scope)
                    else:
                        results["flatpak_failed"] += 1
                except Exception:
                    results["flatpak_failed"] += 1

            self.after(0, lambda: self._restore_done(results))

        threading.Thread(target=_restore, daemon=True).start()

    def _get_installed_dnf_names(self) -> set:
        try:
            r = subprocess.run(["dnf", "repoquery", "--queryformat", "%{name}", "--installed"],
                               capture_output=True, text=True, timeout=60)
            return {line.strip() for line in r.stdout.splitlines() if line.strip()}
        except Exception:
            return set()

    def _restore_done(self, results):
        self.main_app.stop_progress()
        msg_parts = []
        if results["dnf_installed"] or results["dnf_failed"]:
            msg_parts.append(f"DNF: {results['dnf_installed']} installed, {results['dnf_failed']} failed")
        if results["flatpak_installed"] or results["flatpak_failed"]:
            msg_parts.append(f"Flatpak: {results['flatpak_installed']} installed, {results['flatpak_failed']} failed")
        if not msg_parts:
            msg_parts.append("No packages needed restoring (all already installed).")

        summary = "\n".join(msg_parts)
        if results["errors"]:
            summary += "\n\nErrors:\n" + "\n".join(results["errors"][:5])

        messagebox.showinfo("Restore Complete", summary)
        self.status_var.set("Restore completed.")

# ---------------------------------------------------------------------------
# Main Application
# ---------------------------------------------------------------------------
class FiNDyApp(ttk.Window):
    def __init__(self):
        self.settings = load_settings()
        super().__init__(themename=self.settings.get("theme", "darkly"))
        self.title("FiNDy – OpenMandriva Package Manager")
        self.geometry("1000x680")
        self._set_window_icon()
        self.edition = detect_omv_edition()
        self.init_ui()
        threading.Thread(target=ensure_polkit_agent, daemon=True).start()
        self.schedule_update_check()
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _set_window_icon(self):
        if Image is None:
            return
        try:
            size = 64
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle([2, 2, size - 2, size - 2], radius=12, fill="#2196F3")
            draw.text((size // 2, size // 2), "F", fill="white", anchor="mm")
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                img.save(f, format="PNG")
                tmp = f.name
            photo = PhotoImage(file=tmp)
            self.iconphoto(True, photo)
            self._icon_photo = photo
            os.unlink(tmp)
        except Exception:
            pass

    def init_ui(self):
        menu = Menu(self)
        self.config(menu=menu)
        file_menu = Menu(menu, tearoff=False)
        menu.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Settings", command=self.show_settings)
        file_menu.add_command(label="Refresh Repos", command=self.refresh_repos_bg)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.on_closing)

        header = ttk.Frame(self)
        header.pack(fill=X, padx=10, pady=8)
        ttk.Label(header, text=f"FiNDy – OpenMandriva {_display_edition(self.edition)} Package Manager", font=("", 16, "bold")).pack(side=LEFT)
        self.progress = ttk.Progressbar(header, mode="indeterminate")
        self.progress.pack(side=RIGHT, fill=X, expand=True, padx=(20, 0))

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill=BOTH, expand=True, padx=10, pady=5)

        self.dnf_tab = DNFTab(self.nb, self)
        self.nb.add(self.dnf_tab, text="DNF Packages")
        self.flatpak_tab = FlatpakTab(self.nb, self)
        self.nb.add(self.flatpak_tab, text="Flatpaks")
        self.appimage_tab = AppImageTab(self.nb, self)
        self.nb.add(self.appimage_tab, text="AppImages")
        self.updates_tab = UpdatesTab(self.nb, self)
        self.nb.add(self.updates_tab, text="Updates")
        self.sync_tab = SyncTab(self.nb, self)
        self.nb.add(self.sync_tab, text="Sync / Backup")

        self.nb.bind("<<NotebookTabChanged>>", self.on_tab_changed)

    def on_tab_changed(self, event):
        idx = self.nb.index(self.nb.select())
        if idx == 0:
            self.dnf_tab.on_tab_selected()
        elif idx == 1:
            self.flatpak_tab.on_tab_selected()
        elif idx == 2:
            self.appimage_tab.on_tab_selected()
        elif idx == 3:
            self.updates_tab.on_tab_selected()

    def schedule_update_check(self):
        interval = self.settings.get("interval_minutes", 15) * 60000
        self.after(interval, self.check_updates_bg)

    def check_updates_bg(self):
        self.start_progress()
        UpdateChecker(self.on_updates_checked).start()

    def on_updates_checked(self, updates):
        self.after(0, self.stop_progress)
        self.updates_tab._on_updates(updates)
        self.schedule_update_check()

    def start_progress(self, message=""):
        self.progress.start()

    def stop_progress(self):
        self.progress.stop()

    def refresh_repos_bg(self):
        self.start_progress()
        threading.Thread(target=lambda: (refresh_repos(), self.after(0, self.stop_progress)), daemon=True).start()

    def show_settings(self):
        dlg = Toplevel(self)
        dlg.title("Settings")
        dlg.geometry("420x360")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        ttk.Label(dlg, text="FiNDy Settings", font=("", 13, "bold")).pack(padx=16, pady=(16, 8))

        frame = ttk.Frame(dlg, padding=12)
        frame.pack(fill=BOTH, expand=True)

        # Notifications
        notif_var = BooleanVar(value=self.settings.get("notifications", True))
        ttk.Checkbutton(frame, text="Show desktop notifications for updates", variable=notif_var, bootstyle="round-toggle").grid(row=0, column=0, columnspan=2, sticky=W, pady=4)

        # Minimize to tray
        tray_var = BooleanVar(value=self.settings.get("minimize_to_tray", True))
        ttk.Checkbutton(frame, text="Minimize to system tray (requires pystray + Pillow)", variable=tray_var, bootstyle="round-toggle").grid(row=1, column=0, columnspan=2, sticky=W, pady=4)

        # Update interval
        ttk.Label(frame, text="Update check interval:").grid(row=2, column=0, sticky=W, pady=4, padx=(0, 8))
        interval_var = StringVar(value=str(self.settings.get("interval_minutes", 15)))
        interval_spin = ttk.Spinbox(frame, from_=5, to=120, increment=5, width=6, textvariable=interval_var)
        interval_spin.grid(row=2, column=1, sticky=W, pady=4)
        ttk.Label(frame, text="minutes").grid(row=2, column=1, sticky=W, padx=(72, 0), pady=4)

        # Theme
        ttk.Label(frame, text="Theme:").grid(row=3, column=0, sticky=W, pady=4, padx=(0, 8))
        themes = [
            "darkly", "superhero", "solar", "cyborg", "vapor",
            "flatly", "cosmo", "litera", "lumen", "minty", "pulse",
            "journal", "simplex", "cerculean",
        ]
        theme_var = StringVar(value=self.settings.get("theme", "darkly"))
        theme_combo = ttk.Combobox(frame, textvariable=theme_var, values=themes, state="readonly", width=18)
        theme_combo.grid(row=3, column=1, sticky=W, pady=4)

        def _apply():
            self.settings["notifications"] = notif_var.get()
            self.settings["minimize_to_tray"] = tray_var.get()
            try:
                self.settings["interval_minutes"] = max(5, int(interval_var.get()))
            except ValueError:
                self.settings["interval_minutes"] = 15
            self.settings["theme"] = theme_var.get()
            save_settings(self.settings)
            new_theme = self.settings["theme"]
            if new_theme != self.style.theme.name:
                self.style.theme_use(new_theme)
            dlg.destroy()
            self.schedule_update_check()

        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(pady=(4, 12))
        ttk.Button(btn_frame, text="Apply", command=_apply, bootstyle=SUCCESS, width=12).pack(side=LEFT, padx=8)
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy, bootstyle="outline-secondary", width=12).pack(side=LEFT, padx=8)

    def on_closing(self):
        self.quit()

def _parse_dnf_line(line: str):
    line = line.strip()
    if not line or line.startswith(("Last metadata", "Installed", "Available", "Extra", "Loaded", "Obsoleting")):
        return None
    parts = re.split(r'\s+', line)
    if len(parts) < 2:
        return None
    name_arch = parts[0]
    name = _ARCH_RE.sub('', name_arch)
    version = parts[1] if len(parts) > 1 else ""
    repo = parts[2] if len(parts) > 2 else ""
    return name, version, repo

if __name__ == "__main__":
    app = FiNDyApp()
    app.mainloop()