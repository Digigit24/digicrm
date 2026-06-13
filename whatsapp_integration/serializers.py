from rest_framework import serializers
from common.mixins import TenantMixin
from .models import (
    WhatsAppVendorConfig,
    WhatsAppCampaign,
    WhatsAppSequence,
    WhatsAppSequenceStep,
    LeadSequenceEnrollment,
    AgentActionLog,
    CampaignStatusEnum,
    SequenceEnrollmentStatusEnum,
    AgentActionTypeEnum,
)


# ---------------------------------------------------------------------------
# Vendor Config
# ---------------------------------------------------------------------------

class WhatsAppVendorConfigSerializer(TenantMixin):
    """
    Serializer for the Laravel WhatsApp vendor configuration.

    Stores the vendor_uid and API token needed to authenticate with the
    Laravel WhatsApp adapter on behalf of this DigiCRM tenant.
    """
    class Meta:
        model = WhatsAppVendorConfig
        fields = [
            'id', 'vendor_uid', 'api_token', 'api_base_url',
            'webhook_secret', 'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {
            'api_token':      {'write_only': True, 'help_text': 'Laravel vendor API token. Write-only for security.'},
            'webhook_secret': {'write_only': True, 'help_text': 'Shared secret for inbound webhook verification. Write-only.'},
            'vendor_uid':     {'help_text': 'Laravel vendor _uid (e.g. abc123xyz).'},
            'api_base_url':   {'help_text': 'Base URL of the Laravel API. Default: https://whatsappapi.celiyo.com/api'},
            'is_active':      {'help_text': 'Whether this config is active. Only one active config allowed per tenant.'},
        }


# ---------------------------------------------------------------------------
# Campaign
# ---------------------------------------------------------------------------

class WhatsAppCampaignSerializer(TenantMixin):
    """
    Serializer for WhatsApp campaigns planned in DigiCRM.

    Create a campaign by selecting a DigiCRM lead group and a WhatsApp template.
    After saving, call the /launch/ action endpoint to submit it to Laravel.
    Delivery analytics are fetched live from Laravel via /analytics/.
    """
    lead_group_name = serializers.CharField(
        source='lead_group.name', read_only=True,
        help_text='Display name of the linked lead group. Read-only.'
    )
    status_display = serializers.CharField(
        source='get_status_display', read_only=True,
        help_text='Human-readable campaign status label. Read-only.'
    )

    class Meta:
        model = WhatsAppCampaign
        fields = [
            'id', 'name', 'lead_group', 'lead_group_name',
            'template_uid', 'template_name', 'template_components',
            'status', 'status_display', 'scheduled_at', 'launched_at',
            'laravel_campaign_uid', 'laravel_group_uid',
            'total_contacts', 'created_by', 'notes',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'status', 'status_display', 'launched_at',
            'laravel_campaign_uid', 'laravel_group_uid',
            'total_contacts', 'created_by', 'created_at', 'updated_at',
        ]
        extra_kwargs = {
            'name':                 {'help_text': 'Campaign display name.'},
            'lead_group':           {'help_text': 'ID of the DigiCRM lead group to target.'},
            'template_uid':         {'help_text': 'Laravel WhatsApp template _uid.'},
            'template_name':        {'help_text': 'Cached template name for display. Optional.'},
            'template_components':  {'help_text': 'Template variable components array. See WhatsApp template docs.'},
            'scheduled_at':         {'help_text': 'ISO 8601 datetime to schedule the campaign. Null = send immediately on launch.'},
            'notes':                {'help_text': 'Internal notes about this campaign.'},
        }


class WhatsAppCampaignListSerializer(serializers.ModelSerializer):
    """Lightweight list serializer — avoids heavy fields for list views."""
    lead_group_name = serializers.CharField(source='lead_group.name', read_only=True)
    status_display  = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = WhatsAppCampaign
        fields = [
            'id', 'name', 'lead_group', 'lead_group_name',
            'template_name', 'status', 'status_display',
            'scheduled_at', 'total_contacts', 'laravel_campaign_uid',
            'created_at',
        ]


# ---------------------------------------------------------------------------
# Sequence
# ---------------------------------------------------------------------------

