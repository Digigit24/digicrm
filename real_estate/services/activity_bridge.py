"""
Activity bridge — creates crm.LeadActivity (type=REAL_ESTATE) rows whenever
a lead's relationship to a real estate project/unit is created or changes.

Uses lazy imports of crm.models inside each function body, exactly like
telephony/services/call_log_service.py's _create_call_activity, since
real_estate depends on crm but crm must not import real_estate.
"""
import logging
import uuid as _uuid

logger = logging.getLogger(__name__)


def log_project_interest_activity(project_interest, actor_user_id=None):
    """Log a REAL_ESTATE activity for a newly created ProjectInterest."""
    from crm.models import LeadActivity, ActivityTypeEnum
    from django.db import transaction
    from django.utils import timezone

    actor = actor_user_id or _uuid.UUID(int=0)
    content = f'Interested in project "{project_interest.project.name}"'

    try:
        with transaction.atomic():
            LeadActivity.objects.create(
                tenant_id=project_interest.tenant_id,
                lead_id=project_interest.lead_id,
                type=ActivityTypeEnum.REAL_ESTATE,
                content=content,
                happened_at=timezone.now(),
                by_user_id=actor,
                meta={
                    'source': 'real_estate',
                    'event': 'project_interest_created',
                    'project_id': project_interest.project_id,
                    'project_interest_id': project_interest.id,
                    'preferred_unit_type': project_interest.preferred_unit_type,
                    'budget_min': str(project_interest.budget_min) if project_interest.budget_min is not None else None,
                    'budget_max': str(project_interest.budget_max) if project_interest.budget_max is not None else None,
                },
            )
    except Exception as exc:
        logger.error(
            'Failed to create REAL_ESTATE activity for project_interest %s: %s',
            project_interest.id, exc,
        )


def log_unit_lead_activity(unit_lead, actor_user_id=None, previous_relation_type=None):
    """
    Log a REAL_ESTATE activity for a UnitLead.

    Called when a UnitLead is created (previous_relation_type=None) or when
    its relation_type changes (previous_relation_type=<old value>).
    """
    from crm.models import LeadActivity, ActivityTypeEnum
    from django.db import transaction
    from django.utils import timezone

    actor = actor_user_id or _uuid.UUID(int=0)

    if previous_relation_type is None:
        content = (
            f'Linked to unit "{unit_lead.unit.unit_number}" '
            f'as {unit_lead.get_relation_type_display()}'
        )
        event = 'unit_lead_created'
    else:
        content = (
            f'Unit "{unit_lead.unit.unit_number}" relation changed: '
            f'{previous_relation_type} -> {unit_lead.relation_type}'
        )
        event = 'unit_lead_relation_changed'

    try:
        with transaction.atomic():
            LeadActivity.objects.create(
                tenant_id=unit_lead.tenant_id,
                lead_id=unit_lead.lead_id,
                type=ActivityTypeEnum.REAL_ESTATE,
                content=content,
                happened_at=timezone.now(),
                by_user_id=actor,
                meta={
                    'source': 'real_estate',
                    'event': event,
                    'unit_id': unit_lead.unit_id,
                    'unit_lead_id': unit_lead.id,
                    'relation_type': unit_lead.relation_type,
                    'previous_relation_type': previous_relation_type,
                },
            )
    except Exception as exc:
        logger.error(
            'Failed to create REAL_ESTATE activity for unit_lead %s: %s',
            unit_lead.id, exc,
        )
