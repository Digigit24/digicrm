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


class RecordingStorageStatusEnum(models.TextChoices):
    TELECMI = 'telecmi', 'Available from TeleCMI'
    PENDING = 'pending', 'Waiting to archive'
    ARCHIVING = 'archiving', 'Archiving to Zata'
    ARCHIVED = 'archived', 'Archived in Zata'
    FAILED = 'failed', 'Archive failed'


class SMSStatusEnum(models.TextChoices):
    SENT = 'sent', 'Sent'
    FAILED = 'failed', 'Failed'


# TeleCMI login tokens last 24h; refresh at 20 so a long call never straddles
# an expiry. Shared by the per-user agent and the tenant default extension.
TOKEN_MAX_AGE_SECONDS = 72000


def token_is_stale(token, obtained_at):
    """True when a cached TeleCMI login token is missing or past refresh age."""
    if not token or not obtained_at:
        return True
    return (timezone.now() - obtained_at).total_seconds() > TOKEN_MAX_AGE_SECONDS


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
    dek_wrapped = models.TextField(
        blank=True,
        default='',
        help_text=(
            "This tenant's data-encryption key, itself encrypted with "
            'TELECMI_MASTER_KEY. Empty means the row still uses the legacy '
            'shared key.'
        ),
    )
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
    default_agent_id = models.CharField(
        max_length=100,
        blank=True,
        default='',
        help_text=(
            'Shared TeleCMI extension (e.g. 103_1111112) that every user of '
            'this tenant logs the browser softphone in with when they have no '
            'personal TeleCMIAgent row. The app_id/secret are tenant-wide, so '
            'this extension is too.'
        ),
    )
    default_agent_password_encrypted = models.TextField(
        blank=True,
        default='',
        help_text=(
            "Password for `default_agent_id`, encrypted with this tenant's "
            'DEK — the same envelope scheme as `secret_encrypted`.'
        ),
    )
    default_agent_token = models.TextField(
        null=True,
        blank=True,
        help_text='Cached /v2/user/login token for the shared default extension.',
    )
    default_agent_token_obtained_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When default_agent_token was last obtained.',
    )
    default_agent_verified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Last time TeleCMI accepted default_agent_id/password.',
    )
    default_agent_verify_error = models.TextField(
        blank=True,
        default='',
        help_text=(
            'Why the last verification of the default extension did not '
            'succeed. Set when TeleCMI was unreachable at save time, so the '
            'credential is stored but flagged rather than silently trusted.'
        ),
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

    @property
    def has_default_agent(self):
        """True when this tenant has a usable shared softphone extension."""
        return bool(self.default_agent_id and self.default_agent_password_encrypted)

    def is_default_token_stale(self):
        return token_is_stale(self.default_agent_token, self.default_agent_token_obtained_at)


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
    cached_token = models.TextField(
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
        return token_is_stale(self.cached_token, self.token_obtained_at)


class TeleCMICallingProfile(models.Model):
    """
    A named TeleCMI calling identity ("calling profile") a tenant can hand out.

    Why this exists
    ---------------
    `TeleCMICredential.default_agent_id` gave a tenant exactly *one* shared
    softphone extension, settable only by editing the credential row. Teams that
    own two TeleCMI numbers therefore had no way to say "support answers on the
    support line, sales on the sales line" — and there was no admin surface for
    entering an extension password at all, so in practice nearly every user hit
    424 `no_agent`.

    A profile is that missing first-class object: a label a human recognises, the
    TeleCMI extension behind it, the SIP password (encrypted), and the caller ID
    that extension should present. Profiles are assigned to users through
    `TeleCMIProfileAssignment`; one profile per tenant may be flagged
    `is_default` and is used by anyone with no explicit assignment.

    On the password
    ---------------
    Stored under the same envelope scheme as every other TeleCMI secret
    (`telephony/services/crypto.py`): encrypted with a per-tenant DEK which is
    itself wrapped by `TELECMI_MASTER_KEY`. `dek_wrapped` is seeded from the
    tenant's `TeleCMICredential` when one exists, so a tenant keeps a single
    data key across its credential and all of its profiles.
    """

    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    label = models.CharField(
        max_length=150,
        help_text='Human name for this calling identity, e.g. "Sales line".',
    )
    telecmi_user_id = models.CharField(
        max_length=100,
        help_text='TeleCMI extension this profile logs in as, e.g. 103_1111112.',
    )
    password_encrypted = models.TextField(
        blank=True,
        default='',
        help_text="SIP password for the extension, encrypted with this tenant's DEK.",
    )
    dek_wrapped = models.TextField(
        blank=True,
        default='',
        help_text=(
            "This tenant's data-encryption key, itself encrypted with "
            'TELECMI_MASTER_KEY. Shared with the tenant credential row when one '
            'exists.'
        ),
    )
    caller_id = models.CharField(
        max_length=30,
        null=True,
        blank=True,
        help_text=(
            'PSTN number this profile should present. Pushed to TeleCMI with '
            'POST /v2/set_callerid when a session resolves to this profile — '
            'caller ID is a property of the extension, not of a single call.'
        ),
    )
    is_default = models.BooleanField(
        default=False,
        help_text=(
            'Used by any user of this tenant with no explicit assignment. At '
            'most one profile per tenant may set this.'
        ),
    )
    is_active = models.BooleanField(default=True)
    cached_token = models.TextField(
        null=True,
        blank=True,
        help_text='Cached /v2/user/login token for this extension.',
    )
    token_obtained_at = models.DateTimeField(null=True, blank=True)
    caller_id_pushed_value = models.CharField(
        max_length=30,
        null=True,
        blank=True,
        help_text=(
            'Last caller ID we successfully pushed to TeleCMI for this '
            'extension. TeleCMI exposes no "currently active" flag, so this is '
            'the only record of it — and it keeps the softphone path from '
            're-pushing an unchanged value on every page load.'
        ),
    )
    verified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Last time TeleCMI accepted this extension and password.',
    )
    verify_error = models.TextField(
        blank=True,
        default='',
        help_text=(
            'Why the last verification did not succeed. Set when TeleCMI was '
            'unreachable, so the profile is stored but flagged rather than '
            'silently trusted.'
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'telephony_calling_profiles'
        ordering = ['-is_default', 'label']
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_tel_profile_tenant'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['tenant_id', 'telecmi_user_id'],
                name='uniq_tel_profile_tenant_ext',
            ),
            # Partial index: many non-default profiles are fine, one default.
            models.UniqueConstraint(
                fields=['tenant_id'],
                condition=models.Q(is_default=True),
                name='uniq_tel_profile_one_default',
            ),
        ]

    def __str__(self):
        return f'{self.label} ({self.telecmi_user_id})'

    @property
    def has_password(self):
        return bool(self.password_encrypted)

    @property
    def is_usable(self):
        """True when this profile can actually log a softphone in."""
        return bool(self.is_active and self.telecmi_user_id and self.password_encrypted)

    def is_token_stale(self):
        return token_is_stale(self.cached_token, self.token_obtained_at)


class TeleCMIProfileAssignment(models.Model):
    """
    Which calling profile a given CRM user should use.

    Kept as its own table rather than a FK on `TeleCMIAgent` because an agent row
    carries its own extension and password (both NOT NULL) and *wins* the
    softphone resolution outright. Hanging an assignment off it would mean either
    relaxing those columns on a live table or minting placeholder agent rows that
    would then shadow the very profile they point at. A dedicated row is purely
    additive and leaves the existing resolution order untouched.
    """

    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    user_id = models.UUIDField(db_index=True)
    profile = models.ForeignKey(
        TeleCMICallingProfile,
        on_delete=models.CASCADE,
        related_name='assignments',
        db_column='profile_id',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'telephony_calling_profile_assignments'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant_id', 'user_id'],
                name='uniq_tel_profile_assign_user',
            ),
        ]
        indexes = [
            models.Index(
                fields=['tenant_id', 'user_id'], name='idx_tel_profile_assign'
            ),
        ]

    def __str__(self):
        return f'user {self.user_id} -> {self.profile_id}'


