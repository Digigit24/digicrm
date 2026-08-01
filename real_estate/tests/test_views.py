"""
Tests for real_estate API views.

Uses real JWT tokens (signed with a test secret) to pass the JWT middleware,
same pattern as telephony/tests/test_views.py.
"""
import io
import uuid
from unittest.mock import patch

import jwt as pyjwt
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from crm.models import Lead, LeadActivity, ActivityTypeEnum
from real_estate.models import (
    Project, Block, Unit, ProjectInterest, UnitLead,
    ProjectTypeEnum, ProjectStatusEnum, BlockTypeEnum, UnitTypeEnum,
    UnitStatusEnum, LeadUnitRelationEnum,
)

TEST_JWT_SECRET = 'test-jwt-secret-real-estate-unit-tests'
TEST_JWT_ALGO = 'HS256'

TENANT_A = uuid.UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
TENANT_B = uuid.UUID('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb')
USER_A = uuid.UUID('cccccccc-cccc-cccc-cccc-cccccccccccc')
USER_B = uuid.UUID('dddddddd-dddd-dddd-dddd-dddddddddddd')

FULL_RE_PERMISSIONS = {
    'projects': {'view': 'all', 'create': True, 'edit': True, 'delete': True},
    'units': {'view': 'all', 'create': True, 'edit': True, 'delete': True},
    'leads': {'view': 'all', 'create': True, 'edit': True, 'delete': True},
}


def _make_jwt(tenant_id, user_id, is_super_admin=False, real_estate_perms=None, enabled_modules=None):
    payload = {
        'user_id': str(user_id),
        'email': 'test@example.com',
        'tenant_id': str(tenant_id),
        'tenant_slug': 'test-tenant',
        'is_super_admin': is_super_admin,
        'permissions': {'real_estate': real_estate_perms or FULL_RE_PERMISSIONS},
        'enabled_modules': ['real_estate'] if enabled_modules is None else enabled_modules,
    }
    token = pyjwt.encode(payload, TEST_JWT_SECRET, algorithm=TEST_JWT_ALGO)
    return f'Bearer {token}'


def _authed_client(tenant_id, user_id, is_super_admin=False, real_estate_perms=None, enabled_modules=None):
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=_make_jwt(tenant_id, user_id, is_super_admin, real_estate_perms, enabled_modules)
    )
    return client


def _make_project(tenant_id=TENANT_A, **kwargs):
    defaults = dict(
        tenant_id=tenant_id,
        name='Sunrise Heights',
        project_type=ProjectTypeEnum.RESIDENTIAL,
        status=ProjectStatusEnum.UNDER_CONSTRUCTION,
        created_by_user_id=USER_A,
    )
    defaults.update(kwargs)
    return Project.objects.create(**defaults)


def _make_lead(tenant_id=TENANT_A, **kwargs):
    defaults = dict(tenant_id=tenant_id, name='Jane Prospect', phone='9190000000', owner_user_id=USER_A)
    defaults.update(kwargs)
    return Lead.objects.create(**defaults)


