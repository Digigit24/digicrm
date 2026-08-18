from rest_framework import serializers
from common.mixins import TenantMixin
from telephony.models import (
    TeleCMICredential, TeleCMIAgent, ZataStorageCredential,
    CallLog, SMSLog, TeleCMICampaign,
)
from crm.models import LeadGroup
from crm.serializers import LeadGroupMinimalSerializer
from integrations.utils.encryption import encrypt_token
from telephony.services.crypto import encrypt_secret


class TeleCMICredentialSerializer(TenantMixin):
    """
    Tenant-level TeleCMI account credentials.
    The secret is write-only; it is encrypted before storage.
    """
    secret = serializers.CharField(write_only=True, required=False, allow_blank=True)
    webhook_secret = serializers.CharField(write_only=True, required=False, allow_blank=True)
    webhook_secret_configured = serializers.SerializerMethodField()
    sbc_host = serializers.CharField(read_only=True)

    class Meta:
        model = TeleCMICredential
        fields = [
            'id', 'app_id', 'secret', 'sbc_region', 'sbc_host',
            'default_caller_id', 'webhook_secret', 'webhook_secret_configured', 'is_active',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'sbc_host', 'created_at', 'updated_at']
        extra_kwargs = {
            'app_id': {'help_text': 'Your TeleCMI App ID (appid)'},
            'secret': {'help_text': 'Your TeleCMI app secret. Write-only; stored encrypted.'},
            'sbc_region': {'help_text': 'SBC region for WebRTC SDK: sg, ind, us, or uk'},
            'default_caller_id': {'help_text': 'Default outbound caller ID phone number'},
            'webhook_secret': {'help_text': 'Optional secret to verify TeleCMI webhook POST requests'},
        }

    def get_webhook_secret_configured(self, obj):
        return bool(obj.webhook_secret)

    def create(self, validated_data):
        secret = validated_data.pop('secret', None)
        if secret:
            # Envelope encryption: mint a per-tenant key, store it wrapped.
            encrypted, dek_wrapped = encrypt_secret(secret)
            validated_data['secret_encrypted'] = encrypted
            validated_data['dek_wrapped'] = dek_wrapped
        elif not validated_data.get('secret_encrypted'):
            raise serializers.ValidationError({'secret': 'Secret is required when creating credentials.'})
        return super().create(validated_data)

    def update(self, instance, validated_data):
        secret = validated_data.pop('secret', None)
        if secret:
            # Reuse this tenant's existing key when it has one; saving a secret
            # from any environment therefore also repairs a row left unreadable
            # by the old shared-key scheme.
            encrypted, dek_wrapped = encrypt_secret(secret, instance.dek_wrapped)
            validated_data['secret_encrypted'] = encrypted
            validated_data['dek_wrapped'] = dek_wrapped
        return super().update(instance, validated_data)


class TeleCMIAgentSerializer(TenantMixin):
    """
    Per-user TeleCMI agent credentials.
    Password is write-only; token is read-only.
    """
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    token_is_fresh = serializers.SerializerMethodField()

    class Meta:
        model = TeleCMIAgent
        fields = [
            'id', 'user_id', 'telecmi_user_id', 'password',
            'token_is_fresh', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'token_is_fresh', 'created_at', 'updated_at']
        extra_kwargs = {
            'user_id': {'help_text': 'CRM user UUID this agent config belongs to'},
            'telecmi_user_id': {'help_text': 'TeleCMI user ID, e.g. 103_1111112'},
            'password': {'help_text': 'TeleCMI agent password. Write-only; stored encrypted.'},
        }

    def get_token_is_fresh(self, obj) -> bool:
        return not obj.is_token_stale()

    def create(self, validated_data):
        password = validated_data.pop('password', None)
        if password:
            validated_data['password_encrypted'] = encrypt_token(password)
        elif not validated_data.get('password_encrypted'):
            raise serializers.ValidationError({'password': 'Password is required when creating agent config.'})
        return super().create(validated_data)

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        if password:
            validated_data['password_encrypted'] = encrypt_token(password)
        return super().update(instance, validated_data)


