"""
Analytics service — per-agent and team telephony stats.
Used by the admin analytics dashboard and agent dashboard.
"""
import logging
from datetime import date, timedelta
from typing import Optional

from django.db.models import Count, Sum, Avg, Q, F
from django.db.models.functions import TruncDate, Coalesce
from django.utils import timezone

logger = logging.getLogger(__name__)

OUTCOME_CHOICES = [
    ('interested', 'Interested'),
    ('not_interested', 'Not Interested'),
    ('follow_up', 'Follow Up'),
    ('callback', 'Callback'),
    ('converted', 'Converted'),
    ('dnd', 'DND'),
]


def get_agent_daily_stats(tenant_id, date_from: date, date_to: date, agent_user_id=None):
    """
    Per-agent, per-day call stats. Returns list of dicts.
    If agent_user_id is provided, filters to that agent only.
    """
    from telephony.models import CallLog

    qs = (
        CallLog.objects
        .filter(
            tenant_id=tenant_id,
            call_time__date__range=[date_from, date_to],
        )
        .exclude(call_leg='a')  # exclude Leg A of outbound (it's a duplicate leg)
    )

    if agent_user_id:
        qs = qs.filter(agent_user_id=agent_user_id)

    return list(
        qs
        .values(
            'agent_user_id',
            day=TruncDate('call_time'),
        )
        .annotate(
            total_calls=Count('id'),
            answered_calls=Count('id', filter=Q(call_type='answered')),
            missed_calls=Count('id', filter=Q(call_type='missed')),
            total_talk_time=Coalesce(Sum('duration', filter=Q(call_type='answered')), 0),
            avg_call_duration=Avg('duration', filter=Q(call_type='answered')),
            outbound_calls=Count('id', filter=Q(direction='outbound')),
            inbound_calls=Count('id', filter=Q(direction='inbound')),
            calls_with_outcome=Count('id', filter=Q(call_outcome__isnull=False)),
            converted_calls=Count('id', filter=Q(call_outcome='converted')),
            interested_calls=Count('id', filter=Q(call_outcome='interested')),
        )
        .order_by('day', 'agent_user_id')
    )


def get_agent_summary(tenant_id, date_from: date, date_to: date):
    """
    Aggregated totals per agent across the entire date range.
    Returns list of dicts ordered by total_calls desc.
    """
    from telephony.models import CallLog
    from django.contrib.auth import get_user_model
    User = get_user_model()

    qs = (
        CallLog.objects
        .filter(
            tenant_id=tenant_id,
            call_time__date__range=[date_from, date_to],
            agent_user_id__isnull=False,
        )
        .exclude(call_leg='a')
    )

    stats = list(
        qs
        .values('agent_user_id')
        .annotate(
            total_calls=Count('id'),
            answered_calls=Count('id', filter=Q(call_type='answered')),
            missed_calls=Count('id', filter=Q(call_type='missed')),
            total_talk_time=Coalesce(Sum('duration', filter=Q(call_type='answered')), 0),
            avg_call_duration=Avg('duration', filter=Q(call_type='answered')),
            outbound_calls=Count('id', filter=Q(direction='outbound')),
            inbound_calls=Count('id', filter=Q(direction='inbound')),
            converted_calls=Count('id', filter=Q(call_outcome='converted')),
        )
        .order_by('-total_calls')
    )

    # Enrich with user names
    user_ids = [s['agent_user_id'] for s in stats]
    users = {
        str(u.id): {'first_name': u.first_name, 'last_name': u.last_name, 'email': u.email}
        for u in User.objects.filter(id__in=user_ids).only('id', 'first_name', 'last_name', 'email')
    }
    for s in stats:
        uid = str(s['agent_user_id'])
        u = users.get(uid, {})
        s['agent_name'] = f"{u.get('first_name', '')} {u.get('last_name', '')}".strip() or u.get('email', uid)
        s['miss_rate'] = round(s['missed_calls'] / s['total_calls'] * 100, 1) if s['total_calls'] else 0
        s['conversion_rate'] = round(s['converted_calls'] / s['answered_calls'] * 100, 1) if s['answered_calls'] else 0

    return stats


def get_team_summary(tenant_id, date_from: date, date_to: date):
    """Overall team stats for the date range."""
    from telephony.models import CallLog

    qs = CallLog.objects.filter(
        tenant_id=tenant_id,
        call_time__date__range=[date_from, date_to],
    ).exclude(call_leg='a')

    return qs.aggregate(
        total_calls=Count('id'),
        answered_calls=Count('id', filter=Q(call_type='answered')),
        missed_calls=Count('id', filter=Q(call_type='missed')),
        total_talk_time=Coalesce(Sum('duration', filter=Q(call_type='answered')), 0),
        avg_call_duration=Avg('duration', filter=Q(call_type='answered')),
        outbound_calls=Count('id', filter=Q(direction='outbound')),
        inbound_calls=Count('id', filter=Q(direction='inbound')),
        calls_with_outcome=Count('id', filter=Q(call_outcome__isnull=False)),
        converted_calls=Count('id', filter=Q(call_outcome='converted')),
    )


def get_missed_unattended(tenant_id, hours_threshold: int = 2):
    """
    Inbound missed calls in the last 24h with no subsequent callback call to the same number.
    These are the urgent ones that need follow-up.
    """
    from telephony.models import CallLog

    since = timezone.now() - timedelta(hours=24)
    threshold_dt = timezone.now() - timedelta(hours=hours_threshold)

    missed = CallLog.objects.filter(
        tenant_id=tenant_id,
        direction='inbound',
        call_type='missed',
        call_time__gte=since,
    ).exclude(call_leg='a').values('from_number', 'id', 'call_time', 'agent_user_id')

    result = []
    for m in missed:
        # Check if there was an outbound call to this number after the missed call
        called_back = CallLog.objects.filter(
            tenant_id=tenant_id,
            direction='outbound',
            to_number=m['from_number'],
            call_time__gt=m['call_time'],
        ).exists()
        if not called_back:
            m['hours_waiting'] = round((timezone.now() - m['call_time']).total_seconds() / 3600, 1)
            m['is_urgent'] = m['call_time'] <= threshold_dt
            result.append(m)

    return sorted(result, key=lambda x: x['call_time'])


def get_outcome_breakdown(tenant_id, date_from: date, date_to: date, agent_user_id=None):
    """Call outcome distribution for pie/bar chart."""
    from telephony.models import CallLog

    qs = CallLog.objects.filter(
        tenant_id=tenant_id,
        call_time__date__range=[date_from, date_to],
        call_outcome__isnull=False,
    ).exclude(call_leg='a')

    if agent_user_id:
        qs = qs.filter(agent_user_id=agent_user_id)

    return list(
        qs.values('call_outcome')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
