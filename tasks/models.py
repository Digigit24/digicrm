from django.db import models

from crm.models import Lead, PriorityEnum


class TaskStatusEnum(models.TextChoices):
    TODO = 'TODO', 'To Do'
    IN_PROGRESS = 'IN_PROGRESS', 'In Progress'
    DONE = 'DONE', 'Done'
    CANCELLED = 'CANCELLED', 'Cancelled'


class TaskRelatedTypeEnum(models.TextChoices):
    """What CRM object a task hangs off, if any.

    Deliberately an explicit type+id pair rather than a Django
    ``GenericForeignKey``: contenttypes gives no way to express "and the row must
    belong to my tenant" in a single indexed query, and the extra join per row is
    not worth it for four known target models.  See ``tasks.services`` for the
    tenant-scoped resolver.
    """

    NONE = 'NONE', 'Not linked'
    LEAD = 'LEAD', 'Lead'
    PROJECT = 'PROJECT', 'Project'
    UNIT = 'UNIT', 'Unit'
    MEETING = 'MEETING', 'Meeting'


#: Terminal states -- a task in one of these is not "open" work.
CLOSED_TASK_STATUSES = (TaskStatusEnum.DONE, TaskStatusEnum.CANCELLED)


class Task(models.Model):
    """A unit of CRM work.

    Historically a task *had* to belong to a lead.  It no longer does: ``lead``
    is nullable and a task can instead point at a project, a unit, a meeting, or
    at nothing at all via ``related_type``/``related_id``.
    """

    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)

    # NOTE: ``lead`` is kept alongside ``related_type``/``related_id`` on purpose.
    # It is a real FK with real referential integrity, every existing row uses it,
    # and the ViewSet filterset, the MCP tools and both serializers select on it.
    # When ``related_type == 'LEAD'`` the two are kept in sync by ``save()``.
    # Do NOT "clean this up" by dropping the column -- the redundancy is the
    # backwards-compatibility contract.
    lead = models.ForeignKey(
        Lead,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tasks',
        db_column='lead_id'
    )
    title = models.TextField()
    description = models.TextField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=TaskStatusEnum.choices,
        default=TaskStatusEnum.TODO
    )
    priority = models.CharField(
        max_length=10,
        choices=PriorityEnum.choices,
        default=PriorityEnum.MEDIUM
    )

    # ---- CRM-wide linkage ---------------------------------------------------
    related_type = models.CharField(
        max_length=16,
        choices=TaskRelatedTypeEnum.choices,
        default=TaskRelatedTypeEnum.NONE,
        db_index=True,
        help_text='Which CRM object this task is about: LEAD, PROJECT, UNIT, '
                  'MEETING, or NONE for a standalone task.'
    )
    related_id = models.BigIntegerField(
        null=True,
        blank=True,
        db_index=True,
        help_text='Primary key of the related_type object. Always resolved '
                  'tenant-scoped; an id belonging to another tenant resolves to '
                  'nothing rather than leaking a name.'
    )

    # ---- scheduling ---------------------------------------------------------
    start_date = models.DateTimeField(
        null=True, blank=True,
        help_text='UTC instant the work is meant to start. Null = start whenever.'
    )
    due_date = models.DateTimeField(null=True, blank=True)
    is_all_day = models.BooleanField(
        default=False,
        help_text='True when start_date/due_date carry a date only; the UI renders '
                  'them against ``timezone`` rather than as clock times.'
    )
    timezone = models.CharField(
        max_length=64, default='UTC',
        help_text='IANA timezone this task was authored in (e.g. "Asia/Kolkata"). '
                  'start_date/due_date stay UTC instants; this mirrors '
                  'meetings.Meeting.timezone and is what an rrule expands against.'
    )

    # ---- recurrence ---------------------------------------------------------
    rrule = models.TextField(
        null=True, blank=True,
        help_text='RFC 5545 RRULE without the "RRULE:" prefix, e.g. '
                  '"FREQ=WEEKLY;BYDAY=MO". Parsed by meetings.recurrence. Null = '
                  'a one-off task. Completing a recurring task spawns the next '
                  'occurrence instead of ending the series.'
    )
    recurrence_end_at = models.DateTimeField(
        null=True, blank=True, db_index=True,
        help_text='Denormalised UTC instant of the last possible occurrence '
                  '(resolved from UNTIL/COUNT at save time). Null = open-ended.'
    )
    recurring_parent = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.CASCADE,
        related_name='occurrences',
        help_text='The first task in a recurring series. Set on every spawned '
                  'occurrence; null on the series head itself.'
    )

    # ---- assignment ---------------------------------------------------------
    assignee_user_id = models.UUIDField(db_index=True, null=True, blank=True)
    reporter_user_id = models.UUIDField(db_index=True, null=True, blank=True)
    owner_user_id = models.UUIDField(db_index=True)

    # ---- follow-up ----------------------------------------------------------
    reminder_minutes_before = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Minutes before due_date to raise a notifications.Reminder. '
                  'Null = no reminder. Materialised by '
                  'tasks.tasks.materialize_task_reminders and delivered by the '
                  'existing notifications.tasks.dispatch_due_reminders beat job.'
    )
    snoozed_until = models.DateTimeField(
        null=True, blank=True,
        help_text='Set by the snooze endpoint; the reminder materialiser skips a '
                  'task until this instant has passed.'
    )

    # ---- presentation -------------------------------------------------------
    order_index = models.IntegerField(
        default=0, db_index=True,
        help_text='Manual sort position within a board/list. Set by the reorder '
                  'endpoint; lower sorts first.'
    )
    labels = models.JSONField(
        default=list, blank=True,
        help_text='Free-form list of string labels, e.g. ["urgent", "documents"].'
    )

    # ---- legacy -------------------------------------------------------------
    checklist = models.JSONField(
        null=True, blank=True,
        help_text='DEPRECATED raw JSON checklist. Superseded by TaskChecklistItem '
                  'rows; kept (and still written by the data migration reverse) '
                  'only so the change is reversible. Read checklist_items instead.'
    )
    attachments_count = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'tasks'
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_tasks_tenant_id'),
            models.Index(fields=['lead'], name='idx_tasks_lead_id'),
            models.Index(fields=['status'], name='idx_tasks_status'),
            models.Index(fields=['priority'], name='idx_tasks_priority'),
            models.Index(fields=['assignee_user_id'], name='idx_tasks_assignee'),
            models.Index(fields=['reporter_user_id'], name='idx_tasks_reporter'),
            models.Index(fields=['owner_user_id'], name='idx_tasks_owner_user_id'),
            models.Index(fields=['tenant_id', 'related_type', 'related_id'],
                         name='idx_tasks_related'),
            models.Index(fields=['tenant_id', 'assignee_user_id', 'due_date'],
                         name='idx_tasks_my_day'),
            models.Index(fields=['tenant_id', 'order_index'], name='idx_tasks_order'),
        ]

    def __str__(self):
        # ``lead`` is nullable now -- a standalone task must still be printable.
        if self.lead_id and self.lead:
            return f"{self.title} - {self.lead.name}"
        return self.title

    # -- helpers -------------------------------------------------------------
    @property
    def is_recurring(self):
        return bool(self.rrule)

    @property
    def is_open(self):
        return self.status not in CLOSED_TASK_STATUSES

    def _sync_related(self):
        """Keep ``lead`` and ``related_type``/``related_id`` consistent.

        Rules, in order:

        * ``related_type == 'LEAD'`` with a ``related_id`` wins -- the FK follows it.
        * ``related_type == 'LEAD'`` with no ``related_id`` back-fills from ``lead``.
        * A bare ``lead`` (old-style create, ``related_type`` left at NONE) is
          promoted to ``LEAD`` linkage so new clients see it the new way.
        * ``related_type == 'NONE'`` clears any stale ``related_id``.

        A ``related_id`` pointing at another tenant's lead is refused (the FK is
        left alone), which is why the lookup is tenant-filtered here as well as
        in the serializer.
        """
        if self.related_type == TaskRelatedTypeEnum.LEAD:
            if self.related_id:
                if self.lead_id != self.related_id:
                    exists = Lead.objects.filter(
                        pk=self.related_id, tenant_id=self.tenant_id
                    ).exists()
                    if exists:
                        self.lead_id = self.related_id
                    else:
                        # Cross-tenant or dangling id: keep the FK truthful and
                        # drop the unresolvable pointer instead.
                        self.related_id = self.lead_id
            elif self.lead_id:
                self.related_id = self.lead_id
        elif self.related_type == TaskRelatedTypeEnum.NONE:
            if self.lead_id:
                self.related_type = TaskRelatedTypeEnum.LEAD
                self.related_id = self.lead_id
            else:
                self.related_id = None
        # PROJECT / UNIT / MEETING deliberately leave ``lead`` untouched: a task
        # about a unit can still legitimately belong to a lead's pipeline.

    def _sync_recurrence(self):
        from meetings import recurrence

        if not self.rrule:
            self.recurrence_end_at = None
            return
        anchor = self.start_date or self.due_date
        if not anchor:
            self.recurrence_end_at = None
            return
        self.recurrence_end_at = recurrence.compute_recurrence_end(
            self.rrule, anchor, self.timezone
        )

    def save(self, *args, **kwargs):
        from django.utils import timezone as dj_timezone

        if self.status == TaskStatusEnum.DONE and not self.completed_at:
            self.completed_at = dj_timezone.now()
        elif self.status != TaskStatusEnum.DONE:
            self.completed_at = None

        self._sync_related()
        self._sync_recurrence()

        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            # A partial save must still persist the fields we just derived.
            derived = {'completed_at', 'related_type', 'related_id', 'lead',
                       'recurrence_end_at'}
            kwargs['update_fields'] = list(set(update_fields) | derived)

        super().save(*args, **kwargs)


class TaskChecklistItem(models.Model):
    """One line of a task's checklist.

    Replaces ``Task.checklist``'s untyped JSON blob so an item can be toggled,
    reordered, and timestamped without rewriting the whole document (and without
    two concurrent PATCHes clobbering each other).
    """

    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, related_name='checklist_items'
    )
    text = models.TextField()
    is_done = models.BooleanField(default=False)
    order_index = models.IntegerField(default=0)
    done_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'task_checklist_items'
        ordering = ['order_index', 'id']
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_task_cli_tenant'),
            models.Index(fields=['task', 'order_index'], name='idx_task_cli_order'),
        ]

    def __str__(self):
        return self.text[:80]

    def save(self, *args, **kwargs):
        from django.utils import timezone as dj_timezone

        if self.is_done and not self.done_at:
            self.done_at = dj_timezone.now()
        elif not self.is_done:
            self.done_at = None

        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            kwargs['update_fields'] = list(set(update_fields) | {'done_at'})

        super().save(*args, **kwargs)
