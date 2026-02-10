import jwt
import json
import logging
import threading
from django.conf import settings
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)

# Thread-local storage for tenant_id and request (for future database routing)
_thread_locals = threading.local()


def get_current_tenant_id():
    """Get the current tenant_id from thread-local storage"""
    return getattr(_thread_locals, 'tenant_id', None)


def set_current_tenant_id(tenant_id):
    """Set the current tenant_id in thread-local storage"""
    _thread_locals.tenant_id = tenant_id


def get_current_request():
    """Get the current request from thread-local storage"""
    return getattr(_thread_locals, 'request', None)


def set_current_request(request):
    """Set the current request in thread-local storage"""
    _thread_locals.request = request


class JWTAuthenticationMiddleware(MiddlewareMixin):
    """
    Middleware to validate JWT tokens from SuperAdmin and set request attributes
    """
    
    # Public paths that don't require authentication
    PUBLIC_PATHS = [
        '/',  # Root URL (redirects to admin)
        '/api/docs/',
        '/api/schema/',
        '/admin',   # Allow all admin paths - custom admin site handles auth
        '/auth/',   # Allow all auth endpoints
        '/static/',  # Allow static files (CSS, JS, images)
        '/health/',
        '/api/schema.json',
        '/api/schema.yaml',
        '/api/logs/',  # Logs endpoint - public for monitoring
        '/api/crm/debug/',  # Temporary debug endpoint - REMOVE after diagnosis
    ]

    def process_request(self, request):
        """Process incoming request and validate JWT token"""

        logger.debug(f"JWT Middleware - process_request called for path: {request.path}")

        # Store request in thread-local storage for authentication backends
        set_current_request(request)

        # Skip validation for public paths
        # Special case: exact match for root path '/'
        if request.path == '/':
            logger.debug(f"JWT Middleware - Skipping public path: {request.path}")
            return None

        # Special case: OAuth callback GET requests (from Google redirect)
        if request.path == '/api/integrations/connections/oauth_callback/' and request.method == 'GET':
            logger.debug(f"JWT Middleware - Skipping OAuth callback GET request")
            return None

        # Check if request path matches any public path (handle trailing slashes)
        request_path_normalized = request.path.rstrip('/') + '/'
        for public_path in self.PUBLIC_PATHS:
            if public_path == '/':
                continue
            # Normalize public path for comparison
            public_path_normalized = public_path.rstrip('/') + '/'
            # Check if request path starts with public path (or matches exactly)
            if request_path_normalized.startswith(public_path_normalized) or request.path == public_path.rstrip('/'):
                logger.debug(f"JWT Middleware - Skipping public path: {request.path} (matched {public_path})")
                return None

        # Get Authorization header
        auth_header = request.META.get('HTTP_AUTHORIZATION')
        logger.debug(f"JWT Middleware - Authorization header present: {bool(auth_header)}")

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

            # Decode JWT token with leeway for clock skew tolerance (30 seconds)
            payload = jwt.decode(token, secret_key, algorithms=[algorithm], leeway=30)
            logger.debug(f"JWT Middleware - Token decoded successfully. Payload keys: {list(payload.keys())}")
            
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
        logger.debug(f"JWT Middleware - Enabled modules: {enabled_modules}")
        logger.debug(f"JWT Middleware - Is super admin: {payload.get('is_super_admin')}")
        logger.debug(f"JWT Middleware - Permissions: {payload.get('permissions')}")

        if 'crm' not in enabled_modules:
            logger.warning(f"JWT Middleware - CRM module not enabled. enabled_modules={enabled_modules}")
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

        logger.debug(f"JWT Middleware - Request attributes set: user_id={request.user_id}, is_super_admin={request.is_super_admin}")
        logger.debug(f"JWT Middleware - hasattr(request, 'permissions'): {hasattr(request, 'permissions')}")
        
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

        # Log tenant context for debugging
        logger.info(
            f"JWT Middleware - Tenant context set: method={request.method}, path={request.path}, "
            f"tenant_id={request.tenant_id}, tenant_slug={request.tenant_slug}, "
            f"user_id={request.user_id}"
        )
        
        # Debug: Verify the attribute is actually set
        logger.debug(f"JWT Middleware - hasattr(request, 'tenant_id'): {hasattr(request, 'tenant_id')}")
        logger.debug(f"JWT Middleware - getattr(request, 'tenant_id', 'NOT_FOUND'): {getattr(request, 'tenant_id', 'NOT_FOUND')}")

        return None