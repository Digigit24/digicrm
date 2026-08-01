from common.mixins import TenantMixin
from real_estate.models import Project, Block, Unit, ProjectInterest, UnitLead


class ProjectSerializer(TenantMixin):
    class Meta:
        model = Project
        fields = [
            'id', 'name', 'project_type', 'status', 'description',
            'address_line1', 'address_line2', 'city', 'state', 'country', 'postal_code',
            'latitude', 'longitude', 'rera_number', 'possession_date', 'image_url',
            'created_by_user_id', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'image_url', 'created_at', 'updated_at']
        extra_kwargs = {
            'created_by_user_id': {
                'required': False,
                'help_text': 'UUID of the user creating this project. If omitted, the authenticated JWT user_id is used.',
            },
        }


class BlockSerializer(TenantMixin):
    class Meta:
        model = Block
        fields = ['id', 'project', 'name', 'block_type', 'total_floors', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class UnitSerializer(TenantMixin):
    class Meta:
        model = Unit
        fields = [
            'id', 'project', 'block', 'unit_type', 'unit_number', 'floor_number', 'facing',
            'configuration', 'carpet_area_sqft', 'built_up_area_sqft', 'super_built_up_area_sqft',
            'plot_dimensions', 'rate_per_sqft', 'base_price', 'total_price', 'status',
            'amenities', 'metadata', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class ProjectInterestSerializer(TenantMixin):
    class Meta:
        model = ProjectInterest
        fields = [
            'id', 'project', 'lead', 'budget_min', 'budget_max', 'preferred_unit_type',
            'preferred_configuration', 'notes', 'assigned_to', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class UnitLeadSerializer(TenantMixin):
    class Meta:
        model = UnitLead
        fields = [
            'id', 'unit', 'lead', 'relation_type', 'booking_amount', 'booking_date',
            'notes', 'assigned_to', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
