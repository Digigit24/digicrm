from functools import wraps
from django.http import JsonResponse
from rest_framework.response import Response
from rest_framework.authentication import BaseAuthentication


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
            return None

        # Return a tuple of (user, auth)
        # We use user_id as the user object since we don't use Django's User model
        return (request.user_id, None)


def check_permission(request, permission_key, resource_owner_id=None, resource_team_id=None):
    """
    Check if user has permission for a specific action
    
    Args:
        request: Django request object with JWT attributes
        permission_key: Permission key (e.g., 'crm.leads.view')
        resource_owner_id: UUID of resource owner (for 'own' scope checks)
        resource_team_id: UUID of resource team (for 'team' scope checks)
    
    Returns:
        bool: True if permission granted, False otherwise
    """
    if not hasattr(request, 'permissions'):
        return False
    
    permissions = request.permissions
    permission_value = permissions.get(permission_key)
    
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
            # For now, return True for team scope (team logic can be added later)
            # In future: check if resource_team_id matches user's team
            return True
        elif permission_value == "own":
            # Check if resource belongs to the user
            if resource_owner_id is None:
                return True  # If no owner specified, allow (for create operations)
            return str(resource_owner_id) == str(request.user_id)
    
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
    Filter queryset based on view permission scope
    
    Args:
        queryset: Django QuerySet to filter
        request: Django request object with JWT attributes
        view_permission_key: Permission key for viewing (e.g., 'crm.leads.view')
        owner_field: Field name that contains the owner user ID
    
    Returns:
        Filtered QuerySet based on permission scope
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
        if permission_value:
            return base_queryset
        else:
            return queryset.none()
    
    # Handle scope-based permissions
    if isinstance(permission_value, str):
        if permission_value == "all":
            return base_queryset
        elif permission_value == "team":
            # For now, return all tenant resources (team logic can be added later)
            # In future: add team filtering
            return base_queryset
        elif permission_value == "own":
            # Filter by owner
            filter_kwargs = {owner_field: request.user_id}
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

    return module_name in request.enabled_modules


def get_nested_permission(permissions, path):
    """
    Get nested permission value from permissions dictionary

    Args:
        permissions: Nested permissions dictionary
        path: Dot-separated path (e.g., 'crm.leads.view')

    Returns:
        Permission value or None if not found
    """
    keys = path.split('.')
    current = permissions

    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return None

    return current


class HasCRMPermission:
    """
    DRF Permission class for CRM module
    Works with JWT permissions set by middleware

    Usage in ViewSet:
        permission_classes = [HasCRMPermission]
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

    def has_permission(self, request, view):
        """
        Check if user has permission to perform the action on the resource
        """
        # Get the resource and action from the view
        resource = getattr(view, 'permission_resource', None)
        if not resource:
            # No permission resource defined, allow by default
            return True

        # Get the action (list, create, retrieve, etc.)
        action = view.action
        if not action:
            # No action defined, allow by default
            return True

        # Map action to permission type
        permission_type = self.ACTION_PERMISSION_MAP.get(action)
        if not permission_type:
            # Unknown action, allow by default (custom actions should handle their own perms)
            return True

        # Build permission key
        permission_key = f"crm.{resource}.{permission_type}"

        # Check permission
        return self._check_permission(request, permission_key)

    def has_object_permission(self, request, view, obj):
        """
        Check if user has permission to perform the action on this specific object
        """
        # Get the resource and action from the view
        resource = getattr(view, 'permission_resource', None)
        if not resource:
            return True

        action = view.action
        if not action:
            return True

        # Map action to permission type
        permission_type = self.ACTION_PERMISSION_MAP.get(action)
        if not permission_type:
            return True

        # Build permission key
        permission_key = f"crm.{resource}.{permission_type}"

        # Get the owner ID from the object
        owner_id = getattr(obj, 'owner_user_id', None)

        # Check permission with owner context
        return self._check_permission(request, permission_key, owner_id)

    def _check_permission(self, request, permission_key, resource_owner_id=None):
        """
        Internal method to check permissions using JWT data

        Args:
            request: Django request object with JWT attributes
            permission_key: Full permission key (e.g., 'crm.leads.view')
            resource_owner_id: UUID of resource owner (for 'own' scope checks)

        Returns:
            bool: True if permission granted
        """
        # Check if request has permissions attribute (set by JWT middleware)
        if not hasattr(request, 'permissions'):
            return False

        # Get the permission value from nested structure
        permission_value = get_nested_permission(request.permissions, permission_key)

        # If permission not found, deny access
        if permission_value is None:
            return False

        # Handle boolean permissions (simple true/false)
        if isinstance(permission_value, bool):
            return permission_value

        # Handle scope-based permissions (all, team, own)
        if isinstance(permission_value, str):
            if permission_value == "all":
                return True
            elif permission_value == "team":
                # For now, allow team scope (team logic can be added later)
                return True
            elif permission_value == "own":
                # Check if resource belongs to the user
                if resource_owner_id is None:
                    # No owner specified, allow (for create operations)
                    return True
                # Check if the resource owner matches the current user
                return str(resource_owner_id) == str(getattr(request, 'user_id', None))

        # Unknown permission type, deny access
        return False


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
        """Override to filter queryset based on view permissions"""
        queryset = super().get_queryset()

        if not hasattr(self, 'request') or not self.request:
            return queryset

        # Get view permission
        view_permission_key = f"crm.{self.permission_resource}.view"
        permission_value = get_nested_permission(
            getattr(self.request, 'permissions', {}),
            view_permission_key
        )

        # If no permission found, return empty queryset
        if permission_value is None:
            return queryset.none()

        # Handle boolean permissions
        if isinstance(permission_value, bool):
            return queryset if permission_value else queryset.none()

        # Handle scope-based permissions
        if isinstance(permission_value, str):
            if permission_value == "all":
                return queryset
            elif permission_value == "team":
                # For now, return all tenant resources (team logic can be added later)
                return queryset
            elif permission_value == "own":
                # Filter by owner - only show user's own resources
                if hasattr(queryset.model, 'owner_user_id'):
                    return queryset.filter(owner_user_id=self.request.user_id)
                return queryset

        return queryset

    def _has_crm_permission(self, request, permission_key, resource_owner_id=None):
        """
        Internal method to check CRM permissions

        Args:
            request: Django request object
            permission_key: Full permission key (e.g., 'crm.leads.view')
            resource_owner_id: UUID of resource owner (for 'own' scope checks)

        Returns:
            bool: True if permission granted
        """
        if not hasattr(request, 'permissions'):
            return False

        permission_value = get_nested_permission(request.permissions, permission_key)

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
                # For now, return True for team scope
                return True
            elif permission_value == "own":
                # Check if resource belongs to the user
                if resource_owner_id is None:
                    return True  # If no owner specified, allow (for create operations)
                return str(resource_owner_id) == str(request.user_id)

        return False