import jwt
import json
import threading
from django.conf import settings
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin

# Thread-local storage for tenant_id (for future database routing)
_thread_locals = threading.local()


def get_current_tenant_id():
    """Get the current tenant_id from thread-local storage"""
    return getattr(_thread_locals, 'tenant_id', None)


def set_current_tenant_id(tenant_id):
    """Set the current tenant_id in thread-local storage"""
    _thread_locals.tenant_id = tenant_id


class JWTAuthenticationMiddleware(MiddlewareMixin):
    """
    Middleware to validate JWT tokens from SuperAdmin and set request attributes
    """
    
    # Public paths that don't require authentication
    PUBLIC_PATHS = [
        '/api/docs/',
        '/api/schema/',
        '/admin/',
        '/health/',
        '/api/schema.json',
        '/api/schema.yaml',
    ]
    
    def process_request(self, request):
        """Process incoming request and validate JWT token"""
        
        # Skip validation for public paths
        if any(request.path.startswith(path) for path in self.PUBLIC_PATHS):
            return None
        
        # Get Authorization header
        auth_header = request.META.get('HTTP_AUTHORIZATION')
        if not auth_header:
            return JsonResponse(
                {'error': 'Authorization header required'},
                status=401
            )
        
        # Extract token from "Bearer <token>" format
        try:
            scheme, token = auth_header.split(' ', 1)
            if scheme.lower() != 'bearer':
                return JsonResponse(
                    {'error': 'Invalid authorization scheme. Use Bearer token'},
                    status=401
                )
        except ValueError:
            return JsonResponse(
                {'error': 'Invalid authorization header format'},
                status=401
            )
        
        # Decode and validate JWT token
        try:
            # Get JWT settings from Django settings
            secret_key = getattr(settings, 'JWT_SECRET_KEY', None)
            algorithm = getattr(settings, 'JWT_ALGORITHM', 'HS256')
            
            if not secret_key:
                return JsonResponse(
                    {'error': 'JWT_SECRET_KEY not configured'},
                    status=500
                )
            
            # Decode JWT token
            payload = jwt.decode(token, secret_key, algorithms=[algorithm])
            
        except jwt.ExpiredSignatureError:
            return JsonResponse(
                {'error': 'Token has expired'},
                status=401
            )
        except jwt.InvalidTokenError as e:
            return JsonResponse(
                {'error': f'Invalid token: {str(e)}'},
                status=401
            )
        
        # Validate required fields in payload
        required_fields = [
            'user_id', 'email', 'tenant_id', 'tenant_slug',
            'is_super_admin', 'permissions', 'enabled_modules'
        ]
        
        for field in required_fields:
            if field not in payload:
                return JsonResponse(
                    {'error': f'Missing required field in token: {field}'},
                    status=401
                )
        
        # Check if CRM module is enabled
        enabled_modules = payload.get('enabled_modules', [])
        if 'crm' not in enabled_modules:
            return JsonResponse(
                {'error': 'CRM module not enabled for this user'},
                status=403
            )
        
        # Set request attributes from JWT payload
        request.user_id = payload['user_id']
        request.email = payload['email']
        request.tenant_id = payload['tenant_id']
        request.tenant_slug = payload['tenant_slug']
        request.is_super_admin = payload['is_super_admin']
        request.permissions = payload['permissions']
        request.enabled_modules = payload['enabled_modules']
        
        # Check for additional tenant headers as fallback/override
        tenant_token_header = request.META.get('HTTP_TENANTTOKEN')
        x_tenant_id_header = request.META.get('HTTP_X_TENANT_ID')
        x_tenant_slug_header = request.META.get('HTTP_X_TENANT_SLUG')
        
        # If tenanttoken header is provided, use it to override tenant_id
        if tenant_token_header:
            request.tenant_id = tenant_token_header
            
        # If x-tenant-id header is provided, use it to override tenant_id
        if x_tenant_id_header:
            request.tenant_id = x_tenant_id_header
            
        # If x-tenant-slug header is provided, use it to override tenant_slug
        if x_tenant_slug_header:
            request.tenant_slug = x_tenant_slug_header
        
        # Store tenant_id in thread-local storage for database routing
        set_current_tenant_id(request.tenant_id)
        
        return None