from django.contrib import admin
from django import forms
from common.admin_site import tenant_admin_site, TenantModelAdmin
from .models import (
    Lead, LeadStatus, LeadActivity, LeadOrder,
    LeadCustomField, LeadFieldVisibility
)


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
        'priority', 'value_amount', 'assigned_to', 'owner_user_id', 'created_at'
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
            'fields': ('status', 'priority', 'value_amount', 'value_currency', 'source', 'owner_user_id', 'assigned_to')
        }),
        ('Custom Fields', {
            'fields': ('metadata',),
            'description': 'Store custom field values as JSON key-value pairs based on tenant custom field definitions'
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


class LeadCustomFieldForm(forms.ModelForm):
    """Custom form for LeadCustomField with better options handling"""

    class Meta:
        model = LeadCustomField
        exclude = ['id']  # Exclude id field to prevent conflicts
        widgets = {
            'options': forms.Textarea(attrs={
                'rows': 4,
                'placeholder': 'Enter options as JSON array, e.g., ["Option 1", "Option 2", "Option 3"]'
            }),
            'help_text': forms.Textarea(attrs={'rows': 2}),
            'placeholder': forms.TextInput(attrs={'size': 50}),
        }


class LeadCustomFieldAdmin(TenantModelAdmin):
    """Admin interface for LeadCustomField"""
    form = LeadCustomFieldForm
    list_display = [
        'field_label', 'field_name', 'field_type', 'is_required',
        'is_active', 'display_order', 'created_at'
    ]
    list_filter = ['field_type', 'is_required', 'is_active', 'created_at']
    search_fields = ['field_name', 'field_label', 'help_text']
    ordering = ['display_order', 'field_label']
    readonly_fields = ['created_at', 'updated_at']

    fieldsets = (
        ('Field Identification', {
            'fields': ('field_name', 'field_label', 'field_type'),
            'description': 'Define the custom field name (internal key) and display label'
        }),
        ('Field Configuration', {
            'fields': ('is_required', 'default_value', 'placeholder', 'help_text'),
        }),
        ('Options (for Dropdown/Multiselect)', {
            'fields': ('options',),
            'description': 'JSON array of options for dropdown and multiselect fields',
            'classes': ('collapse',)
        }),
        ('Display Settings', {
            'fields': ('display_order', 'is_active'),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


class LeadFieldVisibilityAdmin(TenantModelAdmin):
    """Admin interface for LeadFieldVisibility"""
    list_display = [
        'field_name', 'is_visible', 'display_order', 'updated_at'
    ]
    list_filter = ['is_visible']
    search_fields = ['field_name']
    ordering = ['display_order', 'field_name']
    readonly_fields = ['created_at', 'updated_at']

    fieldsets = (
        ('Field Configuration', {
            'fields': ('field_name', 'is_visible', 'display_order'),
            'description': 'Control visibility and display order of standard Lead model fields'
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


# Register models with the custom tenant admin site
tenant_admin_site.register(LeadStatus, LeadStatusAdmin)
tenant_admin_site.register(Lead, LeadAdmin)
tenant_admin_site.register(LeadActivity, LeadActivityAdmin)
tenant_admin_site.register(LeadOrder, LeadOrderAdmin)
tenant_admin_site.register(LeadCustomField, LeadCustomFieldAdmin)
tenant_admin_site.register(LeadFieldVisibility, LeadFieldVisibilityAdmin)