from rest_framework import serializers

from .models import Notification, Reminder


class ReminderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Reminder
        fields = [
            'id', 'lead', 'recipient_user_id', 'follow_up_at', 'remind_at',
            'offset_minutes', 'status', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class NotificationSerializer(serializers.ModelSerializer):
    is_read = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            'id', 'notification_type', 'title', 'body', 'lead',
            'lead_name_snapshot', 'action_url', 'payload', 'is_read',
            'seen_at', 'read_at', 'created_at',
        ]
        read_only_fields = fields

    def get_is_read(self, obj):
        return obj.read_at is not None

