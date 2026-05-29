from rest_framework import serializers
from .models import Meeting
from common.mixins import TenantMixin


class MeetingSerializer(TenantMixin):
    """
    Serialize scheduled meetings and appointments.

    Agents use this schema to create, update, and inspect meetings with leads,
    including meeting time, location, agenda, and follow-up notes.
    """
    lead_name = serializers.CharField(
        source='lead.name',
        read_only=True,
        help_text='Display name of the linked lead, if the meeting is connected to a lead. Read-only.'
    )
    owner_user_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text='UUID of the meeting owner. If omitted during create, the CRM uses the authenticated JWT user_id.'
    )

    class Meta:
        model = Meeting
        fields = [
            'id', 'lead', 'lead_name', 'title', 'location',
            'description', 'notes', 'start_at', 'end_at',
            'owner_user_id', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this meeting. Read-only.'},
            'lead': {'help_text': 'Optional numeric ID of the lead this meeting is connected to.'},
            'title': {'help_text': 'Short meeting title or subject.'},
            'location': {'help_text': 'Optional physical address, online meeting link, or location label.'},
            'description': {'help_text': 'Optional meeting agenda, purpose, or description.'},
            'notes': {'help_text': 'Optional internal notes, meeting outcome, or follow-up summary.'},
            'start_at': {'help_text': 'Meeting start date and time in ISO 8601 date-time format.'},
            'end_at': {'help_text': 'Meeting end date and time in ISO 8601 date-time format. Must be after start_at.'},
            'created_at': {'help_text': 'Timestamp when this meeting was created, in ISO 8601 date-time format. Read-only.'},
            'updated_at': {'help_text': 'Timestamp when this meeting was last updated, in ISO 8601 date-time format. Read-only.'},
        }

    def validate(self, data):
        """Validate that end_at is after start_at"""
        if 'start_at' in data and 'end_at' in data:
            if data['end_at'] <= data['start_at']:
                raise serializers.ValidationError(
                    "End time must be after start time"
                )
        return data


class MeetingListSerializer(TenantMixin):
    """
    Serialize compact meeting records for calendars and meeting lists.

    Agents use this schema when browsing scheduled meetings without full notes
    or description details.
    """
    lead_name = serializers.CharField(
        source='lead.name',
        read_only=True,
        help_text='Display name of the linked lead, if the meeting is connected to a lead. Read-only.'
    )
    
    class Meta:
        model = Meeting
        fields = [
            'id', 'lead', 'lead_name', 'title', 'location',
            'start_at', 'end_at', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this meeting. Read-only.'},
            'lead': {'help_text': 'Optional numeric ID of the lead this meeting is connected to.'},
            'title': {'help_text': 'Short meeting title or subject.'},
            'location': {'help_text': 'Optional physical address, online meeting link, or location label.'},
            'start_at': {'help_text': 'Meeting start date and time in ISO 8601 date-time format.'},
            'end_at': {'help_text': 'Meeting end date and time in ISO 8601 date-time format.'},
            'created_at': {'help_text': 'Timestamp when this meeting was created, in ISO 8601 date-time format. Read-only.'},
        }