class WhatsAppSequenceStepSerializer(serializers.ModelSerializer):
    """
    A single step in a WhatsApp follow-up sequence.

    step_number defines order (1, 2, 3 …).
    delay_days defines how many days after the previous step this fires
    (0 for immediate, 2 for 2 days later, etc.).
    template_variable_mapping maps template positions to lead field names:
      { "1": "name", "2": "company" }
    """
    class Meta:
        model = WhatsAppSequenceStep
        fields = [
            'id', 'step_number', 'delay_days',
            'template_uid', 'template_name',
            'template_variable_mapping',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {
            'step_number':              {'help_text': 'Step order, starting at 1.'},
            'delay_days':               {'help_text': 'Days to wait after previous step (0 = same day).'},
            'template_uid':             {'help_text': 'Laravel template _uid for this step.'},
            'template_name':            {'help_text': 'Cached template name for display.'},
            'template_variable_mapping': {
                'help_text': 'Maps template variable positions to lead fields. '
                             'Example: {"1": "name", "2": "company"}.'
            },
        }


class WhatsAppSequenceSerializer(TenantMixin):
    """
    A WhatsApp follow-up sequence — a series of timed template messages sent
    automatically to enrolled leads.

    Create a sequence, add steps, then enroll leads from their detail page or
    via the agent enroll endpoint.
    """
    steps = WhatsAppSequenceStepSerializer(many=True, read_only=True)
    enrolled_count = serializers.SerializerMethodField(
        help_text='Number of leads currently active in this sequence.'
    )

    class Meta:
        model = WhatsAppSequence
        fields = [
            'id', 'name', 'description', 'is_active', 'stop_on_reply',
            'steps', 'enrolled_count',
            'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'steps', 'enrolled_count', 'created_by', 'created_at', 'updated_at']
        extra_kwargs = {
            'name':          {'help_text': 'Sequence name, e.g. Dental Follow-Up.'},
            'description':   {'help_text': 'What this sequence is for.'},
            'is_active':     {'help_text': 'Inactive sequences will not fire new steps.'},
            'stop_on_reply': {'help_text': 'Stop sequence automatically when the lead replies.'},
        }

    def get_enrolled_count(self, obj):
        return obj.enrollments.filter(status=SequenceEnrollmentStatusEnum.ACTIVE).count()


class WhatsAppSequenceListSerializer(serializers.ModelSerializer):
    """Lightweight list view."""
    enrolled_count = serializers.SerializerMethodField()
    step_count     = serializers.SerializerMethodField()

    class Meta:
        model = WhatsAppSequence
        fields = ['id', 'name', 'is_active', 'stop_on_reply', 'step_count', 'enrolled_count', 'created_at']

    def get_enrolled_count(self, obj):
        return obj.enrollments.filter(status=SequenceEnrollmentStatusEnum.ACTIVE).count()

    def get_step_count(self, obj):
        return obj.steps.count()


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------

class LeadSequenceEnrollmentSerializer(serializers.ModelSerializer):
    """
    Shows the sequence enrollment state for a lead.
    """
    sequence_name    = serializers.CharField(source='sequence.name', read_only=True)
    current_step_num = serializers.IntegerField(
        source='current_step.step_number', read_only=True, allow_null=True
    )
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = LeadSequenceEnrollment
        fields = [
            'id', 'lead', 'sequence', 'sequence_name',
            'current_step', 'current_step_num',
            'status', 'status_display',
            'next_step_at', 'enrolled_at', 'completed_at', 'stopped_reason',
        ]
        read_only_fields = [
            'id', 'sequence_name', 'current_step_num', 'status_display',
            'enrolled_at', 'completed_at',
        ]


class EnrollLeadSerializer(serializers.Serializer):
    """Input for enrolling a lead in a sequence."""
    sequence_id = serializers.IntegerField(help_text='ID of the WhatsApp sequence to enroll in.')


class BulkEnrollSerializer(serializers.Serializer):
    """Input for bulk-enrolling multiple leads in a sequence."""
    lead_ids    = serializers.ListField(
        child=serializers.IntegerField(), min_length=1,
        help_text='List of lead IDs to enroll.'
    )
    sequence_id = serializers.IntegerField(help_text='ID of the WhatsApp sequence.')


# ---------------------------------------------------------------------------
# Agent action endpoints
# ---------------------------------------------------------------------------

class AgentSendWhatsAppSerializer(serializers.Serializer):
    """Input for agent send-whatsapp action."""
    lead_id             = serializers.IntegerField(help_text='DigiCRM lead ID.')
    template_uid        = serializers.CharField(help_text='Laravel template _uid.')
    template_components = serializers.ListField(
        child=serializers.DictField(), required=False, default=list,
        help_text='Template variable components array.'
    )
    note                = serializers.CharField(
        required=False, allow_blank=True,
        help_text='Optional note to log as a LeadActivity alongside the send.'
    )


class AgentCreateCampaignSerializer(serializers.Serializer):
    """Input for agent create-campaign action."""
    name                = serializers.CharField(help_text='Campaign name.')
    lead_group_id       = serializers.IntegerField(help_text='DigiCRM lead group ID.')
    template_uid        = serializers.CharField(help_text='Laravel template _uid.')
    template_components = serializers.ListField(
        child=serializers.DictField(), required=False, default=list
    )
    scheduled_at        = serializers.DateTimeField(
        required=False, allow_null=True,
        help_text='ISO 8601 schedule datetime. Null = send immediately.'
    )
    notes               = serializers.CharField(required=False, allow_blank=True)


class AgentUpdateLeadStatusSerializer(serializers.Serializer):
    """Input for agent update-lead-status action."""
    lead_id   = serializers.IntegerField()
    status_id = serializers.IntegerField(help_text='DigiCRM LeadStatus ID.')
    note      = serializers.CharField(required=False, allow_blank=True)


class AgentLogActivitySerializer(serializers.Serializer):
    """Input for agent log-activity action."""
    lead_id      = serializers.IntegerField()
    activity_type = serializers.ChoiceField(
        choices=['CALL', 'EMAIL', 'MEETING', 'NOTE', 'SMS', 'OTHER'],
        help_text='Type of activity.'
    )
    content      = serializers.CharField(help_text='Activity content or note text.')
    happened_at  = serializers.DateTimeField(
        required=False, allow_null=True,
        help_text='When the activity happened. Defaults to now.'
    )


class AgentActionLogSerializer(serializers.ModelSerializer):
    """Read-only audit log of agent actions."""
    class Meta:
        model = AgentActionLog
        fields = [
            'id', 'action_type', 'payload_in', 'payload_out',
            'triggered_by', 'status', 'error_message', 'created_at',
        ]
        read_only_fields = fields
