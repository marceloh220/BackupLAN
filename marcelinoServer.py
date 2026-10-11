#!/usr/bin/env python3
"""Publish server metrics to a JSON file inside an SMB-shared folder."""

import argparse
import glob
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

STATUS_FILENAME = ".marcelinoSrv.json"
DEFAULT_FILE_MODE = 0o644
SMART_REFRESH_SECONDS = 60 * 60
SYSTEM_INFO_REFRESH_SECONDS = 15 * 60
_smart_cache = None
_smart_cache_expires_at = 0.0
_smart_sampled_at = None
_system_info_cache = None
_system_info_cache_expires_at = 0.0


def _run_text_command(command, timeout=10):
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _windows_share_paths():
    import ctypes
    from ctypes import wintypes

    class ShareInfo2(ctypes.Structure):
        _fields_ = [
            ("name", wintypes.LPWSTR),
            ("type", wintypes.DWORD),
            ("remark", wintypes.LPWSTR),
            ("permissions", wintypes.DWORD),
            ("max_uses", wintypes.DWORD),
            ("current_uses", wintypes.DWORD),
            ("path", wintypes.LPWSTR),
            ("password", wintypes.LPWSTR),
        ]

    netapi = ctypes.WinDLL("Netapi32", use_last_error=True)
    enumerate_shares = netapi.NetShareEnum
    enumerate_shares.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(ShareInfo2)),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    enumerate_shares.restype = wintypes.DWORD
    free_buffer = netapi.NetApiBufferFree
    free_buffer.argtypes = [ctypes.c_void_p]
    free_buffer.restype = wintypes.DWORD

    paths = []
    resume = wintypes.DWORD()
    while True:
        buffer = ctypes.POINTER(ShareInfo2)()
        entries_read = wintypes.DWORD()
        total_entries = wintypes.DWORD()
        status = enumerate_shares(
            None, 2, ctypes.byref(buffer), 0xFFFFFFFF,
            ctypes.byref(entries_read), ctypes.byref(total_entries), ctypes.byref(resume),
        )
        try:
            if status not in (0, 234):
                raise OSError(status, ctypes.FormatError(status).strip())
            for index in range(entries_read.value):
                entry = buffer[index]
                if entry.type & 0x80000000 or entry.type & 0xFFFF != 0 or not entry.path:
                    continue
                paths.append(entry.path)
        finally:
            if buffer:
                free_buffer(buffer)
        if status == 0:
            break


SHARE_ACCESS_OPTIONS = (
    "valid users",
    "invalid users",
    "read list",
    "write list",
    "admin users",
    "guest ok",
    "read only",
)


def _samba_share_configs(config_path):
    shares = {}
    visited = set()

    def read_config(path):
        path = Path(path).resolve()
        if path in visited or not path.is_file():
            return
        visited.add(path)
        current_share = None
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", ";")):
                continue
            if stripped.startswith("[") and "]" in stripped:
                current_share = stripped[1:stripped.index("]")].strip()
                shares.setdefault(current_share, {})
                continue
            if "=" not in stripped:
                continue
            key, value = (part.strip() for part in stripped.split("=", 1))
            value = value.split("#", 1)[0].split(";", 1)[0].strip().strip('"\'')
            if key.casefold() == "include":
                for included in glob.glob(os.path.expandvars(os.path.expanduser(value))):
                    read_config(included)
            elif current_share and key.casefold() in ("path", *SHARE_ACCESS_OPTIONS):
                shares[current_share][key.casefold()] = value

    read_config(config_path)
    return shares


def _samba_share_entries(config_path):
    shares = _samba_share_configs(config_path)
    entries = {}
    for share_name, settings in shares.items():
        raw_path = settings.get("path")
        if not raw_path:
            continue
        expanded_path = raw_path.replace("%S", share_name).replace("%s", share_name)
        if "%" in expanded_path:
            continue
        entries[share_name] = os.path.expandvars(os.path.expanduser(expanded_path))
    return entries


