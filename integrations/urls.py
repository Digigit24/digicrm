"""
URL Configuration for Integration System

Defines all API endpoints for the integration system.
Uses Django REST Framework routers for ViewSet-based URLs.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_nested import routers as nested_routers

from integrations import views, views_composio

# Main router
router = DefaultRouter()

# Register main viewsets
router.register(r'integrations', views.IntegrationViewSet, basename='integration')
router.register(r'connections', views.ConnectionViewSet, basename='connection')
router.register(r'workflows', views.WorkflowViewSet, basename='workflow')
router.register(r'execution-logs', views.ExecutionLogViewSet, basename='execution-log')

# Composio - managed third-party tool auth (Notion, Gmail, Drive, Calendar, ...).
# A sibling surface to the native Google OAuth above, not a replacement.
router.register(r'composio/toolkits', views_composio.ComposioToolkitViewSet,
                basename='composio-toolkit')
router.register(r'composio/connections', views_composio.ComposioConnectionViewSet,
                basename='composio-connection')
router.register(r'composio/auth-configs', views_composio.ComposioAuthConfigViewSet,
                basename='composio-auth-config')
router.register(r'composio/admin/connections', views_composio.ComposioAdminConnectionViewSet,
                basename='composio-admin-connection')

# Nested routers for workflow triggers
workflows_router = nested_routers.NestedDefaultRouter(
    router, r'workflows', lookup='workflow'
)
workflows_router.register(
    r'triggers', views.WorkflowTriggerViewSet, basename='workflow-trigger'
)

# Nested routers for workflow actions
workflows_router.register(
    r'actions', views.WorkflowActionViewSet, basename='workflow-action'
)

# Nested routers for workflow execution logs
workflows_router.register(
    r'execution-logs', views.ExecutionLogViewSet, basename='workflow-execution-log'
)

# Nested routers for action field mappings
actions_router = nested_routers.NestedDefaultRouter(
    workflows_router, r'actions', lookup='action'
)
actions_router.register(
    r'mappings', views.WorkflowMappingViewSet, basename='action-mapping'
)

app_name = 'integrations'

urlpatterns = [
    # Public inbound webhook (push-based integrations: Make, Zapier, generic).
    # Must come before the routers so its <uuid:public_id> segment can't be
    # swallowed by a router pattern.
    path('webhook/inbound/<uuid:public_id>/', views.InboundWebhookView.as_view(), name='webhook-inbound'),

    # Public Composio endpoints. MUST precede the routers, and are listed in
    # JWTAuthenticationMiddleware.PUBLIC_PATHS - the browser arrives at the
    # callback straight from Composio's hosted auth page with no Authorization
    # header, and Composio's webhook sender has no JWT either. Both authenticate
    # themselves inside the view: the callback by a single-use state nonce, the
    # webhook by an HMAC signature plus a timestamp replay window.
    path('composio/callback/', views_composio.ComposioCallbackView.as_view(), name='composio-callback'),
    path('composio/webhook/', views_composio.ComposioWebhookView.as_view(), name='composio-webhook'),

    # Include main router URLs
    path('', include(router.urls)),

    # Include nested router URLs
    path('', include(workflows_router.urls)),
    path('', include(actions_router.urls)),
]


"""
API Endpoints Overview:
=======================

INTEGRATIONS:
- GET    /api/integrations/integrations/                      - List available integrations
- GET    /api/integrations/integrations/:id/                  - Get integration details

CONNECTIONS:
- GET    /api/integrations/connections/                       - List user's connections
- GET    /api/integrations/connections/:id/                   - Get connection details
- POST   /api/integrations/connections/initiate_oauth/        - Start OAuth flow
- POST   /api/integrations/connections/oauth_callback/        - Handle OAuth callback
- POST   /api/integrations/connections/:id/disconnect/        - Disconnect connection
- POST   /api/integrations/connections/:id/refresh_token/     - Refresh access token
- GET    /api/integrations/connections/:id/test/              - Test connection
- GET    /api/integrations/connections/:id/spreadsheets/      - List spreadsheets
- GET    /api/integrations/connections/:id/spreadsheets/:spreadsheet_id/sheets/ - List sheets

