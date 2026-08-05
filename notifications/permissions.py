from rest_framework.permissions import BasePermission


class HasJWTIdentity(BasePermission):
    """Require the validated JWT identity populated by the shared middleware."""

    message = 'Authentication credentials were not provided.'

    def has_permission(self, request, view):
        return bool(
            getattr(request, 'user_id', None)
            and getattr(request, 'tenant_id', None)
        )

