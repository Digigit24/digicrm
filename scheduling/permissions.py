from rest_framework.permissions import BasePermission


class HasJWTIdentity(BasePermission):
    """Require the validated JWT identity populated by the shared middleware.

    Mirrors ``notifications.permissions.HasJWTIdentity``. DRF's ``IsAuthenticated``
    cannot be used with ``common.permissions.JWTAuthentication`` because that
    class returns a bare ``user_id`` string as the user, which has no
    ``is_authenticated`` attribute.
    """

    message = 'Authentication credentials were not provided.'

    def has_permission(self, request, view):
        return bool(
            getattr(request, 'user_id', None)
            and getattr(request, 'tenant_id', None)
        )
