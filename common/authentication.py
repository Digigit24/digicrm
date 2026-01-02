"""
Custom DRF Authentication Classes for JWT-based authentication
"""
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from common.auth_backends import TenantUser


class JWTRequestAuthentication(BaseAuthentication):
    """
    Custom DRF authentication that works with our JWT middleware.

    The JWT middleware (JWTAuthenticationMiddleware) handles token validation
    and sets request attributes. This class simply reads those attributes
    for DRF compatibility.

    This authentication class:
    - Does NOT validate the JWT token (middleware already did)
    - Does NOT query the database
    - Simply reads request.user_id and request.tenant_id set by middleware
    - Returns a user-like object for DRF views
    """

    def authenticate(self, request):
        """
        Returns a user-like object if request has been authenticated by middleware.
        Returns None if no authentication was performed (allows public endpoints).
        """
        # Check if JWT middleware set authentication attributes
        if not hasattr(request, 'user_id'):
            # No JWT authentication performed - likely a public endpoint
            return None

        # JWT middleware validated the token and set attributes
        # Return a TenantUser instance for DRF
        user_data = {
            'user_id': request.user_id,
            'email': request.email,
            'tenant_id': request.tenant_id,
            'tenant_slug': request.tenant_slug,
            'is_super_admin': request.is_super_admin,
            'permissions': request.permissions,
            'enabled_modules': request.enabled_modules,
        }

        # Return a tuple of (user, auth) as required by DRF
        # Use TenantUser which has is_authenticated property
        user = TenantUser(user_data)
        return (user, None)

    def authenticate_header(self, request):
        """
        Return a string to be used as the value of the `WWW-Authenticate`
        header in a `401 Unauthenticated` response.
        """
        return 'Bearer'
