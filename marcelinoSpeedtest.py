#!/usr/bin/env python3
"""
    file: marcelinoSpeedtest.py
    Description: Measure internet speed, log results, and generate PNG charts.
    Usage:
    marcelinoSpeedtest test                    run a new test and save it to the log
    marcelinoSpeedtest log [-n 10]             show the last n tests
    marcelinoSpeedtest --log FILE log -n 10    use another file and show the last 10 tests
    marcelinoSpeedtest graph [-n 24]           chart the last n tests
    marcelinoSpeedtest graph --hours 24        chart the last 24 hours

    --log sets the JSON file path; -n sets how many tests to list.

    Intended to run on the Linux server.
    Install this feature with: sudo nas-setup.sh --speedtest
    Or install everything on a clean Debian system with: sudo nas-setup.sh --all

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
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_LOG = "/var/log/marcelinoSpeedtest.jsonl"
DEFAULT_OUT = "/srv/marcelo/internet_test"
# speedtest-cli returns 1800000 ms when the server's latency probes fail.
PING_INVALID_MS = 10000


def now():
    return datetime.now().astimezone()


def read_log(path):
    entries = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    entry["_ts"] = datetime.fromisoformat(entry["timestamp"])
                    entries.append(entry)
                except (ValueError, KeyError):
                    continue
    except FileNotFoundError:
        pass
    return entries


def append_log(path, entry):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_server(server_id, timeout):
    cmd = ["speedtest-cli", "--json"] + (["--server", str(server_id)] if server_id else [])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True)
    data = json.loads(proc.stdout)
    server = data.get("server", {})
    return dict(
        download_mbps=round(data["download"] / 1e6, 2),
        upload_mbps=round(data["upload"] / 1e6, 2),
        ping_ms=round(data["ping"], 1) if data["ping"] < PING_INVALID_MS else None,
        server=f"{server.get('sponsor', '')} - {server.get('name', '')}".strip(" -"),
        ip=data.get("client", {}).get("ip", ""),
    )


def nearest_servers(count, timeout):
    """Return IDs of the nearest servers in speedtest-cli list order."""
    proc = subprocess.run(["speedtest-cli", "--list"], capture_output=True, text=True,
                          timeout=timeout, check=True)
    ids = re.findall(r"^\s*(\d+)\)", proc.stdout, re.MULTILINE)
    return ids[:count]


def rank(r):
    # Prefer servers with valid latency; among them, choose the highest download speed.
    return (r["ping_ms"] is not None, r["download_mbps"])


def best_of(candidates, timeout):
    """Test each server and return (best result or None, last error)."""
    best, last_error = None, ""
    for server_id in candidates:
        try:
            result = run_server(server_id, timeout)
        except (subprocess.SubprocessError, ValueError, KeyError, OSError) as exc:
            last_error = str(exc)[:200]
            continue
        if best is None or rank(result) > rank(best):
            best = result
    return best, last_error


def cmd_test(args):
    entry = {"timestamp": now().isoformat(timespec="seconds")}
    # Weak servers skew results, so test several and keep the best one.
    best, last_error = (None, "")
    if args.server:
        best, last_error = best_of(args.server, args.timeout)
        if best is None:
            print("Servidores fixados falharam; tentando os mais próximos.", file=sys.stderr)
    if best is None:
        try:
            candidates = nearest_servers(args.attempts, args.timeout) or [None]
        except (subprocess.SubprocessError, OSError):
            candidates = [None]
        best, last_error = best_of(candidates, args.timeout)
    if best is None:
        entry["error"] = last_error or "nenhum servidor respondeu"
        append_log(args.log, entry)
        print(f"Falha no teste: {entry['error']}", file=sys.stderr)
        return 1
    entry.update(best)
    append_log(args.log, entry)
    print(format_entry(entry))
    return 0


def format_entry(e):
    ts = datetime.fromisoformat(e["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
    if "error" in e:
        return f"{ts}  ERRO: {e['error']}"
    ping = e.get("ping_ms")
    ping = f"{ping:6.1f} ms" if ping is not None and ping < PING_INVALID_MS else "   n/d   "
    return (f"{ts}  download {e['download_mbps']:8.2f} Mbit/s  "
            f"upload {e['upload_mbps']:8.2f} Mbit/s  ping {ping}  {e['server']}")


def cmd_log(args):
    entries = read_log(args.log)[-args.n:]
    if not entries:
        print("Nenhum teste registrado.")
        return 0
    for e in entries:
        print(format_entry(e))
    return 0


def match_owner(path, reference):
    if sys.platform != "linux":
        sys.exit(f"Aviso: marcelinoSpeedtest só é suportado em Linux (sistema atual: {sys.platform}).")
        return
    st = os.stat(reference)
    try:
        os.chown(path, st.st_uid, st.st_gid)
    except (PermissionError, OSError):
        pass


def make_dirs(base, year, month):
    """Create base/year/month directories, inheriting the base directory's owner."""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    target = base
    for part in (year, month):
        target = target / part
        if not target.exists():
            target.mkdir()
            match_owner(target, base)
    return target


