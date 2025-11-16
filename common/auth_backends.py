import jwt
import requests
from django.conf import settings
from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.models import AnonymousUser
import logging

logger = logging.getLogger(__name__)


class TenantUser:
    """
    A custom user class that mimics Django's User model for admin authentication
    without requiring a database User model
    """
    def __init__(self, user_data):
        self.id = user_data.get('user_id')
        self.pk = user_data.get('user_id')
        self.username = user_data.get('email', '')
        self.email = user_data.get('email', '')
        self.first_name = user_data.get('first_name', '')
        self.last_name = user_data.get('last_name', '')
        self.is_active = True
        self.is_staff = True  # Allow access to admin
        self.is_superuser = user_data.get('is_super_admin', False)
        self.tenant_id = user_data.get('tenant_id')
        self.tenant_slug = user_data.get('tenant_slug')
        self.permissions = user_data.get('permissions', {})
        self.enabled_modules = user_data.get('enabled_modules', [])
        self._state = type('obj', (object,), {'adding': False, 'db': None})()
    
    def __str__(self):
        return self.email
    
    def get_username(self):
        return self.username

    @property
    def is_anonymous(self):
        return False

    @property
    def is_authenticated(self):
        return True
    
    def has_perm(self, perm, obj=None):
        """Check if user has specific permission"""
        if self.is_superuser:
            return True
        
        # Parse permission string (e.g., 'crm.add_lead')
        if '.' in perm:
            app_label, permission = perm.split('.', 1)
            app_permissions = self.permissions.get(app_label, {})
            
            # Check if permission exists in user's permissions
            return permission in app_permissions
        
        return False
    
    def has_perms(self, perm_list, obj=None):
        """Check if user has all permissions in the list"""
        return all(self.has_perm(perm, obj) for perm in perm_list)
    
    def has_module_perms(self, app_label):
        """Check if user has any permissions for the given app"""
        if self.is_superuser:
            return True
        
        # Check if app is in enabled modules
        if app_label in self.enabled_modules:
            return True
        
        # Check if user has any permissions for this app
        return bool(self.permissions.get(app_label, {}))
    
    def get_all_permissions(self, obj=None):
        """Get all permissions for the user"""
        if self.is_superuser:
            return set()  # Superuser has all permissions
        
        perms = set()
        for app_label, app_perms in self.permissions.items():
            for perm in app_perms:
                perms.add(f"{app_label}.{perm}")
        
        return perms


class SuperAdminAuthBackend(BaseBackend):
    """
    Custom authentication backend that validates users against SuperAdmin
    """
    
    def authenticate(self, request, username=None, password=None, **kwargs):
        """
        Authenticate user against SuperAdmin API
        """
        if not username or not password:
            return None
        
        try:
            # Call SuperAdmin login API
            superadmin_url = getattr(settings, 'SUPERADMIN_URL', 'https://admin.celiyo.com')
            login_url = f"{superadmin_url}/api/auth/login/"
            
            response = requests.post(login_url, json={
                'email': username,
                'password': password
            }, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                tokens = data.get('tokens', {})
                access_token = tokens.get('access')
                user_data = data.get('user', {})
                
                if access_token:
                    # Decode JWT to get user data
                    secret_key = getattr(settings, 'JWT_SECRET_KEY')
                    algorithm = getattr(settings, 'JWT_ALGORITHM', 'HS256')
                    
                    payload = jwt.decode(access_token, secret_key, algorithms=[algorithm])
                    
                    # Check if CRM module is enabled
                    enabled_modules = payload.get('enabled_modules', [])
                    if 'crm' not in enabled_modules:
                        logger.warning(f"CRM module not enabled for user {username}")
                        return None
                    
                    # Create user data from external API response and JWT payload
                    user_payload = {
                        'user_id': user_data.get('id'),
                        'email': user_data.get('email'),
                        'first_name': user_data.get('first_name', ''),
                        'last_name': user_data.get('last_name', ''),
                        'tenant_id': user_data.get('tenant'),
                        'tenant_slug': user_data.get('tenant_name'),
                        'is_super_admin': user_data.get('is_super_admin', False),
                        'permissions': payload.get('permissions', {}),
                        'enabled_modules': payload.get('enabled_modules', [])
                    }
                    
                    # Create TenantUser instance
                    user = TenantUser(user_payload)
                    
                    # Store JWT token in session for future requests
                    if hasattr(request, 'session'):
                        request.session['jwt_token'] = access_token
                        request.session['tenant_id'] = user_data.get('tenant')
                        request.session['tenant_slug'] = user_data.get('tenant_name')
                    
                    logger.info(f"Successfully authenticated user {username} for tenant {user_data.get('tenant_name')}")
                    return user
            
            logger.warning(f"Authentication failed for user {username}: {response.status_code}")
            return None
            
        except requests.RequestException as e:
            logger.error(f"Error connecting to SuperAdmin: {e}")
            return None
        except jwt.InvalidTokenError as e:
            logger.error(f"Invalid JWT token: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during authentication: {e}")
            return None
    
    def get_user(self, user_id):
        """
        Get user by ID - required by Django auth system
        Reconstructs user from session data
        """
        try:
            # Get request from thread local storage
            from .middleware import get_current_request
            request = get_current_request()

            if request and hasattr(request, 'session'):
                user_data = request.session.get('user_data')
                if user_data and str(user_data.get('user_id')) == str(user_id):
                    return TenantUser(user_data)
        except Exception as e:
            logger.debug(f"Could not reconstruct user from session: {e}")

        return None


class JWTAuthBackend(BaseBackend):
    """
    Authentication backend for JWT tokens (for API requests)
    """
    
    def authenticate(self, request, jwt_token=None, **kwargs):
        """
        Authenticate using JWT token
        """
        if not jwt_token:
            return None
        
        try:
            secret_key = getattr(settings, 'JWT_SECRET_KEY')
            algorithm = getattr(settings, 'JWT_ALGORITHM', 'HS256')
            
            payload = jwt.decode(jwt_token, secret_key, algorithms=[algorithm])
            
            # Check if CRM module is enabled
            enabled_modules = payload.get('enabled_modules', [])
            if 'crm' not in enabled_modules:
                return None
            
            return TenantUser(payload)
            
        except jwt.InvalidTokenError:
            return None
    
    def get_user(self, user_id):
        """
        Get user by ID - reconstructs from session
        """
        try:
            # Get request from thread local storage
            from .middleware import get_current_request
            request = get_current_request()

            if request and hasattr(request, 'session'):
                user_data = request.session.get('user_data')
                if user_data and str(user_data.get('user_id')) == str(user_id):
                    return TenantUser(user_data)
        except Exception as e:
            logger.debug(f"Could not reconstruct user from session: {e}")

        return None