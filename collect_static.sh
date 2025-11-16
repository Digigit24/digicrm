#!/bin/bash
# Collect Django static files
# This script collects all static files from Django apps into the STATIC_ROOT directory

echo "Collecting static files..."
python manage.py collectstatic --noinput

echo ""
echo "Static files collected successfully!"
echo "Static files location: staticfiles/"
