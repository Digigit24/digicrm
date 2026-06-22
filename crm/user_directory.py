"""
User directory proxy.

The CRM does not own users — they live in the SuperAdmin auth service
(admin.celiyo.com) and are referenced inside the CRM by UUID via the
``lead.assigned_to`` field. This module is the single place that fetches the
tenant's users so both the REST endpoint (``/api/crm/users/``) and the MCP
production dispatcher can reuse it.

Tenant scoping is enforced by the service JWT's tenant claim, so callers never
pass a tenant id.
"""
import os
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def _service_token() -> str:
    """Admin-issued service JWT (the same token the MCP / auth middleware use)."""
    return (
        getattr(settings, 'MCP_SERVICE_JWT', '')
        or os.environ.get('DIGICRM_JWT_TOKEN', '')
        or os.environ.get('MCP_SERVICE_JWT', '')
    )


def fetch_tenant_users(search: str = None, page_size: int = 100) -> dict:
    """
    Return the tenant's users from admin.celiyo.com.

    Args:
        search: optional name/email filter (passed through to the auth service).
        page_size: max users to return (default 100).

    Returns:
        The auth service's JSON payload (typically ``{count, results: [...]}``),
        each user carrying at least ``id`` (UUID), ``name``/``first_name`` and
        ``email``.

    Raises:
        requests.HTTPError / requests.RequestException on upstream failure.
    """
    base = getattr(settings, 'SUPERADMIN_URL', 'https://admin.celiyo.com').rstrip('/')
    url = f'{base}/api/users/'

    params = {'page_size': page_size}
    if search:
        params['search'] = search

    token = _service_token()
    headers = {'Authorization': f'Bearer {token}'} if token else {}

    resp = requests.get(url, params=params, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json()
