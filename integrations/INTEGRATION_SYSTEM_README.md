# Integration System - Complete Documentation

## Overview

This is a complete, production-ready integration system for the Django CRM that works like Zapier. It allows you to connect external services (starting with Google Sheets) to your CRM and create automated workflows to sync data.

**Key Features:**
-  Google Sheets OAuth 2.0 integration
-  Secure token encryption
-  Flexible workflow engine
-  Field mapping and data transformation
-  Duplicate detection
-  Automatic polling (every 5-10 minutes)
-  Error handling and retry logic
-  Comprehensive execution logging
-  Multi-tenant support
-  REST API for all operations
-  Django Admin interface
-  Celery background tasks

---

## Architecture

### Components

1. **Models** (`models.py`)
   - `Integration` - Available integration types
   - `Connection` - User OAuth connections
   - `Workflow` - User-created automations
   - `WorkflowTrigger` - Workflow triggers (new row, webhook, etc.)
   - `WorkflowAction` - Actions to execute (create lead, send email, etc.)
   - `WorkflowMapping` - Field mappings (sheet column ’ CRM field)
   - `ExecutionLog` - Detailed execution logs
   - `DuplicateDetectionCache` - Prevents duplicate records

2. **Services**
   - `google_sheets.py` - Google Sheets API integration
   - `workflow_engine.py` - Core workflow execution engine

3. **Utils**
   - `encryption.py` - Secure token encryption/decryption
   - `oauth.py` - OAuth 2.0 flow handling

4. **Tasks** (`tasks.py`)
   - `poll_workflow_triggers` - Periodic trigger checking (every 5 min)
   - `execute_workflow_async` - Background workflow execution
   - `refresh_connection_token` - Token refresh
   - `refresh_expiring_tokens` - Automatic token refresh (hourly)
   - `cleanup_old_execution_logs` - Log cleanup (daily)

5. **API Views** (`views.py`)
   - Full REST API for managing integrations, connections, and workflows
   - OAuth flow endpoints
   - Workflow testing endpoints

---

## Installation & Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

**New Dependencies Added:**
- `celery>=5.3.0` - Background task processing
- `redis>=5.0.0` - Celery broker
- `cryptography>=41.0.0` - Token encryption
- `google-auth>=2.23.0` - Google OAuth
- `google-auth-oauthlib>=1.1.0` - Google OAuth flow
- `google-api-python-client>=2.100.0` - Google APIs
- `drf-nested-routers>=0.93.0` - Nested REST routes

### 2. Install Redis

**On macOS:**
```bash
brew install redis
brew services start redis
```

**On Ubuntu/Debian:**
```bash
sudo apt-get install redis-server
sudo systemctl start redis
```

**On Windows:**
Download from https://github.com/microsoftarchive/redis/releases

### 3. Set Up Google OAuth

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing
3. Enable **Google Sheets API** and **Google Drive API**
4. Create OAuth 2.0 credentials:
   - Application type: **Web application**
   - Authorized redirect URIs: `http://localhost:8000/api/integrations/connections/oauth_callback/`
5. Copy **Client ID** and **Client Secret**

### 4. Configure Environment Variables

Create or update your `.env` file:

```bash
# Google OAuth Configuration
GOOGLE_CLIENT_ID=your-client-id-here
GOOGLE_CLIENT_SECRET=your-client-secret-here
GOOGLE_REDIRECT_URI=http://localhost:8000/api/integrations/connections/oauth_callback/

# Integration Encryption Key (generate with command below)
INTEGRATION_ENCRYPTION_KEY=your-encryption-key-here

# Celery Configuration
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
```

**Generate Encryption Key:**
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 5. Run Migrations

```bash
python manage.py makemigrations integrations
python manage.py migrate
```

### 6. Create Sample Integration

Run this in Django shell (`python manage.py shell`):

```python
from integrations.models import Integration, IntegrationTypeEnum

# Create Google Sheets integration
integration = Integration.objects.create(
    name='Google Sheets',
    type=IntegrationTypeEnum.GOOGLE_SHEETS,
    description='Connect Google Sheets to automatically import leads',
    is_active=True,
    requires_oauth=True,
    oauth_config={
        'scopes': [
            'https://www.googleapis.com/auth/spreadsheets.readonly',
            'https://www.googleapis.com/auth/drive.readonly',
        ]
    }
)
print(f"Created integration: {integration.id}")
```