class ZataStorageCredential(models.Model):
    """Tenant-owned private Zata S3 configuration for call recordings."""

    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(unique=True, db_index=True)
    endpoint_url = models.URLField(default='https://idr01.zata.ai')
    bucket_name = models.CharField(max_length=255)
    access_key_id = models.CharField(max_length=255)
    secret_access_key_encrypted = models.TextField()
    dek_wrapped = models.TextField(blank=True, default='')
    object_prefix = models.CharField(
        max_length=255,
        default='telephony/recordings',
        help_text='Key prefix inside the private bucket.',
    )
    region_name = models.CharField(max_length=64, default='us-east-1')
    is_active = models.BooleanField(default=True)
    last_tested_at = models.DateTimeField(null=True, blank=True)
    last_test_error = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'telephony_zata_storage_credentials'
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_tel_zata_tenant'),
        ]

    def __str__(self):
        return f'Zata storage for tenant {self.tenant_id}'


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
    agent_user_id = models.UUIDField(
        null=True, blank=True, db_index=True,
        help_text='CRM user UUID who handled this call (no FK constraint — '
                   'user records live outside this app, same pattern as '
                   'sent_by_user_id / created_by_id elsewhere in this app)'
    )
    recording_file = models.CharField(
        max_length=300,
        null=True,
        blank=True,
        help_text='TeleCMI recording filename (e.g. demo_1111113.wav)'
    )
    recording_storage_status = models.CharField(
        max_length=20,
        choices=RecordingStorageStatusEnum.choices,
        default=RecordingStorageStatusEnum.TELECMI,
    )
    recording_object_key = models.CharField(max_length=512, null=True, blank=True)
    recording_content_type = models.CharField(max_length=100, null=True, blank=True)
    recording_size = models.BigIntegerField(null=True, blank=True)
    recording_sha256 = models.CharField(max_length=64, null=True, blank=True)
    recording_archived_at = models.DateTimeField(null=True, blank=True)
    recording_archive_error = models.TextField(blank=True, default='')
    recording_archive_attempts = models.PositiveIntegerField(default=0)
    raw_payload = models.JSONField(null=True, blank=True)
    # Track how this record was created
    synced_via = models.CharField(
        max_length=20,
        default='webhook',
        help_text='webhook or manual_sync'
    )
    # Track if we already created a CRM Activity for this call
    activity_created = models.BooleanField(default=False)

    # Outbound call dedup fields
    call_leg = models.CharField(max_length=1, null=True, blank=True, help_text='"a" or "b" for outbound legs')
    telecmi_call_id = models.CharField(max_length=64, null=True, blank=True, db_index=True, help_text='Links Leg A and Leg B of outbound calls')
    conversation_uuid = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    request_id = models.CharField(max_length=128, null=True, blank=True, db_index=True)
    # Routing metadata
    ivr_name = models.CharField(max_length=128, null=True, blank=True)
    team_name = models.CharField(max_length=128, null=True, blank=True)
    # Voicemail support (inbound missed)
    is_voicemail = models.BooleanField(default=False)
    voicemail_filename = models.CharField(max_length=256, null=True, blank=True)
    wait_seconds = models.IntegerField(null=True, blank=True, help_text='Ring wait time for missed calls')
    hangup_reason = models.CharField(max_length=32, null=True, blank=True)
    # Disposition (agent-set after call)
    call_outcome = models.CharField(
        max_length=32, null=True, blank=True,
        help_text='Agent disposition: interested/not_interested/follow_up/callback/converted/dnd'
    )
    call_outcome_note = models.CharField(max_length=512, null=True, blank=True)
    call_outcome_set_at = models.DateTimeField(null=True, blank=True)

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
            models.Index(fields=['tenant_id', 'agent_user_id', 'call_time'], name='idx_tel_calls_agent_time'),
            models.Index(fields=['telecmi_call_id', 'call_leg'], name='idx_tel_calls_legs'),
            models.Index(fields=['conversation_uuid'], name='idx_tel_calls_conv'),
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


