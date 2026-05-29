"""
OpenAPI schema extensions for project-specific DRF classes.

These extensions affect only drf-spectacular schema generation. They do not
change runtime authentication, permissions, request handling, or API behavior.
"""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class JWTRequestAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = 'common.authentication.JWTRequestAuthentication'
    name = 'BearerAuth'

    def get_security_definition(self, auto_schema):
        return {
            'type': 'http',
            'scheme': 'bearer',
            'bearerFormat': 'JWT',
            'description': (
                'JWT access token issued by SuperAdmin. Send it as '
                'Authorization: Bearer <token>.'
            ),
        }


class PermissionsJWTAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = 'common.permissions.JWTAuthentication'
    name = 'CRMJWTBearerAuth'

    def get_security_definition(self, auto_schema):
        return {
            'type': 'http',
            'scheme': 'bearer',
            'bearerFormat': 'JWT',
            'description': (
                'JWT access token issued by SuperAdmin. Send it as '
                'Authorization: Bearer <token>.'
            ),
        }
