#!/usr/bin/env python3
"""
VR Brain Map — HTTPS local server with live reload + ngrok tunnel
WebXR requires a secure context (HTTPS).

Usage:
    python3 serve.py [--vault /path/to/vault] [--ngrok]
"""

import http.server, ssl, socket, subprocess, os, sys, re, json, socketserver
import threading, time, hashlib, argparse, queue
from urllib.parse import urlparse, parse_qs, unquote

PORT = 8443
DIR  = os.path.dirname(os.path.abspath(__file__))

CYAN  = '\033[96m'
BOLD  = '\033[1m'
RESET = '\033[0m'
DIM   = '\033[2m'

# ── Fast server (skip socket.getfqdn to avoid DNS hang under launchd) ────────
class FastHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name = '0.0.0.0'
        self.server_port = self.server_address[1]

# ── CLI args ──────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument('--vault', default=None,
    help='Path to Obsidian vault (default: sibling elektro-brain-docs)')
parser.add_argument('--ngrok', action='store_true',
    help='Launch ngrok tunnel after starting server')
args, _ = parser.parse_known_args()

VAULT = args.vault or os.path.expanduser(
    '~/Desktop/elektro-brain/elektro-brain-docs')

# ── SSE clients registry ──────────────────────────────────────────────────────
_sse_clients: list[queue.SimpleQueue] = []
_sse_lock = threading.Lock()

