from functools import wraps
from django.http import JsonResponse
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import BasePermission
from common.generated_permissions import CRMPermissions
import logging

logger = logging.getLogger(__name__)


class JWTAuthentication(BaseAuthentication):
    """
    DRF Authentication class that works with JWT middleware

    The JWT middleware already validates the token and sets request attributes.
    This authentication class just marks the request as authenticated for DRF.
    """

    def authenticate(self, request):
        """
        Authenticate the request using JWT data from middleware

        Returns:
            tuple: (user_id, auth_data) or None if not authenticated
        """
        # Check if JWT middleware has set the user_id
        if not hasattr(request, 'user_id'):
            logger.debug("JWTAuthentication: No user_id attribute on request")
            return None

        # Debug log the JWT attributes set by middleware
        jwt_data = {
            'user_id': getattr(request, 'user_id', None),
            'tenant_id': getattr(request, 'tenant_id', None),
            'is_super_admin': getattr(request, 'is_super_admin', None),
            'permissions': getattr(request, 'permissions', None),
            'enabled_modules': getattr(request, 'enabled_modules', None),
        }
        logger.debug(f"JWTAuthentication - Decoded JWT attributes: {jwt_data}")

        # Return a tuple of (user, auth)
        # We use user_id as the user object since we don't use Django's User model
        return (request.user_id, None)


def get_permission_value(permissions, permission_key):
    """Resolve a permission value from flat or nested JWT permission payloads."""
    if not isinstance(permissions, dict):
        return None

    if permission_key in permissions:
        return permissions.get(permission_key)

    current = permissions
    for part in permission_key.split('.'):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def is_admin_request(request):
    """Return True only for explicit platform/tenant admin grants in the JWT.

    Role names are intentionally NOT trusted as an admin bypass.
    """
    if getattr(request, 'is_super_admin', False):
        return True

    permissions = getattr(request, 'permissions', {}) or {}
    return any(
        get_permission_value(permissions, key) is True
        for key in ('admin.full_access', 'admin.full_access.enabled')
    )


# ---------------------------------------------------------------------------
# Ownership registry for scoped (own/team) permissions
# ---------------------------------------------------------------------------
# Maps ``app_label.ModelName`` to the fields that determine whether a user
# owns or is on the team for a record.  Scoped grants fail closed when no
# ownership field can be resolved.
OWNERSHIP_FIELDS = {
    'crm.Lead': {
        'owner': 'owner_user_id',
        'team': ('owner_user_id', 'assigned_to'),
    },
    'crm.LeadActivity': {
        'owner': 'lead__owner_user_id',
        'team': ('lead__owner_user_id', 'by_user_id'),
    },
    'crm.LeadGroup': {
        'owner': 'created_by',
        'team': ('created_by',),
    },
    'meetings.Meeting': {
        'owner': 'owner_user_id',
        'team': ('owner_user_id',),
    },
}


def _model_label(obj):
    """Return ``app_label.ModelName`` for an object or queryset."""
    if hasattr(obj, '_meta'):
        return obj._meta.label
    if hasattr(obj, 'model'):
        return obj.model._meta.label
    return None


def _actor_id(request):
    return getattr(request, 'user_id', None)


def get_object_owner_id(obj):
    """Resolve the owner of ``obj`` using the ownership registry.

    Returns the first non-empty owner field found, or ``None`` if the model
    is not registered or has no resolvable owner.  This is a fail-closed
    replacement for the old best-effort helper.
    """
    label = _model_label(obj)
    if not label:
        return None

    owner_field = OWNERSHIP_FIELDS.get(label, {}).get('owner')
    if not owner_field:
        return None

    try:
        value = obj
        for part in owner_field.split('__'):
            value = getattr(value, part, None)
            if value is None:
                return None
        return value
    except Exception:
        return None


def _team_fields_for(obj):
    """Return the tuple of team fields registered for ``obj``'s model."""
    label = _model_label(obj)
    if not label:
        return ()
    return OWNERSHIP_FIELDS.get(label, {}).get('team', ())


