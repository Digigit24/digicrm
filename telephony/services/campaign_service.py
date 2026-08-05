"""
Campaign service — sync CRM campaigns to/from TeleCMI Campaigns API.
"""
import logging
from typing import List

import requests
from telephony.services.crypto import decrypt_secret

logger = logging.getLogger(__name__)

TELECMI_BASE = 'https://rest.telecmi.com'
DEFAULT_TIMEOUT = 30


def _get_credential(tenant_id: str):
    from telephony.models import TeleCMICredential
    try:
        return TeleCMICredential.objects.get(tenant_id=tenant_id, is_active=True)
    except TeleCMICredential.DoesNotExist:
        raise ValueError(f'No active TeleCMI credential for tenant {tenant_id}')


def _agent_telecmi_ids(tenant_id: str, crm_user_ids: List[str]) -> List[str]:
    """Map CRM user UUIDs → TeleCMI user IDs."""
    from telephony.models import TeleCMIAgent
    agents = TeleCMIAgent.objects.filter(
        tenant_id=tenant_id,
        user_id__in=crm_user_ids,
        is_active=True,
    ).values_list('telecmi_user_id', flat=True)
    return list(agents)


def create_campaign_in_telecmi(tenant_id: str, campaign) -> dict:
    """Create a campaign in TeleCMI and update campaign.telecmi_campaign_id."""
    cred = _get_credential(tenant_id)
    secret = decrypt_secret(cred)
    telecmi_agent_ids = _agent_telecmi_ids(tenant_id, campaign.agent_user_ids)

    payload = {
        'inet_no': cred.default_caller_id or '',
        'secret': secret,
        'name': campaign.name,
        'timezone': campaign.timezone,
        'start_date': campaign.start_date.strftime('%Y-%m-%d'),
        'end_date': campaign.end_date.strftime('%Y-%m-%d'),
        'start_time': campaign.start_time.strftime('%H:%M:%S'),
        'end_time': campaign.end_time.strftime('%H:%M:%S'),
        'users': telecmi_agent_ids,
        'call_interval': campaign.call_interval,
        'ring_rule': campaign.ring_rule,
    }

    resp = requests.post(f'{TELECMI_BASE}/v3/campaign/create', json=payload, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    if data.get('campaign_id'):
        campaign.telecmi_campaign_id = data['campaign_id']
        campaign.save(update_fields=['telecmi_campaign_id', 'updated_at'])

    return data


def sync_campaign_to_telecmi(tenant_id: str, campaign) -> dict:
    """Update campaign settings in TeleCMI."""
    if not campaign.telecmi_campaign_id:
        return create_campaign_in_telecmi(tenant_id, campaign)

    cred = _get_credential(tenant_id)
    secret = decrypt_secret(cred)
    telecmi_agent_ids = _agent_telecmi_ids(tenant_id, campaign.agent_user_ids)

    payload = {
        'campaign_id': campaign.telecmi_campaign_id,
        'inet_no': cred.default_caller_id or '',
        'secret': secret,
        'name': campaign.name,
        'active': campaign.is_active,
        'timezone': campaign.timezone,
        'start_date': campaign.start_date.strftime('%Y-%m-%d'),
        'end_date': campaign.end_date.strftime('%Y-%m-%d'),
        'start_time': campaign.start_time.strftime('%H:%M:%S'),
        'end_time': campaign.end_time.strftime('%H:%M:%S'),
        'users': telecmi_agent_ids,
        'call_interval': campaign.call_interval,
        'ring_rule': campaign.ring_rule,
    }

    resp = requests.post(f'{TELECMI_BASE}/v3/campaign/update', json=payload, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def delete_campaign_in_telecmi(tenant_id: str, campaign) -> dict:
    """Delete campaign from TeleCMI."""
    if not campaign.telecmi_campaign_id:
        return {}

    cred = _get_credential(tenant_id)
    secret = decrypt_secret(cred)

    payload = {
        'campaign_id': campaign.telecmi_campaign_id,
        'inet_no': cred.default_caller_id or '',
        'secret': secret,
    }
    resp = requests.post(f'{TELECMI_BASE}/v3/campaign/delete', json=payload, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def push_leads_to_campaign_sync(tenant_id: str, lead_ids: List[str], campaign) -> dict:
    """
    Push CRM leads to TeleCMI campaign dialer using the add-custom endpoint.
    Uses the CRM's own lead ID as the TeleCMI lead_id for referential integrity
    (so campaign CDRs coming back from TeleCMI can be matched to CRM leads).
    Batches in chunks of 1000 per TeleCMI's per-request limit.
    """
    from crm.models import Lead

    cred = _get_credential(tenant_id)
    secret = decrypt_secret(cred)

    leads = Lead.objects.filter(tenant_id=tenant_id, id__in=lead_ids).only(
        'id', 'name', 'phone', 'email'
    )

    payload_leads = []
    for i, lead in enumerate(leads):
        # Lead stores a single display name; TeleCMI wants first/last separately.
        # Split on the first space: first token = firstname, remainder = lastname.
        first, _, last = (lead.name or '').strip().partition(' ')
        payload_leads.append({
            'lead_id': str(lead.id),
            'phone': lead.phone or '',
            'firstname': first,
            'lastname': last,
            'email': lead.email or '',
            'order_index': i + 1,
        })

    total_pushed = 0
    CHUNK = 1000
    for i in range(0, len(payload_leads), CHUNK):
        chunk = payload_leads[i:i + CHUNK]
        resp = requests.post(
            f'{TELECMI_BASE}/v3/leads/add-custom',
            json={
                'lead_name': campaign.name,
                'inet_no': cred.default_caller_id or '',
                'secret': secret,
                'leads': chunk,
            },
            timeout=60,
        )
        resp.raise_for_status()
        total_pushed += len(chunk)

    # Update campaign lead count
    campaign.lead_count = (campaign.lead_count or 0) + total_pushed
    campaign.telecmi_lead_list_name = campaign.name
    campaign.save(update_fields=['lead_count', 'telecmi_lead_list_name', 'updated_at'])

    return {'pushed': total_pushed, 'campaign_id': str(campaign.id)}


def push_group_to_campaign_sync(tenant_id: str, group_id: int, campaign) -> dict:
    """
    Seed a campaign from a CRM lead group: resolve the group's current members
    to lead IDs and push them through the standard add-custom pipeline.

    The group (via the Lead.groups M2M) stays the source of truth; this is a
    snapshot push of the members present at call time.
    """
    from crm.models import Lead

    lead_ids = list(
        Lead.objects.filter(tenant_id=tenant_id, groups__id=group_id)
        .values_list('id', flat=True)
    )
    if not lead_ids:
        return {'pushed': 0, 'campaign_id': str(campaign.id)}

    return push_leads_to_campaign_sync(tenant_id, lead_ids, campaign)
