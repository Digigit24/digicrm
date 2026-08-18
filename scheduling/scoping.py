"""``user_ids`` scoping for the unified calendar feed.

Each source layer resolves its own permission (``crm.meetings.view``,
``crm.tasks.view``, ``crm.leads.view``) **independently**: a user with
``crm.meetings.view: "all"`` but ``crm.tasks.view: "own"`` sees the team's
meetings and only their own tasks. That is why the merge is server-side.

Everything here fails closed.
"""
from common.permissions import get_permission_value, is_admin_request

SCOPE_ALL = 'all'
SCOPE_TEAM = 'team'
SCOPE_OWN = 'own'


def actor_id(request):
    return str(getattr(request, 'user_id', '') or '')


def resolve_scope(request, permission_key):
    """``'all' | 'team' | 'own' | None`` for ``permission_key``.

    ``None`` means the caller holds no usable grant for that layer.
    """
    if is_admin_request(request):
        return SCOPE_ALL
    value = get_permission_value(getattr(request, 'permissions', {}) or {}, permission_key)
    if value is True:
        return SCOPE_ALL
    if value in (SCOPE_ALL, SCOPE_TEAM, SCOPE_OWN):
        return value
    return None


def _team_user_ids(request):
    """Users the caller shares a meeting with, plus the caller.

    Only reachable when a role is actually granted ``team`` -- the generated
    permission catalog currently only offers ``own``/``all``, so this is dormant
    but working code (v1 ships ``all``-for-admins).
    """
    from meetings.models import MeetingAttendee, Meeting

    self_id = actor_id(request)
    tenant_id = getattr(request, 'tenant_id', None)
    if not self_id or not tenant_id:
        return set()

    my_meetings = Meeting.objects.filter(tenant_id=tenant_id).filter(
        attendees__user_id=self_id
    ).values_list('id', flat=True)

    peers = set(
        str(value) for value in MeetingAttendee.objects.filter(
            tenant_id=tenant_id, meeting_id__in=list(my_meetings)
        ).exclude(user_id=None).values_list('user_id', flat=True)
    )
    owners = set(
        str(value) for value in Meeting.objects.filter(
            tenant_id=tenant_id, id__in=list(my_meetings)
        ).values_list('owner_user_id', flat=True)
    )
    return peers | owners | {self_id}


def allowed_user_ids(request, requested, permission_key):
    """Intersect ``requested`` with what the caller may see for ``permission_key``.

    Returns ``(allowed, denied, scope)``. ``scope is None`` means 403 territory.
    """
    self_id = actor_id(request)
    requested = [str(value) for value in (requested or [])] or [self_id]
    scope = resolve_scope(request, permission_key)

    if scope is None:
        return set(), set(requested), None
    if scope == SCOPE_ALL:
        allowed = set(requested)
    elif scope == SCOPE_TEAM:
        allowed = set(requested) & _team_user_ids(request)
    else:
        allowed = set(requested) & {self_id}

    denied = set(requested) - allowed
    return allowed, denied, scope
