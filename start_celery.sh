#!/bin/bash
# Start Celery worker and beat for workflow automation

# Start Celery worker
celery -A digicrm worker --loglevel=info --detach

# Start Celery beat (scheduler)
celery -A digicrm beat --loglevel=info --detach

echo "Celery worker and beat started"
