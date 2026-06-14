"""
DigiCRM MCP Django View
========================
Mounts the MCP server as Django endpoints so Claude in Chrome
and Claude Desktop custom connectors can connect in production.

Setup in main urls.py:
    from mcp.django_view import mcp_urlpatterns
    urlpatterns += mcp_urlpatterns

Custom connector URL (Claude Desktop / Claude.ai / Claude in Chrome):
    https://crm.celiyo.com/mcp/sse

Auth:
    Generate a long-lived tenant JWT from admin.celiyo.com.
    In Claude's custom connector settings → Auth type: Bearer Token → paste the JWT.
    Claude sends it as:  Authorization: Bearer <jwt>
    This view extracts it and uses it for all downstream digicrm API calls.
    tenant_id is still read from the JWT payload (never from the agent).
"""

import json
import time
import threading
import logging

from django.http import JsonResponse, StreamingHttpResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.urls import path

logger = logging.getLogger(__name__)

# Thread-local storage so each request carries its own token
_local = threading.local()


def _cors(response):
    response['Access-Control-Allow-Origin'] = '*'
    response['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response


def _extract_token(request) -> str | None:
    """Extract Bearer token from Authorization header."""
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        return auth[7:].strip()
    return None


def _handle_request_with_token(body: dict, token: str) -> dict:
    """
    Run _handle_request but override the JWT with the per-request token.
    This allows each Claude connector to use its own tenant-specific JWT
    without sharing a single hardcoded server-side secret.
    """
    # Temporarily override the JWT in the client module for this request
    import mcp.client as client_module
    import mcp.config as config_module

    # Save original
    original_token = config_module.DIGICRM_JWT_TOKEN

    try:
        if token:
            config_module.DIGICRM_JWT_TOKEN = token
        from mcp.server import _handle_request
        return _handle_request(body)
    finally:
        # Restore original (important for thread safety with env-based tokens)
        config_module.DIGICRM_JWT_TOKEN = original_token


def mcp_health(request):
    return _cors(JsonResponse({'status': 'ok', 'server': 'digicrm-mcp'}))


def mcp_sse(request):
    """
    SSE stream — Claude connects here to establish the session.
    Returns the /mcp/message URL for Claude to POST tool calls to.
    """
    token = _extract_token(request)
    if not token:
        return _cors(JsonResponse({'error': 'Missing Bearer token. Generate a JWT from admin.celiyo.com and add it as Bearer auth in the custom connector settings.'}, status=401))

    def event_stream():
        scheme = 'https' if request.is_secure() else 'http'
        host = request.get_host()
        endpoint = f'{scheme}://{host}/mcp/message'
        yield f'event: endpoint\ndata: {endpoint}\n\n'

        while True:
            yield ': heartbeat\n\n'
            time.sleep(15)

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'  # disable nginx buffering
    return _cors(response)


@csrf_exempt
def mcp_message(request):
    """POST endpoint — receives JSON-RPC 2.0 tool calls from Claude."""
    if request.method == 'OPTIONS':
        return _cors(HttpResponse())

    if request.method != 'POST':
        return HttpResponse(status=405)

    token = _extract_token(request)
    if not token:
        return _cors(JsonResponse({'error': 'Missing Bearer token'}, status=401))

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return _cors(JsonResponse({'error': 'Invalid JSON'}, status=400))

    try:
        result = _handle_request_with_token(body, token)
        return _cors(JsonResponse(result, safe=False))
    except Exception as e:
        logger.exception('MCP request failed')
        return _cors(JsonResponse({'error': str(e)}, status=500))


mcp_urlpatterns = [
    path('mcp/health',  mcp_health),
    path('mcp/sse',     mcp_sse),
    path('mcp/message', mcp_message),
]