### 7. Start Celery Workers

**Terminal 1 - Celery Worker:**
```bash
celery -A digicrm worker --loglevel=info
```

**Terminal 2 - Celery Beat (Scheduler):**
```bash
celery -A digicrm beat --loglevel=info
```

**Terminal 3 - Django Server:**
```bash
python manage.py runserver
```

---

## Usage Guide

### Complete Workflow Example

Here's how to set up a complete workflow to sync Google Sheets leads to your CRM:

#### 1. Connect Google Account

**API Request:**
```bash
POST /api/integrations/connections/initiate_oauth/
{
  "integration_id": 1
}
```

**Response:**
```json
{
  "authorization_url": "https://accounts.google.com/o/oauth2/auth?...",
  "state": "abc123..."
}
```

- Redirect user to `authorization_url`
- User authorizes your app
- Google redirects back with `code` and `state`

**Complete OAuth:**
```bash
POST /api/integrations/connections/oauth_callback/
{
  "code": "4/0AY...",
  "state": "abc123...",
  "integration_id": 1,
  "connection_name": "Marketing Leads Sheet"
}
```

**Response:**
```json
{
  "id": 1,
  "name": "Marketing Leads Sheet",
  "status": "CONNECTED",
  "integration": {...}
}
```

#### 2. List Available Spreadsheets

```bash
GET /api/integrations/connections/1/spreadsheets/
```

**Response:**
```json
[
  {
    "id": "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms",
    "name": "Lead Form Responses",
    "modified_time": "2024-01-15T10:30:00Z"
  }
]
```

#### 3. List Sheets in Spreadsheet

```bash
GET /api/integrations/connections/1/spreadsheets/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/sheets/
```

**Response:**
```json
[
  {
    "sheet_id": 0,
    "title": "Form Responses 1",
    "index": 0,
    "row_count": 1000,
    "column_count": 10
  }
]
```

#### 4. Create Workflow

```bash
POST /api/integrations/workflows/
{
  "name": "Import Marketing Leads",
  "description": "Auto-import leads from Google Form responses",
  "connection_id": 1,
  "is_active": true
}
```

**Response:**
```json
{
  "id": 1,
  "name": "Import Marketing Leads",
  "is_active": true,
  ...
}
```

#### 5. Create Trigger (New Row Detection)

```bash
POST /api/integrations/workflows/1/triggers/
{
  "trigger_type": "NEW_ROW",
  "trigger_config": {
    "spreadsheet_id": "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms",
    "sheet_name": "Form Responses 1"
  },
  "poll_interval_minutes": 5
}
```

#### 6. Create Action (Create Lead)

```bash
POST /api/integrations/workflows/1/actions/
{
  "action_type": "CREATE_LEAD",
  "order": 1,
  "action_config": {
    "default_source": "Google Form",
    "default_priority": "MEDIUM"
  },
  "retry_on_failure": true,
  "max_retries": 3
}
```

**Response:**
```json
{
  "id": 1,
  "action_type": "CREATE_LEAD",
  "order": 1,
  ...
}
```

#### 7. Create Field Mappings

Map Google Sheet columns to CRM Lead fields:

```bash
POST /api/integrations/workflows/1/actions/1/mappings/
{
  "source_field": "Name",
  "destination_field": "name",
  "is_required": true
}

POST /api/integrations/workflows/1/actions/1/mappings/
{
  "source_field": "Email Address",
  "destination_field": "email",
  "is_required": false,
  "transformation": {
    "trim": true,
    "lowercase": true
  },
  "validation_rules": {
    "pattern": "^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$"
  }
}

POST /api/integrations/workflows/1/actions/1/mappings/
{
  "source_field": "Phone Number",
  "destination_field": "phone",
  "is_required": true,
  "transformation": {
    "trim": true,
    "remove_spaces": true
  }
}

POST /api/integrations/workflows/1/actions/1/mappings/
{
  "source_field": "Company",
  "destination_field": "company",
  "default_value": "Unknown"
}
```

#### 8. Test Workflow

```bash
POST /api/integrations/workflows/1/test/
{}
```

**Response:**
```json
{
  "message": "Workflow executed 3 time(s)",
  "executions": [
    {
      "id": 1,
      "execution_id": "550e8400-e29b-41d4-a716-446655440000",
      "status": "SUCCESS",
      "started_at": "2024-01-15T10:35:00Z",
      "duration_ms": 1234
    }
  ]
}
```

