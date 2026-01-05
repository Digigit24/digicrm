"""
URL Configuration for Integration System

Defines all API endpoints for the integration system.
Uses Django REST Framework routers for ViewSet-based URLs.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_nested import routers as nested_routers

from integrations import views

# Main router
router = DefaultRouter()

# Register main viewsets
router.register(r'integrations', views.IntegrationViewSet, basename='integration')
router.register(r'connections', views.ConnectionViewSet, basename='connection')
router.register(r'workflows', views.WorkflowViewSet, basename='workflow')
router.register(r'execution-logs', views.ExecutionLogViewSet, basename='execution-log')

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
"""
