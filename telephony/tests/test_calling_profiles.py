"""
Tests for TeleCMI calling profiles.

Covers the four-step softphone resolution order, the one-default-per-tenant
constraint, the admin gate on writes, the fact that a SIP password can be
written but never read back, and both outcomes of the verify action.

Every row is built inside Django's test database. Nothing here reads or writes
live tenant data.
"""
import uuid
from unittest.mock import patch

import jwt as pyjwt
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from integrations.utils.encryption import encrypt_token
from telephony.models import (
    TeleCMIAgent, TeleCMICallingProfile, TeleCMICredential,
    TeleCMIProfileAssignment, SBCRegionEnum,
)
from telephony.services.crypto import encrypt_profile_password, encrypt_secret
from telephony.services.softphone_service import (
    REASON_NO_AGENT, SOURCE_ASSIGNED_PROFILE, SOURCE_TENANT_DEFAULT,
    SOURCE_TENANT_PROFILE, SOURCE_USER,
)
from telephony.services.telecmi_client import TeleCMIError

TEST_JWT_SECRET = 'test-jwt-secret-telephony-unit-tests'
TEST_JWT_ALGO = 'HS256'

TENANT = uuid.UUID('11111111-1111-1111-1111-111111111111')
OTHER_TENANT = uuid.UUID('22222222-2222-2222-2222-222222222222')
USER = uuid.UUID('33333333-3333-3333-3333-333333333333')
OTHER_USER = uuid.UUID('44444444-4444-4444-4444-444444444444')

CONFIG_URL = '/api/telephony/webrtc-config/'
PROFILES_URL = '/api/telephony/calling-profiles/'

LEGACY_EXT = '103_9990001'
LEGACY_PASSWORD = 'shared-ext-pw'
AGENT_EXT = '103_9990002'
AGENT_PASSWORD = 'personal-ext-pw'
SALES_EXT = '5001_33338197'
SALES_PASSWORD = 'sales-line-pw'
SUPPORT_EXT = '5002_33338188'
SUPPORT_PASSWORD = 'support-line-pw'


def _client(tenant_id=TENANT, user_id=USER, admin=False):
    payload = {
        'user_id': str(user_id),
        'email': 'test@example.com',
        'tenant_id': str(tenant_id),
        'tenant_slug': 'test-tenant',
        'is_super_admin': False,
        'permissions': {
            'telephony': {
                'calls': {'view': 'all', 'create': True, 'edit': True},
                'settings': {'view': 'all', 'create': True, 'edit': True, 'delete': True},
                'agents': {'view': 'all', 'create': True, 'edit': True, 'delete': True},
            },
        },
        'enabled_modules': ['telephony'],
    }
    if admin:
        payload['permissions']['admin'] = {'full_access': True}
    token = pyjwt.encode(payload, TEST_JWT_SECRET, algorithm=TEST_JWT_ALGO)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
    return client


def _credential(tenant_id=TENANT, with_legacy_default=False):
    secret_encrypted, dek_wrapped = encrypt_secret('app-secret')
    kwargs = {}
    if with_legacy_default:
        pw_encrypted, dek_wrapped = encrypt_secret(LEGACY_PASSWORD, dek_wrapped)
        kwargs = {
            'default_agent_id': LEGACY_EXT,
            'default_agent_password_encrypted': pw_encrypted,
        }
    return TeleCMICredential.objects.create(
        tenant_id=tenant_id,
        app_id='app-123',
        secret_encrypted=secret_encrypted,
        dek_wrapped=dek_wrapped,
        sbc_region=SBCRegionEnum.INDIA,
        is_active=True,
        **kwargs,
    )


def _profile(tenant_id=TENANT, label='Sales line', extension=SALES_EXT,
             password=SALES_PASSWORD, is_default=False, caller_id=None,
             is_active=True):
    encrypted, dek_wrapped = encrypt_profile_password(password, tenant_id=tenant_id)
    return TeleCMICallingProfile.objects.create(
        tenant_id=tenant_id,
        label=label,
        telecmi_user_id=extension,
        password_encrypted=encrypted,
        dek_wrapped=dek_wrapped,
        caller_id=caller_id,
        is_default=is_default,
        is_active=is_active,
    )


# ──────────────────────────────────────────────────────────────
# Resolution order
# ──────────────────────────────────────────────────────────────

