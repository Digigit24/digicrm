"""
Regression tests for the tenant user directory (P0 cross-tenant PII leak).

Covers:
- ``crm.user_directory.fetch_tenant_users`` fails closed without a tenant id
- the upstream request always carries ``x-tenant-id``
- the cache key is tenant-scoped (two tenants never share a cached list)
- pagination follows ``next`` (with safety caps and an off-host guard)
- upstream field drift is normalised into the pinned response shape
- ``GET /api/crm/users/`` returns 403 for a caller with no tenant claim
"""
import os
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory

import requests

from crm import user_directory
from crm.user_directory import (
    ServiceCredentialsMissing,
    TenantScopeRequired,
    fetch_tenant_users,
)
from crm.views import TenantUserListView


TENANT_A = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
TENANT_B = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
USER_A = 'cccccccc-cccc-cccc-cccc-cccccccccccc'
SERVICE_TOKEN = 'test-service-jwt'
UPSTREAM = 'https://admin.test.invalid'

LOCMEM_CACHE = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'user-directory-tests',
    }
}


def _page(results, next_url=None, count=None):
    """Build a DRF PageNumberPagination-shaped upstream payload."""
    return {
        'count': len(results) if count is None else count,
        'next': next_url,
        'previous': None,
        'results': results,
    }


def _raw_user(idx, tenant_label='a', **overrides):
    payload = {
        'id': '{}0000000-0000-0000-0000-{:012d}'.format(tenant_label, idx),
        'email': '{}user{}@example.com'.format(tenant_label, idx),
        'first_name': 'First{}'.format(idx),
        'last_name': 'Last{}'.format(idx),
        'full_name': 'First{} Last{}'.format(idx, idx),
        'is_active': True,
        'avatar': None,
    }
    payload.update(overrides)
    return payload


def _response(payload):
    resp = mock.MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