def sse_broadcast(event: str, data: str = ''):
    msg = f'event: {event}\ndata: {data}\n\n'.encode()
    with _sse_lock:
        dead = []
        for q in _sse_clients:
            try:
                q.put_nowait(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            _sse_clients.remove(q)

# ── IP helper ─────────────────────────────────────────────────────────────────
def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'
    finally:
        s.close()

# ── Self-signed cert ──────────────────────────────────────────────────────────
def ensure_cert(ip):
    # Store cert alongside the script so it persists and is reused across reboots.
    # Use EC P-256 (instant generation, no entropy starvation under launchd).
    cert = os.path.join(DIR, 'server.crt')
    key  = os.path.join(DIR, 'server.key')
    if not os.path.exists(cert):
        print(f'{DIM}Generating self-signed certificate…{RESET}')
        subprocess.run([
            'openssl', 'req', '-x509', '-nodes', '-days', '3650',
            '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256',
            '-keyout', key, '-out', cert,
            '-subj', '/CN=vr-obsidian',
            '-addext', f'subjectAltName=IP:{ip},IP:127.0.0.1,DNS:localhost',
        ], check=True, capture_output=True)
        print(f'{DIM}Certificate ready.{RESET}')
    return cert, key

# ── Graph builder ─────────────────────────────────────────────────────────────
def build_graph():
    nodes, node_map = [], {}
    for root, dirs, files in os.walk(VAULT):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for f in files:
            if not f.endswith('.md'):
                continue
            path = os.path.join(root, f)
            rel  = os.path.relpath(path, VAULT)
            name = f[:-3]
            parts = rel.replace('\\', '/').split('/')
            folder = parts[0] if len(parts) > 1 else 'ROOT'
            node_id = rel.replace(os.sep, '/').replace('.md', '')
            node_map[name.upper()] = node_id
            nodes.append({'id': node_id, 'name': name, 'folder': folder, 'path': rel})

    links = []
    for node in nodes:
        path = os.path.join(VAULT, node['path'])
        try:
            content = open(path, encoding='utf-8', errors='ignore').read()
        except Exception:
            continue
        for target in re.findall(r'\[\[([^\]|#]+?)(?:\|[^\]]*)?\]\]', content):
            key = target.strip().upper()
            if key in node_map and node_map[key] != node['id']:
                links.append({'source': node['id'], 'target': node_map[key]})

    out = os.path.join(DIR, 'graph.json')
    with open(out, 'w') as fh:
        json.dump({'nodes': nodes, 'links': links}, fh)
    print(f'  Graph rebuilt: {len(nodes)} notes · {len(links)} links')
    return len(nodes), len(links)

# ── File watcher ──────────────────────────────────────────────────────────────
def _vault_fingerprint():
    h = hashlib.md5()
    for root, dirs, files in os.walk(VAULT):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
        for f in sorted(files):
            if f.endswith('.md'):
                fp = os.path.join(root, f)
                try:
                    stat = os.stat(fp)
                    h.update(f'{fp}:{stat.st_mtime}:{stat.st_size}'.encode())
                except OSError:
                    pass
    return h.hexdigest()

def watcher_thread():
    prev = _vault_fingerprint()
    while True:
        time.sleep(2)
        try:
            cur = _vault_fingerprint()
            if cur != prev:
                prev = cur
                build_graph()
                sse_broadcast('graph_update', 'reload')
        except Exception as e:
            print(f'  Watcher error: {e}')

# ── HTTP handler ──────────────────────────────────────────────────────────────
class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIR, **kwargs)

    def log_message(self, fmt, *args):
        if args and str(args[1]) not in ('200', '304'):
            super().log_message(fmt, *args)

    def do_GET(self):
        parsed = urlparse(self.path)

        # ── SSE live-reload endpoint ──────────────────────────────────────
        if parsed.path == '/events':
            q: queue.SimpleQueue = queue.SimpleQueue()
            with _sse_lock:
                _sse_clients.append(q)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            try:
                while True:
                    try:
                        msg = q.get(timeout=25)
                        self.wfile.write(msg)
                        self.wfile.flush()
                    except queue.Empty:
                        # heartbeat
                        self.wfile.write(b': ping\n\n')
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                with _sse_lock:
                    try:
                        _sse_clients.remove(q)
                    except ValueError:
                        pass
            return

        # ── Note content endpoint ─────────────────────────────────────────
        if parsed.path == '/content':
            qs = parse_qs(parsed.query)
            rel_path = qs.get('path', [''])[0]
            rel_path = unquote(rel_path).replace('..', '')  # safety
            full = os.path.join(VAULT, rel_path)
            if not full.startswith(VAULT):
                self.send_error(403)
                return
            try:
                content = open(full, encoding='utf-8', errors='replace').read()
                data = json.dumps({'path': rel_path, 'content': content}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', len(data))
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(data)
            except FileNotFoundError:
                self.send_error(404)
            return

        # ── Static files (default) ────────────────────────────────────────
        super().do_GET()

# ── ngrok ────────────────────────────────────────────────────────────────────
def launch_ngrok():
    ngrok_bin = subprocess.run(['which', 'ngrok'], capture_output=True, text=True).stdout.strip()
    if not ngrok_bin:
        print(f'  {DIM}ngrok not found — skipping tunnel{RESET}')
        return
    print(f'  Starting ngrok tunnel…')
    proc = subprocess.Popen(
        [ngrok_bin, 'http', '8000', '--log=stdout'],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
    )
    # Wait for tunnel URL
    for line in proc.stdout:
        if 'url=' in line or 'Forwarding' in line:
            url = re.search(r'https://[a-z0-9\-]+\.ngrok[^\s"]+', line)
            if url:
                print(f'\n  {BOLD}{CYAN}  🌐  ngrok tunnel: {url.group()}{RESET}')
                break
        if 'error' in line.lower():
            print(f'  ngrok error: {line.strip()}')
            break

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    ip = local_ip()
    print(f'\n  Vault: {VAULT}')
    build_graph()
    cert, key = ensure_cert(ip)

    server = FastHTTPServer(('0.0.0.0', PORT), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)

    # Start file watcher
    t = threading.Thread(target=watcher_thread, daemon=True)
    t.start()

    # Optional ngrok
    if args.ngrok or '--ngrok' in sys.argv:
        nt = threading.Thread(target=launch_ngrok, daemon=True)
        nt.start()

    url = f'https://{ip}:{PORT}'
    print()
    print(f'{BOLD}{CYAN}  🧠  VR BRAIN MAP  —  HTTPS Server{RESET}')
    print(f'  {"─" * 44}')
    print(f'  Local:      {BOLD}https://localhost:{PORT}{RESET}')
    print(f'  Network:    {BOLD}{CYAN}{url}{RESET}')
    print(f'  Live reload: watching vault for changes')
    print()
    print(f'  {BOLD}Quest 3:{RESET} open {CYAN}{url}{RESET} → Advanced → Proceed → Enter VR')
    print()
    print(f'  {DIM}Press Ctrl+C to stop{RESET}')
    print()

    # Plain HTTP on 8000 for ngrok tunnelling (ngrok provides its own TLS)
    http_server = socketserver.ThreadingTCPServer(('0.0.0.0', 8000), Handler)
    http_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    http_thread.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f'\n  {DIM}Server stopped.{RESET}\n')
        http_server.shutdown()

if __name__ == '__main__':
    main()
