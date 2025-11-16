# Static Files Setup for DigiCRM

## Issue
Django Admin CSS and JavaScript files were not loading, causing MIME type errors.

## Solution
We've configured Django to properly serve static files in development mode.

## How to Collect Static Files

### Option 1: Using the provided script

**On Linux/Mac:**
```bash
chmod +x collect_static.sh
./collect_static.sh
```

**On Windows:**
```cmd
collect_static.bat
```

### Option 2: Manual command

**With virtual environment activated:**
```bash
python manage.py collectstatic --noinput
```

**Or using the full path (Windows example):**
```cmd
C:\ritik\crm\digicrm\venv\Scripts\python.exe manage.py collectstatic --noinput
```

## What This Does

The `collectstatic` command:
1. Collects all static files from Django apps (including django.contrib.admin)
2. Copies them to the `staticfiles/` directory
3. Makes them available at `/static/` URL path

## Changes Made

### 1. Settings (`digicrm/settings.py`)
- Added `STATICFILES_DIRS` configuration
- Added `STATICFILES_FINDERS` to find static files in apps
- Set `STATIC_URL = '/static/'`
- Set `STATIC_ROOT = BASE_DIR / 'staticfiles'`

### 2. URLs (`digicrm/urls.py`)
- Added static file serving for DEBUG mode
- Imported `static` from `django.conf.urls.static`
- Added: `urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)`

## For Production

In production, static files should be served by your web server (nginx, Apache, etc.), not Django.

The current configuration only serves static files when `DEBUG = True`.

## Verifying It Works

After running collectstatic, you should see:
- A `staticfiles/` directory in your project root
- Files like `staticfiles/admin/css/base.css`
- Django Admin should load with proper styling

## Troubleshooting

**If CSS still doesn't load:**
1. Make sure you ran `collectstatic`
2. Check that `DEBUG = True` in settings
3. Verify `staticfiles/` directory exists and contains files
4. Restart your Django development server
5. Clear your browser cache (Ctrl+Shift+R)

**Common errors:**
- "Static files not found" → Run collectstatic
- "MIME type error" → Usually means Django is returning 404 HTML instead of the CSS file
