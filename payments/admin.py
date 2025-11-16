from django.contrib import admin
from common.admin_site import tenant_admin_site, TenantModelAdmin
from .models import Payment


class PaymentAdmin(TenantModelAdmin):
    """Admin interface for Payment"""
    list_display = [
        'lead', 'type', 'amount', 'currency', 'status',
        'date', 'method', 'reference_no', 'created_at'
    ]
    list_filter = ['type', 'status', 'currency', 'date', 'created_at']
    search_fields = ['lead__name', 'reference_no', 'method', 'notes']
    date_hierarchy = 'date'
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Payment Details', {
            'fields': ('lead', 'type', 'status')
        }),
        ('Amount', {
            'fields': ('amount', 'currency')
        }),
        ('Transaction Info', {
            'fields': ('date', 'method', 'reference_no', 'attachment_url')
        }),
        ('Notes', {
            'fields': ('notes',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


# Register with tenant admin site
tenant_admin_site.register(Payment, PaymentAdmin)