@override_settings(JWT_SECRET_KEY=TEST_JWT_SECRET, JWT_ALGORITHM=TEST_JWT_ALGO)
class ResolutionOrderTest(TestCase):
    """All four steps, each demonstrated by removing the one above it."""

    def test_1_personal_agent_row_still_wins_over_everything(self):
        _credential(with_legacy_default=True)
        profile = _profile(is_default=True)
        TeleCMIProfileAssignment.objects.create(
            tenant_id=TENANT, user_id=USER, profile=profile
        )
        TeleCMIAgent.objects.create(
            tenant_id=TENANT, user_id=USER, telecmi_user_id=AGENT_EXT,
            password_encrypted=encrypt_token(AGENT_PASSWORD), is_active=True,
        )

        response = _client().get(CONFIG_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['telecmi_user_id'], AGENT_EXT)
        self.assertEqual(response.data['source'], SOURCE_USER)
        self.assertEqual(response.data['auth']['value'], AGENT_PASSWORD)

    def test_2_assigned_profile_wins_over_tenant_default_profile(self):
        _credential(with_legacy_default=True)
        _profile(label='Support line', extension=SUPPORT_EXT,
                 password=SUPPORT_PASSWORD, is_default=True)
        assigned = _profile(label='Sales line', extension=SALES_EXT,
                            password=SALES_PASSWORD)
        TeleCMIProfileAssignment.objects.create(
            tenant_id=TENANT, user_id=USER, profile=assigned
        )

        response = _client().get(CONFIG_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['telecmi_user_id'], SALES_EXT)
        self.assertEqual(response.data['source'], SOURCE_ASSIGNED_PROFILE)
        self.assertEqual(response.data['auth']['value'], SALES_PASSWORD)

    def test_3_tenant_default_profile_wins_over_legacy_default_agent(self):
        _credential(with_legacy_default=True)
        _profile(label='Support line', extension=SUPPORT_EXT,
                 password=SUPPORT_PASSWORD, is_default=True)

        response = _client().get(CONFIG_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['telecmi_user_id'], SUPPORT_EXT)
        self.assertEqual(response.data['source'], SOURCE_TENANT_PROFILE)
        self.assertEqual(response.data['auth']['value'], SUPPORT_PASSWORD)

    def test_4_legacy_default_agent_still_works_with_no_profiles(self):
        _credential(with_legacy_default=True)

        response = _client().get(CONFIG_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['telecmi_user_id'], LEGACY_EXT)
        self.assertEqual(response.data['source'], SOURCE_TENANT_DEFAULT)
        self.assertEqual(response.data['auth']['value'], LEGACY_PASSWORD)

    def test_5_no_extension_anywhere_is_424_no_agent(self):
        _credential(with_legacy_default=False)

        response = _client().get(CONFIG_URL)

        self.assertEqual(response.status_code, 424)
        self.assertEqual(response.data['reason'], REASON_NO_AGENT)

    def test_contract_keys_are_unchanged(self):
        _credential(with_legacy_default=True)
        _profile(is_default=True, caller_id='918000000001')

        response = _client().get(CONFIG_URL)

        self.assertEqual(
            set(response.data),
            {'telecmi_user_id', 'sbc_host', 'default_caller_id', 'auth', 'source'},
        )
        self.assertEqual(set(response.data['auth']), {'kind', 'value'})
        self.assertEqual(response.data['auth']['kind'], 'password')

    def test_inactive_profile_is_skipped(self):
        _credential(with_legacy_default=True)
        inactive = _profile(is_default=True, is_active=False)
        TeleCMIProfileAssignment.objects.create(
            tenant_id=TENANT, user_id=USER, profile=inactive
        )

        response = _client().get(CONFIG_URL)

        self.assertEqual(response.data['source'], SOURCE_TENANT_DEFAULT)

    def test_profile_without_a_password_is_not_usable(self):
        _credential(with_legacy_default=True)
        TeleCMICallingProfile.objects.create(
            tenant_id=TENANT, label='Half-built', telecmi_user_id=SALES_EXT,
            is_default=True,
        )

        response = _client().get(CONFIG_URL)

        self.assertEqual(response.data['source'], SOURCE_TENANT_DEFAULT)

    def test_profiles_do_not_leak_across_tenants(self):
        _credential(tenant_id=TENANT, with_legacy_default=False)
        _profile(tenant_id=OTHER_TENANT, is_default=True)

        response = _client(tenant_id=TENANT).get(CONFIG_URL)

        self.assertEqual(response.status_code, 424)
        self.assertEqual(response.data['reason'], REASON_NO_AGENT)

    def test_profile_caller_id_is_reported_as_default_caller_id(self):
        cred = _credential(with_legacy_default=True)
        cred.default_caller_id = '910000000000'
        cred.save(update_fields=['default_caller_id'])
        _profile(is_default=True, caller_id='918000000001')

        with patch('telephony.services.softphone_service.push_caller_id'):
            response = _client().get(CONFIG_URL)

        self.assertEqual(response.data['default_caller_id'], '918000000001')