@override_settings(
    CACHES=LOCMEM_CACHE,
    SUPERADMIN_URL=UPSTREAM,
    MCP_SERVICE_JWT=SERVICE_TOKEN,
    USER_DIRECTORY_CACHE_TTL=300,
)
class FetchTenantUsersScopingTest(SimpleTestCase):
    """The fetch must never leave the caller's tenant."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        env_patch = mock.patch.dict(os.environ, {'DIGICRM_TENANT_ID': ''}, clear=False)
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def test_missing_tenant_id_fails_closed_without_any_http_call(self):
        with mock.patch.object(user_directory.requests, 'get') as get:
            with self.assertRaises(TenantScopeRequired):
                fetch_tenant_users()
        get.assert_not_called()

    def test_blank_tenant_id_fails_closed(self):
        with mock.patch.object(user_directory.requests, 'get') as get:
            with self.assertRaises(TenantScopeRequired):
                fetch_tenant_users(tenant_id='   ')
        get.assert_not_called()

    def test_upstream_request_carries_x_tenant_id_header(self):
        with mock.patch.object(
            user_directory.requests, 'get',
            return_value=_response(_page([_raw_user(1)])),
        ) as get:
            fetch_tenant_users(tenant_id=TENANT_A, search='as', page_size=200)

        self.assertEqual(get.call_count, 1)
        args, kwargs = get.call_args
        self.assertEqual(args[0], '{}/api/users/'.format(UPSTREAM))
        self.assertEqual(kwargs['headers']['x-tenant-id'], TENANT_A)
        self.assertEqual(
            kwargs['headers']['Authorization'], 'Bearer {}'.format(SERVICE_TOKEN)
        )
        self.assertEqual(kwargs['params']['page_size'], 200)
        self.assertEqual(kwargs['params']['search'], 'as')

    def test_page_size_is_clamped(self):
        with mock.patch.object(
            user_directory.requests, 'get',
            return_value=_response(_page([])),
        ) as get:
            fetch_tenant_users(tenant_id=TENANT_A, page_size=100000)
        self.assertEqual(get.call_args[1]['params']['page_size'], user_directory.MAX_PAGE_SIZE)

    @override_settings(MCP_SERVICE_JWT='')
    def test_missing_service_token_fails_loudly(self):
        with mock.patch.dict(
            os.environ, {'DIGICRM_JWT_TOKEN': '', 'MCP_SERVICE_JWT': ''}, clear=False
        ):
            with mock.patch.object(user_directory.requests, 'get') as get:
                with self.assertRaises(ServiceCredentialsMissing):
                    fetch_tenant_users(tenant_id=TENANT_A)
        get.assert_not_called()

    def test_single_tenant_env_fallback_is_used_for_non_http_callers(self):
        """The MCP dispatcher passes no tenant id but pins DIGICRM_TENANT_ID."""
        with mock.patch.dict(os.environ, {'DIGICRM_TENANT_ID': TENANT_B}, clear=False):
            with mock.patch.object(
                user_directory.requests, 'get',
                return_value=_response(_page([_raw_user(1)])),
            ) as get:
                fetch_tenant_users(search=None, page_size=100)
        self.assertEqual(get.call_args[1]['headers']['x-tenant-id'], TENANT_B)


@override_settings(
    CACHES=LOCMEM_CACHE,
    SUPERADMIN_URL=UPSTREAM,
    MCP_SERVICE_JWT=SERVICE_TOKEN,
    USER_DIRECTORY_CACHE_TTL=300,
)
class FetchTenantUsersCacheTest(SimpleTestCase):

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_second_call_is_served_from_cache(self):
        with mock.patch.object(
            user_directory.requests, 'get',
            return_value=_response(_page([_raw_user(1)])),
        ) as get:
            first = fetch_tenant_users(tenant_id=TENANT_A)
            second = fetch_tenant_users(tenant_id=TENANT_A)

        self.assertEqual(get.call_count, 1)
        self.assertEqual(first, second)

    def test_cache_is_tenant_scoped(self):
        responses = {
            TENANT_A: _response(_page([_raw_user(1, 'a')])),
            TENANT_B: _response(_page([_raw_user(9, 'b')])),
        }

        def fake_get(url, params=None, headers=None, timeout=None):
            return responses[headers['x-tenant-id']]

        with mock.patch.object(user_directory.requests, 'get', side_effect=fake_get):
            a_first = fetch_tenant_users(tenant_id=TENANT_A)
            b = fetch_tenant_users(tenant_id=TENANT_B)
            a_again = fetch_tenant_users(tenant_id=TENANT_A)

        self.assertEqual(a_first['results'][0]['email'], 'auser1@example.com')
        self.assertEqual(b['results'][0]['email'], 'buser9@example.com')
        self.assertEqual(a_again, a_first)
        # Tenant B must never be handed tenant A's cached list, and vice versa.
        self.assertNotEqual(a_first['results'], b['results'])

    def test_cache_key_always_contains_tenant_id(self):
        key_a = user_directory._cache_key(TENANT_A, 'asha', 100)
        key_b = user_directory._cache_key(TENANT_B, 'asha', 100)
        self.assertIn(TENANT_A, key_a)
        self.assertIn(TENANT_B, key_b)
        self.assertNotEqual(key_a, key_b)
        # search / page_size also participate
        self.assertNotEqual(key_a, user_directory._cache_key(TENANT_A, 'rao', 100))
        self.assertNotEqual(key_a, user_directory._cache_key(TENANT_A, 'asha', 200))

    def test_use_cache_false_always_hits_upstream(self):
        with mock.patch.object(
            user_directory.requests, 'get',
            return_value=_response(_page([_raw_user(1)])),
        ) as get:
            fetch_tenant_users(tenant_id=TENANT_A, use_cache=False)
            fetch_tenant_users(tenant_id=TENANT_A, use_cache=False)
        self.assertEqual(get.call_count, 2)


@override_settings(
    CACHES=LOCMEM_CACHE,
    SUPERADMIN_URL=UPSTREAM,
    MCP_SERVICE_JWT=SERVICE_TOKEN,
)
class FetchTenantUsersPaginationTest(SimpleTestCase):

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_follows_next_until_exhausted(self):
        """300 users over 3 pages must all come back, not just the first page."""
        page1 = _page(
            [_raw_user(i) for i in range(100)],
            next_url='{}/api/users/?page=2&page_size=100'.format(UPSTREAM),
            count=300,
        )
        page2 = _page(
            [_raw_user(i) for i in range(100, 200)],
            next_url='{}/api/users/?page=3&page_size=100'.format(UPSTREAM),
            count=300,
        )
        page3 = _page([_raw_user(i) for i in range(200, 300)], next_url=None, count=300)

        with mock.patch.object(
            user_directory.requests, 'get',
            side_effect=[_response(page1), _response(page2), _response(page3)],
        ) as get:
            data = fetch_tenant_users(tenant_id=TENANT_A, page_size=100)

        self.assertEqual(get.call_count, 3)
        self.assertEqual(data['count'], 300)
        self.assertEqual(len(data['results']), 300)
        # every page carried the tenant header
        for call in get.call_args_list:
            self.assertEqual(call[1]['headers']['x-tenant-id'], TENANT_A)
        # follow-up requests use the absolute next URL and drop params
        self.assertEqual(get.call_args_list[1][0][0], page1['next'])
        self.assertIsNone(get.call_args_list[1][1]['params'])

    def test_page_loop_is_capped(self):
        endless = _response(
            _page(
                [_raw_user(1)],
                next_url='{}/api/users/?page=2'.format(UPSTREAM),
                count=9999,
            )
        )
        with mock.patch.object(user_directory.requests, 'get', return_value=endless) as get:
            data = fetch_tenant_users(tenant_id=TENANT_A)

        self.assertEqual(get.call_count, user_directory.MAX_PAGES)
        # duplicate ids across the repeated page are collapsed
        self.assertEqual(data['count'], 1)

    def test_off_host_next_link_is_not_followed(self):
        page1 = _page([_raw_user(1)], next_url='https://evil.example.com/api/users/?page=2')
        with mock.patch.object(
            user_directory.requests, 'get', return_value=_response(page1)
        ) as get:
            data = fetch_tenant_users(tenant_id=TENANT_A)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(data['count'], 1)


@override_settings(
    CACHES=LOCMEM_CACHE,
    SUPERADMIN_URL=UPSTREAM,
    MCP_SERVICE_JWT=SERVICE_TOKEN,
)
class NormalizationTest(SimpleTestCase):

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def _fetch(self, raw_users):
        with mock.patch.object(
            user_directory.requests, 'get', return_value=_response(_page(raw_users))
        ):
            return fetch_tenant_users(tenant_id=TENANT_A, use_cache=False)

    def test_response_shape_is_pinned(self):
        data = self._fetch([_raw_user(1)])
        self.assertEqual(sorted(data.keys()), ['count', 'results'])
        self.assertEqual(
            sorted(data['results'][0].keys()),
            ['avatar', 'email', 'first_name', 'full_name', 'id', 'is_active', 'last_name'],
        )

    def test_full_name_is_computed_when_upstream_omits_it(self):
        """Older SuperAdmin builds have no full_name field at all."""
        raw = _raw_user(1)
        raw.pop('full_name')
        data = self._fetch([raw])
        self.assertEqual(data['results'][0]['full_name'], 'First1 Last1')

    def test_full_name_falls_back_to_email(self):
        data = self._fetch([
            _raw_user(2, full_name='', first_name='', last_name='')
        ])
        self.assertEqual(data['results'][0]['full_name'], 'auser2@example.com')

    def test_legacy_profile_picture_maps_to_avatar(self):
        raw = _raw_user(3)
        raw.pop('avatar')
        raw['profile_picture'] = 'https://cdn.example.com/a.png'
        data = self._fetch([raw])
        self.assertEqual(data['results'][0]['avatar'], 'https://cdn.example.com/a.png')

    def test_extra_upstream_fields_are_dropped(self):
        raw = _raw_user(4)
        raw['roles'] = [{'id': 1, 'name': 'admin'}]
        raw['preferences'] = {'theme': 'dark'}
        raw['tenant'] = TENANT_A
        data = self._fetch([raw])
        self.assertNotIn('roles', data['results'][0])
        self.assertNotIn('preferences', data['results'][0])
        self.assertNotIn('tenant', data['results'][0])

    def test_bare_list_payload_is_tolerated(self):
        with mock.patch.object(
            user_directory.requests, 'get', return_value=_response([_raw_user(5)])
        ):
            data = fetch_tenant_users(tenant_id=TENANT_A, use_cache=False)
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['full_name'], 'First5 Last5')


@override_settings(
    CACHES=LOCMEM_CACHE,
    SUPERADMIN_URL=UPSTREAM,
    MCP_SERVICE_JWT=SERVICE_TOKEN,
)
class TenantUserListViewTest(SimpleTestCase):
    """GET /api/crm/users/ must be scoped to the caller's own JWT tenant."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.factory = APIRequestFactory()
        self.view = TenantUserListView.as_view()

    def _request(self, path='/api/crm/users/', tenant_id=TENANT_A, **attrs):
        request = self.factory.get(path)
        request.user_id = USER_A
        request.email = 'caller@example.com'
        request.tenant_id = tenant_id
        request.tenant_slug = 'tenant-a'
        request.is_super_admin = False
        request.permissions = {}
        request.enabled_modules = ['crm']
        request.roles = []
        for key, value in attrs.items():
            setattr(request, key, value)
        return request

    def test_missing_tenant_claim_returns_403(self):
        with mock.patch.object(user_directory.requests, 'get') as get:
            response = self.view(self._request(tenant_id=None))
        self.assertEqual(response.status_code, 403)
        get.assert_not_called()

    def test_blank_tenant_claim_returns_403(self):
        with mock.patch.object(user_directory.requests, 'get') as get:
            response = self.view(self._request(tenant_id='   '))
        self.assertEqual(response.status_code, 403)
        get.assert_not_called()

    def test_tenant_comes_from_jwt_not_from_query_params(self):
        with mock.patch.object(
            user_directory.requests, 'get',
            return_value=_response(_page([_raw_user(1)])),
        ) as get:
            response = self.view(
                self._request(
                    path='/api/crm/users/?tenant_id={0}&tenant={0}'.format(TENANT_B)
                )
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(get.call_args[1]['headers']['x-tenant-id'], TENANT_A)

    def test_response_shape(self):
        with mock.patch.object(
            user_directory.requests, 'get',
            return_value=_response(_page([_raw_user(1)])),
        ):
            response = self.view(self._request())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(sorted(response.data.keys()), ['count', 'results'])
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(
            response.data['results'][0],
            {
                'id': 'a0000000-0000-0000-0000-000000000001',
                'email': 'auser1@example.com',
                'first_name': 'First1',
                'last_name': 'Last1',
                'full_name': 'First1 Last1',
                'is_active': True,
                'avatar': None,
            },
        )

    def test_upstream_http_error_returns_502(self):
        error = requests.HTTPError('boom')
        error.response = mock.MagicMock(status_code=500)
        with mock.patch.object(user_directory.requests, 'get', side_effect=error):
            response = self.view(self._request())
        self.assertEqual(response.status_code, 502)

    @override_settings(MCP_SERVICE_JWT='')
    def test_missing_service_token_returns_503(self):
        with mock.patch.dict(
            os.environ, {'DIGICRM_JWT_TOKEN': '', 'MCP_SERVICE_JWT': ''}, clear=False
        ):
            with mock.patch.object(user_directory.requests, 'get') as get:
                response = self.view(self._request())
        self.assertEqual(response.status_code, 503)
        get.assert_not_called()

    def test_two_tenants_do_not_share_the_cached_list(self):
        responses = {
            TENANT_A: _response(_page([_raw_user(1, 'a')])),
            TENANT_B: _response(_page([_raw_user(9, 'b')])),
        }

        def fake_get(url, params=None, headers=None, timeout=None):
            return responses[headers['x-tenant-id']]

        with mock.patch.object(user_directory.requests, 'get', side_effect=fake_get):
            first = self.view(self._request(tenant_id=TENANT_A))
            second = self.view(self._request(tenant_id=TENANT_B))

        self.assertEqual(first.data['results'][0]['email'], 'auser1@example.com')
        self.assertEqual(second.data['results'][0]['email'], 'buser9@example.com')
