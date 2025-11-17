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
        
        # Debug: Log request attributes
        request_attrs = [attr for attr in dir(request) if not attr.startswith('_')]
        logger.debug(f"Serializer request attributes: {request_attrs}")
        
        # Check for tenant_id in request (set by middleware from headers)
        tenant_id = getattr(request, 'tenant_id', None)
        
        # Debug: Log specific tenant-related attributes
        logger.debug(f"Serializer request.tenant_id: {tenant_id}")
        logger.debug(f"Serializer hasattr(request, 'tenant_id'): {hasattr(request, 'tenant_id')}")
        
        # Also check if it's in the user object (from authentication)
        if hasattr(request, 'user') and isinstance(request.user, dict):
            user_tenant_id = request.user.get('tenant_id')
            logger.debug(f"Serializer request.user.tenant_id: {user_tenant_id}")
            if tenant_id is None and user_tenant_id:
                tenant_id = user_tenant_id
                logger.debug(f"Serializer using tenant_id from user object: {tenant_id}")
        
        if tenant_id is None:
            # Log available headers for debugging
            headers = {k: v for k, v in request.META.items() if k.startswith('HTTP_')}
            logger.error(
                f"Tenant ID not found in request for {request.method} {request.path}. "
                f"Available headers: {headers}"
            )
            
            # Try to extract tenant_id directly from headers as fallback
            fallback_tenant_id = (
                request.META.get('HTTP_X_TENANT_ID') or
                request.META.get('HTTP_TENANTTOKEN')
            )
            if fallback_tenant_id:
                logger.warning(f"Serializer using fallback tenant_id from headers: {fallback_tenant_id}")
                tenant_id = fallback_tenant_id
            else:
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
        
        # Also check if it's in the user object (from authentication)
        if hasattr(self.request, 'user') and isinstance(self.request.user, dict):
            user_tenant_id = self.request.user.get('tenant_id')
            if tenant_id is None and user_tenant_id:
                tenant_id = user_tenant_id
                logger.debug(f"get_queryset using tenant_id from user object: {tenant_id}")
        
        # Try fallback from headers if still not found
        if tenant_id is None:
            fallback_tenant_id = (
                self.request.META.get('HTTP_X_TENANT_ID') or
                self.request.META.get('HTTP_TENANTTOKEN')
            )
            if fallback_tenant_id:
                logger.warning(f"get_queryset using fallback tenant_id from headers: {fallback_tenant_id}")
                tenant_id = fallback_tenant_id
        
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
        
        # Debug: Log all request attributes
        request_attrs = [attr for attr in dir(self.request) if not attr.startswith('_')]
        logger.debug(f"Request attributes: {request_attrs}")
        
        # Check for tenant_id in request (set by middleware from headers)
        tenant_id = getattr(self.request, 'tenant_id', None)
        
        # Debug: Log specific tenant-related attributes
        logger.debug(f"request.tenant_id: {tenant_id}")
        logger.debug(f"hasattr(request, 'tenant_id'): {hasattr(self.request, 'tenant_id')}")
        
        # Also check if it's in the user object (from authentication)
        if hasattr(self.request, 'user') and isinstance(self.request.user, dict):
            user_tenant_id = self.request.user.get('tenant_id')
            logger.debug(f"request.user.tenant_id: {user_tenant_id}")
            if tenant_id is None and user_tenant_id:
                tenant_id = user_tenant_id
                logger.debug(f"Using tenant_id from user object: {tenant_id}")
        
        if tenant_id is None:
            # Log available headers for debugging
            headers = {k: v for k, v in self.request.META.items() if k.startswith('HTTP_')}
            logger.error(
                f"Tenant ID not found in perform_create for {self.request.method} {self.request.path}. "
                f"Available headers: {headers}"
            )
            
            # Try to extract tenant_id directly from headers as fallback
            fallback_tenant_id = (
                self.request.META.get('HTTP_X_TENANT_ID') or
                self.request.META.get('HTTP_TENANTTOKEN')
            )
            if fallback_tenant_id:
                logger.warning(f"Using fallback tenant_id from headers: {fallback_tenant_id}")
                tenant_id = fallback_tenant_id
            else:
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