#### 9. View Execution Logs

```bash
GET /api/integrations/workflows/1/executions/
```

**Response:**
```json
[
  {
    "id": 1,
    "execution_id": "550e8400-e29b-41d4-a716-446655440000",
    "status": "SUCCESS",
    "started_at": "2024-01-15T10:35:00Z",
    "completed_at": "2024-01-15T10:35:01Z",
    "duration_ms": 1234,
    "error_message": null
  }
]
```

**Get Detailed Log:**
```bash
GET /api/integrations/execution-logs/1/
```

```json
{
  "execution_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "SUCCESS",
  "trigger_data": {
    "_row_number": 5,
    "Name": "John Doe",
    "Email Address": "john@example.com",
    "Phone Number": "555-1234",
    "Company": "Acme Corp"
  },
  "result_data": {
    "action_1": {
      "status": "created",
      "lead_id": 123,
      "lead_name": "John Doe"
    }
  },
  "execution_steps": [
    {
      "timestamp": "2024-01-15T10:35:00.123Z",
      "step": "check_trigger",
      "status": "success",
      "details": "Found 1 new rows"
    },
    {
      "timestamp": "2024-01-15T10:35:00.456Z",
      "step": "transform_data",
      "status": "success",
      "details": "Transformed 4 fields"
    },
    {
      "timestamp": "2024-01-15T10:35:01.234Z",
      "step": "create_lead",
      "status": "success",
      "details": "Created lead ID: 123"
    }
  ]
}
```

---

## API Endpoints Reference

### Integrations

- `GET /api/integrations/integrations/` - List available integrations
- `GET /api/integrations/integrations/:id/` - Get integration details

### Connections

- `GET /api/integrations/connections/` - List user's connections
- `POST /api/integrations/connections/initiate_oauth/` - Start OAuth flow
- `POST /api/integrations/connections/oauth_callback/` - Complete OAuth
- `GET /api/integrations/connections/:id/` - Get connection details
- `POST /api/integrations/connections/:id/disconnect/` - Disconnect
- `POST /api/integrations/connections/:id/refresh_token/` - Refresh token
- `GET /api/integrations/connections/:id/test/` - Test connection
- `GET /api/integrations/connections/:id/spreadsheets/` - List spreadsheets
- `GET /api/integrations/connections/:id/spreadsheets/:spreadsheet_id/sheets/` - List sheets

### Workflows

- `GET /api/integrations/workflows/` - List workflows
- `POST /api/integrations/workflows/` - Create workflow
- `GET /api/integrations/workflows/:id/` - Get workflow details
- `PATCH /api/integrations/workflows/:id/` - Update workflow
- `DELETE /api/integrations/workflows/:id/` - Delete workflow
- `POST /api/integrations/workflows/:id/test/` - Test workflow
- `POST /api/integrations/workflows/:id/toggle/` - Toggle active status
- `GET /api/integrations/workflows/:id/executions/` - Get execution logs
- `GET /api/integrations/workflows/stats/` - Get statistics

### Workflow Triggers

- `POST /api/integrations/workflows/:id/triggers/` - Create trigger
- `GET /api/integrations/workflows/:id/triggers/` - Get trigger
- `PATCH /api/integrations/workflows/:id/triggers/:trigger_id/` - Update trigger
- `DELETE /api/integrations/workflows/:id/triggers/:trigger_id/` - Delete trigger

### Workflow Actions

- `GET /api/integrations/workflows/:id/actions/` - List actions
- `POST /api/integrations/workflows/:id/actions/` - Create action
- `GET /api/integrations/workflows/:id/actions/:action_id/` - Get action
- `PATCH /api/integrations/workflows/:id/actions/:action_id/` - Update action
- `DELETE /api/integrations/workflows/:id/actions/:action_id/` - Delete action

### Field Mappings

- `GET /api/integrations/workflows/:wf_id/actions/:action_id/mappings/` - List mappings
- `POST /api/integrations/workflows/:wf_id/actions/:action_id/mappings/` - Create mapping
- `PATCH /api/integrations/workflows/:wf_id/actions/:action_id/mappings/:id/` - Update
- `DELETE /api/integrations/workflows/:wf_id/actions/:action_id/mappings/:id/` - Delete

### Execution Logs

- `GET /api/integrations/execution-logs/` - List execution logs
- `GET /api/integrations/execution-logs/:id/` - Get execution details

