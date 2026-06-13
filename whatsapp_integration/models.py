from django.db import models
from crm.models import Lead, LeadGroup


class CampaignStatusEnum(models.TextChoices):
    DRAFT = 'DRAFT', 'Draft'
    SCHEDULED = 'SCHEDULED', 'Scheduled'
    RUNNING = 'RUNNING', 'Running'
    COMPLETED = 'COMPLETED', 'Completed'
    FAILED = 'FAILED', 'Failed'


class SequenceEnrollmentStatusEnum(models.TextChoices):
    ACTIVE = 'ACTIVE', 'Active'
    PAUSED = 'PAUSED', 'Paused'
    COMPLETED = 'COMPLETED', 'Completed'
    OPTED_OUT = 'OPTED_OUT', 'Opted Out'
    REPLIED = 'REPLIED', 'Replied — stopped on reply'


class AgentActionTypeEnum(models.TextChoices):
    SEND_WHATSAPP = 'SEND_WHATSAPP', 'Send WhatsApp'
    ENROLL_SEQUENCE = 'ENROLL_SEQUENCE', 'Enroll in Sequence'
    CREATE_CAMPAIGN = 'CREATE_CAMPAIGN', 'Create Campaign'
    UPDATE_LEAD_STATUS = 'UPDATE_LEAD_STATUS', 'Update Lead Status'
    LOG_ACTIVITY = 'LOG_ACTIVITY', 'Log Activity'


class AgentActionStatusEnum(models.TextChoices):
    SUCCESS = 'SUCCESS', 'Success'
    FAILED = 'FAILED', 'Failed'


class WhatsAppVendorConfig(models.Model):
    """
    Stores the Laravel WhatsApp API credentials for each DigiCRM tenant.
    Each tenant maps to one Laravel vendor account.

    Agents and the adapter service read vendor_uid and api_token from here
    before making any call to the Laravel WhatsApp adapter.
    """
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True, unique=True)
    vendor_uid = models.CharField(max_length=100, help_text='Laravel vendor _uid')
    api_token = models.TextField(help_text='Laravel vendor API access token')
    api_base_url = models.TextField(
        default='https://whatsappapi.celiyo.com/api',
        help_text='Base URL of the Laravel WhatsApp API'
    )
    webhook_secret = models.TextField(
        null=True, blank=True,
        help_text='Shared secret for verifying inbound webhook calls from Laravel/n8n'
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'whatsapp_vendor_configs'

    def __str__(self):
        return f"Vendor config for tenant {self.tenant_id}"


class WhatsAppCampaign(models.Model):
    """
    A WhatsApp campaign planned in DigiCRM and executed via the Laravel adapter.

    DigiCRM owns campaign planning (group selection, template choice, scheduling).
    Laravel owns execution and delivery tracking.
    After launch, laravel_campaign_uid is populated and delivery stats are fetched
    from Laravel via the adapter analytics endpoint.

    Agents use this model to create, schedule, and monitor campaigns without
    ever touching the Laravel system directly.
    """
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    name = models.TextField(help_text='Campaign display name')
    lead_group = models.ForeignKey(
        LeadGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='whatsapp_campaigns',
        db_column='lead_group_id',
        help_text='DigiCRM lead group used as the contact list for this campaign'
    )
    template_uid = models.CharField(
        max_length=100,
        help_text='Laravel WhatsApp template _uid'
    )
    template_name = models.TextField(
        null=True, blank=True,
        help_text='Human-readable template name (cached from Laravel)'
    )
    template_components = models.JSONField(
        default=list,
        help_text='Template variable components array sent to Laravel'
    )
    status = models.CharField(
        max_length=20,
        choices=CampaignStatusEnum.choices,
        default=CampaignStatusEnum.DRAFT
    )
    scheduled_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When to fire the campaign. Null means send immediately on launch.'
    )
    launched_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Actual datetime the campaign was submitted to Laravel'
    )
    laravel_campaign_uid = models.CharField(
        max_length=100, null=True, blank=True, db_index=True,
        help_text='Campaign _uid returned by the Laravel adapter after launch'
    )
    laravel_group_uid = models.CharField(
        max_length=100, null=True, blank=True,
        help_text='Temporary Laravel contact group uid created for this campaign'
    )
    total_contacts = models.IntegerField(
        default=0,
        help_text='Number of contacts in the campaign at launch time'
    )
    created_by = models.UUIDField(db_index=True)
    notes = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'whatsapp_campaigns'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_wa_campaigns_tenant'),
            models.Index(fields=['status'], name='idx_wa_campaigns_status'),
            models.Index(fields=['scheduled_at'], name='idx_wa_campaigns_scheduled'),
        ]

    def __str__(self):
        return f"{self.name} ({self.status})"