def cmd_graph(args):
    if sys.platform != "linux":
        sys.exit(f"Aviso: marcelinoSpeedtest só é suportado em Linux (sistema atual: {sys.platform}).")
        return
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    ref = now()

    entries = [e for e in read_log(args.log) if "error" not in e]
    if args.hours:
        ref = now()
        entries = [e for e in entries if e["_ts"] >= ref - timedelta(hours=args.hours)]
        label = f"{args.hours}h"
    else:
        entries = entries[-args.n:]
        label = f"ultimos{args.n}"
    if not entries:
        print("Sem testes no período para gerar o gráfico.", file=sys.stderr)
        return 1
    if not args.hours:
        ref = entries[-1]["_ts"]

    times = [e["_ts"] for e in entries]
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(times, [e["download_mbps"] for e in entries], marker="o", label="Download")
    ax.plot(times, [e["upload_mbps"] for e in entries], marker="s", label="Upload")
    ax.set_ylabel("Mbit/s")
    ax.set_ylim(bottom=0)
    ax.set_title(f"Velocidade da internet - {times[0]:%d/%m/%Y %H:%M} a {times[-1]:%d/%m/%Y %H:%M}")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M", tz=times[0].tzinfo))
    fig.autofmt_xdate()
    ax.minorticks_on()
    ax.grid(True, which="major", alpha=0.75)
    ax.grid(True, which="minor", linestyle=":", linewidth=0.5, alpha=0.50)
    ax.legend()
    fig.tight_layout()

    out_dir = make_dirs(args.output_dir, f"{ref:%Y}", f"{ref:%m}")
    out = out_dir / f"speedtest_{ref:%Y-%m-%d}_{label}.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    match_owner(out, args.output_dir)
    print(out)
    return 0


def main():
    if sys.platform != "linux":
        sys.exit(f"Aviso: marcelinoSpeedtest só é suportado em Linux (sistema atual: {sys.platform}).")
        return
    parser = argparse.ArgumentParser(description="Teste de velocidade da internet do NAS")
    parser.add_argument("--log", default=DEFAULT_LOG,
                        help="caminho do arquivo de log (JSON por linha)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("test", help="executa um teste novo")
    p.add_argument("--timeout", type=int, default=180, help="limite em segundos por servidor")
    p.add_argument("--attempts", type=int, default=3,
                   help="quantos servidores próximos testar (usa o melhor resultado)")
    p.add_argument("--server", action="append", metavar="ID",
                   help="ID de servidor fixo (repetível); substitui a seleção automática")
    p.set_defaults(func=cmd_test)

    p = sub.add_parser("log", help="mostra os n últimos testes")
    p.add_argument("-n", type=int, default=10)
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("graph", help="gera gráfico PNG de download e upload")
    p.add_argument("-n", type=int, default=24, help="quantidade de últimos testes")
    p.add_argument("--hours", type=int, help="usa as últimas N horas em vez de -n")
    p.add_argument("--output-dir", default=DEFAULT_OUT)
    p.set_defaults(func=cmd_graph)

    args = parser.parse_args()
    if args.cmd in ("test", "graph") and os.geteuid() != 0:
        sys.exit(f"Erro: '{args.cmd}' grava em {args.log if args.cmd == 'test' else args.output_dir} "
                 f"e precisa ser executado como root. Use: sudo marcelinoSpeedtest {args.cmd}")
    if getattr(args, "n", 1) < 1 or (getattr(args, "hours", None) is not None and args.hours < 1):
        parser.error("n e hours devem ser maiores que zero")
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