def collect_samba_shares(smb_config=None):
    config_path = Path(smb_config or "/etc/samba/smb.conf")
    configurations = _samba_share_configs(config_path)
    global_options = configurations.get("global", {})
    shares = []
    for name, options in configurations.items():
        if name.casefold() == "global":
            continue
        effective_options = {**global_options, **options}
        raw_path = effective_options.get("path", "")
        path = raw_path.replace("%S", name).replace("%s", name)
        if "%" not in path:
            path = os.path.expandvars(os.path.expanduser(path))
        shares.append({
            "name": name,
            "path": path,
            "valid_users": effective_options.get("valid users", ""),
            "invalid_users": effective_options.get("invalid users", ""),
            "read_list": effective_options.get("read list", ""),
            "write_list": effective_options.get("write list", ""),
            "admin_users": effective_options.get("admin users", ""),
            "guest_ok": effective_options.get("guest ok", ""),
            "read_only": effective_options.get("read only", ""),
        })
    return shares


def _samba_share_paths(config_path):
    return list(_samba_share_entries(config_path).values())


def find_public_share_path(smb_config=None, share_name="public"):
    config_path = Path(smb_config or "/etc/samba/smb.conf")
    if not config_path.is_file():
        raise FileNotFoundError(f"Arquivo de configuração SMB não encontrado: {config_path}")
    shares = {
        name.casefold(): Path(path)
        for name, path in _samba_share_entries(config_path).items()
    }
    candidates = (share_name, "public", "publico", "público")
    for candidate in dict.fromkeys(name.casefold() for name in candidates if name):
        if candidate in shares:
            return shares[candidate]
    raise FileNotFoundError(
        f"Compartilhamento público '{share_name}' não encontrado em {config_path}"
    )


def collect_share_paths(smb_config=None):
    if platform.system() != "Linux":
        return [], "O agente de métricas é compatível apenas com Linux"
    config_path = Path(smb_config or "/etc/samba/smb.conf")
    if not config_path.is_file():
        return [], f"Arquivo de configuração SMB não encontrado: {config_path}"
    paths = _samba_share_paths(config_path)
    if not paths:
        return [], f"Nenhum caminho de compartilhamento encontrado em {config_path}"
    return paths, None


def collect_shared_partitions(share_paths, partitions=None):
    partitions = partitions if partitions is not None else psutil.disk_partitions(all=False)
    matched = {}
    for share_path in share_paths:
        try:
            share_root = os.path.normcase(os.path.abspath(os.path.expanduser(share_path)))
        except (OSError, TypeError, ValueError):
            continue
        candidates = []
        for partition in partitions:
            mountpoint = os.path.normcase(os.path.abspath(partition.mountpoint))
            try:
                if os.path.commonpath((share_root, mountpoint)) == mountpoint:
                    candidates.append((len(mountpoint), partition))
            except ValueError:
                continue
        if not candidates:
            continue
        partition = max(candidates, key=lambda item: item[0])[1]
        try:
            usage = psutil.disk_usage(partition.mountpoint)
        except OSError:
            continue
        matched[partition.mountpoint] = {
            "device": partition.device,
            "mountpoint": partition.mountpoint,
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "percent": usage.percent,
        }
    return list(matched.values())


