# Fix for "relation lead_field_configurations does not exist" Error

## Problem
When accessing `/admin/crm/leadfieldconfiguration/` in Django admin, you get the following error:
```
ProgrammingError at /admin/crm/leadfieldconfiguration/
relation "lead_field_configurations" does not exist
```

## Root Cause
The database table for `LeadFieldConfiguration` model hasn't been created because the Django migrations haven't been applied to the production database.

## Solution

### Option 1: Run Migrations (Recommended)

The migration file exists (`crm/migrations/0001_initial.py`) and includes the `LeadFieldConfiguration` model. You just need to apply it:

#### Using the helper script:
```bash
# From the project root directory
./apply_migrations.sh
```

#### Manually:
```bash
# Show current migration status
python manage.py showmigrations crm

# Apply migrations
python manage.py migrate crm

# Verify the migration was applied
python manage.py showmigrations crm
```

### Option 2: Manual SQL (If migrations can't be run)

If for some reason you cannot run Django migrations, you can manually execute this SQL on your PostgreSQL database:

```sql
-- Create lead_field_configurations table
CREATE TABLE IF NOT EXISTS lead_field_configurations (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL,
    field_name TEXT NOT NULL,
    field_label TEXT NOT NULL,
    is_standard BOOLEAN NOT NULL DEFAULT FALSE,
    field_type VARCHAR(20) DEFAULT 'TEXT',
    is_visible BOOLEAN NOT NULL DEFAULT TRUE,
    is_required BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    default_value TEXT,
    options JSONB,
    placeholder TEXT,
    help_text TEXT,
    display_order INTEGER NOT NULL DEFAULT 0,
    validation_rules JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes
CREATE INDEX idx_lead_field_config_tenant ON lead_field_configurations(tenant_id);
CREATE INDEX idx_lead_field_config_active ON lead_field_configurations(tenant_id, is_active);
CREATE INDEX idx_lead_field_config_standard ON lead_field_configurations(tenant_id, is_standard);
CREATE INDEX idx_lead_field_config_order ON lead_field_configurations(display_order);

-- Create unique constraint
ALTER TABLE lead_field_configurations
ADD CONSTRAINT unique_field_config_per_tenant
UNIQUE (tenant_id, field_name);

-- Add check constraint for field_type
ALTER TABLE lead_field_configurations
ADD CONSTRAINT lead_field_configurations_field_type_check
CHECK (field_type IN ('TEXT', 'NUMBER', 'EMAIL', 'PHONE', 'DATE', 'DATETIME',
                      'DROPDOWN', 'MULTISELECT', 'CHECKBOX', 'URL', 'TEXTAREA',
                      'DECIMAL', 'CURRENCY'));
```

## Additional Fixes Applied

### Swagger Documentation
Fixed the swagger/OpenAPI documentation for the `LeadFieldConfigurationViewSet` in `crm/views.py`.

**Initial Fix:** Uncommented the `@extend_schema_view` decorator to add operation descriptions.

**Final Fix:** Removed the `@extend_schema_view` decorator due to compatibility issues with some versions of drf-spectacular and replaced it with comprehensive docstring documentation. The operations are now documented in the class docstring, and drf-spectacular will automatically generate proper OpenAPI documentation from the ViewSet structure and docstrings.

This ensures proper API documentation is generated for all CRUD operations on lead field configurations without decorator-related import errors.

## Verification

After applying the migration, verify that everything works:

1. **Check the table exists:**
   ```bash
   python manage.py dbshell
   ```
   Then in PostgreSQL:
   ```sql
   \dt lead_field_configurations
   SELECT COUNT(*) FROM lead_field_configurations;
   ```

2. **Access Django Admin:**
   - Go to: `https://crm.celiyo.com/admin/crm/leadfieldconfiguration/`
   - You should now see the admin interface without errors

3. **Test the API:**
   - GET: `/api/crm/field-configurations/`
   - Should return 200 OK with an empty list or existing configurations

## Files Changed

1. `crm/views.py` - Uncommented `@extend_schema_view` decorator for `LeadFieldConfigurationViewSet`
2. `apply_migrations.sh` - Created migration helper script
3. `MIGRATION_FIX.md` - This documentation file

## Related Models

The `LeadFieldConfiguration` model manages both:
- **Standard fields**: Pre-defined Lead model fields (phone, email, company, etc.) with visibility/display settings
- **Custom fields**: Dynamic fields stored in the `Lead.metadata` JSON field

This unified approach allows for flexible field management in the CRM system.
