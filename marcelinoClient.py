import os
import queue
import json
import socket
import subprocess
import threading
import codecs
import base64
import hashlib
from datetime import datetime, timezone
import tkinter as tk
import customtkinter as ctk
from concurrent.futures import ThreadPoolExecutor
from tkinter import filedialog, messagebox, simpledialog, ttk
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import shutil
import tempfile
import pystray
import paramiko
import pyte
from types import SimpleNamespace
from pathlib import Path

import smbclient
from PIL import Image, ImageDraw

SMB_PORT = 445
STATUS_FILENAME = ".marcelinoSrv.json"
PUBLIC_SHARE_NAMES = {"public", "publico", "público"}
MUTEX_NAME = "Local\\MarcelinoClientSingleInstance"


def find_public_share(shares):
    return next((share for share in shares if share.casefold() in PUBLIC_SHARE_NAMES), "")


def acquire_single_instance():
    """Retorna um handle a manter vivo, ou None se já houver outra instância."""
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            return None
        return handle
    import fcntl

    lock_file = open(Path(tempfile.gettempdir()) / f"marcelinoClient.{os.getuid()}.lock", "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        return None
    return lock_file


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def scan_network(callback):
    """Varre a sub-rede /24 procurando hosts com a porta SMB aberta."""
    base = local_ip().rsplit(".", 1)[0]

    def probe(i):
        ip = f"{base}.{i}"
        try:
            with socket.create_connection((ip, SMB_PORT), timeout=0.5):
                pass
        except OSError:
            return
        try:
            name = socket.gethostbyaddr(ip)[0].split(".")[0]
        except OSError:
            name = ip
        callback(ip, name)

    with ThreadPoolExecutor(max_workers=64) as ex:
        list(ex.map(probe, range(1, 255)))


class Remote:
    def __init__(self, ip, name, user, password, domain=""):
        self.host = ip
        self.name = name
        smbclient.register_session(
            ip, username=(f"{domain}\\{user}" if domain else user), password=password, port=SMB_PORT,
        )

    def list_shares(self, user, password, domain=""):
        if os.name == "nt":
            return self._list_shares_windows(user, password, domain)
        if os.name == "posix":
            return self._list_shares_linux(user, password, domain)
        raise OSError(f"Enumeração SMB não suportada nesta plataforma: {os.name}")

    def _list_shares_linux(self, user, password, domain=""):
        import tempfile

        smbclient_path = shutil.which("smbclient")
        if not smbclient_path:
            raise OSError("Instale o cliente Samba (smbclient) para listar compartilhamentos no Linux")
        if any("\n" in value or "\r" in value for value in (user, password, domain)):
            raise ValueError("Usuário, senha e domínio não podem conter quebras de linha")

        auth_path = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as auth_file:
                auth_path = auth_file.name
                auth_file.write(f"username = {user}\npassword = {password}\n")
                if domain:
                    auth_file.write(f"domain = {domain}\n")
            os.chmod(auth_path, 0o600)
            result = subprocess.run(
                [smbclient_path, "-g", "-A", auth_path, "-L", f"//{self.host}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            if result.returncode:
                details = result.stderr.strip() or result.stdout.strip()
                raise OSError(details or f"smbclient terminou com código {result.returncode}")

            shares = []
            for line in result.stdout.splitlines():
                fields = line.split("|", 2)
                if len(fields) >= 2 and fields[0].casefold() == "disk" and fields[1]:
                    shares.append(fields[1])
            return sorted(set(shares), key=str.casefold)
        finally:
            if auth_path and os.path.exists(auth_path):
                os.unlink(auth_path)

    def _list_shares_windows(self, user, password, domain=""):
        import ctypes
        from ctypes import wintypes

        class NetResource(ctypes.Structure):
            _fields_ = [
                ("dwScope", wintypes.DWORD),
                ("dwType", wintypes.DWORD),
                ("dwDisplayType", wintypes.DWORD),
                ("dwUsage", wintypes.DWORD),
                ("lpLocalName", wintypes.LPWSTR),
                ("lpRemoteName", wintypes.LPWSTR),
                ("lpComment", wintypes.LPWSTR),
                ("lpProvider", wintypes.LPWSTR),
            ]

        class ShareInfo1(ctypes.Structure):
            _fields_ = [
                ("name", wintypes.LPWSTR),
                ("type", wintypes.DWORD),
                ("remark", wintypes.LPWSTR),
            ]

        mpr = ctypes.WinDLL("mpr", use_last_error=True)
        add_connection = mpr.WNetAddConnection2W
        add_connection.argtypes = [
            ctypes.POINTER(NetResource), wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ]
        add_connection.restype = wintypes.DWORD
        cancel_connection = mpr.WNetCancelConnection2W
        cancel_connection.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.BOOL]
        cancel_connection.restype = wintypes.DWORD

        resource = NetResource()
        resource.dwType = 1
        resource.lpRemoteName = f"\\\\{self.host}\\IPC$"
        username = f"{domain}\\{user}" if domain else user
        result = add_connection(ctypes.byref(resource), password, username, 0x4)
        if result:
            raise OSError(result, ctypes.FormatError(result).strip())

        netapi = ctypes.WinDLL("Netapi32", use_last_error=True)
        net_share_enum = netapi.NetShareEnum
        net_share_enum.argtypes = [
            wintypes.LPWSTR,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(ShareInfo1)),
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
        ]
        net_share_enum.restype = wintypes.DWORD
        free_buffer = netapi.NetApiBufferFree
        free_buffer.argtypes = [ctypes.c_void_p]
        free_buffer.restype = wintypes.DWORD

        try:
            shares = []
            resume = wintypes.DWORD()
            while True:
                buffer = ctypes.POINTER(ShareInfo1)()
                entries_read = wintypes.DWORD()
                total_entries = wintypes.DWORD()
                status = net_share_enum(
                    f"\\\\{self.host}", 1, ctypes.byref(buffer), 0xFFFFFFFF,
                    ctypes.byref(entries_read), ctypes.byref(total_entries), ctypes.byref(resume),
                )
                try:
                    if status not in (0, 234):
                        raise OSError(status, ctypes.FormatError(status).strip())
                    for index in range(entries_read.value):
                        entry = buffer[index]
                        if entry.type & 0x80000000 or entry.type & 0xFFFF != 0:
                            continue
                        if entry.name:
                            shares.append(entry.name)
                finally:
                    if buffer:
                        free_buffer(buffer)
                if status == 0:
                    break
            return sorted(shares, key=str.lower)
        finally:
            cancel_connection(resource.lpRemoteName, 0, False)

    def unc(self, share, path=""):
        return f"\\\\{self.host}\\{share}" + (path or "").replace("/", "\\")

    def list(self, share, path):
        return [
            SimpleNamespace(filename=e.name, isDirectory=e.is_dir())
            for e in smbclient.scandir(self.unc(share, path))
        ]

    def read_status(self, share, path):
        path = path.lstrip("/\\")
        with smbclient.open_file(self.unc(share, f"/{path}"), mode="r", encoding="utf-8") as status_file:
            return json.load(status_file)

    def ensure_dir(self, share, path):
        smbclient.makedirs(self.unc(share, path), exist_ok=True)

    def upload(self, share, remote_path, local_path):
        dst = self.unc(share, remote_path)
        with open(local_path, "rb") as src, smbclient.open_file(dst, mode="wb") as out:
            shutil.copyfileobj(src, out, 1024 * 1024)
        st = os.stat(local_path)
        smbclient.utime(dst, times=(st.st_atime, st.st_mtime))

    def up_to_date(self, share, remote_path, local_path):
        try:
            a = smbclient.stat(self.unc(share, remote_path))
        except Exception:
            return False
        st = os.stat(local_path)
        return a.st_size == st.st_size and a.st_mtime >= st.st_mtime - 2


class VariableLabel(ctk.CTkLabel):
    def __init__(self, master, variable, **kwargs):
        self.variable = variable
        super().__init__(master, text=variable.get(), **kwargs)
        self.trace_id = variable.trace_add("write", self._refresh_text)

    def _refresh_text(self, *_):
        if self.winfo_exists():
            self.configure(text=self.variable.get())

    def destroy(self):
        try:
            self.variable.trace_remove("write", self.trace_id)
        except tk.TclError:
            pass
        super().destroy()


class MetricRing(ctk.CTkFrame):
    def __init__(self, master, title, panel_color):
        super().__init__(
            master,
            width=176,
            height=194,
            corner_radius=8,
            bg_color="transparent",
            fg_color=panel_color,
        )
        self.grid_propagate(False)
        ctk.CTkLabel(self, text=title, font=ctk.CTkFont(size=14, weight="bold")).pack(
            pady=(8, 0),
        )
        self.canvas = ctk.CTkCanvas(
            self,
            width=124,
            height=124,
            bg=self._apply_appearance_mode(panel_color),
            highlightthickness=0,
            borderwidth=0,
        )
        self.canvas.pack(pady=2)
        self.track_id = self.canvas.create_arc(
            13, 13, 111, 111, start=90, extent=-359, style="arc", width=10,
        )
        self.progress_id = self.canvas.create_arc(
            13, 13, 111, 111, start=90, extent=0, style="arc", width=10,
        )
        self.value_id = self.canvas.create_text(
            62, 62, text="--", font=("Segoe UI", 20, "bold"),
        )
        self.detail_label = ctk.CTkLabel(self, text="Aguardando dados", font=ctk.CTkFont(size=11))
        self.detail_label.pack(fill="x", padx=6, pady=(0, 6))

    def set_theme_colors(self, colors):
        self.configure(bg_color="transparent", fg_color=colors["panel"])
        self.canvas.configure(bg=colors["panel"])
        self.canvas.itemconfigure(self.track_id, outline=colors["panel"])
        self.canvas.itemconfigure(self.progress_id, outline=colors["accent"])
        self.canvas.itemconfigure(self.value_id, fill=colors["text"])

    def set_value(self, percent, detail, colors):
        if percent is None:
            self.canvas.itemconfigure(self.progress_id, extent=0)
            self.canvas.itemconfigure(self.value_id, text="--")
            self.detail_label.configure(text=detail)
            self.set_theme_colors(colors)
            return
        percent = max(0.0, min(100.0, float(percent)))
        extent = -min(359.0, 3.59 * percent)
        self.canvas.itemconfigure(self.progress_id, extent=extent)
        self.canvas.itemconfigure(self.value_id, text=f"{percent:.0f}%")
        self.detail_label.configure(text=detail)
        self.set_theme_colors(colors)


class ConfirmHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, app, known_hosts):
        self.app = app
        self.known_hosts = known_hosts

    def missing_host_key(self, client, hostname, key):
        fingerprint = base64.b64encode(hashlib.sha256(key.asbytes()).digest())
        fingerprint = "SHA256:" + fingerprint.decode("ascii").rstrip("=")
        decision = {"event": threading.Event(), "accepted": False}
        self.app.ui(lambda: self.app._confirm_ssh_host_key(hostname, key.get_name(), fingerprint, decision))
        if not decision["event"].wait(300):
            raise paramiko.SSHException("Tempo esgotado aguardando confirmação da chave do servidor.")
        if not decision["accepted"]:
            raise paramiko.SSHException("Chave do servidor não foi aceita.")

        self.known_hosts.parent.mkdir(parents=True, exist_ok=True)
        client.get_host_keys().add(hostname, key.get_name(), key)
        client.save_host_keys(str(self.known_hosts))


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("marcelino Client")
        self.geometry("1000x650")
        self._tray_icon = None
        self._closing = False
        self.protocol("WM_DELETE_WINDOW", self.minimize_to_tray)
        self.bind("<Unmap>", self._on_minimize)
        self.settings_path = self._settings_path()
        self.saved_settings = self._read_settings()
        self.theme = "light"
        ctk.set_appearance_mode(self.theme)
        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self.q = queue.Queue()
        self.remote = None
        self.authenticated = False
        self.authenticated_user = None
        self.authenticated_password = None
        self.ssh_client = None
        self.ssh_connecting_client = None
        self.ssh_channel = None
        self.ssh_screen = None
        self.ssh_stream = None
        self.ssh_decoder = None
        self.ssh_style_tags = {}
        self.ssh_screen_dirty = False
        self.ssh_generation = 0
        self.ssh_connecting = False
        self.status_generation = 0
        self.status_polling_generation = None
        self.status_share = None
        self.status_after_id = None
        self.share = None
        self.path = ""
        self.dest = None  # (share, path)
        self.dest_server = None
        self.cancel = threading.Event()
        self.hosts = {}
        self.saved_server = None
        self.tabs = {}
        self.active_tab = None
        self.theme_colors = {
            "background": "#f2f5f7",
            "panel": "#e8eef2",
            "accent": "#167b88",
            "hover": "#d6e7ea",
            "text": "#1d2a32",
            "selected_text": "#ffffff",
        }
        self._build()
        self._apply_theme(self.saved_settings.get("theme", "light"))
        self._restore_backup_settings()
        self.after_idle(lambda: self.center_window(1000, 650))
        self._start_tray_icon()
        self.after(100, self._pump)

    def _start_tray_icon(self):
        image = Image.new("RGB", (64, 64), "#14343e")
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 13, 54, 51), fill="#ffffff")
        for y in (17, 28, 39):
            draw.rectangle((15, y, 49, y + 7), fill="#167b88")
            draw.ellipse((18, y + 2, 22, y + 6), fill="#5abe82")
        menu = pystray.Menu(
            pystray.MenuItem(
                "Alternar janela",
                lambda icon, item: self._schedule_ui(self.toggle_window),
                default=True,
                visible=False,
            ),
            pystray.MenuItem("Restaurar", lambda icon, item: self._schedule_ui(self.restore_window)),
            pystray.MenuItem("Desconectar", lambda icon, item: self._schedule_ui(self.disconnect)),
            pystray.MenuItem("Fechar", lambda icon, item: self._schedule_ui(self.close_application)),
        )
        self._tray_icon = pystray.Icon("marcelino_client", image, "marcelino Client", menu)
        threading.Thread(target=self._run_tray_icon, daemon=True).start()

    def _run_tray_icon(self):
        try:
            self._tray_icon.run()
        except Exception as error:
            if not self._closing:
                self.say(f"Não foi possível iniciar o ícone da bandeja: {error}")

    def _schedule_ui(self, callback):
        if self._closing:
            return
        try:
            self.after(0, callback)
        except tk.TclError:
            pass

    def _on_minimize(self, event):
        if event.widget is self and self.state() == "iconic":
            self.after_idle(self.minimize_to_tray)

    def minimize_to_tray(self):
        if not self._closing:
            self.withdraw()

    def restore_window(self):
        if not self._closing:
            self.deiconify()
            self.state("normal")
            self.center_window()
            self.lift()
            self.focus_force()

    def center_window(self, width=None, height=None):
        self.update_idletasks()
        width = width or self.winfo_width()
        height = height or self.winfo_height()
        if width <= 1 or height <= 1:
            width, height = 1000, 650
        x = max(0, (self.winfo_screenwidth() - width) // 2)
        y = max(0, (self.winfo_screenheight() - height - 80) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def toggle_window(self):
        if self.state() in ("iconic", "withdrawn"):
            self.restore_window()
        else:
            self.minimize_to_tray()

    def disconnect(self):
        remote = self.remote
        self._close_ssh()
        self.status_generation += 1
        self._cancel_server_status_timer()
        self.status_polling_generation = None
        self.status_share = None
        self.authenticated = False
        self.authenticated_user = None
        self.authenticated_password = None
        self.remote = None
        self.share, self.path = None, ""
        self.cancel.set()
        self.pwd.set("")
        self.auth_summary.set("Não autenticado")
        self.session_display.set("Não autenticado")
        self.login_status.set("Desconectado.")
        self._fill([])
        self._reset_server_dashboard("Aguardando autenticação.")
        self.show_login_form()
        if remote is not None:
            try:
                smbclient.delete_session(remote.host)
            except Exception as error:
                self.say(f"Não foi possível encerrar a sessão SMB: {error}")
        self.say("Desconectado do servidor.")

    def close_application(self):
        if self._closing:
            return
        self._closing = True
        self.cancel.set()
        self._cancel_server_status_timer()
        self._close_ssh(update_status=False)
        self.authenticated_password = None
        if self._tray_icon is not None:
            self._tray_icon.stop()
        self.destroy()

    @staticmethod
    def _settings_path():
        if os.name == "nt":
            config_root = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        else:
            config_root = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        return Path(config_root) / "marcelinoSrv" / "settings.json"

    def _read_settings(self):
        try:
            settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            legacy_path = self.settings_path.parent.parent / "BackupLAN" / "settings.json"
            try:
                settings = json.loads(legacy_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
            if isinstance(settings, dict):
                self._migrate_settings(settings)
        except (OSError, json.JSONDecodeError):
            return {}
        return settings if isinstance(settings, dict) else {}

    def _migrate_settings(self, settings):
        temp_path = None
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.settings_path.parent,
                prefix="settings.", suffix=".tmp", delete=False,
            ) as settings_file:
                temp_path = Path(settings_file.name)
                json.dump(settings, settings_file, ensure_ascii=False, indent=2)
                settings_file.flush()
                os.fsync(settings_file.fileno())
            os.replace(temp_path, self.settings_path)
        except OSError:
            pass
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    def _restore_backup_settings(self):
        username = self.saved_settings.get("username")
        if isinstance(username, str):
            self.user.set(username)

        ssh_key_path = self.saved_settings.get("ssh_key_path")
        if isinstance(ssh_key_path, str):
            self.ssh_key_path.set(ssh_key_path)

        server = self.saved_settings.get("server")
        if isinstance(server, dict):
            selection = server.get("selection")
            ip = server.get("ip")
            name = server.get("name")
            if all(isinstance(value, str) and value for value in (selection, ip, name)):
                self.saved_server = {"selection": selection, "ip": ip, "name": name}
                self.hosts[selection] = (ip, name)
                self.host_cb.configure(values=sorted(self.hosts))
                self.host_var.set(selection)

        sources = self.saved_settings.get("sources", [])
        if isinstance(sources, list):
            self.source_paths = list(dict.fromkeys(
                source for source in sources if isinstance(source, str)
            ))
            self._render_sources()

        destination = self.saved_settings.get("destination")
        if isinstance(destination, dict):
            share = destination.get("share")
            path = destination.get("path", "")
            server = destination.get("server")
            if all(isinstance(value, str) for value in (share, path, server)) and share and server:
                self.dest = (share, path)
                self.dest_server = server
                self._update_dest_label()

        self._apply_theme(self.saved_settings.get("theme", "light"))
    def _save_settings(self):
        destination = None
        if self.dest and self.dest_server:
            destination = {
                "server": self.dest_server,
                "share": self.dest[0],
                "path": self.dest[1],
            }
        settings = {
            "username": self.user.get().strip(),
            "server": self.saved_server,
            "sources": list(self.source_paths),
            "destination": destination,
            "theme": self.theme,
            "ssh_key_path": self.ssh_key_path.get().strip(),
        }
        temp_path = None
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.settings_path.parent,
                prefix="settings.", suffix=".tmp", delete=False,
            ) as settings_file:
                temp_path = Path(settings_file.name)
                json.dump(settings, settings_file, ensure_ascii=False, indent=2)
                settings_file.flush()
                os.fsync(settings_file.fileno())
            os.replace(temp_path, self.settings_path)
        except OSError as error:
            self.say(f"Não foi possível salvar as pastas do backup: {error}")
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    def _update_dest_label(self):
        if not self.dest:
            self.dest_lbl.configure(text="Destino: (nenhum)")
            return
        share, path = self.dest
        location = f"\\\\{self.dest_server}\\{share}" if self.dest_server else f"\\\\{share}"
        if path:
            location += "\\" + path.replace("/", "\\")
        self.dest_lbl.configure(text=f"Destino: {location}")

    def _build(self):
        self.login_page = ctk.CTkFrame(self, fg_color="transparent")
        self._build_login_page(self.login_page)

        self.main_page = ctk.CTkFrame(self, fg_color="transparent")
        session_bar = ctk.CTkFrame(self.main_page, fg_color="transparent")
        session_bar.pack(fill="x", padx=8, pady=(8, 0))
        ctk.CTkLabel(session_bar, text="Sessão").pack(side="left", padx=(4, 8))
        self.session_display = tk.StringVar(value="Não autenticado")
        ctk.CTkEntry(
            session_bar, textvariable=self.session_display, state="disabled", width=300,
        ).pack(side="left", fill="x", expand=True)
        self.theme_button = self._accent_button(
            session_bar, width=120, height=34, corner_radius=6, command=self._toggle_theme,
        )
        self.theme_button.pack(side="right", padx=(4, 0))
        self.disconnect_button = self._accent_button(
            session_bar, text="Desconectar", command=self.disconnect,
            width=120, height=34, corner_radius=6,
        )
        self.disconnect_button.pack(side="right", padx=(4, 0))

        self.tab_bar = ctk.CTkFrame(self.main_page, fg_color="transparent")
        self.tab_bar.pack(fill="x", padx=8, pady=(8, 0))
        ctk.CTkFrame(self.main_page, height=1, fg_color=("#c5d0d7", "#46545e")).pack(
            fill="x", padx=8, pady=(4, 6),
        )
        self.tab_container = ctk.CTkFrame(self.main_page, fg_color="transparent")
        self.tab_container.pack(fill="both", expand=True)
        self.add_tab("Sistema", "▦", self._build_system_page)
        self.add_tab("Discos", "◉", self._build_disks_page)
        self.add_tab("Compartilhamentos", "▤", self._build_shares_page)
        self.add_tab("Backup", "⤓", self._build_backup_page)
        self.add_tab("Terminal", ">_", self._build_terminal_page)
        self.login_page.pack(fill="both", expand=True)

    def _apply_theme(self, theme):
        if theme not in ("light", "dark"):
            theme = "light"
        self.theme = theme
        ctk.set_appearance_mode(theme)
        if theme == "dark":
            colors = {
                "background": "#171d22",
                "surface": "#222b32",
                "panel": "#2b363e",
                "input": "#12171b",
                "text": "#edf2f5",
                "muted": "#a8b6bf",
                "border": "#46545e",
                "accent": "#3d9b8b",
                "hover": "#356f67",
                "selected_text": "#ffffff",
            }
            self.theme_button.configure(text="☀ Claro")
        else:
            colors = {
                "background": "#f2f5f7",
                "surface": "#ffffff",
                "panel": "#e8eef2",
                "input": "#ffffff",
                "text": "#1d2a32",
                "muted": "#53636d",
                "border": "#c5d0d7",
                "accent": "#167b88",
                "hover": "#d6e7ea",
                "selected_text": "#ffffff",
            }
            self.theme_button.configure(text="☾ Escuro")

        self.theme_colors = colors
        self.configure(fg_color=colors["background"])
        for ring in getattr(self, "metric_rings", {}).values():
            ring.set_theme_colors(colors)
        for disk_bar in getattr(self, "disk_usage_bars", []):
            disk_bar.configure(fg_color=colors["panel"], progress_color=colors["accent"])
        for filesystem_bar in getattr(self, "filesystem_progress_bars", []):
            filesystem_bar.configure(fg_color=colors["panel"], progress_color=colors["accent"])
        self.style.configure(".", background=colors["background"], foreground=colors["text"])
        self.style.configure("TFrame", background=colors["background"])
        self.style.configure("TLabel", background=colors["background"], foreground=colors["text"])
        self.style.configure(
            "TLabelframe", background=colors["background"], foreground=colors["text"],
            bordercolor=colors["border"],
        )
        self.style.configure(
            "TLabelframe.Label", background=colors["background"], foreground=colors["text"],
        )
        self.style.configure(
            "TButton", background=colors["panel"], foreground=colors["text"],
            bordercolor=colors["border"], focuscolor=colors["accent"], padding=(8, 4),
        )
        self.style.map(
            "TButton",
            background=[("pressed", colors["accent"]), ("active", colors["hover"])],
            foreground=[("pressed", colors["selected_text"])],
        )
        self.style.configure(
            "TEntry", fieldbackground=colors["input"], foreground=colors["text"],
            insertcolor=colors["text"], bordercolor=colors["border"],
        )
        self.style.configure(
            "TCombobox", fieldbackground=colors["input"], foreground=colors["text"],
            background=colors["panel"], arrowcolor=colors["text"], bordercolor=colors["border"],
        )
        self.style.map(
            "TCombobox",
            fieldbackground=[("readonly", colors["input"])],
            foreground=[("readonly", colors["text"])],
            selectbackground=[("readonly", colors["accent"])],
            selectforeground=[("readonly", colors["selected_text"])],
        )
        self.style.configure(
            "TSpinbox", fieldbackground=colors["input"], foreground=colors["text"],
            background=colors["panel"], arrowcolor=colors["text"], bordercolor=colors["border"],
        )
        self.style.configure(
            "Treeview", background=colors["input"], fieldbackground=colors["input"],
            foreground=colors["text"], bordercolor=colors["border"], rowheight=24,
        )
        self.style.map(
            "Treeview",
            background=[("selected", colors["accent"])],
            foreground=[("selected", colors["selected_text"])],
        )
        self.style.configure(
            "Treeview.Heading", background=colors["panel"], foreground=colors["text"],
            bordercolor=colors["border"], relief="flat",
        )
        self.style.configure("TProgressbar", background=colors["accent"], troughcolor=colors["panel"])
        self.style.configure("TSeparator", background=colors["border"])

        for title, tab in self.tabs.items():
            selected = title == self.active_tab
            tab["button"].configure(
                fg_color=colors["accent"] if selected else colors["panel"],
                hover_color=colors["hover"],
                text_color=colors["selected_text"] if selected else colors["text"],
            )
    def _toggle_theme(self):
        self._apply_theme("dark" if self.theme == "light" else "light")
        self._save_settings()

    def add_tab(self, title, icon, build_page):
        """Adiciona uma aba; build_page recebe o frame onde deve montar a tela."""
        page = ctk.CTkFrame(self.tab_container, fg_color="transparent")
        button = ctk.CTkButton(
            self.tab_bar,
            text=f"{icon}  {title}",
            width=120,
            height=34,
            corner_radius=6,
            command=lambda: self.select_tab(title),
        )
        button.pack(side="left", padx=(0, 4))
        self.tabs[title] = {"page": page, "button": button}
        build_page(page)
        if self.active_tab is None:
            self.select_tab(title)
        return page

    def select_tab(self, title):
        if title not in self.tabs:
            raise KeyError(f"Aba não encontrada: {title}")
        if self.active_tab is not None:
            self.tabs[self.active_tab]["page"].pack_forget()
        self.active_tab = title
        for tab_title, tab in self.tabs.items():
            selected = tab_title == title
            tab["button"].configure(
                fg_color=self.theme_colors["accent"] if selected else self.theme_colors["panel"],
                hover_color=self.theme_colors["hover"],
                text_color=(
                    self.theme_colors["selected_text"] if selected else self.theme_colors["text"]
                ),
            )
        self.tabs[title]["page"].pack(fill="both", expand=True)

    def _section(self, parent, title, **kwargs):
        kwargs.setdefault("fg_color", ("#e8eef2", "#2b363e"))
        section = ctk.CTkFrame(parent, corner_radius=8, **kwargs)
        ctk.CTkLabel(
            section, text=title, font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=12, pady=(8, 2))
        return section

    @staticmethod
    def _accent_button(master, **kwargs):
        kwargs.setdefault("fg_color", ("#167b88", "#3d9b8b"))
        kwargs.setdefault("hover_color", ("#0f6872", "#356f67"))
        kwargs.setdefault("text_color", "#ffffff")
        kwargs.setdefault("border_color", ("#167b88", "#3d9b8b"))
        return ctk.CTkButton(master, **kwargs)

    def _build_backup_page(self, page):
        top = ctk.CTkFrame(page, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=4)
        self.auth_summary = tk.StringVar(value="Não autenticado")
        ctk.CTkLabel(top, text="Sessão:").pack(side="left", padx=(4, 0))
        VariableLabel(top, self.auth_summary).pack(side="left", padx=8)

        mid = ctk.CTkFrame(page, fg_color="transparent")
        mid.pack(fill="both", expand=True, padx=8)

        remote_panel = self._section(mid, "Arquivos compartilhados (destino)")
        remote_panel.pack(side="left", fill="both", expand=True, padx=(0, 4), pady=4)
        self.path_lbl = ctk.CTkLabel(remote_panel, text="-", anchor="w")
        self.path_lbl.pack(fill="x", padx=10, pady=(2, 4))
        self.rlist = ctk.CTkScrollableFrame(remote_panel, fg_color="transparent")
        self.remote_buttons = []
        self.rlist.pack(fill="both", expand=True, padx=6, pady=4)
        self._accent_button(
            remote_panel, text="Usar esta pasta como destino", command=self.set_dest,
        ).pack(pady=4)
        self.dest_lbl = ctk.CTkLabel(remote_panel, text="Destino: (nenhum)", anchor="w")
        self.dest_lbl.pack(fill="x", padx=10, pady=(2, 8))

        source_panel = self._section(mid, "Pastas locais (origem)")
        source_panel.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)
        self.source_paths = []
        self.source_row_widgets = []
        self.src = ctk.CTkScrollableFrame(source_panel, fg_color="transparent")
        self.src.pack(fill="both", expand=True, padx=6, pady=4)
        self._accent_button(source_panel, text="Adicionar pasta", command=self.add_src).pack(pady=8)

        act = ctk.CTkFrame(page, fg_color="transparent")
        act.pack(fill="x", padx=8, pady=4)
        self._accent_button(act, text="Iniciar backup", command=self.start_backup).pack(side="left")
        self._accent_button(act, text="Cancelar", command=self.cancel.set).pack(side="left", padx=4)
        self.bar = ctk.CTkProgressBar(act)
        self.bar.set(0)
        self.bar.pack(side="left", fill="x", expand=True, padx=8)
        self.bar_lbl = ctk.CTkLabel(act, text="0%", width=130)
        self.bar_lbl.pack(side="left")
        self.log = ctk.CTkTextbox(page, height=100, state="disabled", corner_radius=8)
        self.log.pack(fill="x", padx=8, pady=(0, 8))

    def _build_terminal_page(self, page):
        controls = ctk.CTkFrame(page, fg_color="transparent")
        controls.pack(fill="x", padx=8, pady=8)
        self._accent_button(
            controls, text="Conectar SSH", command=self._connect_ssh,
        ).pack(side="left", padx=(0, 4))
        self._accent_button(
            controls, text="Desconectar", command=self._disconnect_ssh,
        ).pack(side="left", padx=4)
        self.ssh_status = tk.StringVar(value="Faça login para conectar ao servidor via SSH.")
        VariableLabel(controls, self.ssh_status).pack(side="left", padx=12)

        key_controls = ctk.CTkFrame(page, fg_color="transparent")
        key_controls.pack(fill="x", padx=8, pady=(0, 8))
        ctk.CTkLabel(key_controls, text="Arquivo de chave").pack(side="left", padx=(0, 6))
        self.ssh_key_path = tk.StringVar()
        key_path_entry = ctk.CTkEntry(key_controls, textvariable=self.ssh_key_path)
        key_path_entry.pack(
            side="left", fill="x", expand=True,
        )
        key_path_entry.bind("<FocusOut>", lambda _event: self._save_settings())
        key_path_entry.bind("<Return>", lambda _event: self._save_settings())
        self._accent_button(
            key_controls, text="Selecionar...", command=self._select_ssh_key,
        ).pack(side="left", padx=6)
        ctk.CTkLabel(key_controls, text="Passphrase").pack(side="left", padx=(4, 6))
        self.ssh_key_passphrase_entry = ctk.CTkEntry(
            key_controls, width=160, show="*", placeholder_text="Opcional",
        )
        self.ssh_key_passphrase_entry.pack(side="left")

        self.terminal_output = ctk.CTkTextbox(
            page, corner_radius=8, font=ctk.CTkFont(family="Consolas", size=13),
            wrap="none", fg_color="#101214", text_color="#d6d6d6",
        )
        self.terminal_output.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        terminal_widget = self.terminal_output._textbox
        terminal_widget.configure(insertbackground="#ffffff", takefocus=True)
        terminal_widget.bind("<KeyPress>", self._send_ssh_key)

    def _select_ssh_key(self):
        key_path = filedialog.askopenfilename(
            parent=self,
            title="Selecionar chave privada SSH",
            filetypes=(("Todos os arquivos", "*.*"),),
        )
        if key_path:
            self.ssh_key_path.set(key_path)
            self._save_settings()

    def _confirm_ssh_host_key(self, hostname, key_type, fingerprint, decision):
        answer = simpledialog.askstring(
            "Confiar no servidor SSH",
            f"O servidor {hostname} ainda não está em known_hosts.\n"
            f"Chave: {key_type}\nFingerprint: {fingerprint}\n\n"
            "Digite yes para confiar e salvar esta chave:",
            parent=self,
        )
        decision["accepted"] = bool(answer and answer.strip().casefold() == "yes")
        decision["event"].set()

    def _terminal_append(self, text):
        if self.ssh_stream is not None:
            self.ssh_stream.feed(text)
            self.ssh_screen_dirty = True
            return
        terminal_widget = self.terminal_output._textbox
        terminal_widget.insert("end", text)
        terminal_widget.see("end")

    def _connect_ssh(self):
        if not self.authenticated or not self.remote or not self.authenticated_password:
            self.ssh_status.set("Faça login na aba Login antes de conectar via SSH.")
            return
        if self.ssh_client or self.ssh_connecting:
            return

        self.ssh_generation += 1
        generation = self.ssh_generation
        host = self.remote.host
        username = self.authenticated_user
        password = self.authenticated_password
        key_path = self.ssh_key_path.get().strip()
        key_passphrase = self.ssh_key_passphrase_entry.get() or None
        self.ssh_key_passphrase_entry.delete(0, "end")
        self.ssh_connecting = True
        self.ssh_status.set(f"Conectando a {username}@{host}:22...")
        client = paramiko.SSHClient()
        self.ssh_connecting_client = client

        def run():
            try:
                client.load_system_host_keys()
                known_hosts = Path.home() / ".ssh" / "known_hosts"
                if known_hosts.exists():
                    client.load_host_keys(str(known_hosts))
                client.set_missing_host_key_policy(ConfirmHostKeyPolicy(self, known_hosts))
                client.connect(
                    host, port=22, username=username, password=password,
                    key_filename=key_path or None, passphrase=key_passphrase,
                    timeout=8, banner_timeout=8, auth_timeout=8,
                    allow_agent=False, look_for_keys=False,
                )
                channel = client.invoke_shell(term="xterm", width=120, height=24)
                self.ui(lambda: self._ssh_connection_result(generation, client, channel, None))
            except Exception as error:
                client.close()
                error_text = str(error)
                self.ui(lambda: self._ssh_connection_result(generation, None, None, error_text))

        threading.Thread(target=run, daemon=True).start()

    def _ssh_connection_result(self, generation, client, channel, error):
        if generation != self.ssh_generation:
            if client:
                client.close()
            return
        self.ssh_connecting_client = None
        self.ssh_connecting = False
        if error:
            self.ssh_status.set(f"Falha na conexão SSH: {error}")
            self._terminal_append(f"Falha na conexão SSH: {error}\n")
            return
        self.ssh_client = client
        self.ssh_channel = channel
        self.ssh_screen = pyte.Screen(120, 24)
        self.ssh_stream = pyte.Stream(self.ssh_screen)
        self.ssh_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.ssh_style_tags = {}
        channel.settimeout(0.05)
        self.ssh_status.set(f"Conectado: {self.authenticated_user}@{self.remote.host}")
        self.ssh_screen_dirty = True
        self.terminal_output._textbox.focus_set()
        threading.Thread(
            target=self._read_ssh_output, args=(generation, channel), daemon=True,
        ).start()

    def _send_ssh_key(self, event):
        channel = self.ssh_channel
        if not channel or channel.closed:
            return "break"
        application_cursor_keys = (
            self.ssh_screen is not None and (1 << 5) in self.ssh_screen.mode
        )
        data = self._ssh_key_data(
            event.keysym, event.char, event.state, application_cursor_keys,
        )
        if data:
            try:
                channel.send(data)
            except Exception as error:
                self._terminal_append(f"\nErro SSH: {error}\n")
        return "break"

    @staticmethod
    def _ssh_key_data(keysym, character, state, application_cursor_keys=False):
        key_sequences = {
            "Return": "\r", "KP_Enter": "\r", "BackSpace": "\x7f",
            "Tab": "\t", "Escape": "\x1b", "Up": "\x1b[A",
            "Down": "\x1b[B", "Right": "\x1b[C", "Left": "\x1b[D",
            "Home": "\x1b[H", "End": "\x1b[F", "Insert": "\x1b[2~",
            "Delete": "\x1b[3~", "Prior": "\x1b[5~", "Next": "\x1b[6~",
            "F1": "\x1bOP", "F2": "\x1bOQ", "F3": "\x1bOR", "F4": "\x1bOS",
            "F5": "\x1b[15~", "F6": "\x1b[17~", "F7": "\x1b[18~",
            "F8": "\x1b[19~", "F9": "\x1b[20~", "F10": "\x1b[21~",
            "F11": "\x1b[23~", "F12": "\x1b[24~",
        }
        control_pressed = bool(state & 0x4)
        if control_pressed and keysym.lower() in "abcdefghijklmnopqrstuvwxyz":
            return bytes((ord(keysym.lower()) - ord("a") + 1,))
        if application_cursor_keys and keysym in ("Up", "Down", "Right", "Left"):
            return ("\x1bO" + {"Up": "A", "Down": "B", "Right": "C", "Left": "D"}[keysym]).encode("ascii")
        if keysym in key_sequences:
            return key_sequences[keysym].encode("ascii")
        if character and character >= " ":
            return character.encode("utf-8")
        return b""

    def _read_ssh_output(self, generation, channel):
        pending = bytearray()

        def flush_pending():
            if pending:
                self.q.put(("ssh_data", (generation, bytes(pending))))
                pending.clear()

        while not channel.closed:
            try:
                output = channel.recv(32768)
            except socket.timeout:
                flush_pending()
                continue
            except Exception as error:
                if not channel.closed:
                    error_text = str(error)
                    self.ui(lambda: self._terminal_append(f"Erro SSH: {error_text}\n"))
                break
            if not output:
                break
            pending.extend(output)
            if len(pending) >= 32768:
                flush_pending()
        flush_pending()
        self.ui(lambda: self._ssh_channel_closed(generation))

    def _feed_ssh_output(self, generation, data):
        if generation != self.ssh_generation or self.ssh_stream is None:
            return
        text = self.ssh_decoder.decode(data)
        if text:
            self.ssh_stream.feed(text)
            self.ssh_screen_dirty = True

    @staticmethod
    def _ssh_color(color, default):
        palette = {
            "black": 0, "red": 1, "green": 2, "brown": 3,
            "blue": 4, "magenta": 5, "cyan": 6, "white": 7,
            "brightblack": 8, "brightred": 9, "brightgreen": 10,
            "brightyellow": 11, "brightblue": 12, "brightmagenta": 13,
            "brightcyan": 14, "brightwhite": 15,
        }
        if color == "default":
            return default
        if color in palette:
            return "#" + pyte.graphics.FG_BG_256[palette[color]]
        if len(color) == 6:
            return "#" + color
        return default

    def _render_ssh_screen(self):
        if self.ssh_screen is None:
            return
        terminal_widget = self.terminal_output._textbox
        terminal_widget.delete("1.0", "end")
        for row_index in range(self.ssh_screen.lines):
            row = self.ssh_screen.buffer[row_index]
            current_style = None
            run = []
            for column in range(self.ssh_screen.columns):
                cell = row[column]
                style = (
                    cell.fg, cell.bg, cell.bold, cell.italics,
                    cell.underscore, cell.reverse,
                )
                if current_style is not None and style != current_style:
                    self._insert_ssh_run(terminal_widget, run, current_style)
                    run = []
                current_style = style
                run.append(cell.data or " ")
            if run:
                self._insert_ssh_run(terminal_widget, run, current_style)
            if row_index < self.ssh_screen.lines - 1:
                terminal_widget.insert("end", "\n")

        cursor = self.ssh_screen.cursor
        terminal_widget.mark_set("insert", f"{cursor.y + 1}.{cursor.x}")
        self.ssh_screen_dirty = False

    def _insert_ssh_run(self, terminal_widget, run, style):
        tag = self.ssh_style_tags.get(style)
        if tag is None:
            tag = f"ssh_style_{len(self.ssh_style_tags)}"
            self.ssh_style_tags[style] = tag
            foreground = self._ssh_color(style[0], "#d6d6d6")
            background = self._ssh_color(style[1], "#101214")
            if style[5]:
                foreground, background = background, foreground
            font_style = (
                "Consolas", 13, "bold" if style[2] else "normal",
                "italic" if style[3] else "roman",
            )
            terminal_widget.tag_configure(
                tag, foreground=foreground, background=background,
                font=font_style, underline=style[4],
            )
        terminal_widget.insert("end", "".join(run), (tag,))

    def _ssh_channel_closed(self, generation):
        if generation != self.ssh_generation:
            return
        self.ssh_channel = None
        client, self.ssh_client = self.ssh_client, None
        if client:
            client.close()
        self._clear_terminal()
        self.ssh_status.set("Conexão SSH encerrada.")

    def _disconnect_ssh(self):
        self._close_ssh()

    def _clear_terminal(self):
        self.ssh_screen = None
        self.ssh_stream = None
        self.ssh_decoder = None
        self.ssh_style_tags = {}
        self.ssh_screen_dirty = False
        if hasattr(self, "terminal_output"):
            self.terminal_output._textbox.delete("1.0", "end")

    def _close_ssh(self, update_status=True):
        self.ssh_generation += 1
        self.ssh_connecting = False
        self._clear_terminal()
        connecting_client, self.ssh_connecting_client = self.ssh_connecting_client, None
        if connecting_client:
            connecting_client.close()
        channel, self.ssh_channel = self.ssh_channel, None
        if channel:
            channel.close()
        client, self.ssh_client = self.ssh_client, None
        if client:
            client.close()
        if update_status and hasattr(self, "ssh_status"):
            self.ssh_status.set("Desconectado do SSH.")

    def _build_login_page(self, page):
        self.login_panel = self._section(page, "Acesso ao servidor SMB")
        self.login_panel.pack(fill="x", padx=8, pady=8)
        login = ctk.CTkFrame(self.login_panel, fg_color="transparent")
        login.pack(fill="x", padx=8, pady=(0, 8))
        self.host_var = tk.StringVar()
        self.host_cb = ctk.CTkComboBox(
            login,
            variable=self.host_var,
            values=[],
            width=240,
            state="readonly",
            fg_color=("#ffffff", "#12171b"),
            text_color=("#1d2a32", "#edf2f5"),
            text_color_disabled=("#53636d", "#a8b6bf"),
            button_color=("#167b88", "#3d9b8b"),
            button_hover_color=("#0f6872", "#356f67"),
            dropdown_fg_color=("#e8eef2", "#2b363e"),
            dropdown_hover_color=("#167b88", "#3d9b8b"),
            dropdown_text_color=("#1d2a32", "#edf2f5"),
        )
        ctk.CTkLabel(login, text="Servidor").grid(row=1, column=0, padx=8, pady=6, sticky="e")
        self.host_cb.grid(row=1, column=1, padx=4, pady=6)
        self._accent_button(login, text="Procurar na rede", command=self.scan).grid(
            row=1, column=2, padx=4, pady=6,
        )
        self.user = tk.StringVar()
        self.pwd = tk.StringVar()
        ctk.CTkLabel(login, text="Usuário").grid(row=2, column=0, padx=8, pady=6, sticky="e")
        ctk.CTkEntry(
            login, textvariable=self.user, width=220,
            text_color=("#1d2a32", "#edf2f5"),
            placeholder_text_color=("#53636d", "#a8b6bf"),
        ).grid(row=2, column=1, padx=4, pady=6)
        ctk.CTkLabel(login, text="Senha").grid(row=2, column=2, padx=4, pady=6, sticky="e")
        password_entry = ctk.CTkEntry(
            login, textvariable=self.pwd, show="*", width=220,
            text_color=("#1d2a32", "#edf2f5"),
            placeholder_text_color=("#53636d", "#a8b6bf"),
        )
        password_entry.grid(row=2, column=3, padx=4, pady=6)
        password_entry.bind("<Return>", self._login_on_enter)
        self._accent_button(login, text="Validar e conectar", command=self.connect).grid(
            row=2, column=4, padx=8, pady=6,
        )
        self.login_status = tk.StringVar(value="Selecione um servidor e informe usuário e senha.")
        self.login_status_frame = ctk.CTkFrame(page, fg_color="transparent")
        self.login_status_frame.pack(fill="x", padx=12, pady=4)
        VariableLabel(self.login_status_frame, self.login_status).pack(side="left", anchor="w")
    def _build_system_page(self, page):
        content = ctk.CTkScrollableFrame(page, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=8, pady=4)
        controls = ctk.CTkFrame(content, fg_color="transparent")
        controls.pack(fill="x", padx=4, pady=4)
        ctk.CTkLabel(controls, text="Atualizar a cada").pack(side="left", padx=(0, 4))
        self.status_interval_var = tk.StringVar(value="5")
        ctk.CTkOptionMenu(
            controls,
            values=["1", "5", "10", "20", "30", "60", "120", "300"],
            variable=self.status_interval_var,
            width=90,
            fg_color=("#167b88", "#3d9b8b"),
            button_color=("#167b88", "#3d9b8b"),
            button_hover_color=("#0f6872", "#356f67"),
            text_color="#ffffff",
            dropdown_fg_color=("#e8eef2", "#2b363e"),
            dropdown_hover_color=("#167b88", "#3d9b8b"),
            dropdown_text_color=("#1d2a32", "#edf2f5"),
            command=self._status_interval_changed,
        ).pack(side="left", padx=4)
        ctk.CTkLabel(controls, text="segundos").pack(side="left", padx=(0, 8))
        self._accent_button(controls, text="Atualizar agora", command=self.refresh_server_status).pack(side="left", padx=4)

        self.server_overview = tk.StringVar(value="Aguardando autenticação.")
        VariableLabel(content, self.server_overview).pack(anchor="w", padx=8, pady=(2, 6))
        metrics = ctk.CTkFrame(content, fg_color="transparent")
        metrics.pack(fill="x", padx=2)
        self.metric_rings = {
            "cpu": MetricRing(metrics, "CPU", ("#e8eef2", "#2b363e")),
            "memory": MetricRing(metrics, "Memória", ("#e8eef2", "#2b363e")),
        }
        self.metric_rings["cpu"].grid(row=0, column=0, sticky="nsew", padx=3, pady=3)
        self.metric_rings["memory"].grid(row=0, column=1, sticky="nsew", padx=3, pady=3)
        self.metric_vars = {
            "uptime": tk.StringVar(value="--"),
            "network": tk.StringVar(value="--"),
        }
        for column, (title, key) in enumerate((("Tempo ativo", "uptime"), ("Rede", "network")), start=2):
            panel = self._section(metrics, title)
            panel.grid(row=0, column=column, sticky="nsew", padx=3, pady=3)
            VariableLabel(panel, self.metric_vars[key], anchor="center").pack(
                fill="both", expand=True, padx=8, pady=(8, 10),
            )
        for column in range(4):
            metrics.columnconfigure(column, weight=1)

        identity = self._section(content, "Sistema e hardware")
        identity.pack(fill="x", padx=2, pady=(6, 4))
        identity_grid = ctk.CTkFrame(identity, fg_color="transparent")
        identity_grid.pack(fill="x", padx=8, pady=(0, 8))
        system_fields = (
            ("Sistema operacional", "os_name"),
            ("Kernel", "kernel"),
            ("Modelo do computador", "computer_model"),
            ("Processador", "cpu_model"),
            ("Núcleos físicos/lógicos", "cpu_cores"),
            ("GPU", "gpu_models"),
            ("Pacotes instalados", "packages"),
            ("Desktop", "desktop_environment"),
            ("Gerenciador de janelas", "window_manager"),
            ("Fonte padrão", "font"),
            ("Shell", "shell"),
        )
        self.system_info_vars = {key: tk.StringVar(value="Indisponível") for _, key in system_fields}
        for index, (label, key) in enumerate(system_fields):
            row, pair = divmod(index, 2)
            column = pair * 2
            ctk.CTkLabel(identity_grid, text=label, anchor="w", width=170).grid(
                row=row, column=column, sticky="nw", padx=(4, 8), pady=4,
            )
            VariableLabel(
                identity_grid, self.system_info_vars[key], anchor="w", wraplength=300,
            ).grid(row=row, column=column + 1, sticky="new", padx=(0, 12), pady=4)
        identity_grid.columnconfigure(1, weight=1)
        identity_grid.columnconfigure(3, weight=1)

    def _build_disks_page(self, page):
        self.disk_overview = tk.StringVar(value="Aguardando dados do servidor.")
        VariableLabel(page, self.disk_overview).pack(anchor="w", padx=12, pady=(8, 4))

        storage = ctk.CTkFrame(page, fg_color="transparent")
        storage.pack(fill="both", expand=True, padx=8, pady=4)
        filesystems = self._section(storage, "Sistemas de arquivos montados")
        filesystems.pack(fill="x", pady=(0, 4))
        self.filesystem_bars_container = ctk.CTkFrame(filesystems, fg_color="transparent")
        self.filesystem_bars_container.pack(fill="x", padx=8, pady=(0, 8))
        self.filesystem_rows = {}
        self.filesystem_progress_bars = []
        self.filesystem_empty_label = None

        physical_disks = self._section(storage, "Discos físicos e SMART")
        physical_disks.pack(fill="both", expand=True, pady=(4, 0))
        self.physical_disk_tree = ttk.Treeview(
            physical_disks,
            columns=("device", "model", "capacity", "temperature", "smart"),
            show="headings",
            height=4,
        )
        for column, title, width in (
            ("device", "Dispositivo", 180),
            ("model", "Modelo", 250),
            ("capacity", "Capacidade", 120),
            ("temperature", "Temperatura", 120),
            ("smart", "SMART", 120),
        ):
            self.physical_disk_tree.heading(column, text=title)
            self.physical_disk_tree.column(column, width=width, anchor="w")
        self.physical_disk_tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.storage_status = tk.StringVar(value="Aguardando métricas de armazenamento.")
        VariableLabel(storage, self.storage_status).pack(anchor="w", padx=8, pady=(4, 2))

    def _build_shares_page(self, page):
        self.samba_share_status = tk.StringVar(value="Aguardando dados do servidor.")
        VariableLabel(page, self.samba_share_status).pack(anchor="w", padx=12, pady=(8, 4))
        table = ctk.CTkFrame(page, fg_color="transparent")
        table.pack(fill="both", expand=True, padx=8, pady=4)
        columns = (
            "share", "path", "valid_users", "read_list", "write_list",
            "admin_users", "invalid_users", "guest_ok", "read_only",
        )
        self.samba_share_tree = ttk.Treeview(table, columns=columns, show="headings")
        headings = (
            ("share", "Compartilhamento", 150),
            ("path", "Caminho", 220),
            ("valid_users", "Usuários/grupos permitidos", 230),
            ("read_list", "Somente leitura", 180),
            ("write_list", "Leitura e escrita", 180),
            ("admin_users", "Administradores", 180),
            ("invalid_users", "Bloqueados", 180),
            ("guest_ok", "Convidados", 100),
            ("read_only", "Share somente leitura", 150),
        )
        for column, heading, width in headings:
            self.samba_share_tree.heading(column, text=heading)
            self.samba_share_tree.column(column, width=width, minwidth=90, anchor="w")
        vertical_scroll = ttk.Scrollbar(table, orient="vertical", command=self.samba_share_tree.yview)
        horizontal_scroll = ttk.Scrollbar(table, orient="horizontal", command=self.samba_share_tree.xview)
        self.samba_share_tree.configure(
            yscrollcommand=vertical_scroll.set, xscrollcommand=horizontal_scroll.set,
        )
        self.samba_share_tree.grid(row=0, column=0, sticky="nsew")
        vertical_scroll.grid(row=0, column=1, sticky="ns")
        horizontal_scroll.grid(row=1, column=0, sticky="ew")
        table.grid_rowconfigure(0, weight=1)
        table.grid_columnconfigure(0, weight=1)

    # ---- util
    def say(self, msg):
        self.q.put(("log", msg))

    def _pump(self):
        while not self.q.empty():
            kind, val = self.q.get()
            if kind == "log":
                self.log.configure(state="normal")
                self.log.insert("end", val + "\n")
                self.log.see("end")
                self.log.configure(state="disabled")
            elif kind == "host":
                ip, name = val
                self.hosts[f"{name} ({ip})"] = (ip, name)
                self.host_cb.configure(values=sorted(self.hosts))
            elif kind == "progress":
                done, total = val
                progress = done / total if total else 1
                self.bar.set(progress)
                self.bar_lbl.configure(text=f"{progress:.0%} ({done}/{total} arquivos)")
            elif kind == "ssh_data":
                generation, data = val
                self._feed_ssh_output(generation, data)
            elif kind == "call":
                val()
        if self.ssh_screen_dirty:
            self._render_ssh_screen()
        self.after(100, self._pump)

    def ui(self, fn):
        self.q.put(("call", fn))

    def _login_on_enter(self, _event=None):
        self.connect()
        return "break"

    def show_login_form(self):
        self.main_page.pack_forget()
        if not self.login_page.winfo_manager():
            self.login_page.pack(fill="both", expand=True)
        self.session_display.set("Não autenticado")
        self.host_cb.focus_set()

    def show_main_page(self):
        self.login_page.pack_forget()
        if not self.main_page.winfo_manager():
            self.main_page.pack(fill="both", expand=True)
        self.session_display.set(f"{self.authenticated_user} @ {self.remote.host}")
        self.select_tab("Sistema")

    # ---- descoberta / conexão
    def scan(self):
        self.say("Procurando servidores SMB na rede...")

        def run():
            scan_network(lambda ip, n: self.q.put(("host", (ip, n))))
            self.say("Busca concluída.")

        threading.Thread(target=run, daemon=True).start()

    def connect(self):
        self._close_ssh(update_status=False)
        self.authenticated_password = None
        self.status_generation += 1
        generation = self.status_generation
        self._cancel_server_status_timer()
        self.status_polling_generation = None
        self.status_share = None
        self.authenticated = False
        self.authenticated_user = None
        self.remote = None
        self.share, self.path = None, ""
        self.auth_summary.set("Não autenticado")
        self._fill([])
        self._reset_server_dashboard("Aguardando autenticação.")
        sel = self.host_var.get().strip()
        ip, name = self.hosts.get(sel, (sel, sel))
        if not ip:
            self.login_status.set("Selecione um servidor nesta aba.")
            return
        user = self.user.get().strip()
        password = self.pwd.get()
        if not user or not password:
            self.login_status.set("Informe usuário e senha.")
            return

        self.auth_summary.set("Autenticando...")
        self.login_status.set(f"Validando acesso a {ip}...")

        def run():
            try:
                remote = Remote(ip, name, user, password)
                shares = remote.list_shares(user, password)
                self.ui(lambda: self._show_shares(remote, shares, user, password, generation))
            except Exception as e:
                error_text = str(e)
                self.ui(lambda: self._login_failed(ip, error_text, generation))

        threading.Thread(target=run, daemon=True).start()

    def _login_failed(self, ip, error, generation):
        if generation != self.status_generation:
            return
        self.show_login_form()
        self.authenticated = False
        self.authenticated_user = None
        self.authenticated_password = None
        self.remote = None
        self.status_share = None
        self.share, self.path = None, ""
        self.auth_summary.set("Não autenticado")
        self.login_status.set(f"Falha ao autenticar em {ip}: {error}")
        self._reset_server_dashboard("Autenticação inválida.")
        self.say(f"Erro ao conectar: {error}")

    def _show_shares(self, remote, shares, user, password, generation):
        if generation != self.status_generation:
            return
        self.authenticated = True
        self.authenticated_user = user
        self.authenticated_password = password
        self.remote = remote
        selection = self.host_var.get().strip() or f"{remote.name} ({remote.host})"
        self.saved_server = {
            "selection": selection,
            "ip": remote.host,
            "name": remote.name,
        }
        self.hosts[selection] = (remote.host, remote.name)
        self.host_cb.configure(values=sorted(self.hosts))
        self.host_var.set(selection)
        self.share, self.path = None, ""
        if self.dest and self.dest_server and self.dest_server.casefold() != remote.host.casefold():
            self.dest = None
            self.dest_server = None
            self._save_settings()
        self.pwd.set("")
        self.auth_summary.set(f"Autenticado: {user} @ {remote.host}")
        self.login_status.set(f"Acesso validado para {user} em {remote.host}.")
        self._save_settings()
        self.show_main_page()
        self.status_share = find_public_share(shares)
        self.path_lbl.configure(text=f"\\\\{remote.host}\\")
        self._update_dest_label()
        self._fill([("share", share) for share in shares])
        if shares:
            self.say(f"Conectado a {remote.host}. Clique em um compartilhamento para abrir.")
            if self.status_share:
                self._request_server_status(generation)
            else:
                self._reset_server_dashboard(
                    "Compartilhamento público não encontrado. "
                    f"O agente deve publicar {STATUS_FILENAME} na raiz dele."
                )
        else:
            self.say(f"Conectado a {remote.host}, mas nenhum compartilhamento de disco foi encontrado.")
            self._reset_server_dashboard("Nenhum compartilhamento disponível para consultar o agente.")

    def _reset_server_dashboard(self, message):
        self.server_overview.set(message)
        self.disk_overview.set(message)
        self.storage_status.set(message)
        for value in self.metric_vars.values():
            value.set("--")
        for value in self.system_info_vars.values():
            value.set("Indisponível")
        for ring in self.metric_rings.values():
            ring.set_value(None, message, self.theme_colors)
        self._render_system_filesystems([])
        self._render_samba_shares([])
        for tree in (self.physical_disk_tree, self.samba_share_tree):
            for row in tree.get_children():
                tree.delete(row)

    def _render_system_filesystems(self, filesystems):
        current_mounts = {
            filesystem.get("mountpoint", filesystem.get("device", str(index))): filesystem
            for index, filesystem in enumerate(filesystems)
        }
        for mountpoint in set(self.filesystem_rows) - set(current_mounts):
            self.filesystem_rows.pop(mountpoint)["frame"].destroy()

        if self.filesystem_empty_label is not None:
            self.filesystem_empty_label.destroy()
            self.filesystem_empty_label = None
        if not current_mounts:
            self.filesystem_progress_bars = []
            self.filesystem_empty_label = ctk.CTkLabel(
                self.filesystem_bars_container,
                text="Nenhum sistema de arquivos local disponível.",
                text_color=("#53636d", "#a8b6bf"),
            )
            self.filesystem_empty_label.pack(anchor="w", padx=4, pady=4)
            return
        for mountpoint, filesystem in current_mounts.items():
            row_widgets = self.filesystem_rows.get(mountpoint)
            if row_widgets is None:
                row = ctk.CTkFrame(self.filesystem_bars_container, fg_color="transparent")
                row.pack(fill="x", padx=2, pady=3)
                name_label = ctk.CTkLabel(row, anchor="w", width=280)
                name_label.pack(side="left", padx=(4, 8))
                progress = ctk.CTkProgressBar(
                    row,
                    height=12,
                    fg_color=self.theme_colors["panel"],
                    progress_color=self.theme_colors["accent"],
                )
                progress.pack(side="left", fill="x", expand=True, padx=4)
                usage_label = ctk.CTkLabel(row, width=150, anchor="e")
                usage_label.pack(side="right", padx=(8, 4))
                row_widgets = {
                    "frame": row,
                    "name": name_label,
                    "progress": progress,
                    "usage": usage_label,
                }
                self.filesystem_rows[mountpoint] = row_widgets

            percent = float(filesystem["percent"])
            row_widgets["name"].configure(text=(
                f"{mountpoint} · {filesystem['device']} · {filesystem['filesystem']}"
            ))
            row_widgets["progress"].set(max(0.0, min(1.0, percent / 100)))
            row_widgets["usage"].configure(text=(
                f"{percent:.0f}% · {self._format_bytes(filesystem['free_bytes'])} livre"
            ))
        self.filesystem_progress_bars = [
            row_widgets["progress"] for row_widgets in self.filesystem_rows.values()
        ]

    def _render_samba_shares(self, shares):
        for row in self.samba_share_tree.get_children():
            self.samba_share_tree.delete(row)
        if shares is None:
            self.samba_share_status.set(
                "Atualize o agente do servidor para publicar as permissões dos compartilhamentos."
            )
            return
        if not shares:
            self.samba_share_status.set("Nenhum compartilhamento Samba publicado pelo servidor.")
            return

        def rule(value):
            return str(value).strip() or "Não especificado"

        def yes_no(value):
            value = str(value).strip().casefold()
            if value in ("yes", "true", "1"):
                return "Sim"
            if value in ("no", "false", "0"):
                return "Não"
            return "Padrão Samba"

        for share in sorted(shares, key=lambda item: item.get("name", "").casefold()):
            self.samba_share_tree.insert("", "end", values=(
                share.get("name", "--"),
                share.get("path") or "--",
                rule(share.get("valid_users")),
                rule(share.get("read_list")),
                rule(share.get("write_list")),
                rule(share.get("admin_users")),
                rule(share.get("invalid_users")),
                yes_no(share.get("guest_ok")),
                yes_no(share.get("read_only")),
            ))
        self.samba_share_status.set(f"{len(shares)} compartilhamentos configurados no Samba.")

    def refresh_server_status(self, _event=None):
        self._cancel_server_status_timer()
        self._request_server_status(self.status_generation)

    def _status_interval_changed(self, _value=None):
        self._cancel_server_status_timer()
        if self.authenticated and self.remote and self.status_share:
            self.status_after_id = self.after(
                self._status_interval_ms(),
                lambda: self._request_server_status(self.status_generation),
            )

    def _cancel_server_status_timer(self):
        if self.status_after_id is not None:
            try:
                self.after_cancel(self.status_after_id)
            except tk.TclError:
                pass
            self.status_after_id = None

    def _status_interval_ms(self):
        try:
            seconds = int(self.status_interval_var.get())
        except (AttributeError, tk.TclError, ValueError):
            seconds = 5
        return max(1, min(300, seconds)) * 1000

    def _request_server_status(self, generation):
        if generation != self.status_generation or not self.authenticated or not self.remote:
            return
        share = self.status_share
        if not share:
            self._reset_server_dashboard("O compartilhamento público não está disponível.")
            return
        if self.status_polling_generation == generation:
            return

        remote = self.remote
        self.status_polling_generation = generation

        def run():
            try:
                data = remote.read_status(share, STATUS_FILENAME)
                error = None
            except Exception as exc:
                data = None
                error = str(exc)
            self.ui(lambda: self._server_status_received(
                generation, remote, share, data, error,
            ))

        threading.Thread(target=run, daemon=True).start()

    def _server_status_received(self, generation, remote, share, data, error):
        if generation != self.status_generation or remote is not self.remote:
            return
        self.status_polling_generation = None
        if share.casefold() != (self.status_share or "").casefold():
            self._request_server_status(generation)
            return
        if error:
            self._reset_server_dashboard(
                f"Sem dados do agente em {share}: {error}",
            )
        else:
            self._render_server_status(data)
        self._cancel_server_status_timer()
        self.status_after_id = self.after(
            self._status_interval_ms(),
            lambda: self._request_server_status(generation),
        )

    def _render_server_status(self, data):
        try:
            memory = data["memory"]
            network = data["network"]
            system_info = data.get("system_info", {})
            package_count = system_info.get("package_count", {})
            if package_count.get("count") is None:
                packages = package_count.get("manager", "Indisponível")
            else:
                packages = f"{package_count['count']} ({package_count.get('manager', 'pacotes')})"
            gpu_models = system_info.get("gpu_models") or []
            system_values = {
                "os_name": system_info.get("os_name") or data.get("os", "Indisponível"),
                "kernel": system_info.get("kernel") or data.get("os_release", "Indisponível"),
                "computer_model": system_info.get("computer_model", "Indisponível"),
                "cpu_model": system_info.get("cpu_model", "Indisponível"),
                "cpu_cores": (
                    f"{system_info.get('cpu_physical_count', 0)} físicos / "
                    f"{system_info.get('cpu_logical_count', data.get('cpu_count', 0))} lógicos"
                ),
                "gpu_models": "\n".join(gpu_models) if gpu_models else "Indisponível",
                "packages": packages,
                "desktop_environment": system_info.get("desktop_environment", "Indisponível"),
                "window_manager": system_info.get("window_manager", "Indisponível"),
                "font": system_info.get("font", "Indisponível"),
                "shell": system_info.get("shell", "Indisponível"),
            }
            for key, value in system_values.items():
                self.system_info_vars[key].set(str(value))

            cpu_percent = float(data["cpu_percent"])
            cpu_count = int(data["cpu_count"])
            self.metric_rings["cpu"].set_value(
                cpu_percent, f"{cpu_count} núcleos", self.theme_colors,
            )
            self.metric_rings["memory"].set_value(
                memory["percent"],
                f"{self._format_bytes(memory['used_bytes'])} de "
                f"{self._format_bytes(memory['total_bytes'])}",
                self.theme_colors,
            )
            self.metric_vars["uptime"].set(self._format_duration(data["uptime_seconds"]))
            self.metric_vars["network"].set(
                f"Recebido {self._format_bytes(network['bytes_received'])} · "
                f"Enviado {self._format_bytes(network['bytes_sent'])}",
            )
            overview = (
                f"{data['hostname']} · {system_values['os_name']} · "
                f"amostra {data['sampled_at']} UTC"
            )
            self.server_overview.set(overview)
            self.disk_overview.set(overview)
            self._render_system_filesystems(data.get("filesystems", []))
            self._render_samba_shares(data.get("samba_shares"))
            for row in self.physical_disk_tree.get_children():
                self.physical_disk_tree.delete(row)
            for disk in data["physical_disks"]:
                temperature = disk.get("temperature_c")
                self.physical_disk_tree.insert("", "end", values=(
                    disk["name"],
                    disk.get("model") or "--",
                    self._format_bytes(disk["capacity_bytes"]) if disk["capacity_bytes"] else "--",
                    f"{temperature} °C" if temperature is not None else "Indisponível",
                    disk["smart_status"],
                ))
            status_notes = []
            if data.get("share_discovery_error"):
                status_notes.append(data["share_discovery_error"])
            if data.get("smartctl_error"):
                status_notes.append(data["smartctl_error"])
            self.storage_status.set(" · ".join(status_notes) or "SMART consultado pelo agente do servidor.")
        except (KeyError, TypeError, ValueError) as exc:
            self._reset_server_dashboard(f"Arquivo de status inválido: {exc}")

    @staticmethod
    def _format_bytes(value):
        size = float(value)
        for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
            if size < 1024 or unit == "PB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024

    @staticmethod
    def _format_duration(seconds):
        minutes, _ = divmod(max(0, int(seconds)), 60)
        days, minutes = divmod(minutes, 1440)
        hours, minutes = divmod(minutes, 60)
        if days:
            return f"{days}d {hours}h"
        if hours:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

    # ---- navegação remota
    def refresh(self):
        if not self.authenticated or not self.remote or not self.share:
            return
        location = f"\\\\{self.remote.host}\\{self.share}"
        if self.path:
            location += "\\" + self.path.replace("/", "\\")
        self.path_lbl.configure(text=location)

        def run():
            try:
                entries = [("dir" if i.isDirectory else "file", i.filename)
                           for i in self.remote.list(self.share, self.path)]
                entries.sort(key=lambda e: (e[0] != "dir", e[1].lower()))
                entries.insert(0, ("up", ".."))
                self.ui(lambda: self._fill(entries))
            except Exception as e:
                self.say(f"Erro ao listar: {e}")

        threading.Thread(target=run, daemon=True).start()

    def _fill(self, entries):
        self.entries = entries
        for button in self.remote_buttons:
            button.destroy()
        self.remote_buttons.clear()
        icons = {"share": "[share] ", "dir": "[pasta] ", "file": "        ", "up": ""}
        for index, (kind, name) in enumerate(entries):
            button = ctk.CTkButton(
                self.rlist,
                text=icons[kind] + name,
                anchor="w",
                fg_color="transparent",
                hover_color=self.theme_colors["hover"],
                command=lambda item_index=index: self.open_item(item_index),
            )
            button.pack(fill="x", padx=2, pady=1)
            self.remote_buttons.append(button)

    def open_item(self, index):
        if not self.authenticated:
            return
        if not isinstance(index, int) or not 0 <= index < len(self.entries):
            return
        kind, name = self.entries[index]
        if kind == "share":
            self.share, self.path = name, ""
        elif kind == "dir":
            self.path = f"{self.path}/{name}"
        elif kind == "up":
            self.path = self.path.rsplit("/", 1)[0] if self.path else ""
        else:
            return
        self.refresh()

    def set_dest(self):
        if not self.authenticated or not self.share:
            messagebox.showinfo("Destino", "Faça login e abra um compartilhamento primeiro.")
            return
        self.dest = (self.share, self.path)
        self.dest_server = self.remote.host
        self._update_dest_label()
        self._save_settings()

    # ---- origem
    def _render_sources(self):
        for row in self.source_row_widgets:
            row.destroy()
        self.source_row_widgets.clear()
        for source in self.source_paths:
            row = ctk.CTkFrame(self.src, fg_color="transparent")
            row.pack(fill="x", padx=2, pady=2)
            ctk.CTkLabel(row, text=source, anchor="w").pack(
                side="left", fill="x", expand=True, padx=(4, 8),
            )
            self._accent_button(
                row,
                text="Remover",
                width=76,
                command=lambda path=source: self.remove_src(path),
            ).pack(side="right", padx=2)
            self.source_row_widgets.append(row)

    def add_src(self):
        d = filedialog.askdirectory()
        if d and d not in self.source_paths:
            self.source_paths.append(d)
            self._render_sources()
            self._save_settings()

    def remove_src(self, source):
        if source in self.source_paths:
            self.source_paths.remove(source)
            self._render_sources()
        self._save_settings()

    # ---- backup
    def start_backup(self):
        sources = list(self.source_paths)
        if not (self.authenticated and self.remote and self.dest and sources):
            messagebox.showinfo(
                "Backup", "Faça login, escolha o destino e adicione pastas de origem.",
            )
            return
        self.cancel.clear()
        threading.Thread(target=self.backup, args=(sources,), daemon=True).start()

    def backup(self, sources):
        share, base = self.dest
        sent = skipped = failed = 0
        total = sum(len(fs) for s in sources for _, _, fs in os.walk(s))
        done = 0
        max_workers = 4
        pending = set()
        cancelled = False
        self.q.put(("progress", (0, total)))

        def transfer_file(local_path, remote_path):
            if self.cancel.is_set():
                return "cancelled", local_path, ""
            try:
                if self.remote.up_to_date(share, remote_path, local_path):
                    return "skipped", local_path, ""
                self.remote.upload(share, remote_path, local_path)
                return "sent", local_path, ""
            except Exception as error:
                return "failed", local_path, str(error)

        def record_completed(block):
            nonlocal sent, skipped, failed, done
            if not pending:
                return
            completed, _ = wait(
                pending,
                return_when=FIRST_COMPLETED if block else FIRST_COMPLETED,
            )
            for future in completed:
                pending.remove(future)
                result, local_path, error = future.result()
                if result == "sent":
                    sent += 1
                    self.say(f"OK {local_path}")
                elif result == "skipped":
                    skipped += 1
                elif result == "failed":
                    failed += 1
                    self.say(f"FALHA {local_path}: {error}")
                else:
                    continue
                done += 1
                self.q.put(("progress", (done, total)))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for src in sources:
                if self.cancel.is_set():
                    cancelled = True
                    break
                root_name = os.path.basename(os.path.normpath(src)) or "raiz"
                for dirpath, _, files in os.walk(src):
                    if self.cancel.is_set():
                        cancelled = True
                        break
                    rel = os.path.relpath(dirpath, src).replace("\\", "/")
                    rdir = f"{base}/{root_name}" + ("" if rel == "." else f"/{rel}")
                    try:
                        self.remote.ensure_dir(share, rdir)
                    except Exception as error:
                        self.say(f"Erro criando {rdir}: {error}")
                        failed += len(files)
                        done += len(files)
                        self.q.put(("progress", (done, total)))
                        continue
                    for filename in files:
                        if self.cancel.is_set():
                            cancelled = True
                            break
                        local_path = os.path.join(dirpath, filename)
                        remote_path = f"{rdir}/{filename}"
                        pending.add(executor.submit(transfer_file, local_path, remote_path))
                        if len(pending) >= max_workers * 2:
                            record_completed(block=True)
                    if cancelled:
                        break
                if cancelled:
                    break

            while pending:
                record_completed(block=True)

        if cancelled or self.cancel.is_set():
            self.say(f"Backup cancelado: {sent} enviados, {skipped} inalterados, {failed} falhas.")
        else:
            self.say(f"Concluído: {sent} enviados, {skipped} inalterados, {failed} falhas.")
        self.ui(self.refresh)


if __name__ == "__main__":
    instance_lock = acquire_single_instance()
    if instance_lock is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("marcelino Client", "O aplicativo já está aberto.")
        root.destroy()
    else:
        App().mainloop()
