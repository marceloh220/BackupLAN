#!/usr/bin/env python3
"""
    file: marcelinoWeb.py
    Description: Dashboard web (somente biblioteca padrão) com login de usuários
    que exibe as métricas publicadas pelo marcelinoServer.

    Uso:
        marcelinoWeb.py serve [--host 0.0.0.0] [--port 8080] [--status ARQUIVO]
        marcelinoWeb.py useradd NOME      cadastra usuário (senha pedida no terminal)
        marcelinoWeb.py passwd NOME       altera a senha
        marcelinoWeb.py userdel NOME      remove o usuário
        marcelinoWeb.py users             lista usuários

    Observation: instalar com: sudo nas-setup.sh --web

    MIT License - Copyright (c) 2026 Marcelo H Moraes
"""

import argparse
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import ssl
import sys
import tempfile
import threading
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs

DEFAULT_USERS = "/etc/marcelinoWeb/users.json"
DEFAULT_STATUS = "/srv/nas/monitor/.marcelinoSrv.json"
COOKIE_NAME = "marcelino_session"
SESSION_TTL = 8 * 3600
PBKDF2_ITERATIONS = 600_000
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
MAX_BODY = 4096
MAX_FAILURES = 5
LOCKOUT_SECONDS = 300

_sessions = {}
_failures = {}
_lock = threading.Lock()
# Hash fictício para igualar o tempo de resposta quando o usuário não existe.
_DUMMY_HASH = None


# ---------------------------------------------------------------- usuários

def _hash_password(password, salt=None, iterations=PBKDF2_ITERATIONS):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return {"salt": salt.hex(), "hash": digest.hex(), "iterations": iterations}


def _verify_password(password, record):
    try:
        salt = bytes.fromhex(record["salt"])
        expected = bytes.fromhex(record["hash"])
        iterations = int(record["iterations"])
    except (KeyError, ValueError, TypeError):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(digest, expected)


def load_users(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    return data if isinstance(data, dict) else {}


def save_users(path, users):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(users, handle, indent=2)
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _read_new_password():
    if not sys.stdin.isatty():
        password = sys.stdin.readline().rstrip("\n")
        if len(password) < 8:
            sys.exit("A senha deve ter ao menos 8 caracteres.")
        return password
    first = getpass.getpass("Senha: ")
    if len(first) < 8:
        sys.exit("A senha deve ter ao menos 8 caracteres.")
    if first != getpass.getpass("Confirme a senha: "):
        sys.exit("As senhas não conferem.")
    return first


def cli_users(args):
    users = load_users(args.users)
    if args.command == "users":
        print("\n".join(sorted(users)) or "(nenhum usuário)")
        return
    if not USERNAME_RE.match(args.name):
        sys.exit("Nome inválido: use letras, números, '.', '_' ou '-' (até 32).")
    if args.command == "useradd":
        if args.name in users:
            sys.exit("Usuário já existe; use 'passwd' para alterar a senha.")
        users[args.name] = _hash_password(_read_new_password())
    elif args.command == "passwd":
        if args.name not in users:
            sys.exit("Usuário não encontrado.")
        users[args.name] = _hash_password(_read_new_password())
    elif args.command == "userdel":
        if users.pop(args.name, None) is None:
            sys.exit("Usuário não encontrado.")
    save_users(args.users, users)
    print("OK")


# ---------------------------------------------------------------- sessões

def _new_session(username):
    token = secrets.token_urlsafe(32)
    with _lock:
        now = time.time()
        for key in [k for k, v in _sessions.items() if v[1] < now]:
            del _sessions[key]
        _sessions[token] = (username, now + SESSION_TTL)
    return token


def _session_user(token):
    if not token:
        return None
    with _lock:
        entry = _sessions.get(token)
        if entry and entry[1] >= time.time():
            return entry[0]
        _sessions.pop(token, None)
    return None


def _is_locked(ip):
    with _lock:
        count, until = _failures.get(ip, (0, 0))
        if count >= MAX_FAILURES and until > time.time():
            return True
        if until and until <= time.time():
            _failures.pop(ip, None)
    return False


def _register_failure(ip):
    with _lock:
        count, _ = _failures.get(ip, (0, 0))
        _failures[ip] = (count + 1, time.time() + LOCKOUT_SECONDS)


# ---------------------------------------------------------------- páginas

LOGIN_HTML = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>marcelinoServer - Login</title><link rel="icon" href="/static/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="/static/style.css"></head>
<body class="login"><form method="post" action="/login" class="card">
<h1>marcelinoServer</h1>__ERROR__
<label>Usuário<input name="username" autocomplete="username" required autofocus maxlength="32"></label>
<label>Senha<input name="password" type="password" autocomplete="current-password" required maxlength="256"></label>
<button type="submit">Entrar</button></form></body></html>"""

DASH_HTML = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>marcelinoServer - Dashboard</title><link rel="icon" href="/static/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="/static/style.css"></head>
<body><header><h1 id="host">marcelinoServer</h1>
<span id="meta"></span><form method="post" action="/logout"><span id="user"></span>
<button type="submit">Sair</button></form></header>
<main id="main"><p>Carregando...</p></main>
<script src="/static/app.js"></script></body></html>"""

FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<rect width="64" height="64" rx="12" fill="#1b2030"/>'
    '<rect x="10" y="12" width="44" height="14" rx="3" fill="#4c8dff"/>'
    '<rect x="10" y="30" width="44" height="14" rx="3" fill="#4c8dff"/>'
    '<circle cx="18" cy="19" r="2.5" fill="#5fd38d"/><circle cx="18" cy="37" r="2.5" fill="#5fd38d"/>'
    '<rect x="10" y="48" width="44" height="5" rx="2" fill="#8c93a8"/></svg>'
)

