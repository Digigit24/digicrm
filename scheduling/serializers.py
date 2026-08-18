from rest_framework import serializers

from meetings import recurrence

from .models import CalendarPreference


class CalendarPreferenceSerializer(serializers.ModelSerializer):
    """Per-user calendar settings. ``timezone`` is the local source of truth for
    the caller's IANA zone (seeded from the browser)."""

    class Meta:
        model = CalendarPreference
        fields = [
            'id', 'timezone', 'default_view', 'week_starts_on',
            'working_hours_start', 'working_hours_end', 'working_days',
            'visible_layers', 'visible_user_ids',
            'default_meeting_duration_minutes', 'show_declined',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_timezone(self, value):
        if not recurrence.is_valid_timezone(value):
            raise serializers.ValidationError(f'"{value}" is not a known IANA timezone name.')
        return value

    def validate_week_starts_on(self, value):
        if value not in (0, 1):
            raise serializers.ValidationError('week_starts_on must be 0 (Sunday) or 1 (Monday).')
        return value

    def validate_working_days(self, value):
        if not isinstance(value, list) or any(
            not isinstance(day, int) or day < 0 or day > 6 for day in value
        ):
            raise serializers.ValidationError('working_days must be a list of ints 0-6.')
        return value

    def validate_visible_layers(self, value):
        from .events import ALL_LAYERS
        if not isinstance(value, list):
            raise serializers.ValidationError('visible_layers must be a list.')
        unknown = [layer for layer in value if layer not in ALL_LAYERS]
        if unknown:
            raise serializers.ValidationError(f'Unknown layers: {", ".join(unknown)}')
        return value

    def validate(self, data):
        start = data.get('working_hours_start') or getattr(self.instance, 'working_hours_start', None)
        end = data.get('working_hours_end') or getattr(self.instance, 'working_hours_end', None)
        if start and end and end <= start:
            raise serializers.ValidationError(
                {'working_hours_end': 'Must be after working_hours_start.'}
            )
        return data


class AvailabilityRequestSerializer(serializers.Serializer):
    user_ids = serializers.ListField(child=serializers.CharField(), required=False)
    start = serializers.DateTimeField()
    end = serializers.DateTimeField()
    duration_minutes = serializers.IntegerField(required=False, default=30, min_value=5)
    granularity_minutes = serializers.IntegerField(required=False, default=15, min_value=5)
    tz = serializers.CharField(required=False, allow_blank=True)
    respect_working_hours = serializers.BooleanField(required=False, default=True)
    max_slots = serializers.IntegerField(required=False, default=50, min_value=1, max_value=500)

    def validate_tz(self, value):
        if value and not recurrence.is_valid_timezone(value):
            raise serializers.ValidationError(f'"{value}" is not a known IANA timezone name.')
        return value

    def validate(self, data):
        if data['end'] <= data['start']:
            raise serializers.ValidationError({'end': 'Must be after start.'})
        if (data['end'] - data['start']).days > 31:
            raise serializers.ValidationError({'end': 'Availability windows are capped at 31 days.'})
        return data


class ConflictRequestSerializer(serializers.Serializer):
    start_at = serializers.DateTimeField()
    end_at = serializers.DateTimeField()
    user_ids = serializers.ListField(child=serializers.CharField(), required=False)
    exclude_meeting_id = serializers.IntegerField(required=False, allow_null=True)
    recurrence_rule = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    timezone = serializers.CharField(required=False, allow_blank=True)

    def validate_timezone(self, value):
        if value and not recurrence.is_valid_timezone(value):
            raise serializers.ValidationError(f'"{value}" is not a known IANA timezone name.')
        return value

    def validate(self, data):
        if data['end_at'] < data['start_at']:
            raise serializers.ValidationError({'end_at': 'Must not be before start_at.'})
        return data
