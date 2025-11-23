from django.contrib import admin
from common.admin_site import tenant_admin_site, TenantModelAdmin
from .models import (
    LeadStatus, Lead, LeadActivity, LeadOrder, LeadFieldConfiguration
)


class LeadStatusAdmin(TenantModelAdmin):
    """Admin interface for Lead Status"""
    list_display = ['id', 'name', 'tenant_id', 'order_index', 'color_hex', 'is_won', 'is_lost', 'is_active', 'created_at']
    list_filter = ['is_won', 'is_lost', 'is_active', 'created_at']
    search_fields = ['name', 'tenant_id']
    ordering = ['tenant_id', 'order_index']
    readonly_fields = ['created_at', 'updated_at']  # Removed tenant_id
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'order_index', 'color_hex')  # Removed tenant_id
        }),
        ('Status Flags', {
            'fields': ('is_won', 'is_lost', 'is_active')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


class LeadAdmin(TenantModelAdmin):
    """Admin interface for Lead"""
    list_display = ['id', 'name', 'phone', 'email', 'company', 'status', 'priority', 'tenant_id', 'owner_user_id', 'created_at']
    list_filter = ['priority', 'status', 'created_at', 'next_follow_up_at']
    search_fields = ['name', 'phone', 'email', 'company', 'tenant_id', 'notes']
    ordering = ['-created_at']
    readonly_fields = ['created_at', 'updated_at']  # Removed tenant_id
    raw_id_fields = ['status']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'phone', 'email', 'company', 'title')  # Removed tenant_id
        }),
        ('Status & Priority', {
            'fields': ('status', 'priority')
        }),
        ('Value Information', {
            'fields': ('value_amount', 'value_currency', 'source'),
            'classes': ('collapse',)
        }),
        ('Assignment', {
            'fields': ('owner_user_id', 'assigned_to')
        }),
        ('Custom Fields', {
            'fields': ('metadata',),
            'classes': ('collapse',),
            'description': 'JSON field containing custom field values'
        }),
        ('Follow-up', {
            'fields': ('last_contacted_at', 'next_follow_up_at')
        }),
        ('Notes', {
            'fields': ('notes',),
            'classes': ('collapse',)
        }),
        ('Address', {
            'fields': ('address_line1', 'address_line2', 'city', 'state', 'country', 'postal_code'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


class LeadActivityAdmin(TenantModelAdmin):
    """Admin interface for Lead Activity"""
    list_display = ['id', 'lead', 'type', 'happened_at', 'by_user_id', 'tenant_id', 'created_at']
    list_filter = ['type', 'happened_at', 'created_at']
    search_fields = ['content', 'tenant_id', 'by_user_id']
    ordering = ['-happened_at']
    readonly_fields = ['created_at']  # Removed tenant_id
    raw_id_fields = ['lead']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('lead', 'type', 'happened_at', 'by_user_id')  # Removed tenant_id
        }),
        ('Content', {
            'fields': ('content', 'file_url')
        }),
        ('Metadata', {
            'fields': ('meta',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )


class LeadOrderAdmin(TenantModelAdmin):
    """Admin interface for Lead Order"""
    list_display = ['id', 'lead', 'status', 'position', 'board_id', 'tenant_id', 'updated_at']
    list_filter = ['status', 'updated_at']
    search_fields = ['tenant_id']
    ordering = ['status', 'position']
    readonly_fields = ['updated_at']  # Removed tenant_id
    raw_id_fields = ['lead', 'status']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('lead', 'status', 'position', 'board_id')  # Removed tenant_id
        }),
        ('Timestamps', {
            'fields': ('updated_at',),
            'classes': ('collapse',)
        }),
    )


class LeadFieldConfigurationAdmin(TenantModelAdmin):
    """Admin interface for Lead Field Configuration (unified standard and custom fields)"""
    list_display = [
        'id', 'field_name', 'field_label', 'is_standard', 'field_type', 
        'is_visible', 'is_required', 'is_active', 'display_order', 'tenant_id'
    ]
    list_filter = ['is_standard', 'field_type', 'is_visible', 'is_required', 'is_active', 'created_at']
    search_fields = ['field_name', 'field_label', 'help_text', 'tenant_id']
    ordering = ['tenant_id', 'display_order', 'field_label']
    readonly_fields = ['created_at', 'updated_at']  # Removed tenant_id
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('field_name', 'field_label', 'is_standard')  # Removed tenant_id
        }),
        ('Field Type & Configuration', {
            'fields': ('field_type', 'options', 'default_value')
        }),
        ('Visibility & Requirements', {
            'fields': ('is_visible', 'is_required', 'is_active', 'display_order')
        }),
        ('UI Configuration', {
            'fields': ('placeholder', 'help_text'),
            'classes': ('collapse',)
        }),
        ('Validation', {
            'fields': ('validation_rules',),
            'classes': ('collapse',),
            'description': 'JSON field for custom validation rules (min, max, pattern, etc.)'
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def get_queryset(self, request):
        """Optimize queryset"""
        qs = super().get_queryset(request)
        return qs.select_related()
    
    def has_delete_permission(self, request, obj=None):
        """Prevent deletion of standard fields, allow deletion of custom fields"""
        if obj and obj.is_standard:
            return False
        return super().has_delete_permission(request, obj)


# Register all models with tenant admin site
tenant_admin_site.register(LeadStatus, LeadStatusAdmin)
tenant_admin_site.register(Lead, LeadAdmin)
tenant_admin_site.register(LeadActivity, LeadActivityAdmin)
tenant_admin_site.register(LeadOrder, LeadOrderAdmin)
tenant_admin_site.register(LeadFieldConfiguration, LeadFieldConfigurationAdmin)