def _is_team_member(obj, user_id):
    """Return True if ``user_id`` matches any registered team field on ``obj``."""
    if not user_id:
        return False
    for field in _team_fields_for(obj):
        try:
            value = obj
            for part in field.split('__'):
                value = getattr(value, part, None)
                if value is None:
                    break
            if value is not None and str(value) == str(user_id):
                return True
        except Exception:
            continue
    return False


def _team_filter_kwargs(model_label, user_id):
    """Return OR-filter kwargs for queryset-level team scoping.

    Example: ``Q(owner_user_id=user_id) | Q(assigned_to=user_id)``
    """
    from django.db.models import Q
    if not model_label or not user_id:
        return Q(pk__in=[])

    fields = OWNERSHIP_FIELDS.get(model_label, {}).get('team', ())
    if not fields:
        return Q(pk__in=[])

    q = Q()
    for field in fields:
        q |= Q(**{field: user_id})
    return q


def _permission_action(permission_key):
    """Return the action suffix of a permission key (e.g. 'create' for 'crm.leads.create')."""
    if permission_key and '.' in permission_key:
        return permission_key.rsplit('.', 1)[-1]
    return None


def check_permission(request, permission_key, resource_owner_id=None, resource_team_id=None):
    """
    Check if user has permission for a specific action.

    This is an *action-level* check: it answers "does the JWT grant this
    permission to the user?"  For ``own``/``team`` scope, a matching grant is
    accepted even without an object, because the queryset/object-level checks
    are responsible for restricting which rows the user actually sees.

    Object-level enforcement is handled by ``check_object_permission``.
    """
    if not hasattr(request, 'permissions'):
        return False

    if is_admin_request(request):
        return True

    permissions = request.permissions
    permission_value = get_permission_value(permissions, permission_key)

    # If permission not found, deny access
    if permission_value is None:
        return False

    # Handle boolean permissions
    if isinstance(permission_value, bool):
        return permission_value

    # Handle scope-based permissions
    if isinstance(permission_value, str):
        if permission_value == "all":
            return True
        elif permission_value == "team":
            if resource_team_id is None:
                return True
            return str(resource_team_id) == str(_actor_id(request))
        elif permission_value == "own":
            if resource_owner_id is None:
                return True
            return str(resource_owner_id) == str(_actor_id(request))

    return False


def check_object_permission(request, obj, permission_key):
    """Object-level permission check using the ownership registry.

    ``own`` and ``team`` scopes are resolved from the object itself.  Missing or
    unresolvable ownership results in denial (fail-closed).
    """
    if not hasattr(request, 'permissions'):
        return False

    if is_admin_request(request):
        return True

    permission_value = get_permission_value(request.permissions, permission_key)
    if permission_value is None:
        return False

    if isinstance(permission_value, bool):
        return permission_value

    if not isinstance(permission_value, str):
        return False

    if permission_value == "all":
        return True

    user_id = _actor_id(request)
    if permission_value == "team":
        return _is_team_member(obj, user_id)

    if permission_value == "own":
        owner_id = get_object_owner_id(obj)
        if owner_id is None:
            # Allow create actions to proceed even though there is no object yet;
            # object-level checks are not used for create.
            return _permission_action(permission_key) == 'create'
        return str(owner_id) == str(user_id)

    return False


