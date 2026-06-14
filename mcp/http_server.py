"""
DigiCRM MCP HTTP Server (SSE transport)
========================================
This lets you register the MCP as a remote connector in:
  - Claude Desktop → Settings → Connectors → Add custom connector
  - Claude.ai → Settings → Integrations → Add MCP server
  - Claude in Chrome → when hosted on production

Usage (development):
    cd C:\ritik\AAAAA\digicrm
    python mcp/http_server.py

URL to add as custom connector:
    http://localhost:8765/sse   (local)
    https://yourdomain.com/mcp/sse   (production)

Production (Django integration):
    Add the Django view in mcp/django_view.py to your urlconf.
"""

import json
import sys
import os
import time
import threading
import queue
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# ── env is set by Claude Desktop / docker / systemd ────────────────────────
# For local dev, you can also set these here:
# os.environ.setdefault('DIGICRM_BASE_URL',  'http://localhost:8000')
# os.environ.setdefault('DIGICRM_JWT_TOKEN', 'your_token')
# os.environ.setdefault('DIGICRM_TENANT_ID', 'your_tenant_id')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp import server as mcp_server
from mcp.server import _handle_request

HOST = os.environ.get('MCP_HTTP_HOST', '0.0.0.0')
PORT = int(os.environ.get('MCP_HTTP_PORT', '8765'))


class MCPSSEHandler(BaseHTTPRequestHandler):
    """Minimal SSE transport for MCP JSON-RPC 2.0."""

    def log_message(self, format, *args):
        # Suppress default access log noise
        pass

    def _cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path

        if path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self._cors_headers()
            self.end_headers()
            self.wfile.write(b'{"status":"ok","server":"digicrm-mcp"}')
            return

        if path == '/sse':
            self._handle_sse()
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path

        if path == '/message':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                request = json.loads(body)
                response = _handle_request(request)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self._cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps(response).encode())
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return

        self.send_response(404)
        self.end_headers()

    def _handle_sse(self):
        """Server-Sent Events endpoint — keeps connection alive for MCP."""
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self._cors_headers()
        self.end_headers()

        # Send the endpoint URL so the client knows where to POST messages
        endpoint_url = f'http://{self.headers.get("Host", f"localhost:{PORT}")}/message'
        self._sse_event('endpoint', endpoint_url)

        # Keep alive with heartbeats
        try:
            while True:
                self._sse_comment('heartbeat')
                time.sleep(15)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _sse_event(self, event: str, data: str):
        msg = f'event: {event}\ndata: {data}\n\n'
        self.wfile.write(msg.encode())
        self.wfile.flush()

    def _sse_comment(self, text: str):
        self.wfile.write(f': {text}\n\n'.encode())
        self.wfile.flush()


def run():
    from mcp import config
    try:
        config.validate()
    except EnvironmentError as e:
        print(f'❌ Config error: {e}')
        print('Set DIGICRM_BASE_URL, DIGICRM_JWT_TOKEN, DIGICRM_TENANT_ID env vars.')
        sys.exit(1)

    httpd = HTTPServer((HOST, PORT), MCPSSEHandler)
    print(f'✅ DigiCRM MCP HTTP server running on http://{HOST}:{PORT}')
    print(f'   SSE endpoint : http://localhost:{PORT}/sse')
    print(f'   POST endpoint: http://localhost:{PORT}/message')
    print(f'   Health check : http://localhost:{PORT}/health')
    print(f'\nAdd this as custom connector in Claude Desktop:')
    print(f'   http://localhost:{PORT}/sse')
    print('\nPress Ctrl+C to stop.')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')


if __name__ == '__main__':
    run()