STYLE_CSS = """
*{box-sizing:border-box}
body{margin:0;font:14px system-ui,sans-serif;background:#12151c;color:#e3e6ee}
header{display:flex;gap:16px;align-items:center;padding:12px 20px;background:#1b2030;flex-wrap:wrap}
header h1{margin:0;font-size:18px;flex:1}
header form{display:flex;gap:10px;align-items:center;margin:0}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:14px;padding:16px}
section,.card{background:#1b2030;border-radius:8px;padding:14px;overflow-x:auto}
h2{margin:0 0 10px;font-size:15px;color:#8fb4ff}
table{width:100%;border-collapse:collapse}
td,th{text-align:left;padding:3px 8px 3px 0;vertical-align:top;font-size:13px}
th{color:#8c93a8;font-weight:500}
.bar{height:8px;background:#2a3147;border-radius:4px;overflow:hidden;min-width:80px}
.bar>i{display:block;height:100%;background:#4c8dff}
.bar>i.warn{background:#e0a030}.bar>i.crit{background:#e05050}
.ok{color:#5fd38d}.bad{color:#ff6b6b}.muted{color:#8c93a8}
button{background:#4c8dff;color:#fff;border:0;border-radius:6px;padding:7px 14px;cursor:pointer}
body.login{display:grid;place-items:center;min-height:100vh}
.login .card{width:min(360px,92vw);display:grid;gap:12px}
.login h1{margin:0;font-size:20px}
label{display:grid;gap:4px;color:#8c93a8}
input{padding:8px;border-radius:6px;border:1px solid #2a3147;background:#12151c;color:#e3e6ee}
.err{color:#ff6b6b;margin:0}
"""

