"""
real_estate API views.

Tenant scoping and authentication follow the same JWT-middleware pattern as
telephony/crm: request.tenant_id / request.user_id are set by
JWTAuthenticationMiddleware, TenantViewSetMixin filters querysets and injects
tenant_id on create, and HasDigiPermission checks
`<permission_module>.<permission_resource>.<action>` against the JWT's
permissions payload.
"""
import logging

from django.db.models import Count
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters

from common.authentication import JWTRequestAuthentication
from common.mixins import TenantViewSetMixin
from common.permissions import HasDigiPermission
from crm.zata_client import upload_to_zata, delete_from_zata
from real_estate.models import Project, Block, Unit, ProjectInterest, UnitLead
from real_estate.serializers import (
    ProjectSerializer, BlockSerializer, UnitSerializer,
    ProjectInterestSerializer, UnitLeadSerializer,
)
from real_estate.services.activity_bridge import (
    log_project_interest_activity, log_unit_lead_activity,
)

logger = logging.getLogger(__name__)


def _tenant_id(request):
    return getattr(request, 'tenant_id', None)


def _user_id(request):
    return getattr(request, 'user_id', None)


class ProjectViewSet(TenantViewSetMixin, viewsets.ModelViewSet):
    """
    /api/real-estate/projects/

    Plus GET /api/real-estate/projects/<id>/summary/ — unit counts grouped
    by status / unit_type / floor_number, computed on demand.
    """
    queryset = Project.objects.order_by('-created_at')
    serializer_class = ProjectSerializer
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [HasDigiPermission]
    permission_module = 'real_estate'
    permission_resource = 'projects'
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'project_type']
    search_fields = ['name', 'city', 'rera_number']
    ordering_fields = ['created_at', 'name', 'possession_date']

    def perform_create(self, serializer):
        tenant_id = _tenant_id(self.request)
        created_by_user_id = serializer.validated_data.get('created_by_user_id') or _user_id(self.request)
        serializer.save(tenant_id=tenant_id, created_by_user_id=created_by_user_id)

    @action(detail=True, methods=['get'], url_path='summary')
    def summary(self, request, pk=None):
        project = self.get_object()
        units = Unit.objects.filter(project=project, tenant_id=_tenant_id(request))

        def _counts(qs, key):
            return {
                str(row[key]) if row[key] is not None else 'null': row['count']
                for row in qs.values(key).annotate(count=Count('id'))
            }

        return Response({
            'unit_counts_by_status': _counts(units, 'status'),
            'unit_counts_by_type': _counts(units, 'unit_type'),
            'unit_counts_by_floor': _counts(units, 'floor_number'),
        })

    @action(detail=True, methods=['post'], url_path='image')
    def upload_image(self, request, pk=None):
        """
        POST /api/real-estate/projects/<pk>/image/
        Upload/replace a project's cover image via Zata storage.
        Requires X-Zata-Folder-ID header; X-Zata-Bucket is optional.
        """
        project = self.get_object()

        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            return Response(
                {'error': 'No file provided. Send the file under the "file" field.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        folder_id = request.META.get('HTTP_X_ZATA_FOLDER_ID')
        if not folder_id:
            return Response(
                {'error': 'X-Zata-Folder-ID header is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_zata_id = project.image_zata_id
        try:
            zata_result = upload_to_zata(uploaded_file, folder_id, uploaded_file.name)
        except Exception as exc:
            logger.error('Zata upload failed for project %s: %s', project.id, exc)
            return Response(
                {'error': f'Zata upload failed: {exc}'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        if old_zata_id:
            try:
                delete_from_zata(old_zata_id)
            except Exception as exc:
                logger.warning('Failed to delete old Zata image %s for project %s: %s', old_zata_id, project.id, exc)

        project.image_zata_id = zata_result.get('id')
        project.image_url = zata_result.get('download_url')
        project.save(update_fields=['image_zata_id', 'image_url', 'updated_at'])

        serializer = self.get_serializer(project)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['delete'], url_path='image')
    def delete_image(self, request, pk=None):
        """
        DELETE /api/real-estate/projects/<pk>/image/
        Remove the project's cover image from Zata and clear stored references.
        """
        project = self.get_object()

        if project.image_zata_id:
            try:
                delete_from_zata(project.image_zata_id)
            except Exception as exc:
                logger.warning('Failed to delete Zata image %s for project %s: %s', project.image_zata_id, project.id, exc)

        if project.image_zata_id or project.image_url:
            project.image_zata_id = None
            project.image_url = None
            project.save(update_fields=['image_zata_id', 'image_url', 'updated_at'])

        return Response(status=status.HTTP_204_NO_CONTENT)


class BlockViewSet(TenantViewSetMixin, viewsets.ModelViewSet):
    """/api/real-estate/blocks/"""
    queryset = Block.objects.order_by('project_id', 'name')
    serializer_class = BlockSerializer
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [HasDigiPermission]
    permission_module = 'real_estate'
    permission_resource = 'projects'
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['project', 'block_type']
    ordering_fields = ['created_at', 'name']


class UnitViewSet(TenantViewSetMixin, viewsets.ModelViewSet):
    """/api/real-estate/units/"""
    queryset = Unit.objects.order_by('project_id', 'unit_number')
    serializer_class = UnitSerializer
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [HasDigiPermission]
    permission_module = 'real_estate'
    permission_resource = 'units'
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['project', 'block', 'status', 'unit_type', 'floor_number']
    search_fields = ['unit_number', 'configuration']
    ordering_fields = ['created_at', 'unit_number', 'floor_number', 'total_price']


class ProjectInterestViewSet(TenantViewSetMixin, viewsets.ModelViewSet):
    """/api/real-estate/project-interests/"""
    queryset = ProjectInterest.objects.order_by('-created_at')
    serializer_class = ProjectInterestSerializer
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [HasDigiPermission]
    permission_module = 'real_estate'
    permission_resource = 'leads'
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['project', 'lead', 'assigned_to', 'preferred_unit_type']
    ordering_fields = ['created_at']

    def perform_create(self, serializer):
        super().perform_create(serializer)
        log_project_interest_activity(serializer.instance, actor_user_id=_user_id(self.request))


class UnitLeadViewSet(TenantViewSetMixin, viewsets.ModelViewSet):
    """/api/real-estate/unit-leads/"""
    queryset = UnitLead.objects.order_by('-created_at')
    serializer_class = UnitLeadSerializer
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [HasDigiPermission]
    permission_module = 'real_estate'
    permission_resource = 'leads'
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['unit', 'lead', 'relation_type', 'assigned_to']
    ordering_fields = ['created_at']

    def perform_create(self, serializer):
        super().perform_create(serializer)
        log_unit_lead_activity(serializer.instance, actor_user_id=_user_id(self.request))

    def perform_update(self, serializer):
        previous_relation_type = serializer.instance.relation_type
        super().perform_update(serializer)
        if serializer.instance.relation_type != previous_relation_type:
            log_unit_lead_activity(
                serializer.instance,
                actor_user_id=_user_id(self.request),
                previous_relation_type=previous_relation_type,
            )