---

## Django Admin

Access the admin interface at `/admin/` to:

- View and manage all integrations
- Monitor connections
- Edit workflows
- View execution logs
- Debug issues

---

## Celery Tasks

### Periodic Tasks (Celery Beat)

| Task | Schedule | Purpose |
|------|----------|---------|
| `poll_workflow_triggers` | Every 5 minutes | Check for new rows in Google Sheets |
| `refresh_expiring_tokens` | Every hour | Refresh tokens expiring in 24 hours |
| `cleanup_old_execution_logs` | Daily | Delete logs older than 90 days |
| `check_connection_health` | Daily | Validate all connections |

### Manual Tasks

- `execute_workflow_async` - Execute specific workflow
- `refresh_connection_token` - Refresh specific connection token
- `retry_failed_execution` - Retry failed execution

---

## Security Features

1. **Token Encryption**
   - All OAuth tokens encrypted using Fernet (symmetric encryption)
   - Encryption key stored in environment variables
   - Tokens never exposed in API responses

2. **Multi-Tenant Isolation**
   - All queries filtered by `tenant_id`
   - Users can only access their own workflows

3. **OAuth State Validation**
   - CSRF protection via state parameter
   - State stored in cache with 10-minute expiry

4. **Input Validation**
   - All API inputs validated via DRF serializers
   - Field-level validation rules supported

---

## Error Handling

1. **Automatic Retries**
   - Failed workflows auto-retry up to 3 times
   - Exponential backoff between retries

2. **Error Logging**
   - All errors logged with full traceback
   - Execution logs store error details

3. **Connection Health Monitoring**
   - Daily health checks
   - Automatic token refresh
   - Error status updates

---

## Extending the System

### Adding New Integration Types

1. Create new enum value in `IntegrationTypeEnum`
2. Implement service class in `services/`
3. Add OAuth handler if needed
4. Update workflow engine to support new trigger/action types

### Adding New Action Types

1. Add to `ActionTypeEnum`
2. Implement `_execute_<action>` method in `WorkflowEngine`
3. Create serializer for action configuration
4. Update API documentation

### Adding Custom Transformations

Edit `WorkflowEngine._apply_transformation()`:

```python
def _apply_transformation(self, value, transformation):
    # ... existing code ...

    if transformation.get('custom_format'):
        value = custom_format_function(value)

    return value
```

---

## Troubleshooting

### OAuth Errors

**Error: "Invalid redirect URI"**
- Ensure `GOOGLE_REDIRECT_URI` in `.env` matches Google Console configuration
- Check for trailing slashes

**Error: "Token expired"**
- Run: `POST /api/integrations/connections/:id/refresh_token/`
- Check Celery Beat is running for automatic refresh

### Celery Issues

**Workers not processing tasks:**
```bash
# Check Redis is running
redis-cli ping  # Should return "PONG"

# Restart workers
celery -A digicrm worker --loglevel=info
```

**Beat scheduler not running:**
```bash
# Start beat scheduler
celery -A digicrm beat --loglevel=info
```

### Database Migrations

**Error: "Table already exists"**
```bash
python manage.py migrate integrations --fake-initial
```

---

## Performance Optimization

1. **Database Indexes**
   - All foreign keys indexed
   - Tenant ID indexed on all models
   - Composite indexes for common queries

2. **Caching**
   - OAuth state cached (10 min)
   - Consider caching spreadsheet metadata

3. **Query Optimization**
   - Use `select_related()` for foreign keys
   - Use `prefetch_related()` for reverse relations

---

## Production Deployment Checklist

- [ ] Set `DEBUG=False`
- [ ] Use production-grade Redis (Redis Cloud, ElastiCache)
- [ ] Set up proper Celery workers (supervisor, systemd)
- [ ] Configure HTTPS for OAuth redirect URI
- [ ] Set up monitoring (Sentry, New Relic)
- [ ] Configure log rotation
- [ ] Set up database backups
- [ ] Use environment variables for all secrets
- [ ] Enable rate limiting
- [ ] Set up SSL/TLS for Redis connection

---

## Support & Contribution

For issues or questions:
1. Check execution logs in Django admin
2. Review Celery worker logs
3. Check Redis connection
4. Verify environment variables

---

## License

This integration system is part of the DigiCRM project.

---

**Created:** January 2026
**Version:** 1.0.0