# ──────────────────────────────────────────────────────────────
# The one-default-per-tenant constraint
# ──────────────────────────────────────────────────────────────

class OnlyOneDefaultTest(TestCase):
    def test_second_default_in_the_same_tenant_is_rejected_by_the_database(self):
        _profile(label='Sales line', extension=SALES_EXT, is_default=True)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _profile(label='Support line', extension=SUPPORT_EXT,
                         password=SUPPORT_PASSWORD, is_default=True)

    def test_many_non_default_profiles_are_fine(self):
        _profile(label='Sales line', extension=SALES_EXT, is_default=True)
        _profile(label='Support line', extension=SUPPORT_EXT,
                 password=SUPPORT_PASSWORD, is_default=False)
        _profile(label='Third line', extension='5003_33338188',
                 password='third-pw', is_default=False)

        self.assertEqual(TeleCMICallingProfile.objects.count(), 3)

    def test_each_tenant_gets_its_own_default(self):
        _profile(tenant_id=TENANT, is_default=True)
        _profile(tenant_id=OTHER_TENANT, is_default=True)

        self.assertEqual(
            TeleCMICallingProfile.objects.filter(is_default=True).count(), 2
        )

    def test_same_extension_twice_in_one_tenant_is_rejected(self):
        _profile(extension=SALES_EXT)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _profile(label='Duplicate', extension=SALES_EXT)


