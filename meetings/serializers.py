from rest_framework import serializers
from .models import Meeting


class MeetingSerializer(serializers.ModelSerializer):
    """Serializer for Meeting model"""
    lead_name = serializers.CharField(source='lead.name', read_only=True)
    
    class Meta:
        model = Meeting
        fields = [
            'id', 'lead', 'lead_name', 'title', 'location',
            'description', 'notes', 'start_at', 'end_at',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, data):
        """Validate that end_at is after start_at"""
        if 'start_at' in data and 'end_at' in data:
            if data['end_at'] <= data['start_at']:
                raise serializers.ValidationError(
                    "End time must be after start time"
                )
        return data


class MeetingListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing meetings"""
    lead_name = serializers.CharField(source='lead.name', read_only=True)
    
    class Meta:
        model = Meeting
        fields = [
            'id', 'lead', 'lead_name', 'title', 'location',
            'start_at', 'end_at', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']