@override_settings(JWT_SECRET_KEY=TEST_JWT_SECRET, JWT_ALGORITHM=TEST_JWT_ALGO)
class ProjectViewSetTest(TestCase):

    def test_list_tenant_isolation(self):
        _make_project(tenant_id=TENANT_A, name='A Project')
        _make_project(tenant_id=TENANT_B, name='B Project')

        response = _authed_client(TENANT_A, USER_A).get('/api/real-estate/projects/')
        self.assertEqual(response.status_code, 200)
        names = [p['name'] for p in response.data['results']]
        self.assertIn('A Project', names)
        self.assertNotIn('B Project', names)

    def test_create_project_defaults_created_by_to_jwt_user(self):
        response = _authed_client(TENANT_A, USER_A).post(
            '/api/real-estate/projects/',
            {
                'name': 'New Project',
                'project_type': ProjectTypeEnum.RESIDENTIAL,
                'status': ProjectStatusEnum.PLANNING,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        project = Project.objects.get(id=response.data['id'])
        self.assertEqual(project.tenant_id, TENANT_A)
        self.assertEqual(str(project.created_by_user_id), str(USER_A))

    def test_unauthenticated_rejected(self):
        response = APIClient().get('/api/real-estate/projects/')
        self.assertEqual(response.status_code, 401)

    def test_missing_module_permission_rejected(self):
        # real_estate module not enabled for this tenant/user
        client = _authed_client(TENANT_A, USER_A, enabled_modules=[])
        response = client.get('/api/real-estate/projects/')
        self.assertEqual(response.status_code, 403)

    def test_summary_endpoint_aggregates_units(self):
        project = _make_project()
        Unit.objects.create(
            tenant_id=TENANT_A, project=project, unit_type=UnitTypeEnum.FLAT,
            unit_number='A-1', floor_number=1, status=UnitStatusEnum.AVAILABLE,
        )
        Unit.objects.create(
            tenant_id=TENANT_A, project=project, unit_type=UnitTypeEnum.FLAT,
            unit_number='A-2', floor_number=1, status=UnitStatusEnum.AVAILABLE,
        )
        Unit.objects.create(
            tenant_id=TENANT_A, project=project, unit_type=UnitTypeEnum.VILLA,
            unit_number='B-1', floor_number=2, status=UnitStatusEnum.SOLD,
        )
        # Unit belonging to a different tenant/project must not leak into the summary
        other_project = _make_project(tenant_id=TENANT_B, name='Other')
        Unit.objects.create(
            tenant_id=TENANT_B, project=other_project, unit_type=UnitTypeEnum.FLAT,
            unit_number='X-1', floor_number=1, status=UnitStatusEnum.AVAILABLE,
        )

        response = _authed_client(TENANT_A, USER_A).get(f'/api/real-estate/projects/{project.id}/summary/')
        self.assertEqual(response.status_code, 200, response.data)

        self.assertEqual(response.data['unit_counts_by_status'], {
            UnitStatusEnum.AVAILABLE: 2,
            UnitStatusEnum.SOLD: 1,
        })
        self.assertEqual(response.data['unit_counts_by_type'], {
            UnitTypeEnum.FLAT: 2,
            UnitTypeEnum.VILLA: 1,
        })
        self.assertEqual(response.data['unit_counts_by_floor'], {
            '1': 2,
            '2': 1,
        })

    @patch('real_estate.views.upload_to_zata')
    def test_upload_image_sets_image_url(self, mock_upload):
        project = _make_project()
        mock_upload.return_value = {
            'id': '11111111-1111-1111-1111-111111111111',
            'download_url': 'https://zata.example.com/image1.jpg',
        }

        response = _authed_client(TENANT_A, USER_A).post(
            f'/api/real-estate/projects/{project.id}/image/',
            {'file': io.BytesIO(b'fake-image-bytes')},
            format='multipart',
            HTTP_X_ZATA_FOLDER_ID='22222222-2222-2222-2222-222222222222',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['image_url'], 'https://zata.example.com/image1.jpg')
        project.refresh_from_db()
        self.assertEqual(str(project.image_zata_id), '11111111-1111-1111-1111-111111111111')
        self.assertEqual(project.image_url, 'https://zata.example.com/image1.jpg')

    @patch('real_estate.views.delete_from_zata')
    @patch('real_estate.views.upload_to_zata')
    def test_upload_image_replaces_existing_image(self, mock_upload, mock_delete):
        project = _make_project(
            image_zata_id=uuid.UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'),
            image_url='https://zata.example.com/old.jpg',
        )
        mock_upload.return_value = {
            'id': '11111111-1111-1111-1111-111111111111',
            'download_url': 'https://zata.example.com/image1.jpg',
        }

        response = _authed_client(TENANT_A, USER_A).post(
            f'/api/real-estate/projects/{project.id}/image/',
            {'file': io.BytesIO(b'fake-image-bytes')},
            format='multipart',
            HTTP_X_ZATA_FOLDER_ID='22222222-2222-2222-2222-222222222222',
        )

        self.assertEqual(response.status_code, 200, response.data)
        mock_delete.assert_called_once_with(project.image_zata_id)
        project.refresh_from_db()
        self.assertEqual(str(project.image_zata_id), '11111111-1111-1111-1111-111111111111')

    @patch('real_estate.views.delete_from_zata')
    def test_delete_image_clears_fields(self, mock_delete):
        project = _make_project(
            image_zata_id=uuid.UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'),
            image_url='https://zata.example.com/old.jpg',
        )

        response = _authed_client(TENANT_A, USER_A).delete(
            f'/api/real-estate/projects/{project.id}/image/'
        )

        self.assertEqual(response.status_code, 204)
        mock_delete.assert_called_once_with(project.image_zata_id)
        project.refresh_from_db()
        self.assertIsNone(project.image_zata_id)
        self.assertIsNone(project.image_url)

    def test_upload_image_requires_file(self):
        project = _make_project()
        response = _authed_client(TENANT_A, USER_A).post(
            f'/api/real-estate/projects/{project.id}/image/',
            {},
            format='multipart',
            HTTP_X_ZATA_FOLDER_ID='22222222-2222-2222-2222-222222222222',
        )
        self.assertEqual(response.status_code, 400)

    def test_upload_image_requires_folder_id_header(self):
        project = _make_project()
        response = _authed_client(TENANT_A, USER_A).post(
            f'/api/real-estate/projects/{project.id}/image/',
            {'file': io.BytesIO(b'fake-image-bytes')},
            format='multipart',
        )
        self.assertEqual(response.status_code, 400)

    def test_image_endpoint_tenant_isolation(self):
        project_a = _make_project(tenant_id=TENANT_A)
        _make_project(tenant_id=TENANT_B, name='B Project')

        response = _authed_client(TENANT_B, USER_B).post(
            f'/api/real-estate/projects/{project_a.id}/image/',
            {'file': io.BytesIO(b'fake-image-bytes')},
            format='multipart',
            HTTP_X_ZATA_FOLDER_ID='22222222-2222-2222-2222-222222222222',
        )
        # Tenant B should not be able to act on Tenant A's project
        self.assertEqual(response.status_code, 404)


@override_settings(JWT_SECRET_KEY=TEST_JWT_SECRET, JWT_ALGORITHM=TEST_JWT_ALGO)
class UnitViewSetTest(TestCase):

    def test_list_tenant_isolation(self):
        project_a = _make_project(tenant_id=TENANT_A)
        project_b = _make_project(tenant_id=TENANT_B)
        Unit.objects.create(
            tenant_id=TENANT_A, project=project_a, unit_type=UnitTypeEnum.FLAT,
            unit_number='A-1', status=UnitStatusEnum.AVAILABLE,
        )
        Unit.objects.create(
            tenant_id=TENANT_B, project=project_b, unit_type=UnitTypeEnum.FLAT,
            unit_number='B-1', status=UnitStatusEnum.AVAILABLE,
        )

        response = _authed_client(TENANT_A, USER_A).get('/api/real-estate/units/')
        self.assertEqual(response.status_code, 200)
        unit_numbers = [u['unit_number'] for u in response.data['results']]
        self.assertIn('A-1', unit_numbers)
        self.assertNotIn('B-1', unit_numbers)

    def test_unique_together_enforced_via_api(self):
        project = _make_project()
        client = _authed_client(TENANT_A, USER_A)
        payload = {
            'project': project.id,
            'unit_type': UnitTypeEnum.FLAT,
            'unit_number': 'A-1203',
            'status': UnitStatusEnum.AVAILABLE,
        }
        first = client.post('/api/real-estate/units/', payload, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        second = client.post('/api/real-estate/units/', payload, format='json')
        self.assertEqual(second.status_code, 400)


@override_settings(JWT_SECRET_KEY=TEST_JWT_SECRET, JWT_ALGORITHM=TEST_JWT_ALGO)
class ProjectInterestActivityBridgeTest(TestCase):

    def test_create_project_interest_creates_lead_activity(self):
        project = _make_project()
        lead = _make_lead()
        client = _authed_client(TENANT_A, USER_A)

        response = client.post(
            '/api/real-estate/project-interests/',
            {'project': project.id, 'lead': lead.id, 'preferred_unit_type': UnitTypeEnum.FLAT},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)

        activity = LeadActivity.objects.filter(
            tenant_id=TENANT_A, lead_id=lead.id, type=ActivityTypeEnum.REAL_ESTATE
        ).first()
        self.assertIsNotNone(activity)
        self.assertIn(project.name, activity.content)

    def test_unique_project_lead_enforced_via_api(self):
        project = _make_project()
        lead = _make_lead()
        client = _authed_client(TENANT_A, USER_A)
        payload = {'project': project.id, 'lead': lead.id}
        first = client.post('/api/real-estate/project-interests/', payload, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        second = client.post('/api/real-estate/project-interests/', payload, format='json')
        self.assertEqual(second.status_code, 400)


@override_settings(JWT_SECRET_KEY=TEST_JWT_SECRET, JWT_ALGORITHM=TEST_JWT_ALGO)
class UnitLeadActivityBridgeTest(TestCase):

    def _make_unit(self, project):
        return Unit.objects.create(
            tenant_id=TENANT_A, project=project, unit_type=UnitTypeEnum.FLAT,
            unit_number='A-1', status=UnitStatusEnum.AVAILABLE,
        )

    def test_create_unit_lead_creates_lead_activity(self):
        project = _make_project()
        unit = self._make_unit(project)
        lead = _make_lead()
        client = _authed_client(TENANT_A, USER_A)

        response = client.post(
            '/api/real-estate/unit-leads/',
            {'unit': unit.id, 'lead': lead.id, 'relation_type': LeadUnitRelationEnum.INTERESTED},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            LeadActivity.objects.filter(
                tenant_id=TENANT_A, lead_id=lead.id, type=ActivityTypeEnum.REAL_ESTATE
            ).count(),
            1,
        )

    def test_relation_type_change_creates_second_activity(self):
        project = _make_project()
        unit = self._make_unit(project)
        lead = _make_lead()
        client = _authed_client(TENANT_A, USER_A)

        create_resp = client.post(
            '/api/real-estate/unit-leads/',
            {'unit': unit.id, 'lead': lead.id, 'relation_type': LeadUnitRelationEnum.INTERESTED},
            format='json',
        )
        unit_lead_id = create_resp.data['id']

        update_resp = client.patch(
            f'/api/real-estate/unit-leads/{unit_lead_id}/',
            {'relation_type': LeadUnitRelationEnum.NEGOTIATING},
            format='json',
        )
        self.assertEqual(update_resp.status_code, 200, update_resp.data)

        self.assertEqual(
            LeadActivity.objects.filter(
                tenant_id=TENANT_A, lead_id=lead.id, type=ActivityTypeEnum.REAL_ESTATE
            ).count(),
            2,
        )

    def test_no_op_update_does_not_create_extra_activity(self):
        project = _make_project()
        unit = self._make_unit(project)
        lead = _make_lead()
        client = _authed_client(TENANT_A, USER_A)

        create_resp = client.post(
            '/api/real-estate/unit-leads/',
            {'unit': unit.id, 'lead': lead.id, 'relation_type': LeadUnitRelationEnum.INTERESTED},
            format='json',
        )
        unit_lead_id = create_resp.data['id']

        client.patch(
            f'/api/real-estate/unit-leads/{unit_lead_id}/',
            {'notes': 'Called back, still interested'},
            format='json',
        )

        self.assertEqual(
            LeadActivity.objects.filter(
                tenant_id=TENANT_A, lead_id=lead.id, type=ActivityTypeEnum.REAL_ESTATE
            ).count(),
            1,
        )

    def test_unique_unit_lead_enforced_via_api(self):
        project = _make_project()
        unit = self._make_unit(project)
        lead = _make_lead()
        client = _authed_client(TENANT_A, USER_A)
        payload = {'unit': unit.id, 'lead': lead.id, 'relation_type': LeadUnitRelationEnum.INTERESTED}
        first = client.post('/api/real-estate/unit-leads/', payload, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        second = client.post('/api/real-estate/unit-leads/', payload, format='json')
        self.assertEqual(second.status_code, 400)
