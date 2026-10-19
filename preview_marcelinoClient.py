#!/usr/bin/env python3
"""Open a client screen with generic local data, without connecting to a server.

Usage:
  python preview_marcelinoClient.py --screen Sistema
  python preview_marcelinoClient.py --screen "DNS / DHCP"
"""
import argparse

from marcelinoClient import App


SCREENS = (
    "Sistema",
    "Discos",
    "RAID",
    "Internet",
    "DNS / DHCP",
    "Compartilhamentos",
    "Backup",
    "Terminal",
)

SAMPLE_STATUS = {
    "hostname": "nas-preview",
    "os": "Linux",
    "os_release": "6.12.0-preview",
    "system_info": {
        "os_name": "Debian GNU/Linux 13 (preview)",
        "kernel": "6.12.0-preview-amd64",
        "package_count": {"manager": "dpkg", "count": 772},
        "font": "DejaVu Sans",
        "shell": "/bin/bash",
        "computer_model": "NAS de demonstração",
        "cpu_model": "CPU de demonstração, 8 núcleos",
        "cpu_logical_count": 8,
        "cpu_physical_count": 4,
        "gpu_models": ["Adaptador gráfico de demonstração"],
    },
    "sampled_at": "2026-10-16T12:00:00+00:00",
    "uptime_seconds": 864000,
    "cpu_percent": 28.5,
    "cpu_count": 8,
    "cpu_frequency_mhz": 3200,
    "cpu_temperature_c": 42.5,
    "memory": {
        "total_bytes": 16 * 1024**3,
        "used_bytes": 6 * 1024**3,
        "available_bytes": 10 * 1024**3,
        "percent": 37.5,
    },
    "network": {
        "bytes_sent": 8 * 1024**3,
        "bytes_received": 28 * 1024**3,
    },
    "filesystems": [
        {
            "device": "/dev/md0",
            "mountpoint": "/srv/nas",
            "filesystem": "ext4",
            "total_bytes": 4 * 1024**4,
            "used_bytes": 2 * 1024**4,
            "free_bytes": 2 * 1024**4,
            "percent": 50,
        },
    ],
    "physical_disks": [
        {
            "name": "/dev/sda",
            "model": "Disco de demonstração 4 TB",
            "capacity_bytes": 4 * 1024**4,
            "temperature_c": 34,
            "smart_status": "PASSED",
        },
    ],
    "raid": {
        "status": "Sincronizando",
        "arrays": [
            {
                "name": "/dev/md0",
                "level": "raid1",
                "status": "Sincronizando",
                "active_devices": 2,
                "total_devices": 2,
                "sync_action": "recovery",
                "progress_percent": 62.4,
            },
        ],
    },
    "samba_shares": [
        {
            "name": "Publico",
            "path": "/srv/nas/publico",
            "valid_users": "@nasgroup",
            "read_list": "",
            "write_list": "@nasgroup",
            "guest_ok": "no",
            "read_only": "no",
            "browseable": "yes",
        },
        {
            "name": "Backup",
            "path": "/srv/nas/backup",
            "valid_users": "marcelo",
            "read_list": "",
            "write_list": "marcelo",
            "guest_ok": "no",
            "read_only": "yes",
            "browseable": "yes",
        },
    ],
    "dns_dhcp": {
        "service_state": "active",
        "dns_enabled": True,
        "dhcp_enabled": True,
        "lease_count": 2,
        "access_log_path": "/var/log/dnsmasq-access-preview.jsonl",
        "leases": [
            {
                "ip": "192.168.1.20",
                "hostname": "desktop-preview",
                "mac": "00:11:22:33:44:55",
                "expires_at": "2026-10-16 18:00:00 UTC",
            },
            {
                "ip": "192.168.1.21",
                "hostname": "laptop-preview",
                "mac": "66:77:88:99:AA:BB",
                "expires_at": "2026-10-16 19:00:00 UTC",
            },
        ],
        "allowed_events": [
            {
                "timestamp": "2026-10-16 11:58:20",
                "kind": "dns",
                "message": "desktop-preview consultou example.test",
            },
        ],
        "blocked_events": [
            {
                "timestamp": "2026-10-16 11:59:05",
                "kind": "dns",
                "message": "laptop-preview bloqueado em ads.example.test",
            },
        ],
    },
    "internet": {
        "local_ip": "192.168.1.10",
        "remote_ip": "203.0.113.10",
        "tests": [
            {
                "timestamp": "2026-10-16T11:55:00",
                "download_mbps": 412.5,
                "upload_mbps": 38.2,
                "ping_ms": 12.4,
                "server": "Servidor de demonstração",
            },
            {
                "timestamp": "2026-10-16T10:55:00",
                "download_mbps": 398.1,
                "upload_mbps": 36.9,
                "ping_ms": 13.1,
                "server": "Servidor de demonstração",
            },
        ],
    },
}


class PreviewApp(App):
    def _start_tray_icon(self):
        pass


def show_screen(app, screen):
    if screen not in SCREENS:
        raise ValueError(f"Tela desconhecida: {screen}")

    if screen == "Backup":
        app.auth_summary.set("Preview local")
        app.source_paths = [r"C:\Users\demo\Documents"]
        app._render_sources()
        app.share = "Backup"
        app.path = ""
        app.path_lbl.configure(text=r"\\nas-preview\Backup")
        app._fill([("dir", "Projetos"), ("file", "relatorio.txt")])
        app.dest = ("Backup", "Preview")
        app.dest_server = "nas-preview"
        app._update_dest_label()
    elif screen == "Terminal":
        app.ssh_status.set("Preview local - sem conexao SSH")
        app.terminal_output.delete("1.0", "end")
        app.terminal_output.insert(
            "1.0",
            "demo@nas-preview:~$ hostname\nnas-preview\n"
            "demo@nas-preview:~$ uname -r\n6.12.0-preview\n"
            "demo@nas-preview:~$ _",
        )

    app.select_tab(screen)
    app.title(f"marcelino Client - Preview: {screen}")
    app.protocol("WM_DELETE_WINDOW", app.close_application)
    return app


def create_preview(screen):
    app = PreviewApp()
    app.login_page.pack_forget()
    app.main_page.pack(fill="both", expand=True)
    app.session_display.set("Preview local - sem conexao")
    app._render_server_status(SAMPLE_STATUS)
    return show_screen(app, screen)


def main():
    parser = argparse.ArgumentParser(
        description="Mostra uma tela do cliente com dados genéricos locais."
    )
    parser.add_argument("--screen", default="Sistema", choices=SCREENS)
    args = parser.parse_args()
    create_preview(args.screen).mainloop()


if __name__ == "__main__":
    main()