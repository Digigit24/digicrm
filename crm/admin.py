from django.contrib import admin
from common.admin_site import tenant_admin_site, TenantModelAdmin
from .models import Lead, LeadStatus, LeadActivity, LeadOrder


class LeadStatusAdmin(TenantModelAdmin):
    """Admin interface for LeadStatus"""
    list_display = ['name', 'order_index', 'color_hex', 'is_won', 'is_lost', 'is_active']
    list_filter = ['is_won', 'is_lost', 'is_active']
    search_fields = ['name']
    ordering = ['order_index']


class LeadActivityInline(admin.TabularInline):
    """Inline admin for Lead Activities"""
    model = LeadActivity
    extra = 0
    fields = ['type', 'content', 'happened_at', 'by_user_id']
    readonly_fields = ['created_at']


class LeadAdmin(TenantModelAdmin):
    """Admin interface for Lead"""
    list_display = [
        'name', 'phone', 'email', 'company', 'status',
        'priority', 'value_amount', 'owner_user_id', 'created_at'
    ]
    list_filter = ['status', 'priority', 'created_at', 'country', 'state']
    search_fields = ['name', 'phone', 'email', 'company', 'notes']
    date_hierarchy = 'created_at'
    readonly_fields = ['created_at', 'updated_at']
    inlines = [LeadActivityInline]
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'phone', 'email', 'company', 'title')
        }),
        ('Lead Details', {
            'fields': ('status', 'priority', 'value_amount', 'value_currency', 'source', 'owner_user_id')
        }),
        ('Follow-up', {
            'fields': ('last_contacted_at', 'next_follow_up_at', 'notes')
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
    """Admin interface for LeadActivity"""
    list_display = ['lead', 'type', 'happened_at', 'by_user_id', 'created_at']
    list_filter = ['type', 'happened_at']
    search_fields = ['lead__name', 'content']
    date_hierarchy = 'happened_at'
    readonly_fields = ['created_at']


class LeadOrderAdmin(TenantModelAdmin):
    """Admin interface for LeadOrder"""
    list_display = ['lead', 'status', 'position', 'board_id', 'updated_at']
    list_filter = ['status', 'board_id']
    search_fields = ['lead__name', 'status__name']
    readonly_fields = ['updated_at']


# Register models with the custom tenant admin site
tenant_admin_site.register(LeadStatus, LeadStatusAdmin)
tenant_admin_site.register(Lead, LeadAdmin)
tenant_admin_site.register(LeadActivity, LeadActivityAdmin)
tenant_admin_site.register(LeadOrder, LeadOrderAdmin)