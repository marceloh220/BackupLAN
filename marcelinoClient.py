"""
    file: marcelinoClient.py
    Description: Cliente para o servidor marcelinoSrv, permitindo autenticação, 
    transferência de arquivos e monitoramento remoto via SMB e SSH.

    date: 2026-06-15

    MIT License

    Copyright (c) 2026 Marcelo H Moraes

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.
"""

import os
import queue
import json
import socket
import subprocess
import sys
import threading
import codecs
import base64
import hashlib
import time
from datetime import datetime, timezone
import tkinter as tk
import tkinter.font as tkfont
import customtkinter as ctk
from concurrent.futures import ThreadPoolExecutor
from tkinter import filedialog
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import shutil
import tempfile
import pystray
import paramiko
import pyte
from pyte.graphics import FG_BG_256
from types import SimpleNamespace
from pathlib import Path

import smbclient
import smbclient.path
import ntpath
from PIL import Image, ImageDraw

SMB_PORT = 445
STATUS_FILENAME = ".marcelinoSrv.json"
MONITOR_SHARE = "Monitor"  # share oculto (browseable = no), acessado pelo nome
MUTEX_NAME = "Local\\MarcelinoClientSingleInstance"
THEME_PALETTE = {
    "background": ("#f2f5f7", "#171d22"),
    "surface": ("#ffffff", "#222b32"),
    "panel": ("#e8eef2", "#2b363e"),
    "input": ("#ffffff", "#12171b"),
    "text": ("#1d2a32", "#edf2f5"),
    "muted": ("#53636d", "#a8b6bf"),
    "border": ("#c5d0d7", "#46545e"),
    "accent": ("#167b88", "#28786d"),
    "hover": ("#d6e7ea", "#356f67"),
    "accent_hover": ("#0f6872", "#356f67"),
    "selected_text": ("#ffffff", "#ffffff"),
}


def show_info_dialog(parent, title, message):
    '''
    Exibe um diálogo de informação com um botão "OK".

    Parâmetros:
    parent (tk.Widget): O widget pai para o diálogo.
    title (str): O título do diálogo.
    message (str): A mensagem a ser exibida no diálogo.
    '''
    dialog = ctk.CTkToplevel(parent)
    dialog.title(title)
    dialog.geometry("420x160")
    dialog.resizable(False, False)
    dialog.transient(parent)
    ctk.CTkLabel(dialog, text=message, wraplength=380, justify="left").pack(
        fill="both", expand=True, padx=20, pady=(20, 10),
    )
    ctk.CTkButton(dialog, text="OK", width=90, command=dialog.destroy).pack(pady=(0, 16))
    dialog.bind("<Return>", lambda _event: dialog.destroy())
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    dialog.wait_visibility()
    dialog.grab_set()
    dialog.wait_window()


def ask_text_dialog(parent, title, message):
    '''
    Exibe um diálogo solicitando ao usuário que insira um texto.

    Parâmetros:
    parent (tk.Widget): O widget pai para o diálogo.
    title (str): O título do diálogo.
    message (str): A mensagem a ser exibida no diálogo.

    Retorna:
    str | None: O texto inserido pelo usuário, ou None se o diálogo for cancelado.
    '''
    dialog = ctk.CTkToplevel(parent)
    dialog.title(title)
    dialog.geometry("480x230")
    dialog.resizable(False, False)
    dialog.transient(parent)
    result: list[str | None] = [None]
    ctk.CTkLabel(dialog, text=message, wraplength=440, justify="left").pack(
        fill="x", padx=20, pady=(20, 12),
    )
    entry = ctk.CTkEntry(dialog, width=430)
    entry.pack(fill="x", padx=20, pady=(0, 16))

    def submit():
        result[0] = entry.get()
        dialog.destroy()

    buttons = ctk.CTkFrame(dialog, fg_color="transparent")
    buttons.pack(pady=(0, 16))
    ctk.CTkButton(buttons, text="Cancelar", width=100, command=dialog.destroy).pack(
        side="left", padx=6,
    )
    ctk.CTkButton(buttons, text="OK", width=90, command=submit).pack(side="left", padx=6)
    dialog.bind("<Return>", lambda _event: submit())
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    dialog.wait_visibility()
    dialog.grab_set()
    entry.focus_set()
    dialog.wait_window()
    return result[0]


def hide_monitor_share(shares):
    """Remove o compartilhamento de monitor da lista de compartilhamentos."""
    return [share for share in shares if share.casefold() != MONITOR_SHARE.casefold()]


