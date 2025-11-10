from functools import wraps
from django.http import JsonResponse
from rest_framework.response import Response


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