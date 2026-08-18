from common.admin_site import tenant_admin_site, TenantModelAdmin
from .models import CalendarPreference


class CalendarPreferenceAdmin(TenantModelAdmin):
    list_display = ['user_id', 'timezone', 'default_view', 'week_starts_on',
                    'working_hours_start', 'working_hours_end']
    list_filter = ['default_view', 'timezone']
    search_fields = ['user_id']
    readonly_fields = ['created_at', 'updated_at']


tenant_admin_site.register(CalendarPreference, CalendarPreferenceAdmin)
