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
        
        if not request:
            logger.error("No request found in serializer context")
            raise ValidationError({
                'tenant_id': 'Request context is required but was not found'
            })
        
        # Check for tenant_id in request (set by middleware from headers)
        tenant_id = getattr(request, 'tenant_id', None)
        
        if tenant_id is None:
            # Log available headers for debugging
            headers = {k: v for k, v in request.META.items() if k.startswith('HTTP_')}
            logger.error(
                f"Tenant ID not found in request for {request.method} {request.path}. "
                f"Available headers: {headers}"
            )
            raise ValidationError({
                'tenant_id': 'Tenant ID is required but was not found in request headers'
            })
        
        # Ensure tenant_id is not empty string
        if not tenant_id or tenant_id.strip() == '':
            logger.error(
                f"Tenant ID is empty in request for {request.method} {request.path}"
            )
            raise ValidationError({
                'tenant_id': 'Tenant ID cannot be empty'
            })
        
        validated_data['tenant_id'] = tenant_id
        logger.debug(f"Creating object with tenant_id: {tenant_id}")
        
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
        
        if not hasattr(self, 'request') or not self.request:
            logger.warning("No request found in ViewSet get_queryset, returning unfiltered queryset")
            return queryset
        
        # Check for tenant_id in request (set by middleware from headers)
        tenant_id = getattr(self.request, 'tenant_id', None)
        
        if tenant_id is None:
            logger.warning(
                f"Tenant ID not found in get_queryset for {self.request.method} {self.request.path}, "
                f"returning unfiltered queryset"
            )
            return queryset
        
        # Ensure tenant_id is not empty string
        if not tenant_id or tenant_id.strip() == '':
            logger.warning(
                f"Tenant ID is empty in get_queryset for {self.request.method} {self.request.path}, "
                f"returning unfiltered queryset"
            )
            return queryset
        
        logger.debug(f"Filtering queryset by tenant_id: {tenant_id}")
        return queryset.filter(tenant_id=tenant_id)
    
    def perform_create(self, serializer):
        """Ensure tenant_id is set when creating objects"""
        if not hasattr(self, 'request') or not self.request:
            logger.error("No request found in ViewSet")
            raise ValidationError({
                'tenant_id': 'Request context is required but was not found'
            })
        
        # Check for tenant_id in request (set by middleware from headers)
        tenant_id = getattr(self.request, 'tenant_id', None)
        
        if tenant_id is None:
            # Log available headers for debugging
            headers = {k: v for k, v in self.request.META.items() if k.startswith('HTTP_')}
            logger.error(
                f"Tenant ID not found in perform_create for {self.request.method} {self.request.path}. "
                f"Available headers: {headers}"
            )
            raise ValidationError({
                'tenant_id': 'Tenant ID is required but was not found in request headers'
            })
        
        # Ensure tenant_id is not empty string
        if not tenant_id or tenant_id.strip() == '':
            logger.error(
                f"Tenant ID is empty in perform_create for {self.request.method} {self.request.path}"
            )
            raise ValidationError({
                'tenant_id': 'Tenant ID cannot be empty'
            })
        
        logger.debug(f"Performing create with tenant_id: {tenant_id}")
        serializer.save(tenant_id=tenant_id)