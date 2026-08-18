"""Cached access to the tenant user directory.

There is no user table in this service -- ``crm.user_directory.fetch_tenant_users``
is an unbounded, uncached HTTP call to the SuperAdmin auth service. A calendar
would hit it on every page load, so everything here goes through the Django
cache (TTL 300s).

This module never mutates ``crm/user_directory.py``; it only wraps it.
"""
import hashlib
import logging

from django.core.cache import cache

from crm.user_directory import fetch_tenant_users

logger = logging.getLogger(__name__)

CACHE_TTL = 300
COLOR_SLOTS = 12


def cache_key(tenant_id, search=None):
    return f'calendar:members:{tenant_id}:{search or ""}'


def color_index(user_id):
    """Deterministic 0..11 colour slot so a person is the same colour everywhere."""
    digest = hashlib.md5(str(user_id).encode('utf-8')).hexdigest()
    return int(digest[:8], 16) % COLOR_SLOTS


def _display_name(user):
    for key in ('name', 'full_name', 'display_name'):
        value = user.get(key)
        if value:
            return value
    first = (user.get('first_name') or '').strip()
    last = (user.get('last_name') or '').strip()
    combined = f'{first} {last}'.strip()
    return combined or user.get('email') or str(user.get('id') or '')


def fetch_users(tenant_id, search=None, page_size=200, use_cache=True):
    """Return the raw ``{count, results}`` payload, cached per tenant+search.

    Raises whatever ``fetch_tenant_users`` raises (``requests`` exceptions) so
    callers can map upstream failures onto the existing 502 contract.
    """
    key = cache_key(tenant_id, search)
    if use_cache:
        cached = cache.get(key)
        if cached is not None:
            return cached
    payload = fetch_tenant_users(search=search, page_size=page_size)
    cache.set(key, payload, CACHE_TTL)
    return payload


def normalized_users(tenant_id, search=None, page_size=200):
    """``[{user_id, name, email, color_index}]`` for the tenant."""
    payload = fetch_users(tenant_id, search=search, page_size=page_size)
    results = payload.get('results') if isinstance(payload, dict) else payload
    users = []
    for user in (results or []):
        user_id = user.get('id') or user.get('user_id')
        if not user_id:
            continue
        users.append({
            'user_id': str(user_id),
            'name': _display_name(user),
            'email': user.get('email') or '',
            'color_index': color_index(user_id),
        })
    return users


def name_map(tenant_id):
    """``{user_id: display name}``. Never raises -- a directory outage must not
    take down the calendar feed, it just leaves ``owner_name`` null."""
    try:
        return {user['user_id']: user['name'] for user in normalized_users(tenant_id)}
    except Exception as exc:  # noqa: BLE001 - upstream directory is best effort here
        logger.warning('Calendar user directory unavailable: %s', exc)
        return {}
