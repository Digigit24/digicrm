"""Calendar URLs.

Mounted at ``/api/calendar/`` from ``digicrm/urls.py``. The Python package is
``scheduling`` because a top-level ``calendar`` package shadows the stdlib
module Django imports.
"""
from django.urls import path

from .views import (
    CalendarAvailabilityView,
    CalendarConflictsView,
    CalendarEventsView,
    CalendarLayersView,
    CalendarMembersView,
    CalendarPreferenceView,
)

app_name = 'scheduling'

urlpatterns = [
    path('events/', CalendarEventsView.as_view(), name='calendar-events'),
    path('members/', CalendarMembersView.as_view(), name='calendar-members'),
    path('availability/', CalendarAvailabilityView.as_view(), name='calendar-availability'),
    path('conflicts/', CalendarConflictsView.as_view(), name='calendar-conflicts'),
    path('preferences/', CalendarPreferenceView.as_view(), name='calendar-preferences'),
    path('layers/', CalendarLayersView.as_view(), name='calendar-layers'),
]