WORKFLOWS:
- GET    /api/integrations/workflows/                         - List workflows
- POST   /api/integrations/workflows/                         - Create workflow
- GET    /api/integrations/workflows/:id/                     - Get workflow details
- PATCH  /api/integrations/workflows/:id/                     - Update workflow
- DELETE /api/integrations/workflows/:id/                     - Delete workflow (soft delete)
- POST   /api/integrations/workflows/:id/test/                - Test workflow manually
- POST   /api/integrations/workflows/:id/toggle/              - Toggle active status
- GET    /api/integrations/workflows/:id/executions/          - Get execution logs (legacy)
- GET    /api/integrations/workflows/:id/execution-logs/      - Get execution logs (paginated)
- GET    /api/integrations/workflows/stats/                   - Get workflow statistics

WORKFLOW TRIGGERS:
- GET    /api/integrations/workflows/:id/triggers/            - List triggers
- POST   /api/integrations/workflows/:id/triggers/            - Create trigger
- GET    /api/integrations/workflows/:id/triggers/:trigger_id/ - Get trigger details
- PATCH  /api/integrations/workflows/:id/triggers/:trigger_id/ - Update trigger
- DELETE /api/integrations/workflows/:id/triggers/:trigger_id/ - Delete trigger

WORKFLOW ACTIONS:
- GET    /api/integrations/workflows/:id/actions/             - List actions
- POST   /api/integrations/workflows/:id/actions/             - Create action
- GET    /api/integrations/workflows/:id/actions/:action_id/  - Get action details
- PATCH  /api/integrations/workflows/:id/actions/:action_id/  - Update action
- DELETE /api/integrations/workflows/:id/actions/:action_id/  - Delete action

FIELD MAPPINGS:
- GET    /api/integrations/workflows/:wf_id/actions/:action_id/mappings/ - List mappings
- POST   /api/integrations/workflows/:wf_id/actions/:action_id/mappings/ - Create mapping
- GET    /api/integrations/workflows/:wf_id/actions/:action_id/mappings/:id/ - Get mapping
- PATCH  /api/integrations/workflows/:wf_id/actions/:action_id/mappings/:id/ - Update mapping
- DELETE /api/integrations/workflows/:wf_id/actions/:action_id/mappings/:id/ - Delete mapping

EXECUTION LOGS:
- GET    /api/integrations/execution-logs/                    - List execution logs
- GET    /api/integrations/execution-logs/:id/                - Get execution log details

COMPOSIO (managed third-party tool auth - Notion, Gmail, Drive, Calendar, ...):

  Catalogue (permission: integrations.providers.view):
  - GET    /api/integrations/composio/toolkits/                        - List connectable toolkits
                                                                         ?search=&category=&connected=true|false
  - GET    /api/integrations/composio/toolkits/:slug/                  - Toolkit details
  - POST   /api/integrations/composio/toolkits/sync/                   - Refresh catalogue (tenant admin)

  Connections (permission: integrations.connections.*, scoped to tenant+user):
  - POST   /api/integrations/composio/connections/initiate/            - Start hosted auth -> redirect_url + state
  - GET    /api/integrations/composio/connections/                     - List own + tenant-shared connections
  - GET    /api/integrations/composio/connections/:public_id/          - Connection details
  - GET    /api/integrations/composio/connections/:public_id/status/   - Poll status (?force=true)
  - POST   /api/integrations/composio/connections/:public_id/refresh/  - Re-authorise
  - POST   /api/integrations/composio/connections/:public_id/disable/  - Disable at Composio
  - POST   /api/integrations/composio/connections/:public_id/enable/   - Re-enable at Composio
  - POST   /api/integrations/composio/connections/:public_id/disconnect/ - Revoke at Composio + tombstone
  - DELETE /api/integrations/composio/connections/:public_id/          - Same as disconnect
  - GET    /api/integrations/composio/connections/:public_id/events/   - Audit trail
  - POST   /api/integrations/composio/connections/:public_id/execute/  - Execute a tool (off by default)

  Auth configs (tenant admin only):
  - GET    /api/integrations/composio/auth-configs/                    - Tenant + platform-wide configs
  - POST   /api/integrations/composio/auth-configs/                    - Create a tenant-owned config
  - PATCH  /api/integrations/composio/auth-configs/:public_id/         - Update a tenant-owned config

  Tenant admin oversight (tenant admin only, still tenant-scoped):
  - GET    /api/integrations/composio/admin/connections/               - All connections in the tenant
  - POST   /api/integrations/composio/admin/connections/:public_id/revoke/ - Revoke someone else's connection

  Public (no JWT; authenticated by nonce / HMAC inside the view):
  - GET    /api/integrations/composio/callback/                        - Composio hosted-auth return -> 302
  - POST   /api/integrations/composio/webhook/                         - Composio trigger events
"""