def acquire_single_instance():
    """Retorna um handle para o mutex de instância única, ou None se já houver outra instância."""
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            return None
        return handle
    """Se não for Windows, utiliza um arquivo de lock para garantir instância única."""
    import fcntl

    lock_file = open(Path(tempfile.gettempdir()) / f"marcelinoClient.{os.getuid()}.lock", "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        return None
    return lock_file


def local_ip():
    """Retorna o endereço IP local da máquina."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def scan_network(callback):
    """Varre a sub-rede /24 procurando hosts com a porta SMB aberta.
    O callback é chamado com o endereço IP e o nome do host para cada host encontrado.
    """
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
    """Representa um host remoto acessível via SMB."""
    def __init__(self, ip, name, user, password, domain=""):
        self.host = ip
        self.name = name
        smbclient.register_session(
            ip, username=(f"{domain}\\{user}" if domain else user), password=password, port=SMB_PORT,
        )

    def list_shares(self, user, password, domain=""):
        """Lista os compartilhamentos SMB disponíveis no host remoto."""
        if os.name == "nt":
            return self._list_shares_windows(user, password, domain)
        if os.name == "posix":
            return self._list_shares_linux(user, password, domain)
        raise OSError(f"Enumeração SMB não suportada nesta plataforma: {os.name}")

    def _list_shares_linux(self, user, password, domain=""):
        """Lista os compartilhamentos SMB disponíveis no host remoto usando smbclient no Linux."""
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
        """Lista os compartilhamentos SMB disponíveis no host remoto usando smbclient no Windows."""
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
        share_str = str(share or "").strip("/\\")
        path_str = str(path or "").replace("/", "\\").strip("\\")
        if path_str:
            return f"\\\\{self.host}\\{share_str}\\{path_str}"
        return f"\\\\{self.host}\\{share_str}"

    def list(self, share, path):
        return [
            SimpleNamespace(filename=e.name, isDirectory=e.is_dir())
            for e in smbclient.scandir(self.unc(share, path))
        ]

    def read_status(self, share, path):
        with smbclient.open_file(self.unc(share, path), mode="r", encoding="utf-8") as status_file:
            return json.load(status_file)

    def is_available(self):
        try:
            with socket.create_connection((self.host, SMB_PORT), timeout=2):
                return True
        except OSError:
            return False

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

    def download(self, share, remote_path, local_path):
        remote_unc = self.unc(share, remote_path)
        os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)
        with smbclient.open_file(remote_unc, mode="rb") as in_file, open(local_path, "wb") as out_file:
            shutil.copyfileobj(in_file, out_file, 1024 * 1024)
        st = smbclient.stat(remote_unc)
        os.utime(local_path, times=(st.st_atime, st.st_mtime))

    def restore_up_to_date(self, share, remote_path, local_path):
        if not os.path.exists(local_path):
            return False
        try:
            r_stat = smbclient.stat(self.unc(share, remote_path))
        except Exception:
            return False
        local_stat = os.stat(local_path)
        return local_stat.st_size == r_stat.st_size and local_stat.st_mtime >= r_stat.st_mtime - 2

    def is_dir(self, share, remote_path=""):
        try:
            return smbclient.path.isdir(self.unc(share, remote_path))
        except Exception:
            return False

    def is_file(self, share, remote_path=""):
        try:
            return smbclient.path.isfile(self.unc(share, remote_path))
        except Exception:
            return False

    def walk(self, share, remote_path="", onerror=None):
        return smbclient.walk(self.unc(share, remote_path), onerror=onerror)


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
        self.canvas.itemconfigure(self.track_id, outline=colors["border"])
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
        self.ssh_rendered_rows: list[object] = []
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
        self.selected_remote_entry = None
        self.last_restore_folder = ""
        self.transfer_in_progress = False
        self.cancel = threading.Event()
        self.hosts = {}
        self.saved_server = None
        self.tabs = {}
        self.active_tab = None
        self.theme_colors = {key: value[0] for key, value in THEME_PALETTE.items()}
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
        icon = self._tray_icon
        if icon is None:
            return
        try:
            icon.run()
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
        self.selected_remote_entry = None
        self.transfer_in_progress = False
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

        saved_status_interval = self.saved_settings.get("status_interval_seconds", 5)
        try:
            saved_status_interval = int(saved_status_interval)
        except (TypeError, ValueError):
            saved_status_interval = 5
        if saved_status_interval not in (1, 5, 10, 20, 30, 60, 120, 300):
            saved_status_interval = 5
        self.status_interval_var.set(str(saved_status_interval))

        server = self.saved_settings.get("server")
        if isinstance(server, dict):
            selection = server.get("selection")
            ip = server.get("ip")
            name = server.get("name")
            if (
                isinstance(selection, str) and selection
                and isinstance(ip, str) and ip
                and isinstance(name, str) and name
            ):
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

        last_restore = self.saved_settings.get("last_restore_folder")
        if isinstance(last_restore, str):
            self.last_restore_folder = last_restore

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
            "last_restore_folder": getattr(self, "last_restore_folder", ""),
            "theme": self.theme,
            "ssh_key_path": self.ssh_key_path.get().strip(),
            "status_interval_seconds": self._status_interval_seconds(),
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
            self.dest_lbl.configure(text="⇥ Destino do backup: (nenhum)")
            return
        share, path = self.dest
        location = f"\\\\{self.dest_server}\\{share}" if self.dest_server else f"\\\\{share}"
        if path:
            location += "\\" + path.replace("/", "\\")
        self.dest_lbl.configure(text=f"⇥ Destino do backup: {location}")

    def _build(self):
        self.login_page = ctk.CTkFrame(self, fg_color="transparent")
        self._build_login_page(self.login_page)

        self.main_page = ctk.CTkFrame(self, fg_color="transparent")
        session_bar = ctk.CTkFrame(self.main_page, fg_color="transparent")
        session_bar.pack(fill="x", padx=8, pady=(8, 0))
        ctk.CTkLabel(session_bar, text="◉ Sessão").pack(side="left", padx=(4, 8))
        self.session_display = tk.StringVar(value="Não autenticado")
        ctk.CTkEntry(
            session_bar, textvariable=self.session_display, state="disabled", width=300,
            fg_color=THEME_PALETTE["input"],
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
        ctk.CTkFrame(self.main_page, height=1, fg_color=THEME_PALETTE["border"]).pack(
            fill="x", padx=8, pady=(4, 6),
        )
        self.tab_container = ctk.CTkFrame(self.main_page, fg_color="transparent")
        self.tab_container.pack(fill="both", expand=True)
        self.add_tab("Sistema", "▦", self._build_system_page)
        self.add_tab("Discos", "◉", self._build_disks_page)
        self.add_tab("Internet", "🌐", self._build_internet_page)
        self.add_tab("DNS / DHCP", "↔", self._build_dns_dhcp_page)
        self.add_tab("Compartilhamentos", "▤", self._build_shares_page)
        self.add_tab("Backup", "⤓", self._build_backup_page)
        self.add_tab("Terminal", ">_", self._build_terminal_page)
        self.login_page.pack(fill="both", expand=True)

    def _apply_theme(self, theme):
        if theme not in ("light", "dark"):
            theme = "light"
        self.theme = theme
        ctk.set_appearance_mode(theme)
        theme_index = 1 if theme == "dark" else 0
        colors = {key: value[theme_index] for key, value in THEME_PALETTE.items()}
        self.theme_button.configure(text="☀ Claro" if theme == "dark" else "☾ Escuro")
        self.login_theme_button.configure(text="☀ Claro" if theme == "dark" else "☾ Escuro")

        self.theme_colors = colors
        self.configure(fg_color=colors["background"])
        backup_bar = getattr(self, "bar", None)
        if backup_bar is not None:
            backup_bar.configure(fg_color=colors["panel"], progress_color=colors["accent"])
        for ring in getattr(self, "metric_rings", {}).values():
            ring.set_theme_colors(colors)
        for disk_bar in getattr(self, "disk_usage_bars", []):
            disk_bar.configure(fg_color=colors["panel"], progress_color=colors["accent"])
        for filesystem_bar in getattr(self, "filesystem_progress_bars", []):
            filesystem_bar.configure(fg_color=colors["panel"], progress_color=colors["accent"])
        for row_widgets in getattr(self, "filesystem_rows", {}).values():
            row_widgets["bar_frame"].configure(border_color=colors["border"])
        for title, tab in self.tabs.items():
            selected = title == self.active_tab
            tab["button"].configure(
                fg_color=colors["accent"] if selected else colors["panel"],
                hover_color=colors["accent_hover"] if selected else colors["hover"],
                text_color=colors["selected_text"] if selected else colors["text"],
            )
        for remote_button in getattr(self, "remote_buttons", []):
            remote_button.configure(hover_color=colors["hover"], text_color=colors["text"])
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
                hover_color=(
                    self.theme_colors["accent_hover"] if selected else self.theme_colors["hover"]
                ),
                text_color=(
                    self.theme_colors["selected_text"] if selected else self.theme_colors["text"]
                ),
            )
        self.tabs[title]["page"].pack(fill="both", expand=True)

    def _section(self, parent, title, **kwargs):
        kwargs.setdefault("fg_color", THEME_PALETTE["panel"])
        section = ctk.CTkFrame(parent, corner_radius=8, **kwargs)
        ctk.CTkLabel(
            section, text=title, font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=12, pady=(8, 2))
        return section

    def _accent_button(self, master, **kwargs):
        kwargs.setdefault("fg_color", THEME_PALETTE["accent"])
        kwargs.setdefault("hover_color", THEME_PALETTE["accent_hover"])
        kwargs.setdefault("text_color", THEME_PALETTE["selected_text"])
        kwargs.setdefault("border_color", THEME_PALETTE["accent"])
        return ctk.CTkButton(master, **kwargs)

    def _build_backup_page(self, page):
        top = ctk.CTkFrame(page, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=4)
        self.auth_summary = tk.StringVar(value="Não autenticado")
        ctk.CTkLabel(top, text="Sessão:").pack(side="left", padx=(4, 0))
        VariableLabel(top, self.auth_summary).pack(side="left", padx=8)

        mid = ctk.CTkFrame(page, fg_color="transparent")
        mid.pack(fill="both", expand=True, padx=8)

        remote_panel = self._section(mid, "▣ Arquivos no servidor remoto")
        remote_panel.pack(side="left", fill="both", expand=True, padx=(0, 4), pady=4)
        self.path_lbl = ctk.CTkLabel(remote_panel, text="-", anchor="w")
        self.path_lbl.pack(fill="x", padx=10, pady=(2, 4))
        self.rlist = ctk.CTkScrollableFrame(remote_panel, fg_color="transparent")
        self.remote_buttons = []
        self.rlist.pack(fill="both", expand=True, padx=6, pady=4)

        self.sel_lbl = ctk.CTkLabel(remote_panel, text="Selecionado: (nenhum)", anchor="w")
        self.sel_lbl.pack(fill="x", padx=10, pady=(2, 2))

        remote_actions = ctk.CTkFrame(remote_panel, fg_color="transparent")
        remote_actions.pack(fill="x", padx=6, pady=4)
        self._accent_button(
            remote_actions, text="Usar como destino", command=self.set_dest,
        ).pack(side="left", fill="x", expand=True, padx=(0, 2))
        self._accent_button(
            remote_actions, text="⤓ Restaurar para o PC", command=self.restore_current_or_selected,
        ).pack(side="left", fill="x", expand=True, padx=(2, 0))

        self.dest_lbl = ctk.CTkLabel(remote_panel, text="⇥ Destino do backup: (nenhum)", anchor="w")
        self.dest_lbl.pack(fill="x", padx=10, pady=(2, 8))

        source_panel = self._section(mid, "📁 Pastas locais (origem do backup)")
        source_panel.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)
        self.source_paths = []
        self.source_row_widgets = []
        self.src = ctk.CTkScrollableFrame(source_panel, fg_color="transparent")
        self.src.pack(fill="both", expand=True, padx=6, pady=4)
        self._accent_button(source_panel, text="＋ Adicionar pasta", command=self.add_src).pack(pady=8)

        act = ctk.CTkFrame(page, fg_color="transparent")
        act.pack(fill="x", padx=8, pady=4)
        self._accent_button(act, text="▶ Iniciar backup", command=self.start_backup).pack(side="left")
        self._accent_button(act, text="⤓ Restaurar arquivos...", command=self.open_restore_dialog).pack(side="left", padx=4)
        self._accent_button(act, text="■ Cancelar", command=self.cancel.set).pack(side="left", padx=4)
        self.bar = ctk.CTkProgressBar(
            act,
            fg_color=self.theme_colors["panel"],
            progress_color=self.theme_colors["accent"],
        )
        self.bar.set(0)
        self.bar.pack(side="left", fill="x", expand=True, padx=8)
        self.bar_lbl = ctk.CTkLabel(act, text="⏳ 0%", width=130)
        self.bar_lbl.pack(side="left")
        self.log = ctk.CTkTextbox(page, height=100, state="disabled", corner_radius=8)
        self.log.pack(fill="x", padx=8, pady=(0, 8))

    def _build_terminal_page(self, page):
        controls = ctk.CTkFrame(page, fg_color="transparent")
        controls.pack(fill="x", padx=8, pady=8)
        self.ssh_toggle_button = self._accent_button(
            controls, text="Conectar SSH", command=self._toggle_ssh,
        )
        self.ssh_toggle_button.pack(side="left", padx=(0, 4))
        self.ssh_status = tk.StringVar(value="Faça login para conectar ao servidor via SSH.")
        VariableLabel(controls, self.ssh_status).pack(side="left", padx=12)
        self._update_ssh_toggle_button()

        self.ssh_key_controls = ctk.CTkFrame(page, fg_color="transparent")
        self.ssh_key_controls.pack(fill="x", padx=8, pady=(0, 8))
        ctk.CTkLabel(self.ssh_key_controls, text="🔑 Arquivo de chave").pack(side="left", padx=(0, 6))
        self.ssh_key_path = tk.StringVar()
        key_path_entry = ctk.CTkEntry(
            self.ssh_key_controls, textvariable=self.ssh_key_path,
            fg_color=THEME_PALETTE["input"],
        )
        key_path_entry.pack(
            side="left", fill="x", expand=True,
        )
        key_path_entry.bind("<FocusOut>", lambda _event: self._save_settings())
        key_path_entry.bind("<Return>", lambda _event: self._save_settings())
        self._accent_button(
            self.ssh_key_controls, text="Selecionar...", command=self._select_ssh_key,
        ).pack(side="left", padx=6)
        ctk.CTkLabel(self.ssh_key_controls, text="🔒 Passphrase").pack(side="left", padx=(4, 6))
        self.ssh_key_passphrase_entry = ctk.CTkEntry(
            self.ssh_key_controls, width=160, show="*", placeholder_text="Opcional",
            fg_color=THEME_PALETTE["input"],
        )
        self.ssh_key_passphrase_entry.pack(side="left")

        families = set(tkfont.families())
        self.terminal_font_family = next(
            (f for f in ("Cascadia Mono", "DejaVu Sans Mono", "Consolas") if f in families),
            "Courier New",
        )
        self.terminal_output = ctk.CTkTextbox(
            page, corner_radius=8,
            font=ctk.CTkFont(family=self.terminal_font_family, size=13),
            wrap="none", fg_color="#101214", text_color="#d6d6d6",
        )
        self.terminal_output.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        terminal_widget = self.terminal_output._textbox
        terminal_widget.configure(
            insertbackground="#ffffff", takefocus=True,
            spacing1=0, spacing2=0, spacing3=0, padx=0, pady=0,
        )
        terminal_widget.bind("<KeyPress>", self._send_ssh_key)
        terminal_widget.bind("<Configure>", self._on_terminal_resize)
        self.terminal_measure_font = tkfont.Font(family=self.terminal_font_family, size=13)
        self.terminal_output.configure(
            height=24 * self.terminal_measure_font.metrics("linespace") + 8,
        )

    def _terminal_size(self):
        widget = self.terminal_output._textbox
        char_width = max(1, self.terminal_measure_font.measure("0"))
        line_height = max(1, self.terminal_measure_font.metrics("linespace"))
        columns = max(20, widget.winfo_width() // char_width - 1)
        rows = max(24, widget.winfo_height() // line_height)
        return columns, rows

    def _on_terminal_resize(self, _event=None):
        screen = self.ssh_screen
        channel = self.ssh_channel
        if screen is None or channel is None or channel.closed:
            return
        columns, rows = self._terminal_size()
        if (columns, rows) == (screen.columns, screen.lines):
            return
        screen.resize(rows, columns)
        self.ssh_rendered_rows = []
        self.ssh_screen_dirty = True
        try:
            channel.resize_pty(width=columns, height=rows)
        except Exception:
            pass

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
        answer = ask_text_dialog(
            self,
            "Confiar no servidor SSH",
            f"O servidor {hostname} ainda não está em known_hosts.\n"
            f"Chave: {key_type}\nFingerprint: {fingerprint}\n\n"
            "Digite yes para confiar e salvar esta chave:",
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

    def _toggle_ssh(self):
        if self.ssh_client:
            self._disconnect_ssh()
        elif not self.ssh_connecting:
            self._connect_ssh()

    def _update_ssh_toggle_button(self):
        button = getattr(self, "ssh_toggle_button", None)
        if button is None:
            return
        if self.ssh_connecting:
            button.configure(text="Conectando...", state="disabled")
        elif self.ssh_client:
            button.configure(text="Desconectar", state="normal")
        else:
            button.configure(text="Conectar SSH", state="normal")
        self._update_ssh_key_controls_visibility()

    def _update_ssh_key_controls_visibility(self):
        controls = getattr(self, "ssh_key_controls", None)
        terminal = getattr(self, "terminal_output", None)
        if controls is None or terminal is None:
            return

        should_show = not self.ssh_connecting and not self.ssh_client
        is_visible = bool(controls.winfo_manager())
        if should_show and not is_visible:
            controls.pack(fill="x", padx=8, pady=(0, 8), before=terminal)
        elif not should_show and is_visible:
            controls.pack_forget()

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
        self._update_ssh_toggle_button()
        self.ssh_status.set(f"Conectando a {username}@{host}:22...")
        client = paramiko.SSHClient()
        self.ssh_connecting_client = client
        self.update_idletasks()
        columns, rows = self._terminal_size()

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
                channel = client.invoke_shell(term="xterm-256color", width=columns, height=rows)
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
            self._update_ssh_toggle_button()
            self.ssh_status.set(f"Falha na conexão SSH: {error}")
            self._terminal_append(f"Falha na conexão SSH: {error}\n")
            return
        self.ssh_client = client
        self._update_ssh_toggle_button()
        self.ssh_channel = channel
        columns, rows = self._terminal_size()
        self.ssh_screen = pyte.Screen(columns, rows)
        try:
            channel.resize_pty(width=columns, height=rows)
        except Exception:
            pass
        self.ssh_stream = pyte.Stream(self.ssh_screen)
        self.ssh_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.ssh_rendered_rows = []
        self.ssh_style_tags = {}
        channel.settimeout(0.05)
        remote = self.remote
        if remote is None:
            self._close_ssh()
            return
        self.ssh_status.set(f"Conectado: {self.authenticated_user}@{remote.host}")
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
        while not channel.closed:
            try:
                output = channel.recv(32768)
            except socket.timeout:
                continue
            except Exception as error:
                if not channel.closed:
                    error_text = str(error)
                    self.ui(lambda: self._terminal_append(f"Erro SSH: {error_text}\n"))
                break
            if not output:
                break
            self.q.put(("ssh_data", (generation, output)))
        self.ui(lambda: self._ssh_channel_closed(generation))

    def _feed_ssh_output(self, generation, data):
        stream = self.ssh_stream
        decoder = self.ssh_decoder
        if generation != self.ssh_generation or stream is None or decoder is None:
            return
        text = decoder.decode(data)
        if text:
            stream.feed(text)
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
            return "#" + FG_BG_256[palette[color]]
        if len(color) == 6:
            return "#" + color
        return default

    def _render_ssh_screen(self):
        screen = self.ssh_screen
        if screen is None:
            return
        terminal_widget = self.terminal_output._textbox
        if len(self.ssh_rendered_rows) != screen.lines:
            terminal_widget.delete("1.0", "end")
            terminal_widget.insert("1.0", "\n" * (screen.lines - 1))
            self.ssh_rendered_rows = [None] * screen.lines
        for row_index in range(screen.lines):
            row = screen.buffer[row_index]
            rendered_row = []
            for column in range(screen.columns):
                cell = row[column]
                style = (
                    cell.fg, cell.bg, cell.bold, cell.italics,
                    cell.underscore, cell.reverse,
                )
                if cell.data == "":
                    continue  # segunda célula de caractere largo
                rendered_row.append((cell.data, style))
            signature = tuple(rendered_row)
            if self.ssh_rendered_rows[row_index] == signature:
                continue

            line = row_index + 1
            terminal_widget.delete(f"{line}.0", f"{line}.end")
            current_style = None
            run = []
            run_start = 0
            for cell_text, style in signature:
                if current_style is not None and style != current_style:
                    self._insert_ssh_run(
                        terminal_widget, run, current_style, f"{line}.{run_start}",
                    )
                    run_start += len(run)
                    run = []
                current_style = style
                run.append(cell_text)
            if run:
                self._insert_ssh_run(
                    terminal_widget, run, current_style, f"{line}.{run_start}",
                )
            self.ssh_rendered_rows[row_index] = signature

        cursor = screen.cursor
        terminal_widget.mark_set("insert", f"{cursor.y + 1}.{cursor.x}")
        terminal_widget.see(f"{cursor.y + 1}.{cursor.x}")
        self.ssh_screen_dirty = False

    def _insert_ssh_run(self, terminal_widget, run, style, index="end"):
        tag = self.ssh_style_tags.get(style)
        if tag is None:
            tag = f"ssh_style_{len(self.ssh_style_tags)}"
            self.ssh_style_tags[style] = tag
            foreground = self._ssh_color(style[0], "#d6d6d6")
            background = self._ssh_color(style[1], "#101214")
            if style[5]:
                foreground, background = background, foreground
            font_style = (
                self.terminal_font_family, 13, "bold" if style[2] else "normal",
                "italic" if style[3] else "roman",
            )
            terminal_widget.tag_configure(
                tag, foreground=foreground, background=background,
                font=font_style, underline=style[4],
            )
        terminal_widget.insert(index, "".join(run), (tag,))

    def _ssh_channel_closed(self, generation):
        if generation != self.ssh_generation:
            return
        self.ssh_channel = None
        client, self.ssh_client = self.ssh_client, None
        if client:
            client.close()
        self._clear_terminal()
        self._update_ssh_toggle_button()
        self.ssh_status.set("Conexão SSH encerrada.")

    def _disconnect_ssh(self):
        self._close_ssh()

    def _clear_terminal(self):
        self.ssh_screen = None
        self.ssh_stream = None
        self.ssh_decoder = None
        self.ssh_rendered_rows = []
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
        self._update_ssh_toggle_button()
        if update_status and hasattr(self, "ssh_status"):
            self.ssh_status.set("Desconectado do SSH.")

    def _build_login_page(self, page):
        self.login_panel = self._section(page, "Acesso ao servidor SMB")
        self.login_panel.pack(fill="x", padx=8, pady=8)
        login = ctk.CTkFrame(self.login_panel, fg_color="transparent")
        login.pack(fill="x", padx=8, pady=(0, 8))
        self.login_theme_button = self._accent_button(
            login, width=120, height=34, corner_radius=6, command=self._toggle_theme,
        )
        self.login_theme_button.grid(row=0, column=4, padx=8, pady=6, sticky="e")
        self.host_var = tk.StringVar()
        self.host_cb = ctk.CTkComboBox(
            login,
            variable=self.host_var,
            values=[],
            width=240,
            state="readonly",
            fg_color=THEME_PALETTE["input"],
            text_color=THEME_PALETTE["text"],
            text_color_disabled=THEME_PALETTE["muted"],
            button_color=THEME_PALETTE["accent"],
            button_hover_color=THEME_PALETTE["accent_hover"],
            dropdown_fg_color=THEME_PALETTE["panel"],
            dropdown_hover_color=THEME_PALETTE["hover"],
            dropdown_text_color=THEME_PALETTE["text"],
        )
        ctk.CTkLabel(login, text="🖥 Servidor").grid(row=1, column=0, padx=8, pady=6, sticky="e")
        self.host_cb.grid(row=1, column=1, padx=4, pady=6)
        self._accent_button(login, text="⌕ Procurar na rede", command=self.scan).grid(
            row=1, column=2, padx=4, pady=6,
        )
        self.user = tk.StringVar()
        self.pwd = tk.StringVar()
        ctk.CTkLabel(login, text="♟ Usuário").grid(row=2, column=0, padx=8, pady=6, sticky="e")
        ctk.CTkEntry(
            login, textvariable=self.user, width=220,
            fg_color=THEME_PALETTE["input"],
            text_color=THEME_PALETTE["text"],
            placeholder_text_color=THEME_PALETTE["muted"],
        ).grid(row=2, column=1, padx=4, pady=6)
        ctk.CTkLabel(login, text="🔒 Senha").grid(row=2, column=2, padx=4, pady=6, sticky="e")
        password_entry = ctk.CTkEntry(
            login, textvariable=self.pwd, show="*", width=220,
            fg_color=THEME_PALETTE["input"],
            text_color=THEME_PALETTE["text"],
            placeholder_text_color=THEME_PALETTE["muted"],
        )
        password_entry.grid(row=2, column=3, padx=4, pady=6)
        password_entry.bind("<Return>", self._login_on_enter)
        self._accent_button(login, text="↪ Validar e conectar", command=self.connect).grid(
            row=2, column=4, padx=8, pady=6,
        )
        self.login_status = tk.StringVar(value="Selecione um servidor e informe usuário e senha.")
        self.login_status_frame = ctk.CTkFrame(page, fg_color="transparent")
        self.login_status_frame.pack(fill="x", padx=12, pady=4)
        VariableLabel(self.login_status_frame, self.login_status).pack(side="left", anchor="w")
    def _build_system_page(self, page):
        content = ctk.CTkScrollableFrame(page, fg_color=THEME_PALETTE["background"])
        content.pack(fill="both", expand=True, padx=8, pady=4)
        summary = self._section(content, "Resumo do servidor")
        summary.pack(fill="x", padx=2, pady=(0, 4))
        controls = ctk.CTkFrame(summary, fg_color="transparent")
        controls.pack(fill="x", padx=8, pady=(0, 4))
        ctk.CTkLabel(controls, text="⟳ Atualizar a cada").pack(side="left", padx=(0, 4))
        self.status_interval_var = tk.StringVar(
            value=str(self.saved_settings.get("status_interval_seconds", 5)),
        )
        if self.status_interval_var.get() not in {"1", "5", "10", "20", "30", "60", "120", "300"}:
            self.status_interval_var.set("5")
        ctk.CTkOptionMenu(
            controls,
            values=["1", "5", "10", "20", "30", "60", "120", "300"],
            variable=self.status_interval_var,
            width=90,
            fg_color=THEME_PALETTE["accent"],
            button_color=THEME_PALETTE["accent"],
            button_hover_color=THEME_PALETTE["accent_hover"],
            text_color=THEME_PALETTE["selected_text"],
            dropdown_fg_color=THEME_PALETTE["panel"],
            dropdown_hover_color=THEME_PALETTE["hover"],
            dropdown_text_color=THEME_PALETTE["text"],
            command=self._status_interval_changed,
        ).pack(side="left", padx=4)
        ctk.CTkLabel(controls, text="segundos").pack(side="left", padx=(0, 8))
        self._accent_button(controls, text="⟳ Atualizar agora", command=self.refresh_server_status).pack(side="left", padx=4)

        self.server_overview = tk.StringVar(value="Aguardando autenticação.")
        VariableLabel(summary, self.server_overview).pack(anchor="w", padx=12, pady=(0, 6))
        metrics = ctk.CTkFrame(summary, fg_color="transparent")
        metrics.pack(fill="x", padx=8, pady=(0, 8))
        self.metric_rings = {
            "cpu": MetricRing(metrics, "⚙ CPU", THEME_PALETTE["panel"]),
            "memory": MetricRing(metrics, "▤ Memória", THEME_PALETTE["panel"]),
        }
        self.metric_rings["cpu"].grid(row=0, column=0, sticky="nsew", padx=3, pady=3)
        self.cpu_details_panel = self._section(metrics, "CPU")
        self.cpu_details_panel.grid(row=0, column=1, sticky="nsew", padx=3, pady=3)
        self.cpu_vars = {
            "frequency": tk.StringVar(value="n/d"),
            "temperature": tk.StringVar(value="n/d"),
        }
        for row, (title, key) in enumerate((
            ("⚡ Frequência", "frequency"),
            ("🌡 Temperatura", "temperature"),
        )):
            detail_row = ctk.CTkFrame(self.cpu_details_panel, fg_color="transparent")
            detail_row.pack(fill="x", padx=10, pady=4)
            ctk.CTkLabel(detail_row, text=title, anchor="w").pack(side="left")
            VariableLabel(
                detail_row, self.cpu_vars[key], anchor="w",
                font=ctk.CTkFont(size=12, weight="bold"),
            ).pack(side="left", padx=(6, 0))
        self.metric_rings["memory"].grid(row=0, column=2, sticky="nsew", padx=3, pady=3)
        self.metric_vars = {
            "uptime": tk.StringVar(value="--"),
            "network": tk.StringVar(value="--"),
        }
        for column, (title, key) in enumerate((
            ("◷ Tempo ativo", "uptime"),
            ("↔ Rede", "network"),
        ), start=3):
            panel = self._section(metrics, title)
            panel.configure(width=132 if key == "uptime" else 154)
            panel.grid_propagate(False)
            panel.grid(row=0, column=column, sticky="nsew", padx=3, pady=3)
            VariableLabel(
                panel, self.metric_vars[key], anchor="nw",
                font=ctk.CTkFont(size=12, weight="bold"),
            ).pack(
                fill="both", expand=True, padx=10, pady=(4, 6),
            )
        for column in range(3):
            metrics.columnconfigure(column, weight=0, minsize=176)
        metrics.columnconfigure(3, weight=0, minsize=132)
        metrics.columnconfigure(4, weight=0, minsize=154)

        identity = self._section(content, "Sistema e hardware")
        identity.pack(fill="x", padx=2, pady=(6, 4))
        identity_grid = ctk.CTkFrame(identity, fg_color="transparent")
        identity_grid.pack(fill="x", padx=8, pady=(0, 8))
        system_fields = (
            ("🖥 Sistema operacional", "os_name"),
            ("⚙ Kernel", "kernel"),
            ("▣ Modelo do computador", "computer_model"),
            ("⚡ Processador", "cpu_model"),
            ("◉ Núcleos físicos/lógicos", "cpu_cores"),
            ("▰ GPU", "gpu_models"),
            ("▤ Pacotes instalados", "packages"),
            ("Aa Fonte padrão", "font"),
            (">_ Shell", "shell"),
        )
        self.system_info_vars = {key: tk.StringVar(value="Indisponível") for _, key in system_fields}
        for index, (label, key) in enumerate(system_fields):
            row, pair = divmod(index, 2)
            column = pair * 2
            # normaliza o espaçamento e largura das labels de acordo com a coluna {
            if column == 0:
                padx = (4, 8)
                width = 170
            else:
                padx = (75, 8)
                width = 120
            # }
            ctk.CTkLabel(identity_grid, text=label, anchor="w", width=width).grid(
                row=row, column=column, sticky="nw", padx=padx, pady=4,
            )
            VariableLabel(
                identity_grid, self.system_info_vars[key], anchor="w", wraplength=300,
            ).grid(row=row, column=column + 1, sticky="new", padx=(0, 4), pady=4)
        identity_grid.columnconfigure(1, weight=0)
        identity_grid.columnconfigure(2, weight=0)
        identity_grid.columnconfigure(3, weight=0)

    def _build_disks_page(self, page):
        content = ctk.CTkScrollableFrame(
            page,
            fg_color=THEME_PALETTE["background"],
            scrollbar_fg_color=THEME_PALETTE["background"],
            scrollbar_button_color=THEME_PALETTE["border"],
            scrollbar_button_hover_color=THEME_PALETTE["accent"],
        )
        content.pack(fill="both", expand=True, padx=8, pady=4)
        self.disk_overview = tk.StringVar(value="Aguardando dados do servidor.")
        VariableLabel(content, self.disk_overview).pack(anchor="w", padx=12, pady=(8, 4))

        storage = ctk.CTkFrame(content, fg_color="transparent")
        storage.pack(fill="x", padx=8, pady=4)
        filesystems = self._section(storage, "▤ Sistemas de arquivos montados")
        filesystems.pack(fill="x", pady=(0, 4))
        self.filesystem_bars_container = ctk.CTkFrame(filesystems, fg_color="transparent")
        self.filesystem_bars_container.pack(fill="x", padx=8, pady=(0, 8))
        filesystem_header = ctk.CTkFrame(
            self.filesystem_bars_container, fg_color="transparent",
        )
        filesystem_header.pack(fill="x", padx=2, pady=(0, 4))
        for column, weight, minsize in (
            (0, 2, 220), (1, 2, 175), (2, 1, 110), (3, 0, 174), (4, 0, 210),
        ):
            filesystem_header.grid_columnconfigure(column, weight=weight, minsize=minsize)
        header_paddings = ((4, 12), (0, 12), (0, 12), (0, 12), (0, 4))
        for column, (title, anchor) in enumerate((
            ("⌂ Montagem", "w"),
            ("▣ Dispositivo", "w"),
            ("▤ Formato", "w"),
            ("◴ Ocupação", "w"),
            ("◫ Espaço livre", "e"),
        )):
            ctk.CTkLabel(
                filesystem_header,
                text=title,
                anchor=anchor,
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=THEME_PALETTE["muted"],
            ).grid(
                row=0,
                column=column,
                sticky="ew",
                padx=header_paddings[column],
            )
        self.filesystem_rows = {}
        self.filesystem_progress_bars = []
        self.filesystem_empty_label = None

        physical_disks = self._section(storage, "Discos físicos e SMART")
        physical_disks.pack(fill="x", pady=(4, 0))
        self.physical_disk_grid = ctk.CTkFrame(physical_disks, fg_color="transparent")
        self.physical_disk_grid.pack(fill="x", padx=8, pady=(0, 8))
        for column, weight, minsize in (
            (0, 1, 150), (1, 3, 220), (2, 1, 150), (3, 1, 150), (4, 1, 140),
        ):
            self.physical_disk_grid.grid_columnconfigure(column, weight=weight, minsize=minsize)
        for column, title in enumerate(("▣ Dispositivo", "▤ Modelo", "◫ Capacidade", "🌡 Temperatura", "✓ SMART")):
            ctk.CTkLabel(
                self.physical_disk_grid,
                text=title,
                anchor="w",
                text_color=THEME_PALETTE["muted"],
            ).grid(row=0, column=column, sticky="ew", padx=(4, 12), pady=(0, 4))
        self.physical_disk_rows = {}
        self.physical_disk_empty_label = None
        self.storage_status = tk.StringVar(value="Aguardando métricas de armazenamento.")
        VariableLabel(storage, self.storage_status).pack(anchor="w", padx=8, pady=(4, 2))

    def _build_dns_dhcp_page(self, page):
        content = ctk.CTkScrollableFrame(page, fg_color=THEME_PALETTE["background"])
        content.pack(fill="both", expand=True, padx=8, pady=4)
        status_panel = self._section(content, "◉ Estado do serviço")
        status_panel.pack(fill="x", padx=2, pady=(0, 4))
        self.dns_dhcp_status = tk.StringVar(value="Aguardando dados do servidor.")
        VariableLabel(status_panel, self.dns_dhcp_status).pack(
            anchor="w", padx=12, pady=(2, 8),
        )

        leases_panel = self._section(content, "▤ Concessões DHCP ativas")
        leases_panel.pack(fill="x", padx=2, pady=4)
        self.dhcp_leases_header = ctk.CTkFrame(
            leases_panel, fg_color="transparent",
        )
        self.dhcp_leases_header.pack(fill="x", padx=8, pady=(2, 0))
        self.dhcp_leases_rows = ctk.CTkScrollableFrame(
            leases_panel, height=150, fg_color="transparent", corner_radius=0,
        )
        self.dhcp_leases_rows.pack(fill="x", padx=8, pady=(0, 8))
        self.dhcp_lease_columns = ((1, 150), (2, 180), (1, 170), (2, 210))
        for column, (weight, minsize) in enumerate(self.dhcp_lease_columns):
            self.dhcp_leases_header.grid_columnconfigure(
                column, weight=weight, minsize=minsize,
            )
            self.dhcp_leases_rows.grid_columnconfigure(
                column, weight=weight, minsize=minsize,
            )
        for column, title in enumerate(("IP", "Hostname", "MAC", "Expira (UTC)")):
            ctk.CTkLabel(
                self.dhcp_leases_header,
                text=title,
                anchor="w",
                text_color=THEME_PALETTE["muted"],
            ).grid(row=0, column=column, sticky="ew", padx=(4, 12), pady=(0, 4))
        self.dhcp_lease_rendered = None

        events_grid = ctk.CTkFrame(content, fg_color="transparent")
        events_grid.pack(fill="both", expand=True, padx=2, pady=4)
        events_grid.grid_columnconfigure(0, weight=1)
        events_grid.grid_columnconfigure(1, weight=1)
        allowed_panel = self._section(events_grid, "✓ Acessos liberados")
        allowed_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 3))
        self.dns_allowed_text = ctk.CTkTextbox(
            allowed_panel, height=240, wrap="word", state="disabled",
        )
        self.dns_allowed_text.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        blocked_panel = self._section(events_grid, "⊘ Acessos bloqueados")
        blocked_panel.grid(row=0, column=1, sticky="nsew", padx=(3, 0))
        self.dns_blocked_text = ctk.CTkTextbox(
            blocked_panel, height=240, wrap="word", state="disabled",
        )
        self.dns_blocked_text.pack(fill="both", expand=True, padx=8, pady=(2, 8))

    def _build_internet_page(self, page):
        content = ctk.CTkScrollableFrame(page, fg_color=THEME_PALETTE["background"])
        content.pack(fill="both", expand=True, padx=8, pady=4)
        addresses = self._section(content, "🌐 Endereços IP")
        addresses.pack(fill="x", padx=2, pady=(0, 6))
        address_grid = ctk.CTkFrame(addresses, fg_color="transparent")
        address_grid.pack(fill="x", padx=8, pady=(0, 8))
        self.internet_local_ip = tk.StringVar(value="Indisponível")
        self.internet_remote_ip = tk.StringVar(value="Indisponível")
        for column, (label, variable) in enumerate((
            ("⌂ IP local (NAS)", self.internet_local_ip),
            ("↗ IP remoto (público)", self.internet_remote_ip),
        )):
            ctk.CTkLabel(address_grid, text=label, anchor="w").grid(
                row=0, column=column * 2, sticky="w", padx=(4, 8), pady=4,
            )
            ctk.CTkEntry(
                address_grid, textvariable=variable, state="disabled",
                fg_color=THEME_PALETTE["input"],
            ).grid(row=0, column=column * 2 + 1, sticky="ew", padx=(0, 16), pady=4)
            address_grid.columnconfigure(column * 2 + 1, weight=1)

        history = self._section(content, "⚡ Testes de velocidade recentes")
        history.pack(fill="both", expand=True, padx=2, pady=4)
        self.internet_status = tk.StringVar(value="Aguardando dados do servidor.")
        VariableLabel(history, self.internet_status).pack(
            anchor="w", padx=12, pady=(2, 6),
        )
        self.internet_tests_text = ctk.CTkTextbox(
            history, height=340, wrap="none", state="disabled",
        )
        self.internet_tests_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _build_shares_page(self, page):
        self.samba_share_panel = self._section(page, "Compartilhamentos Samba")
        self.samba_share_panel.pack(fill="both", expand=True, padx=8, pady=4)
        self.samba_share_status = tk.StringVar(value="Aguardando dados do servidor.")
        share_toolbar = ctk.CTkFrame(self.samba_share_panel, fg_color="transparent")
        share_toolbar.pack(fill="x", padx=12, pady=(2, 4))
        VariableLabel(share_toolbar, self.samba_share_status).pack(
            side="left", fill="x", expand=True, anchor="w",
        )
        self._accent_button(
            share_toolbar,
            text="📂 Abrir Compartilhamentos",
            command=self._open_server_shares,
        ).pack(side="right", padx=(8, 0))
        self.samba_share_grid = ctk.CTkFrame(self.samba_share_panel, fg_color="transparent")
        self.samba_share_grid.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.samba_share_column_specs = (
            (1, 150), (2, 220), (3, 260), (1, 170), (2, 220),
        )
        self.samba_share_header = ctk.CTkFrame(
            self.samba_share_grid, fg_color="transparent",
        )
        self.samba_share_header.pack(fill="x", padx=8)
        self.samba_share_rows_container = ctk.CTkScrollableFrame(
            self.samba_share_grid, fg_color="transparent", corner_radius=0,
        )
        self.samba_share_rows_container.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        for column, (weight, minsize) in enumerate(self.samba_share_column_specs):
            self.samba_share_header.grid_columnconfigure(
                column, weight=weight, minsize=minsize,
            )
            self.samba_share_rows_container.grid_columnconfigure(
                column, weight=weight, minsize=minsize,
            )
        for column, title in enumerate((
            "▣ Compartilhamento", "⌂ Caminho", "♟ Usuários/grupos permitidos",
            "◉ Somente leitura", "✎ Leitura e escrita",
        )):
            ctk.CTkLabel(
                self.samba_share_header,
                text=title,
                anchor="w",
                text_color=THEME_PALETTE["muted"],
            ).grid(row=0, column=column, sticky="ew", padx=(4, 12), pady=(0, 4))
        self.samba_share_rows = {}
        self.samba_share_empty_label = None

    def _open_server_shares(self):
        if not self.authenticated or self.remote is None:
            show_info_dialog(
                self,
                "Compartilhamentos",
                "Faça login no servidor antes de abrir as pastas compartilhadas.",
            )
            return

        host = self.remote.host
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer.exe", f"\\\\{host}"])
            else:
                command = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.Popen([command, f"smb://{host}"])
        except OSError as error:
            show_info_dialog(
                self,
                "Não foi possível abrir",
                f"O explorador de arquivos não conseguiu abrir as pastas de {host}.\n{error}",
            )

    # ---- util
    def say(self, msg):
        self.q.put(("log", msg))

    def _pump(self):
        max_items = 100
        processed = 0
        latest_progress = None
        latest_progress_phase = None
        latest_progress_value = None
        while processed < max_items:
            try:
                kind, val = self.q.get_nowait()
            except queue.Empty:
                break
            processed += 1
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
                latest_progress = val
                latest_progress_phase = None
                latest_progress_value = None
            elif kind == "progress_phase":
                latest_progress_phase = val
                latest_progress = None
                latest_progress_value = None
            elif kind == "progress_value":
                latest_progress_value = val
                latest_progress = None
                latest_progress_phase = None
            elif kind == "ssh_data":
                generation, data = val
                self._feed_ssh_output(generation, data)
            elif kind == "call":
                val()
        if latest_progress_value is not None:
            progress, label = latest_progress_value
            self.bar.set(progress)
            self.bar_lbl.configure(text=label)
        elif latest_progress_phase is not None:
            self.bar.set(0)
            self.bar_lbl.configure(text=f"⏳ {latest_progress_phase}")
        elif latest_progress is not None:
            done, total, *unit = latest_progress
            progress = done / total if total else 1
            self.bar.set(progress)
            label = unit[0] if unit else "arquivos"
            self.bar_lbl.configure(text=f"⏳ {progress:.0%} ({done}/{total} {label})")
        if self.ssh_screen_dirty:
            self._render_ssh_screen()
        delay = 1 if processed == max_items else (20 if self.ssh_channel else 100)
        self.after(delay, self._pump)

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
        remote = self.remote
        if remote is None:
            return
        self.login_page.pack_forget()
        if not self.main_page.winfo_manager():
            self.main_page.pack(fill="both", expand=True)
        self.session_display.set(f"{self.authenticated_user} @ {remote.host}")
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
        self.status_share = MONITOR_SHARE
        shares = hide_monitor_share(shares)
        self.path_lbl.configure(text=f"\\\\{remote.host}\\")
        self._update_dest_label()
        self._fill([("share", share) for share in shares])
        if shares:
            self.say(f"Conectado a {remote.host}. Clique em um compartilhamento para abrir.")
        else:
            self.say(f"Conectado a {remote.host}, mas nenhum compartilhamento de disco foi encontrado.")
        self._request_server_status(generation)

    def _reset_server_dashboard(self, message):
        self.server_overview.set(message)
        self.disk_overview.set(message)
        self.storage_status.set(message)
        for value in self.metric_vars.values():
            value.set("--")
        for value in self.cpu_vars.values():
            value.set("n/d")
        for value in self.system_info_vars.values():
            value.set("Indisponível")
        for ring in self.metric_rings.values():
            ring.set_value(None, message, self.theme_colors)
        self._render_system_filesystems([])
        self._render_samba_shares([])
        self._render_physical_disks([])
        self.dns_dhcp_status.set(message)
        self.dhcp_lease_rendered = None
        self._render_dhcp_leases([])
        self._set_textbox_content(self.dns_allowed_text, "")
        self._set_textbox_content(self.dns_blocked_text, "")
        self.internet_local_ip.set("Indisponível")
        self.internet_remote_ip.set("Indisponível")
        self.internet_status.set(message)
        self.__dict__.setdefault("_render_cache", {}).pop("internet", None)
        self._set_textbox_content(self.internet_tests_text, message)

    @staticmethod
    def _set_textbox_content(textbox, content):
        if textbox.get("1.0", "end-1c") == content:
            return
        textbox.configure(state="normal")
        textbox.delete("1.0", "end")
        if content:
            textbox.insert("end", content)
        textbox.configure(state="disabled")

    def _unchanged(self, key, data):
        # Evita reconstruir/relayout de widgets quando os dados são idênticos aos da última renderização.
        cache = self.__dict__.setdefault("_render_cache", {})
        signature = json.dumps(data, sort_keys=True, default=str)
        if cache.get(key) == signature:
            return True
        cache[key] = signature
        return False

    def _render_system_filesystems(self, filesystems):
        if self._unchanged("filesystems", filesystems):
            return
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
                text_color=THEME_PALETTE["muted"],
            )
            self.filesystem_empty_label.pack(anchor="w", padx=4, pady=4)
            return
        for mountpoint, filesystem in current_mounts.items():
            row_widgets = self.filesystem_rows.get(mountpoint)
            if row_widgets is None:
                row = ctk.CTkFrame(self.filesystem_bars_container, fg_color="transparent")
                row.pack(fill="x", padx=2, pady=3)
                for column, weight, minsize in (
                    (0, 2, 220), (1, 2, 175), (2, 1, 110), (3, 0, 174), (4, 0, 210),
                ):
                    row.grid_columnconfigure(column, weight=weight, minsize=minsize)
                mountpoint_label = ctk.CTkLabel(row, anchor="w")
                mountpoint_label.grid(row=0, column=0, sticky="ew", padx=(4, 12))
                device_label = ctk.CTkLabel(row, anchor="w")
                device_label.grid(row=0, column=1, sticky="ew", padx=(0, 12))
                filesystem_label = ctk.CTkLabel(row, anchor="w")
                filesystem_label.grid(row=0, column=2, sticky="ew", padx=(0, 12))
                bar_frame = ctk.CTkFrame(
                    row,
                    width=174,
                    height=18,
                    fg_color="transparent",
                    border_width=1,
                    border_color=self.theme_colors["border"],
                )
                bar_frame.grid(row=0, column=3, sticky="w", padx=(0, 12))
                bar_frame.grid_propagate(False)
                progress = ctk.CTkProgressBar(
                    bar_frame,
                    width=162,
                    height=10,
                    fg_color=self.theme_colors["panel"],
                    progress_color=self.theme_colors["accent"],
                )
                progress.pack(fill="both", expand=True, padx=5, pady=3)
                usage_label = ctk.CTkLabel(row, anchor="e")
                usage_label.grid(row=0, column=4, sticky="e", padx=(0, 4))
                row_widgets = {
                    "frame": row,
                    "mountpoint": mountpoint_label,
                    "device": device_label,
                    "filesystem": filesystem_label,
                    "bar_frame": bar_frame,
                    "progress": progress,
                    "usage": usage_label,
                }
                self.filesystem_rows[mountpoint] = row_widgets

            percent = float(filesystem["percent"])
            row_widgets["mountpoint"].configure(text=mountpoint)
            row_widgets["device"].configure(text=filesystem["device"])
            row_widgets["filesystem"].configure(text=filesystem["filesystem"])
            row_widgets["progress"].set(max(0.0, min(1.0, percent / 100)))
            row_widgets["usage"].configure(text=(
                f"{percent:.0f}% · {self._format_bytes(filesystem['free_bytes'])} livre"
            ))
        self.filesystem_progress_bars = [
            row_widgets["progress"] for row_widgets in self.filesystem_rows.values()
        ]

    def _render_physical_disks(self, disks):
        if self._unchanged("disks", disks):
            return
        current_disks = {
            disk.get("name", str(index)): disk for index, disk in enumerate(disks)
        }
        for device in set(self.physical_disk_rows) - set(current_disks):
            row_widgets = self.physical_disk_rows.pop(device)
            for label in row_widgets["labels"]:
                label.destroy()
            row_widgets["divider"].destroy()

        if not current_disks:
            if self.physical_disk_empty_label is None:
                self.physical_disk_empty_label = ctk.CTkLabel(
                    self.physical_disk_grid,
                    text="Nenhum disco físico disponível.",
                    anchor="w",
                    text_color=THEME_PALETTE["muted"],
                )
                self.physical_disk_empty_label.grid(
                    row=1, column=0, columnspan=5, sticky="ew", padx=4, pady=4,
                )
            return
        if self.physical_disk_empty_label is not None:
            self.physical_disk_empty_label.destroy()
            self.physical_disk_empty_label = None

        for index, (device, disk) in enumerate(current_disks.items()):
            row = index * 2 + 1
            temperature = disk.get("temperature_c")
            values = (
                disk["name"],
                disk.get("model") or "--",
                self._format_bytes(disk["capacity_bytes"]) if disk["capacity_bytes"] else "--",
                f"{temperature} °C" if temperature is not None else "Indisponível",
                disk["smart_status"],
            )
            row_widgets = self.physical_disk_rows.get(device)
            if row_widgets is None:
                labels = [
                    ctk.CTkLabel(self.physical_disk_grid, text="", anchor="w")
                    for _ in values
                ]
                divider = ctk.CTkFrame(
                    self.physical_disk_grid,
                    height=1,
                    fg_color=THEME_PALETTE["border"],
                )
                row_widgets = {"labels": labels, "divider": divider}
                self.physical_disk_rows[device] = row_widgets
            for column, (label, value) in enumerate(zip(row_widgets["labels"], values)):
                if label.cget("text") != value:
                    label.configure(text=value)
                label.grid(row=row, column=column, sticky="ew", padx=(4, 12), pady=5)
            if index < len(disks) - 1:
                row_widgets["divider"].grid(
                    row=row + 1, column=0, columnspan=5, sticky="ew", padx=4,
                )
            else:
                row_widgets["divider"].grid_remove()

    def _render_samba_shares(self, shares):
        if shares:
            shares = [
                share for share in shares
                if str(share.get("browseable", "")).strip().casefold() not in ("no", "false", "0")
            ]
        if self._unchanged("shares", shares):
            return
        empty_message = "Nenhum compartilhamento Samba disponível."
        if shares is None:
            current_shares = {}
            self.samba_share_status.set(
                "Atualize o agente do servidor para publicar as permissões dos compartilhamentos."
            )
            empty_message = "Permissões Samba indisponíveis."
        elif not shares:
            current_shares = {}
            self.samba_share_status.set("Nenhum compartilhamento Samba publicado pelo servidor.")
            empty_message = "Nenhum compartilhamento Samba disponível."
        else:
            current_shares = {
                share.get("name", "--"): share
                for share in sorted(shares, key=lambda item: item.get("name", "").casefold())
            }
            self.samba_share_status.set(
                f"{len(shares)} compartilhamentos configurados no Samba."
            )

        for name in set(self.samba_share_rows) - set(current_shares):
            row_widgets = self.samba_share_rows.pop(name)
            for label in row_widgets["labels"]:
                label.destroy()
            row_widgets["divider"].destroy()

        if not current_shares:
            if self.samba_share_empty_label is None:
                self.samba_share_empty_label = ctk.CTkLabel(
                    self.samba_share_rows_container,
                    text=empty_message,
                    anchor="w",
                    text_color=THEME_PALETTE["muted"],
                )
                self.samba_share_empty_label.grid(
                    row=0, column=0, columnspan=5, sticky="ew", padx=4, pady=4,
                )
            elif self.samba_share_empty_label.cget("text") != empty_message:
                self.samba_share_empty_label.configure(text=empty_message)
            return
        if self.samba_share_empty_label is not None:
            self.samba_share_empty_label.destroy()
            self.samba_share_empty_label = None

        def rule(value, default):
            return str(value).strip() or default

        def yes_no(value):
            value = str(value).strip().casefold()
            if value in ("yes", "true", "1"):
                return "Sim"
            if value in ("no", "false", "0"):
                return "Não"
            return "Padrão Samba"

        for index, (name, share) in enumerate(current_shares.items()):
            guest_allowed = str(share.get("guest_ok", "")).strip().casefold() in (
                "yes", "true", "1",
            )
            read_only = yes_no(share.get("read_only"))
            write_list = str(share.get("write_list") or "").strip()
            if read_only == "Não":
                write_access = "Todos"
            elif read_only == "Sim" and not write_list:
                write_access = "Ninguém"
            else:
                write_access = rule(write_list, "Ninguém (padrão Samba)")
            values = (
                share.get("name", "--"),
                share.get("path") or "--",
                "guest" if guest_allowed else rule(
                    share.get("valid_users"), "Sem restrição por lista (padrão Samba)"
                ),
                read_only,
                write_access,
            )
            row = index * 2
            row_widgets = self.samba_share_rows.get(name)
            if row_widgets is None:
                labels = [
                    ctk.CTkLabel(
                        self.samba_share_rows_container,
                        text="",
                        anchor="w",
                        wraplength=self.samba_share_column_specs[column][1],
                    )
                    for column in range(len(values))
                ]
                divider = ctk.CTkFrame(
                    self.samba_share_rows_container,
                    height=1,
                    fg_color=THEME_PALETTE["border"],
                )
                row_widgets = {"labels": labels, "divider": divider}
                self.samba_share_rows[name] = row_widgets
            for column, (label, value) in enumerate(zip(row_widgets["labels"], values)):
                if label.cget("text") != value:
                    label.configure(text=value)
                label.grid(row=row, column=column, sticky="ew", padx=(4, 12), pady=5)
            if index < len(current_shares) - 1:
                row_widgets["divider"].grid(
                    row=row + 1, column=0, columnspan=5, sticky="ew", padx=4,
                )
            else:
                row_widgets["divider"].grid_remove()

    def refresh_server_status(self, _event=None):
        self._cancel_server_status_timer()
        self._request_server_status(self.status_generation)

    def _status_interval_changed(self, _value=None):
        self._cancel_server_status_timer()
        self._save_settings()
        if self.authenticated and self.remote and self.status_share:
            if self.status_polling_generation != self.status_generation:
                self._schedule_server_status_poll(self.status_generation)

    def _cancel_server_status_timer(self):
        if self.status_after_id is not None:
            try:
                self.after_cancel(self.status_after_id)
            except tk.TclError:
                pass
            self.status_after_id = None

    def _status_interval_ms(self):
        return self._status_interval_seconds() * 1000

    def _status_interval_seconds(self):
        try:
            seconds = int(self.status_interval_var.get())
        except (AttributeError, tk.TclError, ValueError):
            seconds = 5
        return max(1, min(300, seconds))

    def _schedule_server_status_poll(self, generation):
        if (
            generation != self.status_generation
            or not self.authenticated
            or not self.remote
            or not self.status_share
            or self.status_polling_generation == generation
        ):
            return
        self._cancel_server_status_timer()
        self.status_after_id = self.after(
            self._status_interval_ms(),
            lambda: self._request_server_status(generation),
        )

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
                server_available = True
            except Exception as exc:
                data = None
                error = str(exc)
                server_available = remote.is_available()
            self.ui(lambda: self._server_status_received(
                generation, remote, share, data, error, server_available,
            ))

        threading.Thread(target=run, daemon=True).start()

    def _server_status_received(self, generation, remote, share, data, error, server_available):
        if generation != self.status_generation or remote is not self.remote:
            return
        self.status_polling_generation = None
        if share.casefold() != (self.status_share or "").casefold():
            self._request_server_status(generation)
            return
        if error:
            if not server_available:
                self.say(f"Conexão com {remote.host} perdida; encerrando a sessão.")
                self.disconnect()
                return
            self._reset_server_dashboard(
                f"Sem dados do agente em {share}: {error}",
            )
        else:
            self._render_server_status(data)
        self._schedule_server_status_poll(generation)

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
                "font": system_info.get("font", "Indisponível"),
                "shell": system_info.get("shell", "Indisponível"),
            }
            for key, value in system_values.items():
                if self.system_info_vars[key].get() != str(value):
                    self.system_info_vars[key].set(str(value))

            cpu_percent = float(data["cpu_percent"])
            cpu_count = int(data["cpu_count"])
            self.metric_rings["cpu"].set_value(
                cpu_percent, f"{cpu_count} núcleos", self.theme_colors,
            )
            frequency_mhz = data.get("cpu_frequency_mhz")
            if isinstance(frequency_mhz, (int, float)) and frequency_mhz > 0:
                frequency_text = (
                    f"{frequency_mhz / 1000:.2f} GHz"
                    if frequency_mhz >= 1000 else f"{frequency_mhz:.0f} MHz"
                )
            else:
                frequency_text = "n/d"
            temperature_c = data.get("cpu_temperature_c")
            temperature_text = (
                f"{temperature_c:.1f} °C"
                if isinstance(temperature_c, (int, float)) else "n/d"
            )
            self.cpu_vars["frequency"].set(frequency_text)
            self.cpu_vars["temperature"].set(temperature_text)
            self.metric_rings["memory"].set_value(
                memory["percent"],
                f"{self._format_bytes(memory['used_bytes'])} de "
                f"{self._format_bytes(memory['total_bytes'])}",
                self.theme_colors,
            )
            self.metric_vars["uptime"].set(self._format_duration(data["uptime_seconds"]))
            self.metric_vars["network"].set(
                f"↓ {self._format_bytes(network['bytes_received'])}\n"
                f"↑ {self._format_bytes(network['bytes_sent'])}",
            )
            overview = (
                f"{data['hostname']} · {system_values['os_name']} · "
                f"amostra {data['sampled_at']} UTC"
            )
            self.server_overview.set(overview)
            self.disk_overview.set(overview)
            self._render_system_filesystems(data.get("filesystems", []))
            self._render_samba_shares(data.get("samba_shares"))
            self._render_physical_disks(data["physical_disks"])
            self._render_dns_dhcp(data.get("dns_dhcp", {}))
            self._render_internet_status(data.get("internet", {}))
            status_notes = []
            if data.get("share_discovery_error"):
                status_notes.append(data["share_discovery_error"])
            if data.get("smartctl_error"):
                status_notes.append(data["smartctl_error"])
            self.storage_status.set(" · ".join(status_notes) or "SMART consultado pelo agente do servidor.")
        except (KeyError, TypeError, ValueError) as exc:
            self._reset_server_dashboard(f"Arquivo de status inválido: {exc}")

    def _render_dns_dhcp(self, data):
        service_state = data.get("service_state", "desconhecido")
        dns_state = "ativo" if data.get("dns_enabled") else "inativo"
        dhcp_state = "ativo" if data.get("dhcp_enabled") else "inativo"
        lease_count = data.get("lease_count", 0)
        log_error = data.get("access_log_error") or data.get("journal_error")
        log_status = f" · Erro no log: {log_error}" if log_error else (
            f" · log: {data.get('access_log_path', 'indisponível')}"
        )
        self.dns_dhcp_status.set(
            f"dnsmasq: {service_state} · DNS: {dns_state} · DHCP: {dhcp_state} · "
            f"concessões: {lease_count}{log_status}"
        )

        self._render_dhcp_leases(data.get("leases", []))

        for key, textbox in (
            ("allowed_events", self.dns_allowed_text),
            ("blocked_events", self.dns_blocked_text),
        ):
            events = data.get(key, [])
            event_lines = [
                f"{event.get('timestamp', '')} [{event.get('kind', 'dns').upper()}] "
                f"{event.get('message', '')}".strip()
                for event in events
            ]
            if not event_lines:
                event_lines.append("Nenhum evento nesta categoria na última hora.")
            self._set_textbox_content(textbox, "\n".join(event_lines))

    def _render_internet_status(self, data):
        if self._unchanged("internet", data):
            return
        self.internet_local_ip.set(data.get("local_ip") or "Indisponível")
        self.internet_remote_ip.set(data.get("remote_ip") or "Indisponível")
        tests = data.get("tests") or []
        lines = []
        for entry in reversed(tests):
            timestamp = str(entry.get("timestamp", "")).replace("T", " ")
            if entry.get("error"):
                lines.append(f"{timestamp}  ERRO: {entry['error']}")
                continue
            try:
                download = float(entry["download_mbps"])
                upload = float(entry["upload_mbps"])
                ping = entry.get("ping_ms")
                ping_text = f"{float(ping):.1f} ms" if ping is not None else "n/d"
            except (KeyError, TypeError, ValueError):
                lines.append(f"{timestamp}  Registro inválido")
                continue
            server = entry.get("server") or "servidor desconhecido"
            lines.append(
                f"{timestamp}  Download {download:.2f} Mbit/s  |  "
                f"Upload {upload:.2f} Mbit/s  |  Ping {ping_text}  |  {server}"
            )
        if not lines:
            lines.append("Nenhum teste de velocidade registrado.")
        status = data.get("log_error")
        if status:
            self.internet_status.set(f"Não foi possível ler o log: {status}")
        else:
            self.internet_status.set(f"Exibindo {len(tests)} teste(s) mais recente(s).")
        self._set_textbox_content(self.internet_tests_text, "\n".join(lines))

    def _render_dhcp_leases(self, leases):
        signature = json.dumps(leases, sort_keys=True, ensure_ascii=False, default=str)
        if signature == self.dhcp_lease_rendered:
            return
        self.dhcp_lease_rendered = signature
        for widget in self.dhcp_leases_rows.winfo_children():
            widget.destroy()
        if not leases:
            ctk.CTkLabel(
                self.dhcp_leases_rows,
                text="Nenhuma concessão DHCP encontrada.",
                anchor="w",
                text_color=THEME_PALETTE["muted"],
            ).grid(row=0, column=0, columnspan=4, sticky="ew", padx=4, pady=6)
            return

        for row, lease in enumerate(leases):
            values = (
                lease.get("ip") or "?",
                lease.get("hostname") or "(sem nome)",
                lease.get("mac") or "?",
                lease.get("expires_at") or "sem expiração",
            )
            for column, value in enumerate(values):
                ctk.CTkLabel(
                    self.dhcp_leases_rows,
                    text=value,
                    anchor="w",
                ).grid(row=row, column=column, sticky="ew", padx=(4, 12), pady=3)

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
        remote = self.remote
        share = self.share
        path = self.path
        if not self.authenticated or remote is None:
            return
        if not share:
            self.show_shares_list()
            return
        location = f"\\\\{remote.host}\\{share}"
        if path:
            location += "\\" + path.replace("/", "\\")
        self.path_lbl.configure(text=location)
        self.selected_remote_entry = None
        self._update_selection_label()

        def run():
            try:
                entries = [("dir" if i.isDirectory else "file", i.filename)
                           for i in remote.list(share, path)]
                entries.sort(key=lambda e: (e[0] != "dir", e[1].lower()))
                entries.insert(0, ("up", "Voltar"))
                self.ui(lambda: self._fill(entries))
            except Exception as e:
                self.say(f"Erro ao listar: {e}")

        threading.Thread(target=run, daemon=True).start()

    def show_shares_list(self):
        remote = self.remote
        if not self.authenticated or remote is None:
            return
        self.share = None
        self.path = ""
        self.selected_remote_entry = None
        self.path_lbl.configure(text=f"\\\\{remote.host}\\")
        self._update_selection_label()

        def run():
            try:
                shares = remote.list_shares(
                    self.authenticated_user, self.authenticated_password,
                )
                shares = hide_monitor_share(shares)
                self.ui(lambda: self._fill([("share", s) for s in shares]))
            except Exception as e:
                self.say(f"Erro ao listar compartilhamentos: {e}")

        threading.Thread(target=run, daemon=True).start()

    def _update_selection_label(self):
        if not hasattr(self, "sel_lbl"):
            return
        if self.selected_remote_entry:
            kind, name, _, _ = self.selected_remote_entry
            tag = "[arquivo]" if kind == "file" else "[pasta]"
            self.sel_lbl.configure(text=f"Selecionado: {tag} {name}")
        elif self.share:
            self.sel_lbl.configure(text="Selecionado: (pasta atual inteira)")
        else:
            self.sel_lbl.configure(text="Selecionado: (nenhum)")

    def _fill(self, entries):
        self.entries = entries
        self.selected_remote_entry = None
        self._update_selection_label()
        for button in self.remote_buttons:
            button.destroy()
        self.remote_buttons.clear()
        icons = {"share": "[share] ", "dir": "[pasta] ", "file": "        ", "up": "↩ "}
        for index, (kind, name) in enumerate(entries):
            button = ctk.CTkButton(
                self.rlist,
                text=icons[kind] + name,
                anchor="w",
                fg_color="transparent",
                hover_color=self.theme_colors["hover"],
                text_color=self.theme_colors["text"],
                command=lambda item_index=index: self.on_remote_item_click(item_index),
            )
            button.bind("<Button-3>", lambda e, item_index=index: self._on_remote_context_menu(e, item_index))
            button.pack(fill="x", padx=2, pady=1)
            self.remote_buttons.append(button)

    def on_remote_item_click(self, index):
        if not self.authenticated or not isinstance(index, int) or not 0 <= index < len(self.entries):
            return
        kind, name = self.entries[index]
        if kind in ("share", "dir", "up"):
            self.open_item(index)
        elif kind == "file":
            self.select_remote_item(index)

    def select_remote_item(self, index):
        if not (0 <= index < len(self.entries)):
            return
        kind, name = self.entries[index]
        self.selected_remote_entry = (kind, name, self.share, self.path)
        self._update_selection_label()
        for idx, btn in enumerate(self.remote_buttons):
            if idx == index:
                btn.configure(fg_color=self.theme_colors["hover"])
            else:
                btn.configure(fg_color="transparent")

    def _on_remote_context_menu(self, event, index):
        if not self.authenticated or not isinstance(index, int) or not 0 <= index < len(self.entries):
            return
        kind, name = self.entries[index]
        if kind == "up":
            return
        menu = tk.Menu(self, tearoff=0)
        if kind == "file":
            self.select_remote_item(index)
            menu.add_command(
                label=f"Restaurar '{name}' para o PC...",
                command=lambda: self.open_restore_dialog(target_entry=(kind, name, self.share, self.path)),
            )
        elif kind in ("dir", "share"):
            target_share = name if kind == "share" else self.share
            target_path = "" if kind == "share" else (f"{self.path}/{name}".strip("/") if self.path else name)
            menu.add_command(
                label=f"Abrir '{name}'",
                command=lambda: self.open_item(index),
            )
            menu.add_command(
                label=f"Restaurar pasta '{name}' para o PC...",
                command=lambda: self.open_restore_dialog(target_entry=(kind, name, target_share, target_path)),
            )
            menu.add_command(
                label=f"Usar '{name}' como destino de backup",
                command=lambda: self._set_dest_explicit(target_share, target_path),
            )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _set_dest_explicit(self, share, path):
        if not self.authenticated or self.remote is None or not share:
            return
        self.dest = (share, path)
        self.dest_server = self.remote.host
        self._update_dest_label()
        self._save_settings()
        self.say(f"Destino de backup definido para \\\\{self.remote.host}\\{share}\\{path}")

    def open_item(self, index):
        remote = self.remote
        if not self.authenticated or remote is None:
            return
        if not isinstance(index, int) or not 0 <= index < len(self.entries):
            return
        kind, name = self.entries[index]
        if kind == "share":
            self.share, self.path = name, ""
            self.say(f"Abrindo compartilhamento: \\\\{remote.host}\\{self.share}")
        elif kind == "dir":
            self.path = f"{self.path}/{name}".strip("/") if self.path else name
            clean_path = self.path.replace('/', '\\')
            self.say(f"Acessando pasta: \\\\{remote.host}\\{self.share}\\{clean_path}")
        elif kind == "up":
            if self.path:
                self.path = self.path.rsplit("/", 1)[0] if "/" in self.path else ""
                clean_path = ("\\" + self.path.replace('/', '\\')) if self.path else ""
                self.say(f"Retornando para: \\\\{remote.host}\\{self.share}{clean_path}")
            else:
                self.say("Retornando para lista de compartilhamentos.")
                self.show_shares_list()
                return
        else:
            return
        self.selected_remote_entry = None
        self.refresh()

    def set_dest(self):
        remote = self.remote
        if not self.authenticated or remote is None or not self.share:
            show_info_dialog(self, "Destino", "Faça login e abra um compartilhamento primeiro.")
            return
        self.dest = (self.share, self.path)
        self.dest_server = remote.host
        self._update_dest_label()
        self._save_settings()

    def restore_current_or_selected(self):
        if self.selected_remote_entry:
            self.open_restore_dialog(target_entry=self.selected_remote_entry)
        else:
            self.open_restore_dialog()

    def open_restore_dialog(self, target_entry=None):
        if not self.authenticated or self.remote is None:
            show_info_dialog(self, "Restauração", "Faça login no servidor antes de restaurar arquivos.")
            return

        target = target_entry or self.selected_remote_entry
        options = []
        option_specs = {}

        if target:
            kind, name, t_share, t_path = target
            is_d = (kind != "file")
            if kind == "share":
                t_share = name
                clean_path = ""
                is_d = True
            elif kind == "file":
                clean_path = f"{t_path}/{name}".strip("/") if t_path else name
            else:
                clean_path = f"{t_path}/{name}".strip("/") if t_path else name
            tag = "Arquivo" if kind == "file" else "Pasta"
            path_repr = f"\\\\{self.remote.host}\\{t_share}\\{clean_path.replace('/', chr(92))}".rstrip("\\")
            label = f"{tag}: {name} ({path_repr})"
            options.append(label)
            option_specs[label] = {
                "share": t_share,
                "path": clean_path,
                "is_dir": is_d,
                "name": name,
            }

        if self.share:
            curr_name = self.path.rsplit("/", 1)[-1] if self.path else self.share
            curr_unc = f"\\\\{self.remote.host}\\{self.share}"
            if self.path:
                curr_unc += f"\\{self.path.replace('/', chr(92))}"
            curr_label = f"Pasta atual: {curr_unc}"
            if curr_label not in option_specs:
                options.append(curr_label)
                option_specs[curr_label] = {
                    "share": self.share,
                    "path": self.path,
                    "is_dir": True,
                    "name": curr_name,
                }

        if self.dest:
            d_share, d_path = self.dest
            d_name = d_path.rsplit("/", 1)[-1] if d_path else d_share
            d_unc = f"\\\\{self.remote.host}\\{d_share}"
            if d_path:
                d_unc += f"\\{d_path.replace('/', chr(92))}"
            d_label = f"Destino de backup: {d_unc}"
            if d_label not in option_specs:
                options.append(d_label)
                option_specs[d_label] = {
                    "share": d_share,
                    "path": d_path,
                    "is_dir": True,
                    "name": d_name,
                }

        if not options:
            show_info_dialog(
                self,
                "Restauração",
                "Nenhum arquivo ou pasta remota selecionada.\nAbra um compartilhamento na lista de arquivos ou configure uma pasta de destino de backup.",
            )
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title("Restaurar arquivos para a máquina local")
        dialog.geometry("640x440")
        dialog.resizable(False, False)
        dialog.transient(self)

        ctk.CTkLabel(
            dialog,
            text="Restaurar do Servidor para o Computador Local",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(16, 4))

        ctk.CTkLabel(
            dialog,
            text="Escolha a origem no servidor e selecione a pasta local onde deseja restaurar os arquivos:",
            wraplength=600,
            justify="left",
        ).pack(anchor="w", padx=20, pady=(0, 12))

        src_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        src_frame.pack(fill="x", padx=20, pady=(0, 10))
        ctk.CTkLabel(src_frame, text="Origem remota:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(0, 4))

        selected_source_var = tk.StringVar(value=options[0])
        src_menu = ctk.CTkOptionMenu(
            src_frame,
            variable=selected_source_var,
            values=options,
            width=600,
            fg_color=self.theme_colors["accent"],
            button_color=self.theme_colors["accent_hover"],
            command=lambda _val: update_preview(),
        )
        src_menu.pack(fill="x")

        dst_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        dst_frame.pack(fill="x", padx=20, pady=(0, 6))
        ctk.CTkLabel(dst_frame, text="Salvar na pasta local:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(0, 4))

        dst_input_row = ctk.CTkFrame(dst_frame, fg_color="transparent")
        dst_input_row.pack(fill="x")

        default_dest = self.last_restore_folder
        if not default_dest or not os.path.exists(default_dest):
            downloads = str(Path.home() / "Downloads")
            default_dest = downloads if os.path.exists(downloads) else str(Path.home())

        local_path_var = tk.StringVar(value=default_dest)
        dest_entry = ctk.CTkEntry(dst_input_row, textvariable=local_path_var, width=480)
        dest_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        def browse_dest():
            selected = filedialog.askdirectory(
                initialdir=local_path_var.get() or str(Path.home()),
                title="Selecione a pasta local de destino",
            )
            if selected:
                local_path_var.set(os.path.normpath(selected))
                update_preview()

        self._accent_button(dst_input_row, text="Procurar...", width=100, command=browse_dest).pack(side="right")

        preview_lbl = ctk.CTkLabel(dialog, text="", anchor="w", font=ctk.CTkFont(size=11), text_color=self.theme_colors["muted"])
        preview_lbl.pack(fill="x", padx=20, pady=(2, 6))

        def update_preview():
            spec = option_specs.get(selected_source_var.get())
            base_dir = local_path_var.get().strip() or "C:\\..."
            if not spec:
                preview_lbl.configure(text="")
                return
            if spec["is_dir"]:
                f_name = spec["name"]
                final_path = os.path.join(base_dir, f_name)
                preview_lbl.configure(text=f"📁 Uma pasta '{f_name}' será criada em: {final_path}")
            else:
                f_name = spec["name"]
                final_path = os.path.join(base_dir, f_name)
                preview_lbl.configure(text=f"📄 O arquivo será salvo como: {final_path}")

        local_path_var.trace_add("write", lambda *_: update_preview())
        update_preview()

        only_diff_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            dialog,
            text="Pular arquivos locais que já estão idênticos e atualizados (restauração incremental)",
            variable=only_diff_var,
        ).pack(anchor="w", padx=20, pady=6)

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(16, 16))

        def submit():
            chosen_key = selected_source_var.get()
            spec = option_specs.get(chosen_key)
            dest_dir = local_path_var.get().strip()
            if not spec:
                show_info_dialog(dialog, "Erro", "Selecione uma origem válida.")
                return
            if not dest_dir:
                show_info_dialog(dialog, "Erro", "Informe ou selecione a pasta local de destino.")
                return
            try:
                os.makedirs(dest_dir, exist_ok=True)
            except OSError as err:
                show_info_dialog(dialog, "Erro", f"Não foi possível criar a pasta local:\n{err}")
                return

            self.last_restore_folder = dest_dir
            self._save_settings()
            dialog.destroy()
            self.start_restore(spec, dest_dir, only_modified=only_diff_var.get())

        ctk.CTkButton(btn_frame, text="Cancelar", width=100, command=dialog.destroy).pack(side="right", padx=(8, 0))
        self._accent_button(btn_frame, text="⤓ Iniciar restauração", width=170, command=submit).pack(side="right")

        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        dialog.wait_visibility()
        dialog.grab_set()

    def start_restore(self, remote_spec, local_dir, only_modified=True):
        if getattr(self, "transfer_in_progress", False):
            show_info_dialog(
                self, "Operação em andamento",
                "Já existe uma transferência em andamento. Aguarde ou cancele antes de iniciar outra.",
            )
            return
        if not (self.authenticated and self.remote):
            show_info_dialog(self, "Restauração", "Faça login no servidor primeiro.")
            return
        self.cancel.clear()
        threading.Thread(
            target=self.restore,
            args=(remote_spec, local_dir, only_modified),
            daemon=True,
        ).start()

    def _download_smb_file(self, src_unc, dst_local):
        os.makedirs(os.path.dirname(os.path.abspath(dst_local)), exist_ok=True)
        with smbclient.open_file(src_unc, mode="rb") as in_file, open(dst_local, "wb") as out_file:
            shutil.copyfileobj(in_file, out_file, 1024 * 1024)
        st = smbclient.stat(src_unc)
        os.utime(dst_local, times=(st.st_atime, st.st_mtime))

    def _restore_file_up_to_date(self, src_unc, dst_local):
        if not os.path.exists(dst_local):
            return False
        try:
            r_stat = smbclient.stat(src_unc)
            l_stat = os.stat(dst_local)
            return l_stat.st_size == r_stat.st_size and l_stat.st_mtime >= r_stat.st_mtime - 2
        except Exception:
            return False

    def restore(self, remote_spec, local_dir, only_modified=True):
        self.transfer_in_progress = True
        try:
            remote = self.remote
            if remote is None:
                return
            share = remote_spec["share"]
            remote_path = remote_spec["path"].strip("/\\")
            is_directory = remote_spec["is_dir"]
            name = remote_spec["name"]

            unc_target = remote.unc(share, remote_path) if remote_path else remote.unc(share)
            self.say(f"--- Iniciando restauração de {unc_target} ---")
            self.say(f"Destino local: {local_dir}")

            items_to_restore = []
            if not is_directory:
                filename = ntpath.basename(remote_path.replace("/", "\\"))
                dest_file = os.path.join(local_dir, filename)
                items_to_restore.append((unc_target, dest_file, filename))
            else:
                self.say("Escaneando arquivos no servidor...")
                folder_name = ntpath.basename(remote_path.replace("/", "\\")) if remote_path else share
                dest_root = os.path.join(local_dir, folder_name)

                def on_walk_err(err):
                    self.say(f"Aviso ao acessar pasta remota: {err}")

                try:
                    last_scan_log = time.monotonic()
                    for dirpath, _, filenames in smbclient.walk(unc_target, onerror=on_walk_err):
                        if self.cancel.is_set():
                            break
                        rel = ntpath.relpath(dirpath, unc_target)
                        for fname in filenames:
                            file_unc = ntpath.join(dirpath, fname)
                            rel_file = fname if rel == "." else ntpath.join(rel, fname)
                            local_dest_path = os.path.join(dest_root, rel_file)
                            display_rel = ntpath.join(folder_name, rel_file)
                            items_to_restore.append((file_unc, local_dest_path, display_rel))
                        now = time.monotonic()
                        if now - last_scan_log >= 1.5:
                            self.say(f"Mapeando arquivos... {len(items_to_restore)} encontrados até o momento.")
                            last_scan_log = now
                except Exception as err:
                    self.say(f"Erro durante listagem remota: {err}")
                    return

            total = len(items_to_restore)
            if total == 0:
                if self.cancel.is_set():
                    self.say("Restauração cancelada antes de iniciar.")
                else:
                    self.say("Nenhum arquivo encontrado para restaurar.")
                return

            self.say(f"Total: {total} arquivo(s) a processar. Iniciando transferências...")
            restored = skipped = failed = 0
            done = 0
            max_workers = 4
            pending = set()
            cancelled = False
            last_progress_update = time.monotonic()
            last_log_time = time.monotonic()
            self.q.put(("progress", (0, total)))

            def report_progress(force=False):
                nonlocal last_progress_update
                now = time.monotonic()
                if force or now - last_progress_update >= 0.1:
                    self.q.put(("progress", (done, total)))
                    last_progress_update = now

            def transfer_one(file_unc, local_dest_path, display_rel):
                if self.cancel.is_set():
                    return "cancelled", display_rel, ""
                try:
                    if only_modified and self._restore_file_up_to_date(file_unc, local_dest_path):
                        return "skipped", display_rel, ""
                    self._download_smb_file(file_unc, local_dest_path)
                    return "restored", display_rel, ""
                except Exception as err:
                    return "failed", display_rel, str(err)

            def record_completed():
                nonlocal restored, skipped, failed, done, last_log_time
                if not pending:
                    return
                completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in completed:
                    pending.remove(future)
                    result, display_rel, err = future.result()
                    done += 1
                    now = time.monotonic()
                    if result == "restored":
                        restored += 1
                        self.say(f"[{done}/{total}] Restaurado: {display_rel}")
                        last_log_time = now
                    elif result == "skipped":
                        skipped += 1
                        if total <= 50 or done % 10 == 0 or done == total or (now - last_log_time >= 1.5):
                            self.say(f"[{done}/{total}] Inalterado: {display_rel}")
                            last_log_time = now
                    elif result == "failed":
                        failed += 1
                        self.say(f"[{done}/{total}] FALHA ao restaurar {display_rel}: {err}")
                        last_log_time = now
                    else:
                        continue
                    report_progress()

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                for file_unc, local_dest_path, display_rel in items_to_restore:
                    if self.cancel.is_set():
                        cancelled = True
                        break
                    pending.add(executor.submit(transfer_one, file_unc, local_dest_path, display_rel))
                    if len(pending) >= max_workers * 2:
                        record_completed()
                    if cancelled:
                        break

                while pending:
                    record_completed()

            report_progress(force=True)
            if cancelled or self.cancel.is_set():
                self.say(f"Restauração cancelada: {restored} restaurados, {skipped} inalterados, {failed} falhas.")
            else:
                self.say(f"Restauração concluída com sucesso: {restored} restaurados, {skipped} inalterados, {failed} falhas.")
        finally:
            self.transfer_in_progress = False

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
        if getattr(self, "transfer_in_progress", False):
            show_info_dialog(
                self, "Operação em andamento",
                "Já existe uma transferência em andamento. Aguarde ou cancele antes de iniciar outra.",
            )
            return
        sources = list(self.source_paths)
        if not (self.authenticated and self.remote and self.dest and sources):
            show_info_dialog(
                self, "Backup", "Faça login, escolha o destino e adicione pastas de origem.",
            )
            return
        self.cancel.clear()
        threading.Thread(target=self.backup, args=(sources,), daemon=True).start()

    def backup(self, sources):
        self.transfer_in_progress = True
        try:
            remote = self.remote
            destination = self.dest
            if remote is None or destination is None:
                return
            share, base = destination
            dest_unc = f"\\\\{remote.host}\\{share}" + (f"\\{base.replace('/', chr(92))}" if base else "")
            self.say("--- Iniciando backup ---")
            self.say(f"Destino no servidor: {dest_unc}")
            self.say("Contando arquivos locais para iniciar a verificação...")
            self.q.put(("progress_phase", "Contando arquivos locais..."))
            sent = skipped = failed = 0
            max_workers = 4
            pending = set()
            upload_files = []
            cancelled = False
            last_progress_update = time.monotonic()
            last_log_time = time.monotonic()
            total_files = 0
            scan_log_time = time.monotonic()

            for src in sources:
                for _, _, files in os.walk(src):
                    total_files += len(files)
                    now = time.monotonic()
                    if now - scan_log_time >= 0.5:
                        self.say(f"Contando arquivos locais... {total_files} encontrados.")
                        scan_log_time = now

            checked = 0

            def report_check_progress(force=False):
                nonlocal last_progress_update, last_log_time
                now = time.monotonic()
                if force or now - last_progress_update >= 0.1:
                    fraction = checked / total_files if total_files else (1 if force else 0)
                    progress = min(0.5, fraction * 0.5)
                    self.q.put((
                        "progress_value",
                        (progress, f"⏳ Verificando: {progress:.0%} ({checked}/{total_files} arquivos)"),
                    ))
                    last_progress_update = now
                if force or now - last_log_time >= 0.5:
                    self.say(
                        f"Verificando arquivos: {checked}/{total_files}; "
                        f"{skipped} inalterados, {len(upload_files)} para enviar, {failed} falhas."
                    )
                    last_log_time = now

            def check_file(local_path, remote_path):
                if self.cancel.is_set():
                    return "cancelled", local_path, ""
                try:
                    if remote.up_to_date(share, remote_path, local_path):
                        return "skipped", local_path, ""
                    return "upload", local_path, remote_path
                except Exception as error:
                    return "failed", local_path, str(error)

            def record_checked():
                nonlocal checked, skipped, failed, cancelled
                if not pending:
                    return
                completed, _ = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
                if not completed:
                    report_check_progress()
                    return
                for future in completed:
                    pending.remove(future)
                    result, local_path, error = future.result()
                    if result == "upload":
                        upload_files.append((local_path, error))
                    elif result == "skipped":
                        skipped += 1
                    elif result == "cancelled":
                        cancelled = True
                    elif result == "failed":
                        failed += 1
                    else:
                        cancelled = True
                    if result != "cancelled":
                        checked += 1
                report_check_progress()

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
                            remote.ensure_dir(share, rdir)
                        except Exception as error:
                            self.say(f"Erro criando {rdir}: {error}")
                            failed += len(files)
                            checked += len(files)
                            report_check_progress()
                            continue
                        for filename in files:
                            if self.cancel.is_set():
                                cancelled = True
                                break
                            local_path = os.path.join(dirpath, filename)
                            remote_path = f"{rdir}/{filename}"
                            pending.add(executor.submit(check_file, local_path, remote_path))
                            if len(pending) >= max_workers * 2:
                                record_checked()
                        if cancelled:
                            break
                    if cancelled:
                        break

                while pending:
                    record_checked()

            if not cancelled and not self.cancel.is_set():
                checked = total_files
            report_check_progress(force=True)
            total = len(upload_files)
            done = 0
            last_upload_detail = ""
            self.say(
                f"Verificação concluída: {skipped} inalterados, "
                f"{total} arquivo(s) para enviar."
            )
            if total and not cancelled and not self.cancel.is_set():
                upload_started = True
                self.q.put((
                    "progress_value",
                    (0.5, f"⏳ Enviando: 50% (0/{total} uploads)"),
                ))
            else:
                upload_started = False

            def report_progress(force=False):
                nonlocal last_progress_update, last_log_time
                now = time.monotonic()
                if force or now - last_progress_update >= 0.1:
                    fraction = done / total if total else 0
                    progress = 0.5 + 0.5 * min(fraction, 0.98)
                    self.q.put((
                        "progress_value",
                        (progress, f"⏳ Enviando: {progress:.0%} ({done}/{total} uploads)"),
                    ))
                    last_progress_update = now
                if force or now - last_log_time >= 0.5:
                    self.say(
                        f"Enviando arquivos: {done}/{total}; "
                        f"{sent} enviados, {failed} falhas. {last_upload_detail}"
                    )
                    last_log_time = now

            def transfer_file(local_path, remote_path):
                if self.cancel.is_set():
                    return "cancelled", local_path, ""
                try:
                    remote.upload(share, remote_path, local_path)
                    return "sent", local_path, ""
                except Exception as error:
                    return "failed", local_path, str(error)

            def record_completed():
                nonlocal sent, failed, done, last_log_time, last_upload_detail
                if not pending:
                    return
                completed, _ = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
                if not completed:
                    report_progress()
                    return
                for future in completed:
                    pending.remove(future)
                    result, local_path, error = future.result()
                    if result == "cancelled":
                        continue
                    done += 1
                    fname = os.path.basename(local_path)
                    if result == "sent":
                        sent += 1
                        last_upload_detail = f"Último enviado: {fname}."
                    elif result == "failed":
                        failed += 1
                        last_upload_detail = f"Falha em {fname}: {error}"
                    else:
                        continue
                    report_progress()

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                for local_path, remote_path in upload_files:
                    if self.cancel.is_set():
                        cancelled = True
                        break
                    pending.add(executor.submit(transfer_file, local_path, remote_path))
                    if len(pending) >= max_workers * 2:
                        record_completed()

                while pending:
                    record_completed()

            if upload_started:
                report_progress(force=True)
            if cancelled or self.cancel.is_set():
                self.say(f"Backup cancelado: {sent} enviados, {skipped} inalterados, {failed} falhas.")
            else:
                self.say(f"Concluído: {sent} enviados, {skipped} inalterados, {failed} falhas.")
                self.q.put(("progress_value", (1.0, "⏳ 100% (Backup concluído)")))
            self.ui(self.refresh)
        finally:
            self.transfer_in_progress = False


if __name__ == "__main__":
    instance_lock = acquire_single_instance()
    if instance_lock is None:
        root = ctk.CTk()
        root.withdraw()
        show_info_dialog(root, "marcelino Client", "O aplicativo já está aberto.")
        root.destroy()
    else:
        App().mainloop()