class ZataStorageCredentialSerializer(TenantMixin):
    """Tenant Zata S3 settings; the secret key is write-only."""

    secret_access_key = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        trim_whitespace=False,
    )
    secret_configured = serializers.SerializerMethodField()

    class Meta:
        model = ZataStorageCredential
        fields = [
            'id', 'endpoint_url', 'bucket_name', 'access_key_id',
            'secret_access_key', 'secret_configured', 'object_prefix',
            'region_name', 'is_active', 'last_tested_at', 'last_test_error',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'secret_configured', 'last_tested_at', 'last_test_error',
            'created_at', 'updated_at',
        ]

    def get_secret_configured(self, obj):
        return bool(obj.secret_access_key_encrypted)

    def validate_endpoint_url(self, value):
        return value.rstrip('/')

    def validate_object_prefix(self, value):
        return value.strip().strip('/')

    def create(self, validated_data):
        secret = validated_data.pop('secret_access_key', None)
        if not secret:
            raise serializers.ValidationError({
                'secret_access_key': 'Secret access key is required when configuring Zata.'
            })
        encrypted, dek_wrapped = encrypt_secret(secret)
        validated_data['secret_access_key_encrypted'] = encrypted
        validated_data['dek_wrapped'] = dek_wrapped
        return super().create(validated_data)

    def update(self, instance, validated_data):
        secret = validated_data.pop('secret_access_key', None)
        if secret:
            encrypted, dek_wrapped = encrypt_secret(secret, instance.dek_wrapped)
            validated_data['secret_access_key_encrypted'] = encrypted
            validated_data['dek_wrapped'] = dek_wrapped
        return super().update(instance, validated_data)


class CallLogSerializer(serializers.ModelSerializer):
    """Read-only CDR record."""
    direction_display = serializers.CharField(source='get_direction_display', read_only=True)
    call_type_display = serializers.CharField(source='get_call_type_display', read_only=True)
    has_recording = serializers.SerializerMethodField()
    crm_lead_id = serializers.IntegerField(read_only=True, allow_null=True, default=None)
    crm_lead_name = serializers.CharField(read_only=True, allow_null=True, default=None)

    def get_has_recording(self, obj):
        return bool(obj.recording_file or obj.recording_object_key)

    class Meta:
        model = CallLog
        fields = [
            'id', 'cmiuid', 'direction', 'direction_display',
            'call_type', 'call_type_display',
            'from_number', 'to_number', 'duration', 'billed_sec', 'rate',
            'caller_name', 'telecmi_notes', 'call_time',
            'lead_id', 'crm_lead_id', 'crm_lead_name',
            'agent_user_id', 'synced_via',
            'recording_file', 'has_recording', 'created_at',
            'recording_storage_status', 'recording_content_type',
            'recording_size', 'recording_archived_at',
            'call_leg', 'telecmi_call_id', 'conversation_uuid',
            'ivr_name', 'team_name', 'is_voicemail', 'voicemail_filename',
            'wait_seconds', 'hangup_reason',
            'call_outcome', 'call_outcome_note', 'call_outcome_set_at',
        ]
        read_only_fields = [f for f in fields if f not in ('call_outcome', 'call_outcome_note')]


class SMSLogSerializer(serializers.ModelSerializer):
    """Read-only SMS log."""
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = SMSLog
        fields = [
            'id', 'from_number', 'to_number', 'message',
            'status', 'status_display', 'lead_id',
            'sent_by_user_id', 'error_message', 'created_at',
        ]
        read_only_fields = fields


class ClickToCallSerializer(serializers.Serializer):
    to_number = serializers.CharField(help_text='Destination phone number with country code')
    caller_id = serializers.CharField(
        required=False, allow_blank=True,
        help_text='Override caller ID for this call'
    )
    lead_id = serializers.IntegerField(
        required=False, allow_null=True,
        help_text='CRM Lead ID to associate with this call'
    )
    extra_params = serializers.DictField(
        required=False, default=dict,
        help_text='Extra params forwarded to TeleCMI (e.g. {"lead_id": 42})'
    )