class WhatsAppSequence(models.Model):
    """
    A follow-up sequence: an ordered series of WhatsApp template messages
    sent automatically at defined intervals after a lead is enrolled.

    Example: Day 0 → intro template, Day 2 → follow-up, Day 5 → case study,
    Day 10 → final nudge.

    Agents enroll leads into sequences to automate all follow-up without
    any manual effort.
    """
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    name = models.TextField(help_text='Sequence display name, e.g. Dental Follow-Up Sequence')
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    stop_on_reply = models.BooleanField(
        default=True,
        help_text='If true, sequence stops automatically when the lead replies'
    )
    created_by = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'whatsapp_sequences'
        ordering = ['name']
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_wa_sequences_tenant'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['tenant_id', 'name'],
                name='unique_sequence_name_per_tenant'
            )
        ]

    def __str__(self):
        return self.name


class WhatsAppSequenceStep(models.Model):
    """
    A single step in a WhatsApp sequence.

    step_number defines the order. delay_days defines how many days after the
    previous step this step fires (0 = same day as enrollment or previous step).

    template_variable_mapping is a JSON object mapping template variable
    positions to lead field names:
      { "1": "name", "2": "company", "3": "phone" }
    """
    id = models.BigAutoField(primary_key=True)
    sequence = models.ForeignKey(
        WhatsAppSequence,
        on_delete=models.CASCADE,
        related_name='steps',
        db_column='sequence_id'
    )
    step_number = models.IntegerField(help_text='Order of this step, starting at 1')
    delay_days = models.IntegerField(
        default=0,
        help_text='Days to wait after the previous step (or enrollment for step 1)'
    )
    template_uid = models.CharField(
        max_length=100,
        help_text='Laravel WhatsApp template _uid for this step'
    )
    template_name = models.TextField(
        null=True, blank=True,
        help_text='Human-readable template name (cached for display)'
    )
    template_variable_mapping = models.JSONField(
        default=dict,
        help_text='Maps template variable positions to lead field names. '
                  'Example: {"1": "name", "2": "company"}'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'whatsapp_sequence_steps'
        ordering = ['step_number']
        unique_together = [['sequence', 'step_number']]
        indexes = [
            models.Index(fields=['sequence'], name='idx_wa_seq_steps_seq'),
        ]

    def __str__(self):
        return f"Step {self.step_number} of {self.sequence.name} (+{self.delay_days}d)"


class LeadSequenceEnrollment(models.Model):
    """
    Tracks which leads are enrolled in which sequences and at which step.

    The Celery beat task reads active enrollments where next_step_at <= now
    and fires the appropriate template message via the Laravel adapter.

    When a lead replies (inbound webhook from n8n → DigiCRM), the enrollment
    status is set to REPLIED and no further steps are sent.
    """
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    lead = models.ForeignKey(
        Lead,
        on_delete=models.CASCADE,
        related_name='sequence_enrollments',
        db_column='lead_id'
    )
    sequence = models.ForeignKey(
        WhatsAppSequence,
        on_delete=models.CASCADE,
        related_name='enrollments',
        db_column='sequence_id'
    )
    current_step = models.ForeignKey(
        WhatsAppSequenceStep,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='active_enrollments',
        db_column='current_step_id',
        help_text='The step that was most recently sent'
    )
    status = models.CharField(
        max_length=20,
        choices=SequenceEnrollmentStatusEnum.choices,
        default=SequenceEnrollmentStatusEnum.ACTIVE
    )
    enrolled_at = models.DateTimeField(auto_now_add=True)
    next_step_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When the next step message should fire'
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    stopped_reason = models.TextField(
        null=True, blank=True,
        help_text='Why the sequence stopped (replied, opted_out, manual, etc.)'
    )
    enrolled_by = models.UUIDField(
        null=True, blank=True,
        help_text='User or agent who enrolled this lead'
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'lead_sequence_enrollments'
        ordering = ['-enrolled_at']
        unique_together = [['lead', 'sequence']]
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_lse_tenant'),
            models.Index(fields=['status'], name='idx_lse_status'),
            models.Index(fields=['next_step_at'], name='idx_lse_next_step_at'),
            models.Index(fields=['lead'], name='idx_lse_lead'),
        ]

    def __str__(self):
        return f"{self.lead.name} → {self.sequence.name} [{self.status}]"


class AgentActionLog(models.Model):
    """
    Audit log for every write action performed by the Claude agent.

    Every POST to /api/agent/actions/* creates a record here.
    This provides full traceability: what did the agent do, when, with what
    input, and what was the result.
    """
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    action_type = models.CharField(
        max_length=30,
        choices=AgentActionTypeEnum.choices
    )
    payload_in = models.JSONField(help_text='Input payload sent to the action endpoint')
    payload_out = models.JSONField(
        null=True, blank=True,
        help_text='Response payload returned after executing the action'
    )
    triggered_by = models.CharField(
        max_length=100,
        default='claude-agent',
        help_text='Who triggered this action: claude-agent or user:<uuid>'
    )
    status = models.CharField(
        max_length=10,
        choices=AgentActionStatusEnum.choices,
        default=AgentActionStatusEnum.SUCCESS
    )
    error_message = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'agent_action_logs'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_aal_tenant'),
            models.Index(fields=['action_type'], name='idx_aal_action_type'),
            models.Index(fields=['created_at'], name='idx_aal_created_at'),
        ]

    def __str__(self):
        return f"{self.action_type} by {self.triggered_by} at {self.created_at}"
