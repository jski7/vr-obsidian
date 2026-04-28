#!/usr/bin/env python3
"""
VR Brain Map — HTTPS local server
WebXR requires a secure context (HTTPS). This script generates a self-signed
certificate and serves the app over HTTPS so Quest 3 can access it.

Usage:
    python3 serve.py

Then on Quest 3 browser: open the URL printed in the terminal.
Accept the security warning once (tap Advanced → Proceed).
"""

import http.server, ssl, socket, subprocess, os, sys, tempfile, re, json

PORT = 8443
DIR  = os.path.dirname(os.path.abspath(__file__))

CYAN  = '\033[96m'
BOLD  = '\033[1m'
RESET = '\033[0m'
DIM   = '\033[2m'


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'
    finally:
        s.close()


def ensure_cert(ip):
    """Generate a self-signed cert valid for the local IP (cached in /tmp)."""
    cert = os.path.join(tempfile.gettempdir(), f'vrbrain_{ip.replace(".","_")}.crt')
    key  = os.path.join(tempfile.gettempdir(), f'vrbrain_{ip.replace(".","_")}.key')
    if not os.path.exists(cert):
        print(f'{DIM}Generating self-signed certificate for {ip}…{RESET}')
        subprocess.run([
            'openssl', 'req', '-x509', '-nodes', '-days', '365',
            '-newkey', 'rsa:2048',
            '-keyout', key,
            '-out',    cert,
            '-subj',   f'/CN={ip}',
            '-addext', f'subjectAltName=IP:{ip},IP:127.0.0.1',
        ], check=True, capture_output=True)
    return cert, key


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIR, **kwargs)

    def log_message(self, fmt, *args):
        # Only log errors, not every request
        if args and str(args[1]) not in ('200', '304'):
            super().log_message(fmt, *args)


def build_graph():
    """Scan parent directory for markdown files and regenerate graph.json."""
    base = os.path.dirname(DIR)   # one level up from VR_BRAIN
    nodes, node_map = [], {}

    for root, dirs, files in os.walk(base):
        if 'VR_BRAIN' in root:
            continue
        for f in files:
            if not f.endswith('.md'):
                continue
            path = os.path.join(root, f)
            rel  = os.path.relpath(path, base)
            name = f[:-3]
            folder = rel.split(os.sep)[0] if os.sep in rel else 'ROOT'
            node_id = rel.replace(os.sep, '/').replace('.md', '')
            node_map[name.upper()] = node_id
            nodes.append({'id': node_id, 'name': name, 'folder': folder, 'path': rel})

    links = []
    for node in nodes:
        path = os.path.join(base, node['path'])
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
    print(f'  Graph rebuilt: {len(nodes)} pages · {len(links)} links')


def main():
    ip   = local_ip()
    build_graph()
    cert, key = ensure_cert(ip)

    httpd = http.server.HTTPServer(('0.0.0.0', PORT), QuietHandler)
    ctx   = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)

    url = f'https://{ip}:{PORT}'

    print()
    print(f'{BOLD}{CYAN}  🧠  VR BRAIN MAP  —  HTTPS Server{RESET}')
    print(f'  {"─" * 40}')
    print(f'  Local:   {BOLD}https://localhost:{PORT}{RESET}')
    print(f'  Quest 3: {BOLD}{CYAN}{url}{RESET}')
    print()
    print(f'  {BOLD}How to open on Quest 3:{RESET}')
    print(f'  1. Make sure Quest 3 is on the same Wi-Fi as this Mac')
    print(f'  2. Open Meta Quest Browser')
    print(f'  3. Navigate to: {CYAN}{url}{RESET}')
    print(f'  4. Tap {BOLD}Advanced → Proceed{RESET} on the certificate warning')
    print(f'  5. Tap the {BOLD}Enter VR{RESET} button that appears')
    print()
    print(f'  {DIM}Press Ctrl+C to stop the server{RESET}')
    print()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print(f'\n  {DIM}Server stopped.{RESET}\n')


if __name__ == '__main__':
    main()