APP_JS = r"""
const $ = (t, a, kids) => {
  const e = document.createElement(t);
  if (a) for (const k in a) { if (k === 'class') e.className = a[k]; else e.textContent = a[k]; }
  (kids || []).forEach(c => e.append(c));
  return e;
};
const txt = v => (v === null || v === undefined || v === '') ? '-' : String(v);
const bytes = n => {
  if (typeof n !== 'number') return '-';
  const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return n.toFixed(i ? 1 : 0) + ' ' + u[i];
};
const dur = s => {
  if (typeof s !== 'number') return '-';
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return (d ? d + 'd ' : '') + h + 'h ' + m + 'm';
};
const bar = p => {
  const v = Math.max(0, Math.min(100, Number(p) || 0));
  const i = $('i'); i.style.width = v + '%';
  if (v >= 90) i.className = 'crit'; else if (v >= 75) i.className = 'warn';
  return $('div', {class: 'bar'}, [i]);
};
const table = (rows) => {
  const t = $('table');
  rows.forEach(r => t.append($('tr', null, r.map((c, i) =>
    c instanceof Node ? $('td', null, [c]) : $(i === 0 && rows.pairs ? 'th' : 'td', {t: txt(c)})))));
  return t;
};
const kv = pairs => { const r = pairs.map(p => [p[0], p[1]]); r.pairs = true; return table(r); };
const ICONS = {
  'Sistema': '\u{1F5A5}\uFE0F', 'CPU e memória': '\u{1F4CA}', 'Rede e internet': '\u{1F310}',
  'Armazenamento': '\u{1F4BE}', 'Discos (SMART)': '\u{1FA7A}', 'Compartilhamentos Samba': '\u{1F4C1}',
    'Estado do RAID': '\u{1F5C3}\uFE0F',
  'DNS / DHCP': '\u{1F9ED}', 'Leases DHCP': '\u{1F4CB}', 'Consultas bloqueadas recentes': '\u{1F6E1}\uFE0F',
};
const sec = (title, ...kids) => $('section', null, [$('h2', {t: (ICONS[title] || '') + ' ' + title}), ...kids]);
const cell = (v, cls) => { const e = $('span', {t: txt(v)}); if (cls) e.className = cls; return e; };

function render(s) {
  const main = document.getElementById('main');
  main.replaceChildren();
  document.getElementById('host').textContent = '\u{1F5C4}\uFE0F ' + txt(s.hostname);
  document.getElementById('meta').textContent = 'Atualizado: ' + (s.sampled_at ? new Date(s.sampled_at).toLocaleTimeString() : '-');
  const si = s.system_info || {};
  main.append(sec('Sistema', kv([
    ['SO', si.os_name], ['Kernel', si.kernel], ['Modelo', si.computer_model],
    ['CPU', si.cpu_model], ['GPU', (si.gpu_models || []).join(', ')],
    ['Pacotes', si.package_count && si.package_count.count],
    ['Uptime', dur(s.uptime_seconds)],
  ])));
  const m = s.memory || {};
  main.append(sec('CPU e memória', kv([
    ['CPU', s.cpu_percent + '% (' + txt(s.cpu_count) + ' núcleos)'],
  ]), bar(s.cpu_percent), kv([
    ['Frequência', s.cpu_frequency_mhz ? Math.round(s.cpu_frequency_mhz) + ' MHz' : '-'],
    ['Temperatura', s.cpu_temperature_c != null ? s.cpu_temperature_c.toFixed(0) + ' °C' : '-'],
    ['Memória', bytes(m.used_bytes) + ' / ' + bytes(m.total_bytes) + ' (' + txt(m.percent) + '%)'],
  ]), bar(m.percent)));
  const net = s.network || {};
  const inet = s.internet || {};
  const last = (inet.tests || []).slice(-1)[0] || {};
  main.append(sec('Rede e internet', kv([
    ['IP local', inet.local_ip], ['IP público', inet.remote_ip],
    ['Enviado', bytes(net.bytes_sent)], ['Recebido', bytes(net.bytes_received)],
    ['Último teste', last.timestamp ? new Date(last.timestamp).toLocaleString() : '-'],
    ['Download', last.download_mbps != null ? last.download_mbps + ' Mbps' : '-'],
    ['Upload', last.upload_mbps != null ? last.upload_mbps + ' Mbps' : '-'],
    ['Ping', last.ping_ms != null ? last.ping_ms + ' ms' : '-'],
  ])));
  const parts = (s.shared_partitions || []).concat(s.filesystems || []);
  const seen = new Set();
  const fsRows = [];
  parts.forEach(p => {
    if (seen.has(p.mountpoint)) return; seen.add(p.mountpoint);
    fsRows.push([p.mountpoint, bytes(p.used_bytes) + ' / ' + bytes(p.total_bytes), bar(p.percent)]);
  });
  main.append(sec('Armazenamento', table(fsRows)));
    const raid = s.raid || {};
    const raidStatus = raid.status || 'Indisponível';
    const raidClass = raidStatus === 'OK' ? 'ok' :
        ['Degradada', 'Inativa'].includes(raidStatus) ? 'bad' : 'muted';
    const raidRows = [['Array', 'Nível', 'Estado', 'Discos', 'Atividade']].concat(
        (raid.arrays || []).map(a => [
            a.name, a.level, cell(a.status, a.status === 'Ativa' ? 'ok' :
                a.status === 'Degradada' || a.status === 'Inativa' ? 'bad' : 'muted'),
            a.active_devices != null && a.total_devices != null ?
                a.active_devices + '/' + a.total_devices : '-',
            a.sync_action ? a.sync_action + ' ' + a.progress_percent + '%' : '-',
        ]));
    if (raidRows.length === 1) raidRows.push(['-', '-', cell(raidStatus, raidClass), '-', '-']);
    main.append(sec('Estado do RAID', table(raidRows)));
  const dRows = [['Disco', 'Modelo', 'Temp.', 'SMART']].concat((s.physical_disks || []).map(d => [
    d.name, d.model, d.temperature_c != null ? d.temperature_c + ' °C' : '-',
    cell(d.smart_status, d.smart_status === 'OK' ? 'ok' : d.smart_status === 'Falha' ? 'bad' : 'muted'),
  ]));
  main.append(sec('Discos (SMART)', table(dRows)));
  main.append(sec('Compartilhamentos Samba', table([['Nome', 'Caminho']].concat(
        (s.samba_shares || [])
            .filter(x => !['no', 'false', '0'].includes(String(x.browseable ?? '').trim().toLowerCase()))
            .map(x => [x.name, x.path])))));
  const dns = s.dns_dhcp || {};
  main.append(sec('DNS / DHCP', kv([
    ['Serviço', dns.service_state], ['DNS', dns.dns_enabled ? 'ativo' : 'inativo'],
    ['DHCP', dns.dhcp_enabled ? 'ativo' : 'inativo'], ['Leases', dns.lease_count],
    ['Bloqueios (1h)', (dns.blocked_events || []).length],
  ])));
  main.append(sec('Leases DHCP', table([['IP', 'MAC', 'Host']].concat(
    (dns.leases || []).map(l => [l.ip, l.mac, l.hostname || l.name])))));
  main.append(sec('Consultas bloqueadas recentes', table(
    (dns.blocked_events || []).slice(-15).reverse().map(e => [
      e.timestamp ? new Date(e.timestamp).toLocaleTimeString() : '-', e.message]))));
}

async function refresh() {
  try {
    const r = await fetch('/api/status', {credentials: 'same-origin'});
    if (r.status === 401) { location.href = '/login'; return; }
    if (!r.ok) throw new Error(r.status);
    render(await r.json());
  } catch (e) {
    document.getElementById('meta').textContent = 'Falha ao atualizar';
  }
}
fetch('/api/me').then(r => r.json()).then(u => { document.getElementById('user').textContent = u.user; });
refresh();
setInterval(refresh, 5000);
"""


