"""Task domain helpers: tenant-scoped related-object resolution and recurrence.

Two rules this module exists to enforce:

1. **Nothing resolves a ``related_id`` without a ``tenant_id``.**  ``related_type``
   + ``related_id`` is an unconstrained integer pair -- there is no FK stopping a
   caller from writing another tenant's project id into it.  Every lookup here
   goes through ``_queryset_for`` which filters on ``tenant_id`` first, so a
   cross-tenant id resolves to ``None`` (no label, no leak) instead of to a real
   row.

2. **There is exactly one RRULE parser in this codebase**, ``meetings.recurrence``.
   Task recurrence calls into it rather than re-deriving occurrence maths.
"""
from datetime import timedelta

from django.utils import timezone as dj_timezone

from meetings import recurrence

from .models import Task, TaskChecklistItem, TaskRelatedTypeEnum, TaskStatusEnum

#: related_type -> (app_label.Model, attribute used as the human label)
RELATED_MODELS = {
    TaskRelatedTypeEnum.LEAD: ('crm.Lead', 'name'),
    TaskRelatedTypeEnum.PROJECT: ('real_estate.Project', 'name'),
    TaskRelatedTypeEnum.UNIT: ('real_estate.Unit', 'unit_number'),
    TaskRelatedTypeEnum.MEETING: ('meetings.Meeting', 'title'),
}


def _model_for(related_type):
    from django.apps import apps

    entry = RELATED_MODELS.get(related_type)
    if not entry:
        return None, None
    label, attribute = entry
    app_label, model_name = label.split('.')
    return apps.get_model(app_label, model_name), attribute


def _queryset_for(related_type, tenant_id):
    """Tenant-scoped queryset for a related_type, or ``None`` for NONE/unknown."""
    model, attribute = _model_for(related_type)
    if model is None or tenant_id is None:
        return None, None
    return model.objects.filter(tenant_id=tenant_id), attribute


def related_exists(tenant_id, related_type, related_id):
    """True only when ``related_id`` is a row of ``related_type`` in this tenant."""
    if not related_id or related_type in (None, '', TaskRelatedTypeEnum.NONE):
        return False
    queryset, _ = _queryset_for(related_type, tenant_id)
    if queryset is None:
        return False
    return queryset.filter(pk=related_id).exists()


def resolve_related_labels(tenant_id, pairs):
    """Bulk-resolve ``[(related_type, related_id), ...]`` to a label map.

    One query per distinct ``related_type`` instead of one per task, so a page of
    tasks costs at most four queries no matter how long the page is.
    """
    wanted = {}
    for related_type, related_id in pairs:
        if not related_id or related_type in (None, '', TaskRelatedTypeEnum.NONE):
            continue
        wanted.setdefault(related_type, set()).add(related_id)

    labels = {}
    for related_type, ids in wanted.items():
        queryset, attribute = _queryset_for(related_type, tenant_id)
        if queryset is None:
            continue
        for pk, value in queryset.filter(pk__in=ids).values_list('pk', attribute):
            labels[(related_type, pk)] = str(value) if value is not None else None
    return labels


def resolve_related_label(tenant_id, related_type, related_id, cache=None):
    """Display name for one related object, or ``None`` if it is not ours."""
    if not related_id or related_type in (None, '', TaskRelatedTypeEnum.NONE):
        return None
    key = (related_type, related_id)
    if cache is not None and key in cache:
        return cache[key]
    resolved = resolve_related_labels(tenant_id, [key]).get(key)
    if cache is not None:
        cache[key] = resolved
    return resolved


# ---------------------------------------------------------------------------
# Recurrence
# ---------------------------------------------------------------------------
def next_occurrence_after(task, anchor=None):
    """UTC instant of the occurrence of ``task``'s rrule strictly after ``anchor``.

    ``None`` when the task is not recurring, has no anchor date, or the series
    has run out.  Expansion happens in ``task.timezone`` (via
    ``meetings.recurrence.build_rrule``) so a daily 09:00 IST follow-up stays
    09:00 IST across a DST change anywhere else.
    """
    if not task.rrule:
        return None
    base = anchor or task.start_date or task.due_date
    if not base:
        return None
    try:
        rule = recurrence.build_rrule(task.rrule, base, task.timezone)
    except Exception:
        return None
    if rule is None:
        return None

    zone = recurrence.get_zone(task.timezone)
    cursor = recurrence.to_utc(base).astimezone(zone)
    try:
        following = rule.after(cursor, inc=False)
    except Exception:
        return None
    if following is None:
        return None
    return recurrence.to_utc(following)


