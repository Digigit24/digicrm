from django.contrib import admin
from common.admin_site import tenant_admin_site, TenantModelAdmin
from .models import Project, Block, Unit, ProjectInterest, UnitLead


class ProjectAdmin(TenantModelAdmin):
    list_display = ['id', 'name', 'project_type', 'status', 'city', 'tenant_id', 'created_at']
    list_filter = ['project_type', 'status', 'created_at']
    search_fields = ['name', 'city', 'rera_number', 'tenant_id']
    ordering = ['-created_at']
    readonly_fields = ['created_at', 'updated_at']


class BlockAdmin(TenantModelAdmin):
    list_display = ['id', 'name', 'project', 'block_type', 'total_floors', 'tenant_id', 'created_at']
    list_filter = ['block_type', 'created_at']
    search_fields = ['name', 'tenant_id']
    ordering = ['project', 'name']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['project']


class UnitAdmin(TenantModelAdmin):
    list_display = ['id', 'unit_number', 'project', 'block', 'unit_type', 'status', 'tenant_id', 'created_at']
    list_filter = ['unit_type', 'status', 'facing', 'created_at']
    search_fields = ['unit_number', 'configuration', 'tenant_id']
    ordering = ['project', 'unit_number']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['project', 'block']


class ProjectInterestAdmin(TenantModelAdmin):
    list_display = ['id', 'project', 'lead', 'preferred_unit_type', 'assigned_to', 'tenant_id', 'created_at']
    list_filter = ['preferred_unit_type', 'created_at']
    search_fields = ['tenant_id']
    ordering = ['-created_at']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['project', 'lead']


class UnitLeadAdmin(TenantModelAdmin):
    list_display = ['id', 'unit', 'lead', 'relation_type', 'assigned_to', 'tenant_id', 'created_at']
    list_filter = ['relation_type', 'created_at']
    search_fields = ['tenant_id']
    ordering = ['-created_at']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['unit', 'lead']


tenant_admin_site.register(Project, ProjectAdmin)
tenant_admin_site.register(Block, BlockAdmin)
tenant_admin_site.register(Unit, UnitAdmin)
tenant_admin_site.register(ProjectInterest, ProjectInterestAdmin)
tenant_admin_site.register(UnitLead, UnitLeadAdmin)