class HangupSerializer(serializers.Serializer):
    cmiuuid = serializers.CharField(help_text='TeleCMI Leg B call UUID to hang up')


class SMSSendSerializer(serializers.Serializer):
    to_number = serializers.CharField(help_text='Destination phone number with country code')
    message = serializers.CharField(help_text='SMS message text (max ~160 chars for single SMS)')
    lead_id = serializers.IntegerField(
        required=False, allow_null=True,
        help_text='CRM Lead ID to link this SMS to'
    )


class CallerIDUpdateSerializer(serializers.Serializer):
    caller_id = serializers.CharField(help_text='The caller ID number to set as active')


class CDRSyncSerializer(serializers.Serializer):
    hours_back = serializers.IntegerField(
        default=24, min_value=1, max_value=720,
        help_text='How many hours of history to sync (max 720 = 30 days)'
    )


class AddNoteSerializer(serializers.Serializer):
    from_number = serializers.CharField(help_text='Caller phone number')
    caller_name = serializers.CharField(default='', allow_blank=True)
    timestamp_ms = serializers.IntegerField(help_text='UTC millisecond timestamp of the call')
    message = serializers.CharField(help_text='Note text to add to this call')


# ──────────────────────────────────────────────────────────────
# Call outcome (disposition)
# ──────────────────────────────────────────────────────────────

from telephony.services.analytics_service import OUTCOME_CHOICES


class CallOutcomeSerializer(serializers.Serializer):
    """PATCH body for /api/telephony/calls/<pk>/outcome/"""
    outcome = serializers.ChoiceField(choices=OUTCOME_CHOICES, help_text='Call disposition outcome')
    note = serializers.CharField(required=False, allow_blank=True, default='', help_text='Optional note about the outcome')


# ──────────────────────────────────────────────────────────────
# Campaigns (auto-dialer)
# ──────────────────────────────────────────────────────────────

class TeleCMICampaignSerializer(serializers.ModelSerializer):
    """CRUD serializer for auto-dialer campaigns."""

    # Read: nested {id, name, color_hex} (matches the LeadGroupMinimal shape
    # used on Lead responses). Write: source_group_id (tenant-scoped PK).
    source_group = LeadGroupMinimalSerializer(read_only=True)
    source_group_id = serializers.PrimaryKeyRelatedField(
        source='source_group',
        queryset=LeadGroup.objects.all(),
        required=False,
        allow_null=True,
        write_only=True,
        help_text='CRM LeadGroup ID this campaign is seeded from',
    )

    class Meta:
        model = TeleCMICampaign
        fields = [
            'id', 'tenant_id', 'telecmi_campaign_id', 'name', 'is_active',
            'timezone', 'start_date', 'end_date', 'start_time', 'end_time',
            'call_interval', 'ring_rule', 'agent_user_ids',
            'source_group', 'source_group_id',
            'lead_count', 'leads_called', 'telecmi_lead_list_name', 'notes',
            'created_by_id', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'tenant_id', 'telecmi_campaign_id', 'lead_count',
            'leads_called', 'telecmi_lead_list_name', 'created_by_id',
            'created_at', 'updated_at',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Scope the writable source_group_id choices to the caller's tenant so a
        # campaign can only be seeded from a group it owns.
        request = self.context.get('request')
        tenant_id = getattr(request, 'tenant_id', None) if request else None
        if tenant_id is not None and 'source_group_id' in self.fields:
            self.fields['source_group_id'].queryset = LeadGroup.objects.filter(
                tenant_id=tenant_id
            )


class CampaignLeadPushSerializer(serializers.Serializer):
    """POST body for /api/telephony/campaigns/<pk>/push-leads/"""
    lead_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=False,
        help_text='CRM Lead IDs to push into this campaign',
    )


class CampaignGroupPushSerializer(serializers.Serializer):
    """POST body for /api/telephony/campaigns/<pk>/push-group/"""
    group_id = serializers.IntegerField(
        help_text='CRM LeadGroup ID whose members seed this campaign',
    )
