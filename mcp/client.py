"""
client.py — HTTP client for all MCP → digicrm API calls.

Rules:
  - tenant_id is ALWAYS from config, never from agent input
  - JWT token is injected here, never passed by the agent
  - All errors raise McpApiError with a human-readable message
"""

import logging
import requests
from typing import Any

from . import config

logger = logging.getLogger(__name__)


class McpApiError(Exception):
    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code


def _headers(extra: dict = None) -> dict:
    """Build standard headers for digicrm API calls."""
    h = {
        'Authorization': f'Bearer {config.DIGICRM_JWT_TOKEN}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-Tenant-ID': config.DIGICRM_TENANT_ID,
    }
    # Inject WhatsApp vendor credentials when available (so digicrm can call Laravel)
    if config.WA_VENDOR_UID:
        h['X-WA-Vendor-Uid'] = config.WA_VENDOR_UID
    if config.WA_API_TOKEN:
        h['X-WA-Api-Token'] = config.WA_API_TOKEN
    if config.WA_BASE_URL:
        h['X-WA-Base-Url'] = config.WA_BASE_URL
    if extra:
        h.update(extra)
    return h


def request(method: str, path: str, **kwargs) -> Any:
    """
    Make an authenticated request to digicrm Django API.
    Returns parsed JSON on success, raises McpApiError on failure.
    """
    url = f"{config.DIGICRM_BASE_URL}{path}"
    logger.debug(f"[MCP] {method} {url} kwargs={list(kwargs.keys())}")

    try:
        resp = requests.request(
            method, url,
            headers=_headers(),
            timeout=30,
            **kwargs
        )
    except requests.exceptions.Timeout:
        raise McpApiError("Request to digicrm timed out", 504)
    except requests.exceptions.ConnectionError as e:
        raise McpApiError(f"Cannot connect to digicrm: {e}", 503)

    # Try to parse JSON
    try:
        data = resp.json()
    except Exception:
        raise McpApiError(f"digicrm returned non-JSON (HTTP {resp.status_code})", 502)

    if resp.status_code >= 400:
        # Extract best error message
        msg = (
            data.get('detail')
            or data.get('error')
            or data.get('message')
            or f"HTTP {resp.status_code}"
        )
        raise McpApiError(f"digicrm error: {msg}", resp.status_code)

    return data


def get(path: str, params: dict = None) -> Any:
    return request('GET', path, params=params)

def post(path: str, body: dict = None) -> Any:
    return request('POST', path, json=body or {})

def patch(path: str, body: dict = None) -> Any:
    return request('PATCH', path, json=body or {})

def put(path: str, body: dict = None) -> Any:
    return request('PUT', path, json=body or {})

def delete(path: str) -> Any:
    return request('DELETE', path)

def delete_with_body(path: str, body: dict = None) -> Any:
    """DELETE with a JSON body (for unenroll endpoints)."""
    return request('DELETE', path, json=body or {})

def post_multipart(path: str, files: dict, data: dict = None) -> Any:
    """POST with multipart/form-data (for file uploads)."""
    url = f"{config.DIGICRM_BASE_URL}{path}"
    # Don't set Content-Type header — requests sets it with boundary automatically
    h = _headers()
    h.pop('Content-Type', None)
    try:
        resp = requests.post(url, headers=h, files=files, data=data or {}, timeout=60)
    except requests.exceptions.Timeout:
        raise McpApiError("Upload request to digicrm timed out", 504)
    except requests.exceptions.ConnectionError as e:
        raise McpApiError(f"Cannot connect to digicrm: {e}", 503)

    try:
        data_resp = resp.json()
    except Exception:
        raise McpApiError(f"digicrm returned non-JSON (HTTP {resp.status_code})", 502)

    if resp.status_code >= 400:
        msg = data_resp.get('detail') or data_resp.get('error') or f"HTTP {resp.status_code}"
        raise McpApiError(f"digicrm error: {msg}", resp.status_code)

    return data_resp