@override_settings(JWT_SECRET_KEY=TEST_JWT_SECRET, JWT_ALGORITHM=TEST_JWT_ALGO)
class DefaultSwitchoverTest(TestCase):
    """Promoting a profile through the API demotes the incumbent for you."""

    @patch('telephony.services.telecmi_client.get_user_login_token')
    def test_setting_a_new_default_demotes_the_old_one(self, mock_login):
        mock_login.return_value = 'tok'
        old = _profile(label='Sales line', extension=SALES_EXT, is_default=True)
        new = _profile(label='Support line', extension=SUPPORT_EXT,
                       password=SUPPORT_PASSWORD)

        response = _client(admin=True).patch(
            f'{PROFILES_URL}{new.id}/', {'is_default': True}, format='json'
        )

        self.assertEqual(response.status_code, 200, response.data)
        old.refresh_from_db()
        new.refresh_from_db()
        self.assertFalse(old.is_default)
        self.assertTrue(new.is_default)

    @patch('telephony.services.telecmi_client.get_user_login_token')
    def test_creating_a_default_demotes_the_old_one(self, mock_login):
        mock_login.return_value = 'tok'
        old = _profile(label='Sales line', extension=SALES_EXT, is_default=True)

        response = _client(admin=True).post(
            PROFILES_URL,
            {
                'label': 'Support line', 'telecmi_user_id': SUPPORT_EXT,
                'password': SUPPORT_PASSWORD, 'is_default': True,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        old.refresh_from_db()
        self.assertFalse(old.is_default)


# ──────────────────────────────────────────────────────────────
# The password is write-only
# ──────────────────────────────────────────────────────────────

@override_settings(JWT_SECRET_KEY=TEST_JWT_SECRET, JWT_ALGORITHM=TEST_JWT_ALGO)
class PasswordNeverSerializedTest(TestCase):
    @patch('telephony.services.telecmi_client.get_user_login_token')
    def test_no_response_on_any_verb_contains_the_password(self, mock_login):
        mock_login.return_value = 'tok'
        client = _client(admin=True)

        created = client.post(
            PROFILES_URL,
            {
                'label': 'Sales line', 'telecmi_user_id': SALES_EXT,
                'password': SALES_PASSWORD, 'caller_id': '918000000001',
            },
            format='json',
        )
        self.assertEqual(created.status_code, 201, created.data)
        profile_id = created.data['id']

        listed = client.get(PROFILES_URL)
        detail = client.get(f'{PROFILES_URL}{profile_id}/')
        patched = client.patch(
            f'{PROFILES_URL}{profile_id}/', {'label': 'Renamed'}, format='json'
        )

        for response in (created, listed, detail, patched):
            body = response.content.decode()
            self.assertNotIn(SALES_PASSWORD, body)
            self.assertNotIn('password_encrypted', body)
            self.assertNotIn('dek_wrapped', body)
            self.assertNotIn('cached_token', body)

        self.assertNotIn('password', detail.data)
        self.assertTrue(detail.data['has_password'])

    @patch('telephony.services.telecmi_client.get_user_login_token')
    def test_stored_password_is_encrypted_and_round_trips(self, mock_login):
        mock_login.return_value = 'tok'

        _client(admin=True).post(
            PROFILES_URL,
            {'label': 'Sales line', 'telecmi_user_id': SALES_EXT,
             'password': SALES_PASSWORD},
            format='json',
        )

        profile = TeleCMICallingProfile.objects.get(telecmi_user_id=SALES_EXT)
        self.assertNotIn(SALES_PASSWORD, profile.password_encrypted)
        self.assertTrue(profile.dek_wrapped, 'must use envelope encryption')

        from telephony.services.crypto import decrypt_profile_password
        self.assertEqual(decrypt_profile_password(profile), SALES_PASSWORD)

    @patch('telephony.services.telecmi_client.get_user_login_token')
    def test_profile_shares_the_tenant_credential_data_key(self, mock_login):
        mock_login.return_value = 'tok'
        cred = _credential()

        _client(admin=True).post(
            PROFILES_URL,
            {'label': 'Sales line', 'telecmi_user_id': SALES_EXT,
             'password': SALES_PASSWORD},
            format='json',
        )

        profile = TeleCMICallingProfile.objects.get(telecmi_user_id=SALES_EXT)
        self.assertEqual(profile.dek_wrapped, cred.dek_wrapped)

    def test_the_serializer_exposes_exactly_the_pinned_contract(self):
        _profile()
        response = _client(admin=True).get(PROFILES_URL)
        rows = response.data.get('results', response.data)

        self.assertEqual(
            set(rows[0]),
            {'id', 'label', 'telecmi_user_id', 'caller_id', 'is_default',
             'is_active', 'has_password', 'verified_at', 'verify_error'},
        )

    def test_creating_without_a_password_is_rejected(self):
        response = _client(admin=True).post(
            PROFILES_URL,
            {'label': 'Sales line', 'telecmi_user_id': SALES_EXT},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('password', response.data)


# ──────────────────────────────────────────────────────────────
# Admin gate
# ──────────────────────────────────────────────────────────────

@override_settings(JWT_SECRET_KEY=TEST_JWT_SECRET, JWT_ALGORITHM=TEST_JWT_ALGO)
class AdminGateTest(TestCase):
    def test_non_admin_cannot_create(self):
        response = _client().post(
            PROFILES_URL,
            {'label': 'Sneaky', 'telecmi_user_id': SALES_EXT, 'password': 'x'},
            format='json',
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(TeleCMICallingProfile.objects.exists())

    def test_non_admin_cannot_edit(self):
        profile = _profile(is_default=True)

        response = _client().patch(
            f'{PROFILES_URL}{profile.id}/', {'label': 'Renamed'}, format='json'
        )

        self.assertEqual(response.status_code, 403)
        profile.refresh_from_db()
        self.assertEqual(profile.label, 'Sales line')

    def test_non_admin_cannot_delete(self):
        profile = _profile(is_default=True)

        response = _client().delete(f'{PROFILES_URL}{profile.id}/')

        self.assertEqual(response.status_code, 403)
        self.assertTrue(TeleCMICallingProfile.objects.filter(pk=profile.pk).exists())

    def test_non_admin_cannot_assign_or_verify(self):
        profile = _profile(is_default=True)
        client = _client()

        assign = client.post(
            f'{PROFILES_URL}{profile.id}/assign/', {'user_id': str(OTHER_USER)},
            format='json',
        )
        verify = client.post(f'{PROFILES_URL}{profile.id}/verify/')

        self.assertEqual(assign.status_code, 403)
        self.assertEqual(verify.status_code, 403)
        self.assertFalse(TeleCMIProfileAssignment.objects.exists())

    def test_non_admin_sees_only_the_profiles_they_can_use(self):
        mine = _profile(label='Sales line', extension=SALES_EXT)
        tenant_default = _profile(label='Support line', extension=SUPPORT_EXT,
                                  password=SUPPORT_PASSWORD, is_default=True)
        someone_elses = _profile(label='Finance line', extension='5009_33338188',
                                 password='finance-pw')
        TeleCMIProfileAssignment.objects.create(
            tenant_id=TENANT, user_id=USER, profile=mine
        )
        TeleCMIProfileAssignment.objects.create(
            tenant_id=TENANT, user_id=OTHER_USER, profile=someone_elses
        )

        response = _client().get(PROFILES_URL)
        rows = response.data.get('results', response.data)
        ids = {row['id'] for row in rows}

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ids, {mine.id, tenant_default.id})
        self.assertNotIn(someone_elses.id, ids)

    def test_non_admin_cannot_fetch_another_users_profile_by_id(self):
        someone_elses = _profile(label='Finance line', extension='5009_33338188')
        TeleCMIProfileAssignment.objects.create(
            tenant_id=TENANT, user_id=OTHER_USER, profile=someone_elses
        )

        response = _client().get(f'{PROFILES_URL}{someone_elses.id}/')

        self.assertEqual(response.status_code, 404)

    def test_admin_sees_every_profile_in_the_tenant(self):
        _profile(label='Sales line', extension=SALES_EXT)
        _profile(label='Support line', extension=SUPPORT_EXT, password=SUPPORT_PASSWORD)
        _profile(tenant_id=OTHER_TENANT, label='Other tenant', extension=SALES_EXT)

        response = _client(admin=True).get(PROFILES_URL)
        rows = response.data.get('results', response.data)

        self.assertEqual(len(rows), 2)


# ──────────────────────────────────────────────────────────────
# Assignment endpoints
# ──────────────────────────────────────────────────────────────

@override_settings(JWT_SECRET_KEY=TEST_JWT_SECRET, JWT_ALGORITHM=TEST_JWT_ALGO)
class AssignmentEndpointTest(TestCase):
    def test_assign_then_list_then_unassign(self):
        profile = _profile()
        client = _client(admin=True)

        assigned = client.post(
            f'{PROFILES_URL}{profile.id}/assign/', {'user_id': str(OTHER_USER)},
            format='json',
        )
        self.assertEqual(assigned.status_code, 200, assigned.data)

        listed = client.get(f'{PROFILES_URL}assignments/')
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(
            [dict(row) for row in listed.data],
            [{'user_id': str(OTHER_USER), 'profile_id': profile.id}],
        )

        removed = client.delete(
            f'{PROFILES_URL}{profile.id}/assign/', {'user_id': str(OTHER_USER)},
            format='json',
        )
        self.assertEqual(removed.status_code, 200, removed.data)
        self.assertFalse(TeleCMIProfileAssignment.objects.exists())

    def test_reassigning_a_user_replaces_rather_than_duplicates(self):
        sales = _profile(label='Sales line', extension=SALES_EXT)
        support = _profile(label='Support line', extension=SUPPORT_EXT,
                           password=SUPPORT_PASSWORD)
        client = _client(admin=True)

        client.post(f'{PROFILES_URL}{sales.id}/assign/',
                    {'user_id': str(USER)}, format='json')
        client.post(f'{PROFILES_URL}{support.id}/assign/',
                    {'user_id': str(USER)}, format='json')

        rows = TeleCMIProfileAssignment.objects.filter(tenant_id=TENANT, user_id=USER)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().profile_id, support.id)

    def test_assign_requires_a_user_id(self):
        profile = _profile()

        response = _client(admin=True).post(
            f'{PROFILES_URL}{profile.id}/assign/', {}, format='json'
        )

        self.assertEqual(response.status_code, 400)

    def test_non_admin_assignments_list_shows_only_their_own(self):
        profile = _profile()
        TeleCMIProfileAssignment.objects.create(
            tenant_id=TENANT, user_id=USER, profile=profile
        )
        TeleCMIProfileAssignment.objects.create(
            tenant_id=TENANT, user_id=OTHER_USER, profile=profile
        )

        response = _client().get(f'{PROFILES_URL}assignments/')

        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['user_id'], str(USER))

    def test_deleting_a_profile_removes_its_assignments(self):
        profile = _profile()
        TeleCMIProfileAssignment.objects.create(
            tenant_id=TENANT, user_id=USER, profile=profile
        )

        response = _client(admin=True).delete(f'{PROFILES_URL}{profile.id}/')

        self.assertEqual(response.status_code, 204)
        self.assertFalse(TeleCMIProfileAssignment.objects.exists())


# ──────────────────────────────────────────────────────────────
# Verify action
# ──────────────────────────────────────────────────────────────

@override_settings(JWT_SECRET_KEY=TEST_JWT_SECRET, JWT_ALGORITHM=TEST_JWT_ALGO)
class VerifyActionTest(TestCase):
    @patch('telephony.services.telecmi_client.get_user_login_token')
    def test_success_returns_ok_and_stamps_the_row(self, mock_login):
        mock_login.return_value = 'tok'
        profile = _profile()

        response = _client(admin=True).post(f'{PROFILES_URL}{profile.id}/verify/')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['ok'])
        self.assertIsNone(response.data['error'])
        profile.refresh_from_db()
        self.assertIsNotNone(profile.verified_at)
        self.assertEqual(profile.verify_error, '')
        mock_login.assert_called_once_with(SALES_EXT, SALES_PASSWORD)

    @patch('telephony.services.telecmi_client.get_user_login_token')
    def test_rejection_returns_not_ok_and_records_why(self, mock_login):
        mock_login.side_effect = TeleCMIError('Invalid credentials', status_code=401)
        profile = _profile()
        profile.verified_at = timezone.now()
        profile.save(update_fields=['verified_at'])

        response = _client(admin=True).post(f'{PROFILES_URL}{profile.id}/verify/')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['ok'])
        self.assertIn('rejected', response.data['error'])
        profile.refresh_from_db()
        self.assertIsNone(profile.verified_at)
        self.assertIn('Invalid credentials', profile.verify_error)

    @patch('telephony.services.telecmi_client.get_user_login_token')
    def test_unreachable_telecmi_is_flagged_not_fatal(self, mock_login):
        # status_code=None is what telecmi_client raises for a network failure.
        mock_login.side_effect = TeleCMIError('Network error calling TeleCMI')
        profile = _profile()

        response = _client(admin=True).post(f'{PROFILES_URL}{profile.id}/verify/')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['ok'])
        self.assertIn('unreachable', response.data['error'])
        profile.refresh_from_db()
        self.assertIn('unreachable', profile.verify_error)

    @patch('telephony.services.telecmi_client.get_user_login_token')
    def test_a_rejected_password_is_still_saved_but_flagged(self, mock_login):
        mock_login.side_effect = TeleCMIError('Invalid credentials', status_code=401)

        response = _client(admin=True).post(
            PROFILES_URL,
            {'label': 'Sales line', 'telecmi_user_id': SALES_EXT,
             'password': SALES_PASSWORD},
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        profile = TeleCMICallingProfile.objects.get(telecmi_user_id=SALES_EXT)
        self.assertIsNone(profile.verified_at)
        self.assertIn('rejected', profile.verify_error)
        # And the admin can see the problem without re-reading the password.
        self.assertIn('rejected', response.data['verify_error'])

    @patch('telephony.services.telecmi_client.get_user_login_token')
    def test_saving_a_new_password_drops_the_cached_token(self, mock_login):
        mock_login.return_value = 'tok'
        profile = _profile()
        profile.cached_token = 'stale-token'
        profile.token_obtained_at = timezone.now()
        profile.save(update_fields=['cached_token', 'token_obtained_at'])
        self.assertFalse(profile.is_token_stale())

        response = _client(admin=True).patch(
            f'{PROFILES_URL}{profile.id}/', {'password': 'rotated-pw'}, format='json'
        )

        self.assertEqual(response.status_code, 200, response.data)
        profile.refresh_from_db()
        self.assertIsNone(profile.cached_token)
        self.assertTrue(profile.is_token_stale())


# ──────────────────────────────────────────────────────────────
# Caller ID
# ──────────────────────────────────────────────────────────────

@override_settings(JWT_SECRET_KEY=TEST_JWT_SECRET, JWT_ALGORITHM=TEST_JWT_ALGO)
class CallerIDPushTest(TestCase):
    @patch('telephony.services.telecmi_client.set_caller_id')
    @patch('telephony.services.telecmi_client.get_user_login_token')
    def test_caller_id_is_pushed_once_then_deduped(self, mock_login, mock_set):
        mock_login.return_value = 'tok'
        _credential()
        profile = _profile(is_default=True, caller_id='918000000001')

        first = _client().get(CONFIG_URL)
        self.assertEqual(first.status_code, 200)
        mock_set.assert_called_once_with('tok', '918000000001')

        profile.refresh_from_db()
        self.assertEqual(profile.caller_id_pushed_value, '918000000001')

        # A second page load must not cost another pair of HTTP calls.
        second = _client().get(CONFIG_URL)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(mock_set.call_count, 1)

    @patch('telephony.services.telecmi_client.set_caller_id')
    @patch('telephony.services.telecmi_client.get_user_login_token')
    def test_a_failed_push_never_blocks_the_softphone(self, mock_login, mock_set):
        mock_login.return_value = 'tok'
        mock_set.side_effect = TeleCMIError('set_callerid exploded', status_code=500)
        _credential()
        profile = _profile(is_default=True, caller_id='918000000001')

        response = _client().get(CONFIG_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['auth']['value'], SALES_PASSWORD)
        profile.refresh_from_db()
        self.assertIsNone(profile.caller_id_pushed_value)

    @patch('telephony.services.telecmi_client.set_caller_id')
    @patch('telephony.services.telecmi_client.get_user_login_token')
    def test_no_caller_id_means_no_telecmi_calls_at_all(self, mock_login, mock_set):
        _credential()
        _profile(is_default=True, caller_id=None)

        response = _client().get(CONFIG_URL)

        self.assertEqual(response.status_code, 200)
        mock_login.assert_not_called()
        mock_set.assert_not_called()

    @patch('telephony.services.telecmi_client.set_caller_id')
    @patch('telephony.services.telecmi_client.get_user_login_token')
    def test_changing_the_caller_id_forces_a_fresh_push(self, mock_login, mock_set):
        mock_login.return_value = 'tok'
        _credential()
        profile = _profile(is_default=True, caller_id='918000000001')
        _client().get(CONFIG_URL)
        self.assertEqual(mock_set.call_count, 1)

        _client(admin=True).patch(
            f'{PROFILES_URL}{profile.id}/',
            {'caller_id': '918000000002'}, format='json',
        )
        _client().get(CONFIG_URL)

        self.assertEqual(mock_set.call_count, 2)
        mock_set.assert_called_with('tok', '918000000002')


# ──────────────────────────────────────────────────────────────
# Token service
# ──────────────────────────────────────────────────────────────

class ProfileTokenServiceTest(TestCase):
    """REST calls (click-to-call, SMS, ...) must use the same extension."""

    @patch('telephony.services.token_service.get_user_login_token')
    def test_assigned_profile_supplies_the_rest_token(self, mock_login):
        from telephony.services.token_service import get_agent_token
        mock_login.return_value = 'profile-tok'
        _credential(with_legacy_default=True)
        profile = _profile(label='Sales line', extension=SALES_EXT)
        TeleCMIProfileAssignment.objects.create(
            tenant_id=TENANT, user_id=USER, profile=profile
        )

        self.assertEqual(get_agent_token(TENANT, USER), 'profile-tok')
        mock_login.assert_called_once_with(SALES_EXT, SALES_PASSWORD)

        # Cached on the profile row from here on.
        self.assertEqual(get_agent_token(TENANT, USER), 'profile-tok')
        self.assertEqual(mock_login.call_count, 1)

    @patch('telephony.services.token_service.get_user_login_token')
    def test_legacy_tenant_default_still_supplies_a_token(self, mock_login):
        from telephony.services.token_service import get_agent_token
        mock_login.return_value = 'legacy-tok'
        _credential(with_legacy_default=True)

        self.assertEqual(get_agent_token(TENANT, USER), 'legacy-tok')
        mock_login.assert_called_once_with(LEGACY_EXT, LEGACY_PASSWORD)


# ──────────────────────────────────────────────────────────────
# Management command
# ──────────────────────────────────────────────────────────────

class SetCallingProfileCommandTest(TestCase):
    def _run(self, **kwargs):
        from io import StringIO
        from django.core.management import call_command

        out = StringIO()
        call_command('set_calling_profile', stdout=out, stderr=out, **kwargs)
        return out.getvalue()

    @patch('telephony.services.telecmi_client.get_user_login_token')
    @patch('getpass.getpass')
    def test_creates_a_profile_with_a_prompted_password(self, mock_getpass, mock_login):
        mock_getpass.side_effect = [SALES_PASSWORD, SALES_PASSWORD]
        mock_login.return_value = 'tok'

        output = self._run(
            tenant=str(TENANT), extension=SALES_EXT, label='Sales line',
            caller_id='918000000001', default=True,
        )

        profile = TeleCMICallingProfile.objects.get(tenant_id=TENANT)
        self.assertEqual(profile.label, 'Sales line')
        self.assertEqual(profile.telecmi_user_id, SALES_EXT)
        self.assertEqual(profile.caller_id, '918000000001')
        self.assertTrue(profile.is_default)
        self.assertIsNotNone(profile.verified_at)
        self.assertNotIn(SALES_PASSWORD, profile.password_encrypted)

        from telephony.services.crypto import decrypt_profile_password
        self.assertEqual(decrypt_profile_password(profile), SALES_PASSWORD)
        self.assertIn('Created calling profile', output)
        # The password is prompted for, never taken from argv.
        self.assertEqual(mock_getpass.call_count, 2)

    @patch('telephony.services.telecmi_client.get_user_login_token')
    @patch('getpass.getpass')
    def test_mismatched_confirmation_aborts(self, mock_getpass, mock_login):
        from django.core.management.base import CommandError
        mock_getpass.side_effect = [SALES_PASSWORD, 'typo']

        with self.assertRaises(CommandError):
            self._run(tenant=str(TENANT), extension=SALES_EXT, label='Sales line')

        self.assertFalse(TeleCMICallingProfile.objects.exists())
        mock_login.assert_not_called()

    @patch('telephony.services.telecmi_client.get_user_login_token')
    @patch('getpass.getpass')
    def test_rotating_a_password_keeps_everything_else(self, mock_getpass, mock_login):
        mock_getpass.side_effect = ['rotated-pw', 'rotated-pw']
        mock_login.return_value = 'tok'
        profile = _profile(label='Sales line', extension=SALES_EXT,
                           caller_id='918000000001', is_default=True)
        profile.cached_token = 'stale'
        profile.token_obtained_at = timezone.now()
        profile.save(update_fields=['cached_token', 'token_obtained_at'])

        self._run(tenant=str(TENANT), extension=SALES_EXT)

        profile.refresh_from_db()
        self.assertEqual(profile.label, 'Sales line')
        self.assertEqual(profile.caller_id, '918000000001')
        self.assertTrue(profile.is_default)
        self.assertIsNone(profile.cached_token)

        from telephony.services.crypto import decrypt_profile_password
        self.assertEqual(decrypt_profile_password(profile), 'rotated-pw')

    @patch('telephony.services.telecmi_client.get_user_login_token')
    @patch('getpass.getpass')
    def test_assign_flag_wires_a_user_up_in_one_shot(self, mock_getpass, mock_login):
        mock_getpass.side_effect = [SALES_PASSWORD, SALES_PASSWORD]
        mock_login.return_value = 'tok'

        self._run(
            tenant=str(TENANT), extension=SALES_EXT, label='Sales line',
            assign=[str(USER)],
        )

        assignment = TeleCMIProfileAssignment.objects.get(tenant_id=TENANT, user_id=USER)
        self.assertEqual(assignment.profile.telecmi_user_id, SALES_EXT)

    @patch('telephony.services.telecmi_client.get_user_login_token')
    def test_no_verify_stores_the_profile_flagged(self, mock_login):
        """--stdin-password keeps the secret out of argv just as the prompt does."""
        import io as _io
        import sys as _sys
        from django.core.management import call_command

        original_stdin = _sys.stdin
        _sys.stdin = _io.StringIO(SALES_PASSWORD + '\n')
        try:
            call_command(
                'set_calling_profile', tenant=str(TENANT), extension=SALES_EXT,
                label='Sales line', stdin_password=True, no_verify=True,
                stdout=_io.StringIO(), stderr=_io.StringIO(),
            )
        finally:
            _sys.stdin = original_stdin

        profile = TeleCMICallingProfile.objects.get(tenant_id=TENANT)
        self.assertIsNone(profile.verified_at)
        self.assertIn('no-verify', profile.verify_error)
        mock_login.assert_not_called()

        from telephony.services.crypto import decrypt_profile_password
        self.assertEqual(decrypt_profile_password(profile), SALES_PASSWORD)
