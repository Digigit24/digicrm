"""
API Views for Integration System

Provides REST API endpoints for:
- OAuth authentication
- Managing connections
- Creating and managing workflows
- Listing spreadsheets and sheets
- Viewing execution logs
- Testing workflows
"""

import logging
import uuid
from django.utils import timezone
from django.db.models import Q
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.core.cache import cache

from integrations.models import (
    Integration, Connection, Workflow, WorkflowTrigger,
    WorkflowAction, WorkflowMapping, ExecutionLog,
    ConnectionStatusEnum, IntegrationTypeEnum
)
from integrations.serializers import (
    IntegrationSerializer, ConnectionListSerializer, ConnectionDetailSerializer,
    WorkflowListSerializer, WorkflowDetailSerializer, WorkflowCreateSerializer,
    WorkflowTriggerSerializer, WorkflowTriggerCreateSerializer,
    WorkflowActionSerializer, WorkflowActionCreateSerializer,
    WorkflowMappingSerializer, WorkflowMappingCreateSerializer,
    ExecutionLogSerializer, ExecutionLogListSerializer,
    OAuthInitiateSerializer, OAuthCallbackSerializer,
    SpreadsheetListSerializer, SheetListSerializer,
    TestWorkflowSerializer, WorkflowStatsSerializer
)
from integrations.utils.oauth import get_oauth_handler, OAuthError
from integrations.utils.encryption import encrypt_token, decrypt_token
from integrations.services.google_sheets import create_sheets_service, GoogleSheetsError
from integrations.services.workflow_engine import WorkflowEngine, WorkflowEngineError
from common.authentication import JWTRequestAuthentication

logger = logging.getLogger(__name__)


class IntegrationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for listing available integrations.
    Read-only as integrations are pre-configured.
    """
    queryset = Integration.objects.filter(is_active=True)
    serializer_class = IntegrationSerializer
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Filter active integrations"""
        return Integration.objects.filter(is_active=True)


class ConnectionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing OAuth connections.

    Endpoints:
    - GET /connections/ - List user's connections
    - GET /connections/:id/ - Get connection details
    - POST /connections/initiate_oauth/ - Start OAuth flow
    - POST /connections/oauth_callback/ - Handle OAuth callback
    - POST /connections/:id/disconnect/ - Disconnect connection
    - POST /connections/:id/refresh_token/ - Refresh access token
    - GET /connections/:id/test/ - Test connection
    """
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Get connections for current tenant and user"""
        tenant_id = getattr(self.request, 'tenant_id', None)

        # Debug logging
        logger.info(f"ConnectionViewSet.get_queryset - tenant_id from request: {tenant_id} (type: {type(tenant_id).__name__})")
        logger.info(f"All connections count: {Connection.objects.count()}")

        if not tenant_id:
            logger.warning("get_queryset called without tenant_id")
            return Connection.objects.none()

        # Filter by tenant_id (no conversion needed, Django handles UUID comparison)
        queryset = Connection.objects.filter(
            tenant_id=tenant_id
        ).select_related('integration')

        logger.info(f"Filtered connections count: {queryset.count()}")
        if queryset.exists():
            for conn in queryset:
                logger.info(f"  Found connection: id={conn.id}, name={conn.name}, status={conn.status}, tenant={conn.tenant_id}")

        return queryset

    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'retrieve':
            return ConnectionDetailSerializer
        return ConnectionListSerializer

    @action(detail=False, methods=['post'])
    def initiate_oauth(self, request):
        """
        Initiate OAuth flow for a connection.

        POST /api/integrations/connections/initiate_oauth/
        {
            "integration_id": 1
        }

        Returns: {"authorization_url": "...", "state": "..."}
        """
        serializer = OAuthInitiateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        integration_id = serializer.validated_data['integration_id']

        try:
            # Get integration
            integration = Integration.objects.get(id=integration_id, is_active=True)

            if integration.type != IntegrationTypeEnum.GOOGLE_SHEETS:
                return Response(
                    {"error": "OAuth not supported for this integration type"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Check if already connected
            existing_connection = Connection.objects.filter(
                tenant_id=request.tenant_id,
                integration_id=integration_id,
                status=ConnectionStatusEnum.CONNECTED
            ).first()

            if existing_connection:
                return Response({
                    'already_connected': True,
                    'message': f'Already connected to {integration.name}',
                    'connection': ConnectionDetailSerializer(existing_connection).data
                }, status=status.HTTP_200_OK)

            # Generate state with tenant and user info
            state = f"{request.tenant_id}:{request.user_id}:{uuid.uuid4()}"

            # Get OAuth handler
            oauth_handler = get_oauth_handler()
            authorization_url, state = oauth_handler.get_authorization_url(state)

            # Cache state for validation
            cache.set(f"oauth_state:{state}", {
                'tenant_id': str(request.tenant_id),
                'user_id': str(request.user_id),
                'integration_id': integration_id
            }, timeout=600)  # 10 minutes

            return Response({
                'authorization_url': authorization_url,
                'state': state,
                'integration': IntegrationSerializer(integration).data
            })

        except Integration.DoesNotExist:
            return Response(
                {"error": "Integration not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        except OAuthError as e:
            logger.error(f"OAuth initiation failed: {e}")
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=False, methods=['post', 'get'], permission_classes=[])
    def oauth_callback(self, request):
        """
        Handle OAuth callback and save connection.

        POST /api/integrations/connections/oauth_callback/
        {
            "code": "...",
            "state": "...",
            "integration_id": 1,
            "connection_name": "My Leads Sheet" (optional)
        }

        Returns: Connection details
        """
        # Handle GET request from Google OAuth redirect
        # Handle GET request from Google OAuth redirect
        if request.method == 'GET':
            from django.shortcuts import redirect
            from django.conf import settings as django_settings

            logger.info(f"oauth_callback GET request from Google with params: {request.GET}")
            code = request.GET.get('code')
            state = request.GET.get('state')
            error = request.GET.get('error')

            frontend_url = getattr(django_settings, 'FRONTEND_URL', 'http://localhost:3000')

            if error:
                logger.error(f"OAuth error from Google: {error}")
                return redirect(f"{frontend_url}/integrations?oauth_error={error}")

            if not code or not state:
                logger.error("Missing code or state in OAuth callback")
                return redirect(f"{frontend_url}/integrations?oauth_error=missing_params")

            try:
                # EXCHANGE CODE HERE IN GET REQUEST
                logger.info(f"Exchanging code in GET request...")
                
                # Get cached state
                cached_state = cache.get(f"oauth_state:{state}")
                if not cached_state:
                    logger.error("State validation failed")
                    return redirect(f"{frontend_url}/integrations?oauth_error=invalid_state")
                
                tenant_id = cached_state['tenant_id']
                user_id = cached_state['user_id']
                integration_id = cached_state['integration_id']
                
                # Exchange code for tokens
                oauth_handler = get_oauth_handler()
                token_data = oauth_handler.exchange_code_for_tokens(code, state)
                logger.info(f"Token exchange successful")
                
                # Get integration
                integration = Integration.objects.get(id=integration_id)
                
                # Encrypt tokens
                encrypted_access_token = encrypt_token(token_data['access_token'])
                encrypted_refresh_token = encrypt_token(token_data['refresh_token']) if token_data.get('refresh_token') else None
                
                # Create/update connection
                connection, created = Connection.objects.update_or_create(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    integration=integration,
                    defaults={
                        'name': 'Google Sheets',
                        'status': ConnectionStatusEnum.CONNECTED,
                        'access_token_encrypted': encrypted_access_token,
                        'refresh_token_encrypted': encrypted_refresh_token,
                        'token_expires_at': token_data.get('expires_at'),
                        'connected_at': timezone.now()
                    }
                )
                
                logger.info(f"Connection {'created' if created else 'updated'}: {connection.id}")
                
                # Clear cached state
                cache.delete(f"oauth_state:{state}")
                
                # Redirect to frontend with success
                return redirect(f"{frontend_url}/integrations?oauth_success=true&connection_name={connection.name}")
                
            except Exception as e:
                logger.error(f"OAuth callback failed: {e}", exc_info=True)
                return redirect(f"{frontend_url}/integrations?oauth_error={str(e)}")

        # Handle POST request from frontend with authorization code
        logger.info(f"oauth_callback POST called with data: {request.data}")
        logger.info(f"Request tenant_id: {getattr(request, 'tenant_id', 'NOT_FOUND')}, user_id: {getattr(request, 'user_id', 'NOT_FOUND')}")

        # Verify authentication (middleware should have set these)
        if not hasattr(request, 'tenant_id') or not hasattr(request, 'user_id'):
            logger.error("POST request to oauth_callback without authentication")
            return Response(
                {"error": "Authentication required for POST requests"},
                status=status.HTTP_401_UNAUTHORIZED
            )

        serializer = OAuthCallbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        code = serializer.validated_data['code']
        state = serializer.validated_data['state']
        integration_id = serializer.validated_data['integration_id']
        connection_name = serializer.validated_data.get('connection_name', 'Google Sheets Connection')

        try:
            # Validate state
            logger.info(f"Checking cached state for: oauth_state:{state}")
            cached_state = cache.get(f"oauth_state:{state}")
            logger.info(f"Cached state data: {cached_state}")

            if not cached_state:
                logger.error("State validation failed - state not found in cache")
                return Response(
                    {"error": "Invalid or expired state"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Exchange code for tokens
            logger.info(f"Exchanging code for tokens...")
            oauth_handler = get_oauth_handler()
            token_data = oauth_handler.exchange_code_for_tokens(code, state)
            logger.info(f"Token exchange successful, expires_at: {token_data.get('expires_at')}")

            # Get integration
            integration = Integration.objects.get(id=integration_id)
            logger.info(f"Found integration: {integration.name}")

            # Encrypt tokens
            encrypted_access_token = encrypt_token(token_data['access_token'])
            encrypted_refresh_token = encrypt_token(token_data['refresh_token']) if token_data.get('refresh_token') else None
            logger.info("Tokens encrypted successfully")

            # Check if connection already exists and update it, otherwise create new
            logger.info(f"Creating/updating connection for tenant={request.tenant_id}, user={request.user_id}, integration={integration.id}")
            connection, created = Connection.objects.update_or_create(
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                integration=integration,
                defaults={
                    'name': connection_name,
                    'status': ConnectionStatusEnum.CONNECTED,
                    'access_token_encrypted': encrypted_access_token,
                    'refresh_token_encrypted': encrypted_refresh_token,
                    'token_expires_at': token_data.get('expires_at'),
                    'connected_at': timezone.now()
                }
            )

            logger.info(f"Connection {'created' if created else 'updated'}: id={connection.id}, tenant={connection.tenant_id} (type={type(connection.tenant_id).__name__}), user={connection.user_id}")

            # Verify connection was saved
            verify_conn = Connection.objects.filter(id=connection.id).first()
            if verify_conn:
                logger.info(f"Connection verified in database: id={verify_conn.id}, status={verify_conn.status}")
            else:
                logger.error(f"Connection NOT found in database after save!")

            # Clear cached state
            cache.delete(f"oauth_state:{state}")

            return Response(
                ConnectionDetailSerializer(connection).data,
                status=status.HTTP_201_CREATED
            )

        except Integration.DoesNotExist:
            logger.error(f"Integration not found: {integration_id}")
            return Response(
                {"error": "Integration not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        except OAuthError as e:
            logger.error(f"OAuth callback failed: {e}", exc_info=True)
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.error(f"Unexpected error in oauth_callback: {e}", exc_info=True)
            return Response(
                {"error": f"Internal error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['post'])
    def disconnect(self, request, pk=None):
        """
        Disconnect a connection.

        POST /api/integrations/connections/:id/disconnect/
        """
        connection = self.get_object()

        # Update connection status
        connection.status = ConnectionStatusEnum.DISCONNECTED
        connection.access_token_encrypted = None
        connection.refresh_token_encrypted = None
        connection.save()

        return Response({
            'message': 'Connection disconnected successfully',
            'connection': ConnectionDetailSerializer(connection).data
        })

    @action(detail=True, methods=['post'])
    def refresh_token(self, request, pk=None):
        """
        Manually refresh access token for a connection.

        POST /api/integrations/connections/:id/refresh_token/
        """
        connection = self.get_object()

        try:
            if not connection.refresh_token_encrypted:
                return Response(
                    {"error": "No refresh token available"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Decrypt refresh token
            refresh_token = decrypt_token(connection.refresh_token_encrypted)

            # Refresh token
            oauth_handler = get_oauth_handler()
            token_data = oauth_handler.refresh_access_token(refresh_token)

            # Update connection
            connection.access_token_encrypted = encrypt_token(token_data['access_token'])
            if token_data.get('refresh_token'):
                connection.refresh_token_encrypted = encrypt_token(token_data['refresh_token'])
            connection.token_expires_at = token_data.get('expires_at')
            connection.status = ConnectionStatusEnum.CONNECTED
            connection.save()

            return Response({
                'message': 'Token refreshed successfully',
                'expires_at': connection.token_expires_at
            })

        except OAuthError as e:
            logger.error(f"Token refresh failed: {e}")
            connection.mark_as_error(str(e))
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=['get'])
    def test(self, request, pk=None):
        """
        Test connection by making a simple API call.

        GET /api/integrations/connections/:id/test/
        """
        connection = self.get_object()

        try:
            # Create Google Sheets service
            sheets_service = create_sheets_service(connection)

            # Try to list spreadsheets
            spreadsheets = sheets_service.list_spreadsheets(page_size=1)

            # Update last used
            connection.last_used_at = timezone.now()
            connection.save(update_fields=['last_used_at'])

            return Response({
                'status': 'success',
                'message': 'Connection is working',
                'test_result': f'Successfully accessed {len(spreadsheets)} spreadsheet(s)'
            })

        except GoogleSheetsError as e:
            logger.error(f"Connection test failed: {e}")
            return Response(
                {"error": str(e), "status": "failed"},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=['get'])
    def spreadsheets(self, request, pk=None):
        """
        List spreadsheets for a connection.

        GET /api/integrations/connections/:id/spreadsheets/
        """
        connection = self.get_object()

        try:
            sheets_service = create_sheets_service(connection)
            spreadsheets = sheets_service.list_spreadsheets(page_size=100)

            # Update last used
            connection.last_used_at = timezone.now()
            connection.save(update_fields=['last_used_at'])

            serializer = SpreadsheetListSerializer(spreadsheets, many=True)
            return Response(serializer.data)

        except GoogleSheetsError as e:
            logger.error(f"Failed to list spreadsheets: {e}")
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=['get'], url_path='sheets')
    def list_sheets(self, request, pk=None):
        """
        List sheets in a spreadsheet (alternative endpoint with query param).

        GET /api/integrations/connections/:id/sheets/?spreadsheet_id=xxx
        """
        connection = self.get_object()
        spreadsheet_id = request.query_params.get('spreadsheet_id')

        if not spreadsheet_id:
            return Response(
                {"error": "spreadsheet_id query parameter is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            sheets_service = create_sheets_service(connection)
            sheets = sheets_service.list_sheets(spreadsheet_id)

            serializer = SheetListSerializer(sheets, many=True)
            return Response(serializer.data)

        except GoogleSheetsError as e:
            logger.error(f"Failed to list sheets: {e}")
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=['get'], url_path='spreadsheets/(?P<spreadsheet_id>[^/.]+)/sheets')
    def sheets(self, request, pk=None, spreadsheet_id=None):
        """
        List sheets in a spreadsheet.

        GET /api/integrations/connections/:id/spreadsheets/:spreadsheet_id/sheets/
        """
        connection = self.get_object()

        try:
            sheets_service = create_sheets_service(connection)
            sheets = sheets_service.list_sheets(spreadsheet_id)

            serializer = SheetListSerializer(sheets, many=True)
            return Response(serializer.data)

        except GoogleSheetsError as e:
            logger.error(f"Failed to list sheets: {e}")
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )


class WorkflowViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing workflows.

    Endpoints:
    - GET /workflows/ - List workflows
    - POST /workflows/ - Create workflow
    - GET /workflows/:id/ - Get workflow details
    - PATCH /workflows/:id/ - Update workflow
    - DELETE /workflows/:id/ - Delete workflow (soft delete)
    - POST /workflows/:id/test/ - Test workflow manually
    - POST /workflows/:id/toggle/ - Toggle active status
    - GET /workflows/:id/executions/ - Get execution logs
    - GET /workflows/stats/ - Get workflow statistics
    """
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Get workflows for current tenant"""
        tenant_id = getattr(self.request, 'tenant_id', None)
        if not tenant_id:
            return Workflow.objects.none()

        queryset = Workflow.objects.filter(
            tenant_id=tenant_id,
            is_deleted=False
        ).select_related('connection', 'connection__integration')

        # Filter by status if provided
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')

        return queryset

    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'create':
            return WorkflowCreateSerializer
        elif self.action == 'retrieve':
            return WorkflowDetailSerializer
        return WorkflowListSerializer

    def create(self, request, *args, **kwargs):
        """Create workflow with better error logging"""
        logger.info(f"Creating workflow with data: {request.data}")
        logger.info(f"Request tenant_id: {getattr(request, 'tenant_id', 'NOT_SET')}")
        logger.info(f"Request user_id: {getattr(request, 'user_id', 'NOT_SET')}")

        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except Exception as e:
            logger.error(f"Workflow creation validation failed: {e}")
            logger.error(f"Validation errors: {getattr(serializer, 'errors', {})}")
            raise

        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_destroy(self, instance):
        """Soft delete workflow"""
        instance.soft_delete()

    @action(detail=True, methods=['post'])
    def test(self, request, pk=None):
        """
        Test workflow manually with optional test data.

        POST /api/integrations/workflows/:id/test/
        {
            "trigger_data": {...} (optional)
        }
        """
        workflow = self.get_object()

        serializer = TestWorkflowSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        trigger_data = serializer.validated_data.get('trigger_data')

        try:
            engine = WorkflowEngine(workflow)

            if trigger_data:
                # Use provided test data
                execution_logs = engine.execute_workflow([trigger_data])
            else:
                # Check trigger automatically
                execution_logs = engine.execute_workflow()

            if not execution_logs:
                return Response({
                    'message': 'No trigger data found to execute workflow',
                    'executions': []
                })

            return Response({
                'message': f'Workflow executed {len(execution_logs)} time(s)',
                'executions': ExecutionLogListSerializer(execution_logs, many=True).data
            })

        except WorkflowEngineError as e:
            logger.error(f"Workflow test failed: {e}")
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=['post'])
    def toggle(self, request, pk=None):
        """
        Toggle workflow active status.

        POST /api/integrations/workflows/:id/toggle/
        """
        workflow = self.get_object()
        workflow.is_active = not workflow.is_active
        workflow.save(update_fields=['is_active', 'updated_at'])

        return Response({
            'message': f'Workflow {"activated" if workflow.is_active else "deactivated"}',
            'is_active': workflow.is_active
        })

    @action(detail=True, methods=['get'])
    def executions(self, request, pk=None):
        """
        Get execution logs for a workflow.

        GET /api/integrations/workflows/:id/executions/
        """
        workflow = self.get_object()

        logs = ExecutionLog.objects.filter(
            workflow=workflow
        ).order_by('-started_at')[:50]  # Last 50 executions

        serializer = ExecutionLogListSerializer(logs, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def stats(self, request):
        """
        Get workflow statistics for tenant.

        GET /api/integrations/workflows/stats/
        """
        workflows = self.get_queryset()

        stats = {
            'total_workflows': workflows.count(),
            'active_workflows': workflows.filter(is_active=True).count(),
            'total_executions': sum(w.total_executions for w in workflows),
            'successful_executions': sum(w.successful_executions for w in workflows),
            'failed_executions': sum(w.failed_executions for w in workflows),
        }

        # Calculate success rate
        if stats['total_executions'] > 0:
            stats['success_rate'] = round(
                (stats['successful_executions'] / stats['total_executions']) * 100,
                2
            )
        else:
            stats['success_rate'] = 0

        serializer = WorkflowStatsSerializer(stats)
        return Response(serializer.data)


class WorkflowTriggerViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing workflow triggers.

    Triggers are tied to workflows (one-to-one relationship).
    """
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = WorkflowTriggerSerializer

    def get_queryset(self):
        """Get triggers for workflows owned by current tenant"""
        tenant_id = getattr(self.request, 'tenant_id', None)
        if not tenant_id:
            return WorkflowTrigger.objects.none()

        workflow_id = self.kwargs.get('workflow_pk')
        return WorkflowTrigger.objects.filter(
            workflow_id=workflow_id,
            workflow__tenant_id=tenant_id
        )

    def get_serializer_class(self):
        """Return appropriate serializer"""
        if self.action == 'create':
            return WorkflowTriggerCreateSerializer
        return WorkflowTriggerSerializer

    def create(self, request, *args, **kwargs):
        """Create trigger for workflow"""
        workflow_id = self.kwargs.get('workflow_pk')

        # Check if trigger already exists
        if WorkflowTrigger.objects.filter(workflow_id=workflow_id).exists():
            return Response(
                {"error": "Workflow already has a trigger"},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        trigger = serializer.save(workflow_id=workflow_id)

        return Response(
            WorkflowTriggerSerializer(trigger).data,
            status=status.HTTP_201_CREATED
        )


class WorkflowActionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing workflow actions.

    Actions belong to workflows and are executed in order.
    """
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = WorkflowActionSerializer

    def get_queryset(self):
        """Get actions for a workflow"""
        tenant_id = getattr(self.request, 'tenant_id', None)
        if not tenant_id:
            return WorkflowAction.objects.none()

        workflow_id = self.kwargs.get('workflow_pk')
        return WorkflowAction.objects.filter(
            workflow_id=workflow_id,
            workflow__tenant_id=tenant_id
        ).prefetch_related('field_mappings')

    def get_serializer_class(self):
        """Return appropriate serializer"""
        if self.action == 'create':
            return WorkflowActionCreateSerializer
        return WorkflowActionSerializer

    def create(self, request, *args, **kwargs):
        """Create action for workflow"""
        workflow_id = self.kwargs.get('workflow_pk')

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        action = serializer.save(workflow_id=workflow_id)

        return Response(
            WorkflowActionSerializer(action).data,
            status=status.HTTP_201_CREATED
        )


class WorkflowMappingViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing field mappings.

    Mappings belong to workflow actions.
    """
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = WorkflowMappingSerializer

    def get_queryset(self):
        """Get mappings for a workflow action"""
        tenant_id = getattr(self.request, 'tenant_id', None)
        if not tenant_id:
            return WorkflowMapping.objects.none()

        action_id = self.kwargs.get('action_pk')
        return WorkflowMapping.objects.filter(
            workflow_action_id=action_id,
            workflow_action__workflow__tenant_id=tenant_id
        )

    def get_serializer_class(self):
        """Return appropriate serializer"""
        if self.action == 'create':
            return WorkflowMappingCreateSerializer
        return WorkflowMappingSerializer

    def create(self, request, *args, **kwargs):
        """Create mapping for action"""
        action_id = self.kwargs.get('action_pk')

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        mapping = serializer.save(workflow_action_id=action_id)

        return Response(
            WorkflowMappingSerializer(mapping).data,
            status=status.HTTP_201_CREATED
        )


class ExecutionLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing execution logs.

    Read-only as logs are created by the system.
    """
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Get execution logs for current tenant"""
        tenant_id = getattr(self.request, 'tenant_id', None)
        if not tenant_id:
            return ExecutionLog.objects.none()

        queryset = ExecutionLog.objects.filter(
            tenant_id=tenant_id
        ).select_related('workflow').order_by('-started_at')

        # Filter by workflow if provided
        workflow_id = self.request.query_params.get('workflow_id')
        if workflow_id:
            queryset = queryset.filter(workflow_id=workflow_id)

        # Filter by status if provided
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        return queryset[:100]  # Limit to last 100

    def get_serializer_class(self):
        """Return appropriate serializer"""
        if self.action == 'retrieve':
            return ExecutionLogSerializer
        return ExecutionLogListSerializer
