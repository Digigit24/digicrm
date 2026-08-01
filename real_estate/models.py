from django.db import models

from crm.models import Lead


class ProjectTypeEnum(models.TextChoices):
    RESIDENTIAL = 'residential', 'Residential'
    COMMERCIAL = 'commercial', 'Commercial'
    MIXED = 'mixed', 'Mixed'
    PLOTTED = 'plotted', 'Plotted'


class ProjectStatusEnum(models.TextChoices):
    PLANNING = 'planning', 'Planning'
    UNDER_CONSTRUCTION = 'under_construction', 'Under Construction'
    READY_TO_MOVE = 'ready_to_move', 'Ready To Move'
    COMPLETED = 'completed', 'Completed'
    ON_HOLD = 'on_hold', 'On Hold'


class BlockTypeEnum(models.TextChoices):
    TOWER = 'tower', 'Tower'
    WING = 'wing', 'Wing'
    PHASE = 'phase', 'Phase'
    SECTOR = 'sector', 'Sector'
    BLOCK = 'block', 'Block'


class UnitTypeEnum(models.TextChoices):
    FLAT = 'flat', 'Flat'
    VILLA = 'villa', 'Villa'
    ROW_HOUSE = 'row_house', 'Row House'
    PLOT = 'plot', 'Plot'
    COMMERCIAL_SHOP = 'commercial_shop', 'Commercial Shop'
    COMMERCIAL_OFFICE = 'commercial_office', 'Commercial Office'
    OTHER = 'other', 'Other'


class UnitStatusEnum(models.TextChoices):
    AVAILABLE = 'available', 'Available'
    HELD = 'held', 'Held'
    BOOKED = 'booked', 'Booked'
    SOLD = 'sold', 'Sold'
    BLOCKED = 'blocked', 'Blocked'


class UnitFacingEnum(models.TextChoices):
    NORTH = 'north', 'North'
    SOUTH = 'south', 'South'
    EAST = 'east', 'East'
    WEST = 'west', 'West'
    NORTH_EAST = 'north_east', 'North East'
    NORTH_WEST = 'north_west', 'North West'
    SOUTH_EAST = 'south_east', 'South East'
    SOUTH_WEST = 'south_west', 'South West'


class LeadUnitRelationEnum(models.TextChoices):
    INTERESTED = 'interested', 'Interested'
    SITE_VISIT_SCHEDULED = 'site_visit_scheduled', 'Site Visit Scheduled'
    SITE_VISIT_DONE = 'site_visit_done', 'Site Visit Done'
    NEGOTIATING = 'negotiating', 'Negotiating'
    BOOKED = 'booked', 'Booked'
    SOLD = 'sold', 'Sold'
    CANCELLED = 'cancelled', 'Cancelled'


class Project(models.Model):
    """A real estate project (e.g. a residential/commercial development)."""
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    name = models.TextField()
    project_type = models.CharField(max_length=20, choices=ProjectTypeEnum.choices)
    status = models.CharField(max_length=30, choices=ProjectStatusEnum.choices, db_index=True)
    description = models.TextField(null=True, blank=True)
    address_line1 = models.TextField(null=True, blank=True)
    address_line2 = models.TextField(null=True, blank=True)
    city = models.TextField(null=True, blank=True)
    state = models.TextField(null=True, blank=True)
    country = models.TextField(null=True, blank=True)
    postal_code = models.TextField(null=True, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    rera_number = models.TextField(null=True, blank=True)
    possession_date = models.DateField(null=True, blank=True)
    image_zata_id = models.UUIDField(null=True, blank=True)
    image_url = models.TextField(null=True, blank=True)
    created_by_user_id = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'real_estate_projects'
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_re_projects_tenant'),
            models.Index(fields=['status'], name='idx_re_projects_status'),
            models.Index(fields=['project_type'], name='idx_re_projects_type'),
        ]

    def __str__(self):
        return self.name