def collect_physical_disks(smartctl_path=None):
    smartctl = smartctl_path or shutil.which("smartctl")
    if not smartctl:
        return [], "smartctl não encontrado; instale o smartmontools no servidor"
    try:
        scan = subprocess.run(
            [smartctl, "--scan", "--json"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=20, check=False,
        )
        scan_data = json.loads(scan.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        return [], f"Não foi possível consultar smartctl: {error}"

    disks = []
    for device in scan_data.get("devices", []):
        name = device.get("name")
        if not name:
            continue
        command = [smartctl, "-a", "--json"]
        device_type = device.get("type")
        if device_type:
            command.extend(("--device", device_type))
        command.append(name)
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=20, check=False,
            )
            details = json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            disks.append({
                "name": name,
                "model": "",
                "capacity_bytes": 0,
                "temperature_c": None,
                "smart_status": "Indisponível",
                "error": str(error),
            })
            continue

        sat_error = None
        if (
            device_type
            and device_type.casefold() == "scsi"
            and details.get("smart_support", {}).get("available") is False
        ):
            sat_command = [smartctl, "-a", "--json", "--device", "sat", name]
            try:
                sat_result = subprocess.run(
                    sat_command, capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=20, check=False,
                )
                sat_details = json.loads(sat_result.stdout)
            except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
                sat_error = str(error)
            else:
                sat_smart = sat_details.get("smart_status", {}).get("passed")
                sat_available = sat_details.get("smart_support", {}).get("available")
                if isinstance(sat_smart, bool) or sat_available is True:
                    command, result, details = sat_command, sat_result, sat_details
                else:
                    sat_error = (
                        "fallback --device sat sem status SMART; "
                        f"stderr={sat_result.stderr.strip()}"
                    )

        smart = details.get("smart_status", {}).get("passed")
        smart_support = details.get("smart_support", {})
        temperature = details.get("temperature", {}).get("current")
        if temperature is None:
            temperature = details.get("nvme_smart_health_information_log", {}).get("temperature")
        capacity = details.get("user_capacity", {}).get("bytes", 0)
        if smart is None:
            error = (
                "smart_status.passed ausente; "
                f"smart_support={smart_support}; "
                f"smartctl exit_status={details.get('smartctl', {}).get('exit_status')}; "
                f"JSON keys={','.join(sorted(details))}; "
                f"stderr={result.stderr.strip()}; sat_fallback={sat_error}"
            )
            print("SMART diagnostics:", {
                "device": name,
                "command": command,
                "process_exit_status": result.returncode,
                "sat_fallback_error": sat_error,
                "smartctl_exit_status": details.get("smartctl", {}).get("exit_status"),
                "smart_support": smart_support,
                "smart_status": details.get("smart_status"),
                "json_keys": sorted(details),
                "stderr": result.stderr.strip(),
            })
        else:
            error = ""

        disk_data = {
            "name": name,
            "model": (
                details.get("model_name")
                or details.get("model_family")
                or details.get("product")
                or ""
            ),
            "capacity_bytes": capacity,
            "temperature_c": temperature,
            "smart_status": "OK" if smart is True else "Falha" if smart is False else "Indisponível",
            "smartctl_exit_status": details.get("smartctl", {}).get("exit_status"),
            "smart_support": smart_support,
            "error": error,
        }
        disks.append(disk_data)
        print("Collected disk:", disk_data)
    return disks, None


def collect_cached_physical_disks(smartctl_path=None):
    global _smart_cache, _smart_cache_expires_at, _smart_sampled_at
    now = time.monotonic()
    if _smart_cache is None or now >= _smart_cache_expires_at:
        _smart_cache = collect_physical_disks(smartctl_path)
        _smart_cache_expires_at = time.monotonic() + SMART_REFRESH_SECONDS
        _smart_sampled_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return _smart_cache[0], _smart_cache[1], _smart_sampled_at


def _read_os_name():
    try:
        values = {}
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value.strip().strip('"')
        return values.get("PRETTY_NAME", platform.system())
    except OSError:
        return platform.system()


def _read_cpu_model():
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip().casefold() in ("model name", "hardware", "processor"):
                if value.strip():
                    return value.strip()
    except OSError:
        pass
    return platform.processor() or "Indisponível"


def _read_computer_model():
    for filename in ("product_name", "product_version"):
        try:
            value = Path("/sys/class/dmi/id", filename).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value and value not in ("To be filled by O.E.M.", "None"):
            return value
    return "Indisponível"


def _read_gpu_models():
    lspci = shutil.which("lspci")
    if not lspci:
        return []
    output = _run_text_command([lspci, "-Dnn"], timeout=10)
    return [
        line.split(":", 3)[-1].strip()
        for line in output.splitlines()
        if any(kind in line.casefold() for kind in (
            "vga compatible controller", "3d controller", "display controller",
        ))
    ]


def _read_package_count():
    managers = (
        ("pacman", ["pacman", "-Qq"]),
        ("dpkg", ["dpkg-query", "-f=${binary:Package}\\n", "-W"]),
        ("rpm", ["rpm", "-qa"]),
    )
    for manager, command in managers:
        executable = shutil.which(command[0])
        if not executable:
            continue
        command[0] = executable
        output = _run_text_command(command, timeout=30)
        if output:
            return {"manager": manager, "count": len(output.splitlines())}
    return {"manager": "Indisponível", "count": None}


def _read_window_manager():
    known_managers = {
        "hyprland": "Hyprland", "sway": "Sway", "kwin_wayland": "KWin",
        "kwin_x11": "KWin", "mutter": "Mutter", "xfwm4": "Xfwm4",
        "openbox": "Openbox", "i3": "i3", "bspwm": "bspwm", "awesome": "Awesome",
    }
    try:
        for process in psutil.process_iter(["name"]):
            name = (process.info.get("name") or "").casefold()
            if name in known_managers:
                return known_managers[name]
    except (OSError, psutil.Error):
        pass
    return "Indisponível"


def _read_gpu_models():
    lspci = shutil.which("lspci")
    if not lspci:
        return []
    try:
        result = subprocess.run(
            [lspci, "-Dnn"], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [
        line.split(":", 3)[-1].strip()
        for line in result.stdout.splitlines()
        if any(kind in line.casefold() for kind in (
            "vga compatible controller", "3d controller", "display controller",
        ))
    ]


def _read_package_count():
    managers = (
        ("pacman", ["pacman", "-Qq"]),
        ("dpkg", ["dpkg-query", "-f=${binary:Package}\\n", "-W"]),
        ("rpm", ["rpm", "-qa"]),
    )
    for manager, command in managers:
        executable = shutil.which(command[0])
        if not executable:
            continue
        command[0] = executable
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return {"manager": manager, "count": sum(bool(line.strip()) for line in result.stdout.splitlines())}
    return {"manager": "Indisponível", "count": None}


def _read_window_manager():
    known_managers = {
        "hyprland": "Hyprland", "sway": "Sway", "kwin_wayland": "KWin",
        "kwin_x11": "KWin", "mutter": "Mutter", "xfwm4": "Xfwm4",
        "openbox": "Openbox", "i3": "i3", "bspwm": "bspwm", "awesome": "Awesome",
    }
    try:
        for process in psutil.process_iter(["name"]):
            name = (process.info.get("name") or "").casefold()
            if name in known_managers:
                return known_managers[name]
    except (OSError, psutil.Error):
        pass
    return "Indisponível"


def collect_system_info():
    shell = os.environ.get("SHELL")
    if not shell:
        try:
            import pwd
            shell = pwd.getpwuid(os.getuid()).pw_shell
        except (ImportError, KeyError, OSError):
            shell = "Indisponível"

    fc_match = shutil.which("fc-match")
    default_font = _run_text_command([fc_match, "-f", "%{family}", "sans"], timeout=5) if fc_match else ""

    desktop = (
        os.environ.get("XDG_CURRENT_DESKTOP")
        or os.environ.get("XDG_SESSION_DESKTOP")
        or os.environ.get("DESKTOP_SESSION")
        or "Sem ambiente gráfico no serviço"
    )
    return {
        "os_name": _read_os_name(),
        "kernel": platform.release(),
        "package_count": _read_package_count(),
        "desktop_environment": desktop,
        "window_manager": _read_window_manager(),
        "font": default_font or "Indisponível",
        "shell": shell or "Indisponível",
        "computer_model": _read_computer_model(),
        "cpu_model": _read_cpu_model(),
        "cpu_logical_count": psutil.cpu_count(logical=True) or 0,
        "cpu_physical_count": psutil.cpu_count(logical=False) or 0,
        "gpu_models": _read_gpu_models(),
    }


def collect_cached_system_info():
    global _system_info_cache, _system_info_cache_expires_at
    now = time.monotonic()
    if _system_info_cache is None or now >= _system_info_cache_expires_at:
        _system_info_cache = collect_system_info()
        _system_info_cache_expires_at = time.monotonic() + SYSTEM_INFO_REFRESH_SECONDS
    return _system_info_cache


def collect_mounted_filesystems():
    network_filesystems = {"cifs", "smbfs", "nfs", "nfs4", "sshfs", "fuse.sshfs"}
    filesystems = []
    seen_mountpoints = set()
    for partition in psutil.disk_partitions(all=False):
        mountpoint = os.path.normpath(partition.mountpoint)
        if mountpoint in seen_mountpoints or partition.fstype.casefold() in network_filesystems:
            continue
        seen_mountpoints.add(mountpoint)
        try:
            usage = psutil.disk_usage(partition.mountpoint)
        except OSError:
            continue
        filesystems.append({
            "device": partition.device,
            "mountpoint": partition.mountpoint,
            "filesystem": partition.fstype,
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "percent": usage.percent,
        })
    return filesystems


def collect_system_filesystems():
    network_filesystems = {"cifs", "smbfs", "nfs", "nfs4", "sshfs", "fuse.sshfs"}
    filesystems = []
    seen_mountpoints = set()
    for partition in psutil.disk_partitions(all=False):
        mountpoint = os.path.normpath(partition.mountpoint)
        if mountpoint in seen_mountpoints or partition.fstype.casefold() in network_filesystems:
            continue
        seen_mountpoints.add(mountpoint)
        try:
            usage = psutil.disk_usage(partition.mountpoint)
        except OSError:
            continue
        filesystems.append({
            "device": partition.device,
            "mountpoint": partition.mountpoint,
            "filesystem": partition.fstype,
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "percent": usage.percent,
        })
    return filesystems


def collect_status(smb_config=None, smartctl_path=None):
    share_paths, share_error = collect_share_paths(smb_config)
    memory = psutil.virtual_memory()
    shared_partitions = collect_shared_partitions(share_paths)
    physical_disks, smartctl_error, smart_sampled_at = collect_cached_physical_disks(smartctl_path)
    filesystems = collect_system_filesystems()
    network = psutil.net_io_counters()
    return {
        "hostname": platform.node(),
        "os": platform.system(),
        "os_release": platform.release(),
        "system_info": collect_cached_system_info(),
        "filesystems": filesystems,
        "sampled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "uptime_seconds": max(0, int(time.time() - psutil.boot_time())),
        "cpu_percent": psutil.cpu_percent(interval=0.5),
        "cpu_count": psutil.cpu_count() or 0,
        "memory": {
            "total_bytes": memory.total,
            "used_bytes": memory.used,
            "available_bytes": memory.available,
            "percent": memory.percent,
        },
        "shared_partitions": shared_partitions,
        "share_discovery_error": share_error,
        "samba_shares": collect_samba_shares(smb_config),
        "physical_disks": physical_disks,
        "smartctl_error": smartctl_error,
        "smart_sampled_at": smart_sampled_at,
        "network": {
            "bytes_sent": network.bytes_sent,
            "bytes_received": network.bytes_recv,
        },
    }


def publish_status(output_path, smb_config=None, file_mode=DEFAULT_FILE_MODE):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=output_path.parent,
            prefix=f"{output_path.name}.", suffix=".tmp", delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            json.dump(collect_status(smb_config=smb_config), temp_file, ensure_ascii=False)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.chmod(temp_path, file_mode)
        os.replace(temp_path, output_path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        help=(
            f"caminho explícito do JSON (padrão: {STATUS_FILENAME} na raiz do "
            "compartilhamento público)"
        ),
    )
    parser.add_argument("--smb-config", type=Path, help="caminho do smb.conf no Linux")
    parser.add_argument(
        "--public-share", default="public",
        help="nome do compartilhamento público no smb.conf (padrão: public)",
    )
    parser.add_argument(
        "--file-mode",
        type=lambda value: int(value, 8),
        default=DEFAULT_FILE_MODE,
        help="permissão Unix do JSON em octal (padrão: 0644; use 0640 para acesso pelo grupo)",
    )
    parser.add_argument("--interval", type=float, default=5.0, help="intervalo em segundos")
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval deve ser maior que zero")
    if args.file_mode < 0 or args.file_mode > 0o777:
        parser.error("--file-mode deve conter permissões Unix entre 0000 e 0777")
    if platform.system() != "Linux":
        parser.error("o agente de métricas só pode ser executado em Linux")
    if args.output is not None:
        output_path = args.output
    else:
        try:
            output_path = find_public_share_path(args.smb_config, args.public_share) / STATUS_FILENAME
        except (FileNotFoundError, OSError) as error:
            parser.error(str(error))

    print(
        f"Publicando métricas em {output_path.resolve()} "
        f"a cada {args.interval:g}s. Ctrl+C para sair."
    )
    try:
        while True:
            started = time.monotonic()
            try:
                publish_status(output_path, args.smb_config, args.file_mode)
                print(f"Atualizado: {datetime.now().strftime('%H:%M:%S')}")
            except Exception as error:
                print(f"Falha ao publicar métricas: {error}")
            time.sleep(max(0, args.interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        print("Agente encerrado.")


if __name__ == "__main__":
    main()