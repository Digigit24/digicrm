"""
URL configuration for digicrm project.
"""
from django.urls import path, include
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import RedirectView
from django.conf import settings
from django.conf.urls.static import static
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView
)
from common.admin_site import tenant_admin_site
from common.views import TokenLoginView, AdminHealthView, SuperAdminProxyLoginView, superadmin_proxy_login_view
from mcp.django_view import mcp_urlpatterns, oauth_well_known, oauth_protected_resource

urlpatterns = [
    # Root URL - redirect to admin
    path('', RedirectView.as_view(url='/admin/', permanent=False), name='home'),

    # Admin authentication endpoints (must be before admin/ to avoid conflicts)
    path('auth/token-login/', csrf_exempt(TokenLoginView.as_view()), name='admin-token-login'),
    path('auth/superadmin-login/', superadmin_proxy_login_view, name='admin-superadmin-login'),
    path('auth/health/', AdminHealthView.as_view(), name='admin-health'),

    # Custom tenant-based admin
    path('admin/', tenant_admin_site.urls),

    # API Schema and Documentation
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),

    # App URLs
    path('api/crm/', include('crm.urls')),
    path('api/meetings/', include('meetings.urls')),
    path('api/payments/', include('payments.urls')),
    path('api/tasks/', include('tasks.urls')),
    path('api/integrations/', include('integrations.urls')),
    path('api/telephony/', include('telephony.urls')),
    path('api/whatsapp/', include('whatsapp_integration.urls')),  # WhatsApp adapter
]

# MCP server endpoints (Claude Desktop / Claude in Chrome custom connector)
urlpatterns += mcp_urlpatterns

# OAuth discovery (required by MCP spec for remote HTTP servers)
urlpatterns += [
    path('.well-known/oauth-authorization-server', oauth_well_known),
    path('.well-known/oauth-protected-resource', oauth_protected_resource),
    path('.well-known/oauth-protected-resource/<path:path>', oauth_protected_resource),
]

# Serve static files in development
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)