class Block(models.Model):
    """A tower/wing/phase/sector/block within a project."""
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='blocks',
        db_column='project_id',
    )
    name = models.TextField()
    block_type = models.CharField(max_length=20, choices=BlockTypeEnum.choices)
    total_floors = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'real_estate_blocks'
        unique_together = [('project', 'name')]
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_re_blocks_tenant'),
            models.Index(fields=['project'], name='idx_re_blocks_project'),
        ]

    def __str__(self):
        return f'{self.name} ({self.project.name})'


class Unit(models.Model):
    """A sellable unit (flat/villa/plot/shop/office) within a project."""
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='units',
        db_column='project_id',
    )
    block = models.ForeignKey(
        Block,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='units',
        db_column='block_id',
    )
    unit_type = models.CharField(max_length=30, choices=UnitTypeEnum.choices)
    unit_number = models.TextField(help_text='Free-text display code, e.g. "A-1203" or "Plot-042"')
    floor_number = models.IntegerField(null=True, blank=True, help_text='Can be negative for basement floors')
    facing = models.CharField(max_length=20, choices=UnitFacingEnum.choices, null=True, blank=True)
    configuration = models.TextField(null=True, blank=True, help_text='e.g. "2BHK"')
    carpet_area_sqft = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    built_up_area_sqft = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    super_built_up_area_sqft = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    plot_dimensions = models.TextField(null=True, blank=True, help_text='e.g. "30ft x 45ft"')
    rate_per_sqft = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    base_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    total_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, choices=UnitStatusEnum.choices, db_index=True)
    amenities = models.JSONField(null=True, blank=True)
    metadata = models.JSONField(null=True, blank=True, help_text='Tenant custom-field escape hatch')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'real_estate_units'
        unique_together = [('project', 'unit_number')]
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_re_units_tenant'),
            models.Index(fields=['project'], name='idx_re_units_project'),
            models.Index(fields=['block'], name='idx_re_units_block'),
            models.Index(fields=['status'], name='idx_re_units_status'),
            models.Index(fields=['unit_type'], name='idx_re_units_type'),
            models.Index(fields=['floor_number'], name='idx_re_units_floor'),
        ]

    def __str__(self):
        return f'{self.unit_number} ({self.project.name})'


class ProjectInterest(models.Model):
    """A CRM Lead's interest in a project (pre-unit-selection stage)."""
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='lead_interests',
        db_column='project_id',
    )
    lead = models.ForeignKey(
        Lead,
        on_delete=models.CASCADE,
        related_name='real_estate_project_interests',
        db_column='lead_id',
    )
    budget_min = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    budget_max = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    preferred_unit_type = models.CharField(
        max_length=30, choices=UnitTypeEnum.choices, null=True, blank=True
    )
    preferred_configuration = models.TextField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    assigned_to = models.UUIDField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'real_estate_project_interests'
        unique_together = [('project', 'lead')]
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_re_interests_tenant'),
            models.Index(fields=['assigned_to'], name='idx_re_interests_assigned'),
        ]

    def __str__(self):
        return f'{self.lead_id} interested in {self.project.name}'


class UnitLead(models.Model):
    """A CRM Lead's relationship to a specific Unit (site visit, negotiation, booking...)."""
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    unit = models.ForeignKey(
        Unit,
        on_delete=models.CASCADE,
        related_name='lead_links',
        db_column='unit_id',
    )
    lead = models.ForeignKey(
        Lead,
        on_delete=models.CASCADE,
        related_name='real_estate_units',
        db_column='lead_id',
    )
    relation_type = models.CharField(
        max_length=30, choices=LeadUnitRelationEnum.choices, db_index=True
    )
    booking_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    booking_date = models.DateField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    assigned_to = models.UUIDField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'real_estate_unit_leads'
        unique_together = [('unit', 'lead')]
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_re_unit_leads_tenant'),
            models.Index(fields=['relation_type'], name='idx_re_unit_leads_relation'),
            models.Index(fields=['assigned_to'], name='idx_re_unit_leads_assigned'),
        ]

    def __str__(self):
        return f'{self.lead_id} / {self.unit.unit_number} ({self.relation_type})'
