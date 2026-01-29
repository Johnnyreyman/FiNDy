#!/usr/bin/env python3
"""
FiNDy Package Manager
A modern Tkinter-based GUI for managing DNF packages, Flatpaks, and AppImages
Fast, Intelligent package management for OpenMandriva Lx
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

class PackageWorker(threading.Thread):
    """Worker thread for package operations"""
    
    def __init__(self, operation, package_type, package_name, callback):
        super().__init__(daemon=True)
        self.operation = operation
        self.package_type = package_type
        self.package_name = package_name
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
    
    def _run_dnf(self):
        """Execute DNF operations"""
        cmd_map = {
            "install": ["pkexec", "dnf", "install", "-y", self.package_name],
            "remove": ["pkexec", "dnf", "remove", "-y", self.package_name],
            "update": ["pkexec", "dnf", "distro-sync", "-y"]
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
    
    def _run_flatpak(self):
        """Execute Flatpak operations"""
        cmd_map = {
            "install": ["flatpak", "install", "-y", self.package_name],
            "remove": ["flatpak", "uninstall", "-y", self.package_name],
            "update": ["flatpak", "update", "-y"]
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
    
    def _run_appimage(self):
        """Execute manual AppImage operations (Fallback)"""
        appimage_dir = os.path.expanduser("~/.local/share/applications/appimages")
        
        if self.operation == "install":
            if not os.path.exists(appimage_dir):
                os.makedirs(appimage_dir, exist_ok=True)
                
            dest = os.path.join(appimage_dir, os.path.basename(self.package_name))
            subprocess.run(["cp", self.package_name, dest])
            subprocess.run(["chmod", "+x", dest])
            self.callback(True, f"AppImage installed to {dest}")
            
        elif self.operation == "remove":
            file_path = os.path.join(appimage_dir, self.package_name)
            if os.path.exists(file_path):
                os.remove(file_path)
                self.callback(True, f"Removed {self.package_name}")
            else:
                self.callback(False, "AppImage not found")
    
    def _run_gearlever(self):
        """Execute Gear Lever operations via Flatpak CLI"""
        base_cmd = ["flatpak", "run", "it.mijorus.gearlever"]
        
        if self.operation == "integrate":
            cmd = base_cmd + ["--integrate", self.package_name]
            result = subprocess.run(cmd, capture_output=True, text=True)
            success = result.returncode == 0
            msg = f"Integrated {os.path.basename(self.package_name)}" if success else result.stderr
            self.callback(success, msg)
            
        elif self.operation == "remove":
            # Gear Lever's --remove expects the full path to the AppImage
            # Use the full path directly
            cmd = base_cmd + ["--remove", self.package_name]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                self.callback(True, f"Removed {os.path.basename(self.package_name)} via Gear Lever")
            else:
                # If Gear Lever fails, try manual removal as fallback
                if os.path.exists(self.package_name):
                    try:
                        os.remove(self.package_name)
                        # Also try to remove associated .desktop file
                        desktop_file = self.package_name + '.desktop'
                        if os.path.exists(desktop_file):
                            os.remove(desktop_file)
                        
                        # Try to remove from ~/.local/share/applications if it exists
                        app_name = os.path.splitext(os.path.basename(self.package_name))[0]
                        local_desktop = os.path.expanduser(f"~/.local/share/applications/{app_name}.desktop")
                        if os.path.exists(local_desktop):
                            os.remove(local_desktop)
                        
                        self.callback(True, f"Removed {os.path.basename(self.package_name)} (manual cleanup)")
                    except Exception as e:
                        self.callback(False, f"Gear Lever failed and manual removal failed: {str(e)}\nGear Lever error: {result.stderr}")
                else:
                    self.callback(False, f"Gear Lever removal failed: {result.stderr}\nFile not found: {self.package_name}")
    
        elif self.operation == "update":
            cmd = base_cmd + ["--update-all"]
            result = subprocess.run(cmd, capture_output=True, text=True)
            self.callback(result.returncode == 0, "Gear Lever: Update check completed.")

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
        filter_combo = ttk.Combobox(search_frame, textvariable=self.filter_var,
                                     values=["All Packages", "Installed Only", "Available Only"],
                                    state="readonly", width=15)
        filter_combo.pack(side=LEFT, padx=5)
        
        search_btn = ttk.Button(search_frame, text="Search", command=self.search_packages, bootstyle=PRIMARY)
        search_btn.pack(side=LEFT, padx=5)
        
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
        
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=X, padx=10, pady=(0, 10))
        
        ttk.Button(btn_frame, text="Install", command=lambda: self.package_action("install"),
                   bootstyle=SUCCESS).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Remove", command=lambda: self.package_action("remove"),
                   bootstyle=DANGER).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Update All", command=self.update_all,
                   bootstyle=WARNING).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Refresh List", command=self.load_installed_packages,
                   bootstyle=INFO).pack(side=LEFT, padx=5)
        
        self.output_text = Text(self, height=5, wrap=WORD)
        self.output_text.pack(fill=X, padx=10, pady=(0, 10))
        
        self.after(500, self.load_installed_packages)
    
    def log(self, message):
        self.output_text.insert(END, message + "\n")
        self.output_text.see(END)
    
    def search_packages(self):
        query = self.search_var.get().strip()
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
                
                # Get descriptions in a separate process
                package_names = []
                lines = res.stdout.split('\n')
                for line in lines:
                    line = line.strip()
                    if line and not line.startswith("Installed") and not line.startswith("Available") and not line.startswith("Last"):
                        parts = re.split(r'\s+', line)
                        if len(parts) >= 3:
                            pkg_full_name = parts[0]
                            if '.' in pkg_full_name:
                                pkg_name = pkg_full_name.rsplit('.', 1)[0]
                            else:
                                pkg_name = pkg_full_name
                            package_names.append(pkg_name)
                
                # Get descriptions for all found packages in one call
                desc_map = {}
                if package_names:
                    # Limit to first 50 packages to avoid command line too long
                    packages_to_query = package_names[:50]
                    cmd_desc = ["dnf", "info"] + packages_to_query
                    desc_res = subprocess.run(cmd_desc, capture_output=True, text=True, timeout=30)
                    desc_map = self.parse_dnf_info_output(desc_res.stdout)
                
                self.after(0, lambda: self.parse_dnf_list_output(res.stdout, desc_map))
            except subprocess.TimeoutExpired:
                self.after(0, lambda: self.log("Timeout: DNF command took too long"))
            except Exception as e:
                self.after(0, lambda: self.log(f"Error: {str(e)}"))
            finally:
                self.after(0, self.main_app.stop_progress)
        
        threading.Thread(target=run_search, daemon=True).start()
    
    def parse_dnf_info_output(self, info_output):
        """Parse DNF info output to extract package descriptions"""
        desc_map = {}
        current_pkg = ""
        current_desc = ""
        in_description = False
        
        lines = info_output.split('\n')
        for line in lines:
            line = line.strip()
            
            # Start of a new package section
            if line.startswith("Name"):
                # Save previous package description
                if current_pkg and current_desc:
                    desc_map[current_pkg] = current_desc
                
                # Start new package
                parts = line.split(":", 1)
                if len(parts) > 1:
                    current_pkg = parts[1].strip()
                    current_desc = ""
                    in_description = False
            
            # Get description
            elif line.startswith("Summary"):
                parts = line.split(":", 1)
                if len(parts) > 1 and current_pkg:
                    current_desc = parts[1].strip()
            
            # Handle multi-line description (if exists)
            elif in_description and line and not line.startswith(":"):
                current_desc += " " + line
            elif line.startswith("Description"):
                in_description = True
            elif in_description and (line.startswith(":") or not line):
                in_description = False
        
        # Save last package description
        if current_pkg and current_desc:
            desc_map[current_pkg] = current_desc
        
        return desc_map
    
    def parse_dnf_list_output(self, list_output, desc_map):
        """Parse DNF list output and combine with descriptions"""
        for item in self.tree.get_children(): 
            self.tree.delete(item)
        
        lines = list_output.split('\n')
        for line in lines:
            line = line.strip()
            
            # Skip headers and empty lines
            if not line or line.startswith("Installed") or line.startswith("Available") or line.startswith("Last"):
                continue
            
            parts = re.split(r'\s+', line)
            if len(parts) >= 3:
                pkg_full_name = parts[0]
                
                # Extract package name without arch
                if '.' in pkg_full_name:
                    pkg_name = pkg_full_name.rsplit('.', 1)[0]
                else:
                    pkg_name = pkg_full_name
                
                # Get description
                description = desc_map.get(pkg_name, desc_map.get(pkg_full_name, "No description available"))
                
                # Determine status
                version = parts[1]
                repo = parts[2]
                status = "installed" if repo.startswith('@') else "available"
                
                self.tree.insert("", END, values=(pkg_full_name, description, version, repo, status))
    
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
    
    def show_package_details(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        
        item = self.tree.item(sel[0])
        values = item['values']
        
        details = f"""Package Details:\n────────────────\nName: {values[0]}\nDescription: {values[1]}\nVersion: {values[2]}\nRepository: {values[3]}\nStatus: {values[4]}\n\nFull Information:\n────────────────"""
        
        try:
            cmd = ["dnf", "info", values[0]]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                details += f"\n{result.stdout}"
        except Exception as e:
            details += f"\nCould not fetch additional info: {str(e)}"
        
        self.output_text.delete(1.0, END)
        self.output_text.insert(END, details)
        self.output_text.see(END)

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
        ttk.Entry(search_frame, textvariable=self.search_var, width=40).pack(side=LEFT, padx=(0, 5))
        
        self.filter_var = StringVar(value="Installed Apps")
        ttk.Combobox(search_frame, textvariable=self.filter_var, values=["Installed Apps", "Search Flathub"], state="readonly").pack(side=LEFT, padx=5)
        
        ttk.Button(search_frame, text="Search", command=self.search_flatpaks).pack(side=LEFT, padx=5)
        
        list_frame = ttk.Frame(self)
        list_frame.pack(fill=BOTH, expand=True, padx=10, pady=(0, 10))
        
        columns = ("Name", "Description", "ID", "Version", "Branch")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings")
        
        for col in columns:
            self.tree.heading(col, text=col)
            if col == "Description":
                self.tree.column(col, width=250)
            elif col == "Name":
                self.tree.column(col, width=150)
            elif col == "ID":
                self.tree.column(col, width=150)
            else:
                self.tree.column(col, width=100)
        
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        self.tree.bind('<Double-Button-1>', self.show_flatpak_details)
        
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=X, padx=10, pady=(0, 10))
        ttk.Button(btn_frame, text="Install", command=lambda: self.flat_action("install"), bootstyle=SUCCESS).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Remove", command=lambda: self.flat_action("remove"), bootstyle=DANGER).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Update All", command=self.update_all, bootstyle=WARNING).pack(side=LEFT, padx=5)
 
        self.output_text = Text(self, height=5, wrap=WORD)
        self.output_text.pack(fill=X, padx=10, pady=(0, 10))
        self.after(500, self.search_flatpaks)
    
    def log(self, message):
        self.output_text.insert(END, message + "\n")
        self.output_text.see(END)
    
    def search_flatpaks(self):
        query = self.search_var.get().strip()
        self.main_app.start_progress()
        
        def run_search():
            try:
                if self.filter_var.get() == "Installed Apps":
                    # Added description to columns for installed list
                    cmd = ["flatpak", "list", "--app", "--columns=application,name,version,branch,description"]
                    res = subprocess.run(cmd, capture_output=True, text=True)
                    self.after(0, lambda: self.parse_flat_output(res.stdout, is_search=False))
                else:
                    if query:
                        cmd = ["flatpak", "search", query, "--columns=application,name,version,branch,description"]
                        res = subprocess.run(cmd, capture_output=True, text=True)
                        self.after(0, lambda: self.parse_flat_output(res.stdout, is_search=True))
                    else:
                        cmd = ["flatpak", "list", "--app", "--columns=application,name,version,branch,description"]
                        res = subprocess.run(cmd, capture_output=True, text=True)
                        self.after(0, lambda: self.parse_flat_output(res.stdout, is_search=False))
            finally:
                self.after(0, self.main_app.stop_progress)
        
        threading.Thread(target=run_search, daemon=True).start()
    
    def parse_flat_output(self, output, is_search):
        for item in self.tree.get_children(): 
            self.tree.delete(item)
        
        lines = output.strip().split('\n')
        if not lines:
            return
 
        # flatpak search/list output uses tabs as delimiters
        for line in lines:
            if "Application ID" in line or "Name" in line: # Skip header if present
                continue
            parts = line.split('\t')
            if len(parts) >= 5:
                # Columns: Application ID (0), Name (1), Version (2), Branch (3), Description (4)
                self.tree.insert("", END, values=(parts[1], parts[4], parts[0], parts[2], parts[3]))
            elif len(parts) == 4:
                # Fallback if description is missing
                self.tree.insert("", END, values=(parts[1], "No description available", parts[0], parts[2], parts[3]))
    
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
    
    def show_flatpak_details(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        
        item = self.tree.item(sel[0])
        values = item['values']
        
        details = f"""Flatpak Details:\n────────────────\nName: {values[0]}\nDescription: {values[1]}\nApplication ID: {values[2]}\nVersion: {values[3]}\nBranch: {values[4]}\n\nFull Information:\n────────────────"""
        
        try:
            cmd = ["flatpak", "info", values[2]]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                details += f"\n{result.stdout}"
        except Exception as e:
            details += f"\nCould not fetch additional info: {str(e)}"
        
        self.output_text.delete(1.0, END)
        self.output_text.insert(END, details)
        self.output_text.see(END)

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
    
    def _check_gear_lever(self):
        try:
            res = subprocess.run(["flatpak", "info", "it.mijorus.gearlever"], capture_output=True)
            return res.returncode == 0
        except:
            return False
    
    def init_ui(self):
        status_text = "Gear Lever Mode: Active" if self.gear_lever_active else "Manual Mode"
        
        search_frame = ttk.Frame(self)
        search_frame.pack(fill=X, padx=10, pady=10)
        
        self.search_var = StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=40)
        search_entry.pack(side=LEFT, padx=(0, 5))
        search_entry.bind('<Return>', lambda e: self.load_apps())
        
        ttk.Button(search_frame, text="Search Local", command=self.load_apps, bootstyle=PRIMARY).pack(side=LEFT, padx=5)
        ttk.Label(search_frame, text=f"({status_text})", bootstyle=INFO).pack(side=RIGHT, padx=10)
        
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
        
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=X, padx=10, pady=(0, 10))
        
        add_text = "Add (Integrate)" if self.gear_lever_active else "Add Manual"
        ttk.Button(btn_frame, text=add_text, command=self.add_app, bootstyle=SUCCESS).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Run", command=self.run_app, bootstyle=PRIMARY).pack(side=LEFT, padx=5)
        ttk.Button(btn_frame, text="Remove", command=self.remove_app, bootstyle=DANGER).pack(side=LEFT, padx=5)
        
        if self.gear_lever_active:
            ttk.Button(btn_frame, text="Check Updates", command=self.check_updates, bootstyle=WARNING).pack(side=LEFT, padx=5)
        
        ttk.Button(btn_frame, text="Refresh", command=self.load_apps).pack(side=RIGHT, padx=5)
        
        self.output_text = Text(self, height=5, wrap=WORD)
        self.output_text.pack(fill=X, padx=10, pady=(0, 10))
        
        self.after(500, self.load_apps)
    
    def load_apps(self):
        query = self.search_var.get().lower().strip()
        for item in self.tree.get_children(): 
            self.tree.delete(item)
        
        if not os.path.exists(self.appimage_dir): 
            return
        
        for f in os.listdir(self.appimage_dir):
            if f.lower().endswith('.appimage'):
                fpath = os.path.join(self.appimage_dir, f)
                
                # Get actual app name, description, and metadata
                app_name, description = self.get_appimage_metadata(f, fpath)
                
                if query and query not in app_name.lower() and query not in f.lower() and query not in description.lower():
                    continue
 
                size = os.path.getsize(fpath)
                mtime = os.path.getmtime(fpath)
                from datetime import datetime
                mtime_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')
                
                self.tree.insert("", END, values=(app_name, f, description, f"{size//1048576} MB", mtime_str))
    
    def get_appimage_metadata(self, filename, filepath):
        """Extract actual app name and description from AppImage"""
        app_name = ""
        description = ""
        
        # Try to extract from embedded desktop file
        try:
            # Use AppImage runtime to extract desktop file info
            cmd = [filepath, "--appimage-extract", ".DirIcon"]
            subprocess.run(cmd, capture_output=True, timeout=2)
        except:
            pass
        
        # Try to read .desktop file created by Gear Lever or similar tools
        desktop_file = filepath + '.desktop'
        if os.path.exists(desktop_file):
            try:
                with open(desktop_file, 'r') as f:
                    lines = f.readlines()
                    for line in lines:
                        line = line.strip()
                        if line.startswith('Name=') and not app_name:
                            app_name = line.split('=', 1)[1].strip()
                        elif line.startswith('Comment=') and not description:
                            description = line.split('=', 1)[1].strip()
            except:
                pass
        
        # Fallback: Use filename without extension and common patterns
        if not app_name:
            name = os.path.splitext(filename)[0]
            # Clean up common patterns
            app_name = name.replace('.AppImage', '').replace('.appimage', '')
            app_name = app_name.replace('_', ' ').replace('-', ' ')
            app_name = re.sub(r'[0-9]+(\.[0-9]+)*', '', app_name)  # Remove version numbers
            app_name = app_name.strip()
            
            # Capitalize words
            words = app_name.split()
            app_name = ' '.join([word.capitalize() for word in words])
        
        # Fallback description
        if not description:
            description = f"AppImage: {app_name}"
        
        return app_name, description
    
    def add_app(self):
        f = filedialog.askopenfilename(filetypes=[("AppImage", "*.AppImage *.appimage")])
        if f:
            self.main_app.start_progress()
            ptype = "gearlever" if self.gear_lever_active else "appimage"
            PackageWorker("integrate" if self.gear_lever_active else "install", ptype, f, self.on_finished).start()
    
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
        filename = self.tree.item(sel[0])['values'][1]
        full_path = os.path.join(self.appimage_dir, filename)
        
        if messagebox.askyesno("Confirm", f"Remove {filename}?"):
            self.main_app.start_progress()
            ptype = "gearlever" if self.gear_lever_active else "appimage"
            # Pass the full path to PackageWorker
            PackageWorker("remove", ptype, full_path, self.on_finished).start()
    
    def on_finished(self, success, msg):
        self.main_app.stop_progress()
        messagebox.showinfo("FiNDy", msg)
        self.load_apps()
    
    def show_appimage_details(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        
        item = self.tree.item(sel[0])
        values = item['values']
        app_name = values[0]
        filename = values[1]
        filepath = os.path.join(self.appimage_dir, filename)
        
        details = f"""AppImage Details:\n────────────────\nApp Name: {app_name}\nFilename: {filename}\nDescription: {values[2]}\nSize: {values[3]}\nLast Modified: {values[4]}\nPath: {filepath}\n\nFile Information:\n────────────────"""
        
        try:
            import stat
            import datetime
            st = os.stat(filepath)
            details += f"\nPermissions: {oct(stat.S_IMODE(st.st_mode))}"
            details += f"\nCreated: {datetime.datetime.fromtimestamp(st.st_ctime).strftime('%Y-%m-%d %H:%M:%S')}"
            details += f"\nExecutable: {'Yes' if os.access(filepath, os.X_OK) else 'No'}"
            
            # Try to get more metadata from AppImage
            try:
                # Check if AppImage has --appimage-version flag
                result = subprocess.run([filepath, "--appimage-version"], capture_output=True, text=True, timeout=2)
                if result.returncode == 0:
                    details += f"\nAppImage Version: {result.stdout.strip()}"
            except:
                pass
        except Exception as e:
            details += f"\nCould not fetch file info: {str(e)}"
        
        self.output_text.delete(1.0, END)
        self.output_text.insert(END, details)
        self.output_text.see(END)

class FiNDyApp(ttk.Window):
    def __init__(self):
        super().__init__(themename="darkly")
        self.title("FiNDy Package Manager")
        self.geometry("1000x750")
        
        self.progress = ttk.Progressbar(self, mode='indeterminate', bootstyle="success")
        self.progress.pack(fill=X, side=TOP)
        
        header = ttk.Frame(self)
        header.pack(fill=X, padx=20, pady=10)
        ttk.Label(header, text="FiNDy Package Manager", font=("", 18, "bold")).pack(anchor=W)
        
        self.notebook = ttk.Notebook(self, bootstyle="primary")
        self.notebook.pack(fill=BOTH, expand=True, padx=10, pady=10)
        
        self.notebook.add(DNFTab(self.notebook, self), text=" DNF Packages ")
        self.notebook.add(FlatpakTab(self.notebook, self), text=" Flatpak Apps ")
        self.notebook.add(AppImageTab(self.notebook, self), text=" AppImages ")
        
        self.status = ttk.Label(self, text="Ready", relief=SUNKEN, anchor=W)
        self.status.pack(fill=X, side=BOTTOM, padx=5, pady=2)
    
    def start_progress(self):
        self.progress.start(10)
        self.status.config(text="Processing...")
    
    def stop_progress(self):
        self.progress.stop()
        self.status.config(text="Ready")

if __name__ == "__main__":
    FiNDyApp().mainloop()