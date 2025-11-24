#!/bin/bash

# Migration helper script for DigiCRM
# This script applies pending Django migrations to fix the missing lead_field_configurations table

set -e  # Exit on error

echo "======================================"
echo "DigiCRM Migration Helper"
echo "======================================"
echo ""

# Check if we're in the correct directory
if [ ! -f "manage.py" ]; then
    echo "ERROR: This script must be run from the Django project root directory (where manage.py is located)"
    exit 1
fi

# Check if Python is available
if ! command -v python3 &> /dev/null && ! command -v python &> /dev/null; then
    echo "ERROR: Python is not installed or not in PATH"
    exit 1
fi

# Determine Python command
PYTHON_CMD="python3"
if ! command -v python3 &> /dev/null; then
    PYTHON_CMD="python"
fi

echo "Using Python: $PYTHON_CMD"
echo ""

# Show current migration status
echo "Current migration status:"
echo "-------------------------"
$PYTHON_CMD manage.py showmigrations crm || {
    echo ""
    echo "WARNING: Could not check migration status. This might indicate a database connection issue."
    echo ""
}

# Apply migrations
echo ""
echo "Applying CRM migrations..."
echo "-------------------------"
$PYTHON_CMD manage.py migrate crm

# Show final status
echo ""
echo "Final migration status:"
echo "-------------------------"
$PYTHON_CMD manage.py showmigrations crm

echo ""
echo "======================================"
echo "Migration Complete!"
echo "======================================"
echo ""
echo "The lead_field_configurations table should now be created."
echo "You can now access /admin/crm/leadfieldconfiguration/ without errors."