class CampaignRingRuleEnum(models.TextChoices):
    RING_ALL = 'ring-all', 'Ring All'
    ROUND_ROBIN = 'round-robin', 'Round Robin'


class TeleCMICampaign(models.Model):
    """Auto-dialer campaign backed by TeleCMI Campaigns API."""
    INTERVAL_CHOICES = [(10,'10s'),(20,'20s'),(30,'30s'),(40,'40s'),(50,'50s'),(60,'1 min'),(120,'2 min')]

    tenant_id = models.UUIDField(db_index=True)
    telecmi_campaign_id = models.CharField(max_length=64, null=True, blank=True, help_text='UUID from TeleCMI after creation')
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=False)
    timezone = models.CharField(max_length=64, default='Asia/Kolkata')
    start_date = models.DateField()
    end_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    call_interval = models.IntegerField(default=30, choices=INTERVAL_CHOICES)
    ring_rule = models.CharField(max_length=20, default='round-robin', choices=CampaignRingRuleEnum.choices)
    agent_user_ids = models.JSONField(default=list, help_text='CRM user UUIDs assigned to this campaign')
    source_group = models.ForeignKey(
        'crm.LeadGroup',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='telephony_campaigns',
        db_column='source_group_id',
        help_text='CRM lead group this campaign is seeded from, if any',
    )
    lead_count = models.IntegerField(default=0)
    leads_called = models.IntegerField(default=0)
    telecmi_lead_list_name = models.CharField(max_length=255, null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'telephony_campaigns'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_tel_camp_tenant'),
            models.Index(fields=['tenant_id', 'is_active'], name='idx_tel_camp_active'),
        ]

    def __str__(self):
        return f'Campaign: {self.name} (tenant {self.tenant_id})'


class DevicePlatformEnum(models.TextChoices):
    ANDROID = 'android', 'Android'
    IOS = 'ios', 'iOS'


class DeviceToken(models.Model):
    """
    A push token for a mobile client, used to wake it for an incoming call.

    The SIP/WebRTC socket a mobile softphone holds against the SBC does not
    survive the OS suspending/killing the app in the background, so a
    "waiting" live-event alone (see `LiveEventWebhookView`) cannot reach a
    backgrounded phone. This is the address book the webhook handler
    consults to send a wake-up push (FCM data message / iOS VoIP push)
    before the SBC's own INVITE retransmits time out.

    One user can hold multiple rows (multiple installs/devices); the most
    recently updated one per (tenant, user, platform) is what actually
    matters, but old rows are left to expire naturally on next login rather
    than being pruned here.
    """
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    user_id = models.UUIDField(db_index=True)
    platform = models.CharField(max_length=10, choices=DevicePlatformEnum.choices)
    fcm_token = models.CharField(max_length=512)
    app = models.CharField(
        max_length=64, default='crmflutter',
        help_text='Which client registered this token, e.g. crmflutter or celiyocrmmobileapp',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'telephony_device_tokens'
        unique_together = [('tenant_id', 'fcm_token')]
        indexes = [
            models.Index(fields=['tenant_id', 'user_id'], name='idx_tel_devtok_user'),
        ]

    def __str__(self):
        return f'{self.platform} token for user {self.user_id} ({self.app})'