def permission_required(permission_key):
    """
    Decorator for views to check permissions before execution
    
    Args:
        permission_key: Permission key to check (e.g., 'crm.leads.create')
    
    Returns:
        Decorator function
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not check_permission(request, permission_key):
                return JsonResponse(
                    {'error': 'Permission denied'}, 
                    status=403
                )
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def get_queryset_for_permission(queryset, request, view_permission_key, owner_field='owner_user_id'):
    """
    Filter queryset based on view permission scope.

    Uses the ownership registry for ``own``/``team`` scope.  Unregistered models
    fall back to ``owner_field`` for ``own`` scope and deny ``team`` scope.
    """
    if not hasattr(request, 'permissions') or not hasattr(request, 'tenant_id'):
        return queryset.none()

    permissions = request.permissions
    permission_value = permissions.get(view_permission_key)

    # Always filter by tenant_id first
    base_queryset = queryset.filter(tenant_id=request.tenant_id)

    # If permission not found, deny access
    if permission_value is None:
        return queryset.none()

    # Handle boolean permissions
    if isinstance(permission_value, bool):
        return base_queryset if permission_value else queryset.none()

    # Handle scope-based permissions
    if isinstance(permission_value, str):
        if permission_value == "all":
            return base_queryset
        elif permission_value == "team":
            model_label = _model_label(queryset)
            team_q = _team_filter_kwargs(model_label, _actor_id(request))
            return base_queryset.filter(team_q)
        elif permission_value == "own":
            model_label = _model_label(queryset)
            fields = OWNERSHIP_FIELDS.get(model_label, {}).get('team', ())
            if fields:
                owner_field = fields[0]
            filter_kwargs = {owner_field: _actor_id(request)}
            return base_queryset.filter(**filter_kwargs)

    return queryset.none()


class PermissionRequiredMixin:
    """
    Mixin for ViewSets to add permission checking
    """
    permission_map = {
        'list': None,
        'retrieve': None,
        'create': None,
        'update': None,
        'partial_update': None,
        'destroy': None,
    }
    
    def check_permissions(self, request):
        """Override to check custom permissions"""
        super().check_permissions(request)
        
        # Get permission key for current action
        permission_key = self.permission_map.get(self.action)
        if permission_key:
            if not check_permission(request, permission_key):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("You don't have permission to perform this action")
    
    def check_object_permissions(self, request, obj):
        """Override to check object-level permissions"""
        super().check_object_permissions(request, obj)
        
        # For update/delete operations, check with resource owner
        if self.action in ['update', 'partial_update', 'destroy']:
            permission_key = self.permission_map.get(self.action)
            if permission_key:
                owner_id = getattr(obj, 'owner_user_id', None)
                if not check_permission(request, permission_key, owner_id):
                    from rest_framework.exceptions import PermissionDenied
                    raise PermissionDenied("You don't have permission to modify this resource")


def has_module_access(request, module_name):
    """
    Check if user has access to a specific module

    Args:
        request: Django request object with JWT attributes
        module_name: Name of the module (e.g., 'crm', 'whatsapp')

    Returns:
        bool: True if module is enabled, False otherwise
    """
    if not hasattr(request, 'enabled_modules'):
        return False

    if is_admin_request(request):
        return True

    enabled_modules = getattr(request, 'enabled_modules', []) or []
    return module_name in enabled_modules


def get_nested_permission(permissions, path):
    """
    Get nested permission value from permissions dictionary

    Args:
        permissions: Nested permissions dictionary
        path: Dot-separated path (e.g., 'crm.leads.view')

    Returns:
        Permission value or None if not found
    """
    return get_permission_value(permissions, path)


def get_object_owner_id(obj):
    """Best-effort ownership resolver used for scoped permissions."""
    for field_name in ('owner_user_id', 'by_user_id', 'created_by', 'uploaded_by'):
        if hasattr(obj, field_name):
            value = getattr(obj, field_name)
            if value:
                return value

    lead = getattr(obj, 'lead', None)
    if lead is not None and hasattr(lead, 'owner_user_id'):
        return lead.owner_user_id

    return None


class HasDigiPermission(BasePermission):
    """
    DRF Permission class for DigiCRM modules.
    Works with JWT permissions set by middleware

    Usage in ViewSet:
        permission_classes = [HasDigiPermission]
        permission_module = 'crm'
        permission_resource = 'leads'  # or 'activities', 'statuses', etc.
    """

    # Map DRF actions to permission types
    ACTION_PERMISSION_MAP = {
        'list': 'view',
        'retrieve': 'view',
        'create': 'create',
        'update': 'edit',
        'partial_update': 'edit',
        'destroy': 'delete',
    }

    WRITE_METHOD_ACTION_MAP = {
        'GET': 'view',
        'HEAD': 'view',
        'OPTIONS': 'view',
        'POST': 'create',
        'PUT': 'edit',
        'PATCH': 'edit',
        'DELETE': 'delete',
    }

    def has_permission(self, request, view):
        """
        Check if user has permission to perform the action on the resource
        """
        resource = getattr(view, 'permission_resource', None)
        if not resource:
            self.message = "Permission not granted for this module."
            return False

        module = getattr(view, 'permission_module', 'crm')

        if not has_module_access(request, module):
            self.message = f"Permission not granted for {module} module."
            return False

        action = getattr(view, 'action', None)
        permission_type = getattr(view, 'permission_action', None)
        if not permission_type:
            permission_type = self.ACTION_PERMISSION_MAP.get(action) if action else None
        if not permission_type:
            permission_type = self.WRITE_METHOD_ACTION_MAP.get(request.method.upper())

        custom_map = getattr(view, 'action_permission_map', {}) or {}
        if action in custom_map:
            permission_type = custom_map[action]

        if not permission_type:
            self.message = "Permission not granted for this module."
            return False

        permission_key = f"{module}.{resource}.{permission_type}"

        allowed = self._check_permission(request, permission_key)
        if not allowed:
            self.message = "Permission not granted for this module."
        return allowed

    def has_object_permission(self, request, view, obj):
        """
        Check if user has permission to perform the action on this specific object.

        Uses the ownership registry so ``own``/``team`` scope is evaluated from
        the object itself rather than an externally supplied owner ID.
        """
        # Get the resource and action from the view
        resource = getattr(view, 'permission_resource', None)
        if not resource:
            self.message = "Permission not granted for this module."
            return False

        module = getattr(view, 'permission_module', 'crm')

        action = getattr(view, 'action', None)
        custom_map = getattr(view, 'action_permission_map', {}) or {}
        permission_type = getattr(view, 'permission_action', None)
        if not permission_type:
            permission_type = custom_map.get(action) or self.ACTION_PERMISSION_MAP.get(action)
        if not permission_type:
            permission_type = self.WRITE_METHOD_ACTION_MAP.get(request.method.upper())

        if not permission_type:
            self.message = "Permission not granted for this module."
            return False

        permission_key = f"{module}.{resource}.{permission_type}"

        allowed = check_object_permission(request, obj, permission_key)
        if not allowed:
            self.message = "Permission not granted for this module."
        return allowed

    def _check_permission(self, request, permission_key, resource_owner_id=None):
        """Internal method now delegates to the centralized ``check_permission``."""
        return check_permission(request, permission_key, resource_owner_id=resource_owner_id)


class CRMPermissionMixin:
    """
    Mixin for CRM ViewSets to add permission checking
    Handles nested permission structure from JWT

    DEPRECATED: Use HasCRMPermission permission class instead
    This mixin is kept for backwards compatibility
    """
    # Map of actions to permission keys
    # Should be overridden in each ViewSet
    permission_resource = None  # e.g., 'leads', 'activities', 'payments', 'statuses'

    permission_action_map = {
        'list': 'view',
        'retrieve': 'view',
        'create': 'create',
        'update': 'edit',
        'partial_update': 'edit',
        'destroy': 'delete',
    }

    def get_permission_key(self, action):
        """Get the permission key for the current action"""
        if not self.permission_resource:
            return None

        permission_action = self.permission_action_map.get(action)
        if not permission_action:
            return None

        return f"crm.{self.permission_resource}.{permission_action}"

    def get_queryset(self):
        """Override to filter queryset based on view permissions."""
        queryset = super().get_queryset()

        if not hasattr(self, 'request') or not self.request:
            return queryset

        if is_admin_request(self.request):
            return queryset

        # Get view permission
        view_permission_key = f"crm.{self.permission_resource}.view"
        return get_queryset_for_permission(
            queryset, self.request, view_permission_key
        )

    def _has_crm_permission(self, request, permission_key, resource_owner_id=None):
        """Internal method now delegates to the centralized action-level check."""
        return check_permission(request, permission_key, resource_owner_id=resource_owner_id)


class HasCRMPermission(HasDigiPermission):
    """Backward-compatible CRM permission class."""
    pass
