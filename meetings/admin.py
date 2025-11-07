from django.contrib import admin
from .models import Meeting


@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    """Admin interface for Meeting"""
    list_display = ['title', 'lead', 'location', 'start_at', 'end_at', 'created_at']
    list_filter = ['start_at', 'created_at']
    search_fields = ['title', 'location', 'description', 'lead__name']
    date_hierarchy = 'start_at'
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Meeting Details', {
            'fields': ('lead', 'title', 'location')
        }),
        ('Schedule', {
            'fields': ('start_at', 'end_at')
        }),
        ('Content', {
            'fields': ('description', 'notes')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )