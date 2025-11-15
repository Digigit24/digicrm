from rest_framework import serializers


class TenantMixin(serializers.ModelSerializer):
    """
    Mixin to automatically handle tenant_id from request context
    """
    
    def create(self, validated_data):
        """Override create to automatically set tenant_id from request"""
        request = self.context.get('request')
        if request and hasattr(request, 'tenant_id'):
            validated_data['tenant_id'] = request.tenant_id
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
            serializer.save(tenant_id=self.request.tenant_id)
        else:
            serializer.save()