# ---------------------------------------------------------------- servidor

class Handler(BaseHTTPRequestHandler):
    server_version = "marcelinoWeb"
    sys_version = ""

    def _send(self, code, body, ctype="text/html; charset=utf-8", headers=()):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; form-action 'self'",
        )
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location, headers=()):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()

    def _token(self):
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except cookies.CookieError:
            return None
        morsel = jar.get(COOKIE_NAME)
        return morsel.value if morsel else None

    def _cookie(self, value, max_age):
        flags = f"{COOKIE_NAME}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}"
        if cast("Server", self.server).tls:
            flags += "; Secure"
        return ("Set-Cookie", flags)

    def _login_page(self, error=""):
        message = f'<p class="err">{error}</p>' if error else ""
        self._send(200, LOGIN_HTML.replace("__ERROR__", message))

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/static/style.css":
            return self._send(200, STYLE_CSS, "text/css; charset=utf-8")
        if path == "/static/app.js":
            return self._send(200, APP_JS, "application/javascript; charset=utf-8")
        if path == "/static/favicon.svg":
            return self._send(200, FAVICON_SVG, "image/svg+xml")
        if path == "/login":
            return self._login_page()
        user = _session_user(self._token())
        if path == "/":
            return self._send(200, DASH_HTML) if user else self._redirect("/login")
        if path in ("/api/status", "/api/me"):
            if not user:
                return self._send(401, '{"error":"unauthorized"}', "application/json")
            if path == "/api/me":
                return self._send(200, json.dumps({"user": user}), "application/json")
            try:
                with open(cast("Server", self.server).status_path, encoding="utf-8") as handle:
                    body = handle.read()
                json.loads(body)
            except (OSError, ValueError):
                return self._send(503, '{"error":"status unavailable"}', "application/json")
            return self._send(200, body, "application/json")
        self._send(404, "Não encontrado", "text/plain; charset=utf-8")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            return self._send(413, "Requisição inválida", "text/plain; charset=utf-8")
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))

        if path == "/logout":
            token = self._token()
            with _lock:
                _sessions.pop(token, None)
            return self._redirect("/login", [self._cookie("", 0)])
        if path != "/login":
            return self._send(404, "Não encontrado", "text/plain; charset=utf-8")

        ip = self.client_address[0]
        if _is_locked(ip):
            return self._send(429, LOGIN_HTML.replace(
                "__ERROR__", '<p class="err">Muitas tentativas. Aguarde alguns minutos.</p>'))
        username = (form.get("username") or [""])[0]
        password = (form.get("password") or [""])[0]
        record = cast("Server", self.server).users().get(username)
        valid = _verify_password(password, record or _DUMMY_HASH)
        if record is None or not valid:
            _register_failure(ip)
            self.log_message("login falhou usuario=%r", username[:32])
            return self._send(401, LOGIN_HTML.replace(
                "__ERROR__", '<p class="err">Usuário ou senha inválidos.</p>'))
        with _lock:
            _failures.pop(ip, None)
        token = _new_session(username)
        self._redirect("/", [self._cookie(token, SESSION_TTL)])


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, users_path, status_path, tls):
        super().__init__(address, Handler)
        self.users_path = users_path
        self.status_path = status_path
        self.tls = tls

    def users(self):
        return load_users(self.users_path)


def serve(args):
    global _DUMMY_HASH
    _DUMMY_HASH = _hash_password(secrets.token_hex(8))
    tls = bool(args.cert and args.key)
    server = Server((args.host, args.port), args.users, args.status, tls)
    if tls:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(args.cert, args.key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    print(f"Dashboard em {'https' if tls else 'http'}://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def main():
    parser = argparse.ArgumentParser(description="Dashboard web do marcelinoServer")
    parser.add_argument("--users", default=os.environ.get("MARCELINOWEB_USERS", DEFAULT_USERS))
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("serve")
    run.add_argument("--host", default="0.0.0.0")
    run.add_argument("--port", type=int, default=8080)
    run.add_argument("--status", default=DEFAULT_STATUS)
    run.add_argument("--cert")
    run.add_argument("--key")
    for name in ("useradd", "passwd", "userdel"):
        sub.add_parser(name).add_argument("name")
    sub.add_parser("users")
    args = parser.parse_args()
    if args.command == "serve":
        serve(args)
    else:
        cli_users(args)


if __name__ == "__main__":
    main()
