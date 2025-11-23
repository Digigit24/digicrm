from django.contrib import admin
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.utils.decorators import method_decorator
from django.contrib.admin import AdminSite
from django.shortcuts import render
import logging

logger = logging.getLogger(__name__)


class TenantAdminSite(AdminSite):
    """
    Custom admin site that handles tenant-based authentication
    """
    
    # Customization
    site_title = _('DigiCRM Administration')
    site_header = _('DigiCRM Admin')
    index_title = _('Welcome to DigiCRM Administration')
    
    def __init__(self, name='admin'):
        super().__init__(name)
        self.login_template = 'admin/login.html'
    
    def has_permission(self, request):
        """
        Check if the current user has permission to view the admin site.
        """
        # Check if user is authenticated via session
        if hasattr(request, 'session'):
            jwt_token = request.session.get('jwt_token')
            user_data = request.session.get('user_data')
            
            if jwt_token and user_data:
                # Create a temporary user object for admin
                from .auth_backends import TenantUser
                request.user = TenantUser(user_data)
                
                # Set tenant information on request
                request.tenant_id = user_data.get('tenant_id')
                request.tenant_slug = user_data.get('tenant_slug')
                request.permissions = user_data.get('permissions', {})
                request.enabled_modules = user_data.get('enabled_modules', [])
                
                return True
        
        # Check if user is authenticated via JWT middleware (API requests)
        if hasattr(request, 'user_id') and hasattr(request, 'tenant_id'):
            # Create a temporary user object for admin
            from .auth_backends import TenantUser
            user_data = {
                'user_id': request.user_id,
                'email': getattr(request, 'email', ''),
                'tenant_id': request.tenant_id,
                'tenant_slug': getattr(request, 'tenant_slug', ''),
                'is_super_admin': getattr(request, 'is_super_admin', False),
                'permissions': getattr(request, 'permissions', {}),
                'enabled_modules': getattr(request, 'enabled_modules', [])
            }
            request.user = TenantUser(user_data)
            return True
        
        return False
    
    @method_decorator(never_cache)
    def login(self, request, extra_context=None):
        """
        Display the login form for the given HttpRequest.
        """
        if request.method == 'GET' and self.has_permission(request):
            # Already logged in, redirect to index
            index_path = reverse('admin:index', current_app=self.name)
            return HttpResponseRedirect(index_path)
        
        # Use our custom login view
        from .views import SuperAdminLoginView
        login_view = SuperAdminLoginView.as_view(
            template_name=self.login_template or 'admin/login.html',
            extra_context=extra_context or {}
        )
        return login_view(request)
    
    def logout(self, request, extra_context=None):
        """
        Log out the user and display the logout page.
        """
        from .views import admin_logout_view
        return admin_logout_view(request)
    
    def index(self, request, extra_context=None):
        """
        Display the main admin index page, which lists all available apps.
        """
        if not self.has_permission(request):
            return redirect_to_login(
                request.get_full_path(),
                reverse('admin:login', current_app=self.name)
            )
        
        # Add tenant information to context
        extra_context = extra_context or {}
        if hasattr(request, 'user') and hasattr(request.user, 'tenant_slug'):
            extra_context.update({
                'tenant_name': request.user.tenant_slug,
                'tenant_id': request.user.tenant_id,
                'user_email': request.user.email,
                'enabled_modules': getattr(request.user, 'enabled_modules', []),
                'is_tenant_admin': True
            })
        
        return super().index(request, extra_context)
    
    def app_index(self, request, app_label, extra_context=None):
        """
        Display the admin index page for a single app.
        """
        if not self.has_permission(request):
            return redirect_to_login(
                request.get_full_path(),
                reverse('admin:login', current_app=self.name)
            )
        
        # Check if user has permission for this app
        if hasattr(request, 'user') and hasattr(request.user, 'has_module_perms'):
            if not request.user.has_module_perms(app_label):
                from django.core.exceptions import PermissionDenied
                raise PermissionDenied("You don't have permission to access this app.")
        
        # Add tenant information to context
        extra_context = extra_context or {}
        if hasattr(request, 'user') and hasattr(request.user, 'tenant_slug'):
            extra_context.update({
                'tenant_name': request.user.tenant_slug,
                'tenant_id': request.user.tenant_id,
                'user_email': request.user.email,
                'is_tenant_admin': True
            })
        
        return super().app_index(request, app_label, extra_context)


# Create the custom admin site instance
tenant_admin_site = TenantAdminSite(name='admin')


class TenantModelAdmin(admin.ModelAdmin):
    """
    Base ModelAdmin class that automatically filters by tenant_id
    """
    
    def get_exclude(self, request, obj=None):
        """
        Exclude tenant_id from the form - it will be set automatically
        """
        exclude = list(super().get_exclude(request, obj) or [])
        if hasattr(self.model, 'tenant_id'):
            if 'tenant_id' not in exclude:
                exclude.append('tenant_id')
        return exclude
    
    def get_queryset(self, request):
        """
        Filter queryset by tenant_id from request
        """
        qs = super().get_queryset(request)
        
        # Check if model has tenant_id field
        if hasattr(qs.model, 'tenant_id') and hasattr(request, 'tenant_id'):
            qs = qs.filter(tenant_id=request.tenant_id)
        
        return qs
    
    def save_model(self, request, obj, form, change):
        """
        Set tenant_id when saving model
        """
        if hasattr(obj, 'tenant_id') and hasattr(request, 'tenant_id'):
            if not change:  # Only set on create, not update
                obj.tenant_id = request.tenant_id
        
        super().save_model(request, obj, form, change)
    
    def has_view_permission(self, request, obj=None):
        """
        Check view permission based on tenant and user permissions
        """
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return False
        
        # Check if user has permission for this app
        app_label = self.model._meta.app_label
        if hasattr(request.user, 'has_module_perms'):
            return request.user.has_module_perms(app_label)
        
        return super().has_view_permission(request, obj)
    
    def has_add_permission(self, request):
        """
        Check add permission based on user permissions
        """
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return False
        
        app_label = self.model._meta.app_label
        model_name = self.model._meta.model_name
        perm = f"{app_label}.add_{model_name}"
        
        if hasattr(request.user, 'has_perm'):
            return request.user.has_perm(perm)
        
        return super().has_add_permission(request)
    
    def has_change_permission(self, request, obj=None):
        """
        Check change permission based on user permissions
        """
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return False
        
        app_label = self.model._meta.app_label
        model_name = self.model._meta.model_name
        perm = f"{app_label}.change_{model_name}"
        
        if hasattr(request.user, 'has_perm'):
            return request.user.has_perm(perm)
        
        return super().has_change_permission(request, obj)
    
    def has_delete_permission(self, request, obj=None):
        """
        Check delete permission based on user permissions
        """
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return False
        
        app_label = self.model._meta.app_label
        model_name = self.model._meta.model_name
        perm = f"{app_label}.delete_{model_name}"
        
        if hasattr(request.user, 'has_perm'):
            return request.user.has_perm(perm)
        
        return super().has_delete_permission(request, obj)