import logging
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

logger = logging.getLogger(__name__)


class TenantMixin(serializers.ModelSerializer):
    """
    Mixin to automatically handle tenant_id from request context
    """

    def create(self, validated_data):
        """Override create to automatically set tenant_id from request"""
        request = self.context.get('request')
        if request and hasattr(request, 'tenant_id'):
            tenant_id = request.tenant_id
            if tenant_id is None:
                logger.error(
                    f"Tenant ID is None in request for {request.method} {request.path}"
                )
                raise ValidationError({
                    'tenant_id': 'Tenant ID is required but was not found in request'
                })
            validated_data['tenant_id'] = tenant_id
            logger.debug(f"Creating object with tenant_id: {tenant_id}")
        else:
            logger.error(
                f"Request does not have tenant_id attribute for {request.method if request else 'unknown'} "
                f"{request.path if request else 'unknown'}"
            )
            raise ValidationError({
                'tenant_id': 'Tenant context is required but was not found'
            })
        return super().create(validated_data)
    
    def update(self, instance, validated_data):
        """Override update to ensure tenant_id cannot be changed"""
        # Remove tenant_id from validated_data if present to prevent changes
        validated_data.pop('tenant_id', None)
        return super().update(instance, validated_data)
    
    class Meta:
        abstract = True


class TenantViewSetMixin:
    """
    Mixin for ViewSets to automatically filter by tenant_id
    """
    
    def get_queryset(self):
        """Filter queryset by tenant_id from request"""
        queryset = super().get_queryset()
        if hasattr(self.request, 'tenant_id'):
            return queryset.filter(tenant_id=self.request.tenant_id)
        return queryset
    
    def perform_create(self, serializer):
        """Ensure tenant_id is set when creating objects"""
        if hasattr(self.request, 'tenant_id'):
            tenant_id = self.request.tenant_id
            if tenant_id is None:
                logger.error(
                    f"Tenant ID is None in perform_create for {self.request.method} {self.request.path}"
                )
                raise ValidationError({
                    'tenant_id': 'Tenant ID is required but was not found in request'
                })
            logger.debug(f"Performing create with tenant_id: {tenant_id}")
            serializer.save(tenant_id=tenant_id)
        else:
            logger.error(
                f"Request does not have tenant_id attribute in perform_create for "
                f"{self.request.method} {self.request.path}"
            )
            raise ValidationError({
                'tenant_id': 'Tenant context is required but was not found'
            })