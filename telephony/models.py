from django.db import models
from django.utils import timezone


class SBCRegionEnum(models.TextChoices):
    ASIA = 'sg', 'Asia (Singapore)'
    INDIA = 'ind', 'India'
    US = 'us', 'Americas'
    EUROPE = 'uk', 'Europe'


class CallDirectionEnum(models.TextChoices):
    INBOUND = 'inbound', 'Inbound'
    OUTBOUND = 'outbound', 'Outbound'


class CallTypeEnum(models.TextChoices):
    MISSED = 'missed', 'Missed'
    ANSWERED = 'answered', 'Answered'


class SMSStatusEnum(models.TextChoices):
    SENT = 'sent', 'Sent'
    FAILED = 'failed', 'Failed'


SBC_HOST_MAP = {
    SBCRegionEnum.ASIA: 'sbcsg.telecmi.com',
    SBCRegionEnum.INDIA: 'sbcind.telecmi.com',
    SBCRegionEnum.US: 'sbcus.telecmi.com',
    SBCRegionEnum.EUROPE: 'sbcuk.telecmi.com',
}


class TeleCMICredential(models.Model):
    """
    Tenant-level TeleCMI account credentials.
    One record per tenant. Stores the TeleCMI app_id/secret and default config.
    """
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(unique=True, db_index=True)
    app_id = models.CharField(max_length=100, help_text='TeleCMI App ID (appid)')
    secret_encrypted = models.TextField(help_text='Encrypted TeleCMI app secret')
    sbc_region = models.CharField(
        max_length=10,
        choices=SBCRegionEnum.choices,
        default=SBCRegionEnum.INDIA,
        help_text='SBC region for WebRTC SDK login'
    )
    default_caller_id = models.CharField(
        max_length=30,
        null=True,
        blank=True,
        help_text='Default caller ID displayed on outgoing calls'
    )
    webhook_secret = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        help_text='Optional shared secret to verify incoming TeleCMI webhooks'
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'telephony_credentials'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_tel_cred_tenant'),
        ]

    def __str__(self):
        return f'TeleCMI credential for tenant {self.tenant_id}'

    @property
    def sbc_host(self):
        return SBC_HOST_MAP.get(self.sbc_region, 'sbcind.telecmi.com')


class TeleCMIAgent(models.Model):
    """
    Per-user TeleCMI agent credentials and cached login token.
    Each CRM user who uses telephony has one record per tenant.
    The token is fetched via POST /v2/user/login and cached here.
    """
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    user_id = models.UUIDField(db_index=True)
    telecmi_user_id = models.CharField(
        max_length=100,
        help_text='TeleCMI user ID (e.g. 103_1111112)'
    )
    password_encrypted = models.TextField(help_text='Encrypted TeleCMI agent password')
    cached_token = models.CharField(
        max_length=200,
        null=True,
        blank=True,
        help_text='Cached login token from /v2/user/login'
    )
    token_obtained_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the cached token was last obtained'
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'telephony_agents'
        unique_together = [('tenant_id', 'user_id')]
        indexes = [
            models.Index(fields=['tenant_id', 'user_id'], name='idx_tel_agent_tenant_user'),
        ]

    def __str__(self):
        return f'TeleCMI agent {self.telecmi_user_id} (tenant {self.tenant_id})'

    def is_token_stale(self):
        """Token is considered stale after 20 hours (TeleCMI tokens last 24h)."""
        if not self.token_obtained_at or not self.cached_token:
            return True
        age = timezone.now() - self.token_obtained_at
        return age.total_seconds() > 72000  # 20 hours


class CallLog(models.Model):
    """
    Normalized TeleCMI CDR record. Populated by webhook (real-time) or manual sync.
    Each record corresponds to one call on the TeleCMI platform.
    """
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    cmiuid = models.CharField(
        max_length=100,
        db_index=True,
        help_text='TeleCMI unique call identifier'
    )
    direction = models.CharField(max_length=10, choices=CallDirectionEnum.choices)
    call_type = models.CharField(max_length=10, choices=CallTypeEnum.choices)
    from_number = models.CharField(max_length=30, db_index=True)
    to_number = models.CharField(max_length=30, db_index=True)
    duration = models.IntegerField(default=0, help_text='Total call duration in seconds')
    billed_sec = models.IntegerField(default=0, help_text='Billed duration in seconds')
    rate = models.DecimalField(
        max_digits=10, decimal_places=4, default=0,
        help_text='Per-second call rate in USD'
    )
    caller_name = models.CharField(max_length=200, null=True, blank=True)
    telecmi_notes = models.JSONField(
        null=True,
        blank=True,
        help_text='Notes array from TeleCMI CDR response'
    )
    call_time = models.DateTimeField(db_index=True, help_text='When the call occurred (UTC)')
    # Link to CRM entities - nullable because call may come from unknown number
    lead_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    agent_user_id = models.UUIDField(null=True, blank=True, db_index=True)
    # Track how this record was created
    synced_via = models.CharField(
        max_length=20,
        default='webhook',
        help_text='webhook or manual_sync'
    )
    # Track if we already created a CRM Activity for this call
    activity_created = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'telephony_call_logs'
        # cmiuid is unique per tenant
        unique_together = [('tenant_id', 'cmiuid')]
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_tel_calls_tenant'),
            models.Index(fields=['tenant_id', 'direction'], name='idx_tel_calls_direction'),
            models.Index(fields=['tenant_id', 'call_type'], name='idx_tel_calls_type'),
            models.Index(fields=['tenant_id', 'call_time'], name='idx_tel_calls_time'),
            models.Index(fields=['from_number'], name='idx_tel_calls_from'),
            models.Index(fields=['to_number'], name='idx_tel_calls_to'),
            models.Index(fields=['lead_id'], name='idx_tel_calls_lead'),
        ]

    def __str__(self):
        return f'{self.get_direction_display()} {self.get_call_type_display()} - {self.from_number} ({self.cmiuid})'


class SMSLog(models.Model):
    """
    Record of every SMS sent via TeleCMI from this CRM.
    """
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    from_number = models.CharField(max_length=30, null=True, blank=True)
    to_number = models.CharField(max_length=30, db_index=True)
    message = models.TextField()
    status = models.CharField(
        max_length=10,
        choices=SMSStatusEnum.choices,
        default=SMSStatusEnum.SENT
    )
    lead_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    sent_by_user_id = models.UUIDField(null=True, blank=True)
    telecmi_response = models.JSONField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'telephony_sms_logs'
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_tel_sms_tenant'),
            models.Index(fields=['to_number'], name='idx_tel_sms_to'),
            models.Index(fields=['lead_id'], name='idx_tel_sms_lead'),
            models.Index(fields=['tenant_id', 'created_at'], name='idx_tel_sms_tenant_time'),
        ]

    def __str__(self):
        return f'SMS to {self.to_number} ({self.get_status_display()})'