def spawn_next_occurrence(task):
    """Create the next occurrence of a recurring task. Returns it, or ``None``.

    Called when a recurring task is completed: the completed row stays completed
    (it is the historical record) and a fresh TODO row is created for the next
    date in the series.  Idempotent per date -- if the occurrence already exists
    it is returned rather than duplicated.
    """
    if not task.rrule:
        return None

    anchor = task.start_date or task.due_date
    if not anchor:
        return None

    following = next_occurrence_after(task, anchor)
    if following is None:
        return None

    # Preserve the start->due gap so a "starts Monday, due Friday" task keeps its
    # week when it rolls forward.
    if task.start_date and task.due_date:
        offset = task.due_date - task.start_date
        next_start, next_due = following, following + offset
    elif task.start_date:
        next_start, next_due = following, None
    else:
        next_start, next_due = None, following

    parent = task.recurring_parent or task

    existing = Task.objects.filter(
        tenant_id=task.tenant_id,
        recurring_parent=parent,
        start_date=next_start,
        due_date=next_due,
    ).first()
    if existing:
        return existing

    occurrence = Task.objects.create(
        tenant_id=task.tenant_id,
        lead_id=task.lead_id,
        title=task.title,
        description=task.description,
        status=TaskStatusEnum.TODO,
        priority=task.priority,
        related_type=task.related_type,
        related_id=task.related_id,
        start_date=next_start,
        due_date=next_due,
        is_all_day=task.is_all_day,
        timezone=task.timezone,
        rrule=task.rrule,
        recurring_parent=parent,
        assignee_user_id=task.assignee_user_id,
        reporter_user_id=task.reporter_user_id,
        owner_user_id=task.owner_user_id,
        reminder_minutes_before=task.reminder_minutes_before,
        order_index=task.order_index,
        labels=list(task.labels or []),
    )

    # A recurring checklist ("call, email, log") must come back unticked.
    template = list(task.checklist_items.all())
    if template:
        TaskChecklistItem.objects.bulk_create([
            TaskChecklistItem(
                tenant_id=task.tenant_id,
                task=occurrence,
                text=item.text,
                is_done=False,
                order_index=item.order_index,
            )
            for item in template
        ])
    return occurrence


# ---------------------------------------------------------------------------
# my-day bucketing
# ---------------------------------------------------------------------------
MY_DAY_GROUPS = ('overdue', 'today', 'this_week', 'later', 'done_today')


def bucket_for_my_day(tasks, tz_name, now=None):
    """Group ``tasks`` into the my-day buckets **in the caller's timezone**.

    "Today" is the caller's local calendar day, not the UTC one -- an IST user's
    02:00 task is 20:30 UTC *yesterday*, and bucketing on the UTC date would file
    it under overdue every single morning.
    """
    now = now or dj_timezone.now()
    zone = recurrence.get_zone(tz_name)
    local_now = recurrence.to_utc(now).astimezone(zone)
    today = local_now.date()
    # ISO week: Monday..Sunday containing today.
    week_end = today + timedelta(days=(6 - today.weekday()))

    groups = {name: [] for name in MY_DAY_GROUPS}
    for task in tasks:
        if task.status == TaskStatusEnum.DONE:
            if task.completed_at and recurrence.to_utc(
                task.completed_at
            ).astimezone(zone).date() == today:
                groups['done_today'].append(task)
            continue
        if task.status == TaskStatusEnum.CANCELLED:
            continue

        due = task.due_date
        if not due:
            groups['later'].append(task)
            continue

        due_local = recurrence.to_utc(due).astimezone(zone)
        due_date = due_local.date()
        if due_date < today or (due_date == today and due_local < local_now
                                and not task.is_all_day):
            groups['overdue'].append(task)
        elif due_date == today:
            groups['today'].append(task)
        elif due_date <= week_end:
            groups['this_week'].append(task)
        else:
            groups['later'].append(task)
    return groups
