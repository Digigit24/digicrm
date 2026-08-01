"""Tests for real_estate models."""
import uuid
from django.db import IntegrityError, transaction
from django.test import TestCase

from crm.models import Lead
from real_estate.models import (
    Project, Block, Unit, ProjectInterest, UnitLead,
    ProjectTypeEnum, ProjectStatusEnum, BlockTypeEnum, UnitTypeEnum,
    UnitStatusEnum, LeadUnitRelationEnum,
)

TENANT_A = uuid.UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
TENANT_B = uuid.UUID('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb')
USER_A = uuid.UUID('cccccccc-cccc-cccc-cccc-cccccccccccc')


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
    defaults = dict(
        tenant_id=tenant_id,
        name='Jane Prospect',
        phone='9190000000',
        owner_user_id=USER_A,
    )
    defaults.update(kwargs)
    return Lead.objects.create(**defaults)


class ProjectModelTest(TestCase):
    def test_create_project(self):
        project = _make_project()
        self.assertEqual(project.name, 'Sunrise Heights')
        self.assertEqual(project.status, ProjectStatusEnum.UNDER_CONSTRUCTION)

    def test_tenant_isolation(self):
        _make_project(tenant_id=TENANT_A, name='A Project')
        _make_project(tenant_id=TENANT_B, name='B Project')
        self.assertEqual(Project.objects.filter(tenant_id=TENANT_A).count(), 1)
        self.assertEqual(Project.objects.filter(tenant_id=TENANT_B).count(), 1)
        self.assertEqual(Project.objects.filter(tenant_id=TENANT_A).first().name, 'A Project')


class BlockModelTest(TestCase):
    def test_create_block(self):
        project = _make_project()
        block = Block.objects.create(
            tenant_id=TENANT_A, project=project, name='Tower A',
            block_type=BlockTypeEnum.TOWER, total_floors=20,
        )
        self.assertEqual(block.project_id, project.id)

    def test_unique_project_name(self):
        project = _make_project()
        Block.objects.create(
            tenant_id=TENANT_A, project=project, name='Tower A', block_type=BlockTypeEnum.TOWER,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Block.objects.create(
                    tenant_id=TENANT_A, project=project, name='Tower A', block_type=BlockTypeEnum.WING,
                )


class UnitModelTest(TestCase):
    def test_create_unit(self):
        project = _make_project()
        unit = Unit.objects.create(
            tenant_id=TENANT_A, project=project, unit_type=UnitTypeEnum.FLAT,
            unit_number='A-1203', floor_number=12, status=UnitStatusEnum.AVAILABLE,
        )
        self.assertEqual(unit.unit_number, 'A-1203')

    def test_negative_floor_number_allowed(self):
        project = _make_project()
        unit = Unit.objects.create(
            tenant_id=TENANT_A, project=project, unit_type=UnitTypeEnum.COMMERCIAL_SHOP,
            unit_number='B-001', floor_number=-1, status=UnitStatusEnum.AVAILABLE,
        )
        self.assertEqual(unit.floor_number, -1)

    def test_unique_project_unit_number(self):
        project = _make_project()
        Unit.objects.create(
            tenant_id=TENANT_A, project=project, unit_type=UnitTypeEnum.FLAT,
            unit_number='A-1203', status=UnitStatusEnum.AVAILABLE,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Unit.objects.create(
                    tenant_id=TENANT_A, project=project, unit_type=UnitTypeEnum.VILLA,
                    unit_number='A-1203', status=UnitStatusEnum.AVAILABLE,
                )

    def test_same_unit_number_different_project_allowed(self):
        project1 = _make_project(name='Project 1')
        project2 = _make_project(name='Project 2')
        Unit.objects.create(
            tenant_id=TENANT_A, project=project1, unit_type=UnitTypeEnum.FLAT,
            unit_number='A-101', status=UnitStatusEnum.AVAILABLE,
        )
        unit2 = Unit.objects.create(
            tenant_id=TENANT_A, project=project2, unit_type=UnitTypeEnum.FLAT,
            unit_number='A-101', status=UnitStatusEnum.AVAILABLE,
        )
        self.assertEqual(unit2.unit_number, 'A-101')

    def test_block_set_null_on_delete(self):
        project = _make_project()
        block = Block.objects.create(
            tenant_id=TENANT_A, project=project, name='Tower A', block_type=BlockTypeEnum.TOWER,
        )
        unit = Unit.objects.create(
            tenant_id=TENANT_A, project=project, block=block, unit_type=UnitTypeEnum.FLAT,
            unit_number='A-1', status=UnitStatusEnum.AVAILABLE,
        )
        block.delete()
        unit.refresh_from_db()
        self.assertIsNone(unit.block_id)


class ProjectInterestModelTest(TestCase):
    def test_create_project_interest(self):
        project = _make_project()
        lead = _make_lead()
        interest = ProjectInterest.objects.create(
            tenant_id=TENANT_A, project=project, lead=lead,
            budget_min=1000000, budget_max=2000000,
            preferred_unit_type=UnitTypeEnum.FLAT,
        )
        self.assertEqual(interest.project_id, project.id)
        self.assertEqual(interest.lead_id, lead.id)

    def test_unique_project_lead(self):
        project = _make_project()
        lead = _make_lead()
        ProjectInterest.objects.create(tenant_id=TENANT_A, project=project, lead=lead)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProjectInterest.objects.create(tenant_id=TENANT_A, project=project, lead=lead)


class UnitLeadModelTest(TestCase):
    def test_create_unit_lead(self):
        project = _make_project()
        unit = Unit.objects.create(
            tenant_id=TENANT_A, project=project, unit_type=UnitTypeEnum.FLAT,
            unit_number='A-1', status=UnitStatusEnum.AVAILABLE,
        )
        lead = _make_lead()
        unit_lead = UnitLead.objects.create(
            tenant_id=TENANT_A, unit=unit, lead=lead,
            relation_type=LeadUnitRelationEnum.INTERESTED,
        )
        self.assertEqual(unit_lead.relation_type, LeadUnitRelationEnum.INTERESTED)

    def test_unique_unit_lead(self):
        project = _make_project()
        unit = Unit.objects.create(
            tenant_id=TENANT_A, project=project, unit_type=UnitTypeEnum.FLAT,
            unit_number='A-1', status=UnitStatusEnum.AVAILABLE,
        )
        lead = _make_lead()
        UnitLead.objects.create(
            tenant_id=TENANT_A, unit=unit, lead=lead, relation_type=LeadUnitRelationEnum.INTERESTED,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                UnitLead.objects.create(
                    tenant_id=TENANT_A, unit=unit, lead=lead,
                    relation_type=LeadUnitRelationEnum.NEGOTIATING,
                )
