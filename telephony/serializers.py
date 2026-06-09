from rest_framework import serializers
from common.mixins import TenantMixin
from telephony.models import TeleCMICredential, TeleCMIAgent, CallLog, SMSLog
from integrations.utils.encryption import encrypt_token


class TeleCMICredentialSerializer(TenantMixin):
    """
    Tenant-level TeleCMI account credentials.
    The secret is write-only; it is encrypted before storage.
    """
    secret = serializers.CharField(write_only=True, required=False, allow_blank=True)
    sbc_host = serializers.CharField(read_only=True)

    class Meta:
        model = TeleCMICredential
        fields = [
            'id', 'app_id', 'secret', 'sbc_region', 'sbc_host',
            'default_caller_id', 'webhook_secret', 'is_active',
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

    def create(self, validated_data):
        secret = validated_data.pop('secret', None)
        if secret:
            validated_data['secret_encrypted'] = encrypt_token(secret)
        elif not validated_data.get('secret_encrypted'):
            raise serializers.ValidationError({'secret': 'Secret is required when creating credentials.'})
        return super().create(validated_data)

    def update(self, instance, validated_data):
        secret = validated_data.pop('secret', None)
        if secret:
            validated_data['secret_encrypted'] = encrypt_token(secret)
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


class CallLogSerializer(serializers.ModelSerializer):
    """Read-only CDR record."""
    direction_display = serializers.CharField(source='get_direction_display', read_only=True)
    call_type_display = serializers.CharField(source='get_call_type_display', read_only=True)
    has_recording = serializers.SerializerMethodField()

    def get_has_recording(self, obj):
        return bool(obj.recording_file)

    class Meta:
        model = CallLog
        fields = [
            'id', 'cmiuid', 'direction', 'direction_display',
            'call_type', 'call_type_display',
            'from_number', 'to_number', 'duration', 'billed_sec', 'rate',
            'caller_name', 'telecmi_notes', 'call_time',
            'lead_id', 'agent_user_id', 'synced_via',
            'recording_file', 'has_recording', 'created_at',
        ]
        read_only_fields = fields


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
