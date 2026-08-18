from rest_framework.permissions import BasePermission

from common.permissions import (
    CRMPermissions,
    HasCRMPermission,
    check_object_permission,
    has_module_access,
)


class CanRespondToMeeting(HasCRMPermission):
    """Allow RSVP when the caller is an attendee, or holds meetings.edit on it.

    An attendee holding only ``crm.meetings.view: "own"`` legitimately needs to
    respond to a meeting they do not own, so RSVP cannot go through
    ``HasCRMPermission`` alone.
    """

    message = 'Permission not granted for this module.'

    def has_permission(self, request, view):
        # Module gate still applies; the per-object check does the real work.
        module = getattr(view, 'permission_module', 'crm')
        if not has_module_access(request, module):
            self.message = f'Permission not granted for {module} module.'
            return False
        return getattr(request, 'user_id', None) is not None

    def has_object_permission(self, request, view, obj):
        user_id = getattr(request, 'user_id', None)
        if user_id and obj.attendees.filter(user_id=user_id).exists():
            return True
        allowed = check_object_permission(request, obj, CRMPermissions.CRM_MEETINGS_EDIT)
        if not allowed:
            self.message = 'Permission not granted for this module.'
        return allowed


class HasMeetingCancelPermission(BasePermission):
    """``crm.meetings.cancel`` object-level gate for the cancel action."""

    message = 'Permission not granted for this module.'

    def has_permission(self, request, view):
        return has_module_access(request, getattr(view, 'permission_module', 'crm'))

    def has_object_permission(self, request, view, obj):
        return check_object_permission(request, obj, CRMPermissions.CRM_MEETINGS_CANCEL)
