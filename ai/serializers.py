from rest_framework import serializers
from .models import AIChatSession, AIChatMessage


class AIChatMessageSerializer(serializers.ModelSerializer):
    """Serialize a single chat message."""

    class Meta:
        model = AIChatMessage
        fields = ['id', 'role', 'content', 'sequence', 'created_at']
        read_only_fields = ['id', 'sequence', 'created_at']


class AIChatSessionListSerializer(serializers.ModelSerializer):
    """Serialize a session for list views (without messages)."""

    message_count = serializers.SerializerMethodField()

    class Meta:
        model = AIChatSession
        fields = ['id', 'title', 'created_at', 'updated_at', 'message_count']
        read_only_fields = ['id', 'created_at', 'updated_at', 'message_count']

    def get_message_count(self, obj):
        return obj.messages.count()


class AIChatSessionDetailSerializer(serializers.ModelSerializer):
    """Serialize a session with all its messages (for resume)."""

    messages = AIChatMessageSerializer(many=True, read_only=True)

    class Meta:
        model = AIChatSession
        fields = ['id', 'title', 'created_at', 'updated_at', 'messages']
        read_only_fields = ['id', 'created_at', 'updated_at', 'messages']


class AIChatSessionCreateSerializer(serializers.ModelSerializer):
    """Serialize session creation (title optional)."""

    class Meta:
        model = AIChatSession
        fields = ['title']
        extra_kwargs = {
            'title': {'required': False, 'allow_blank': True},
        }


class AIChatSessionUpdateSerializer(serializers.ModelSerializer):
    """Serialize session title update."""

    class Meta:
        model = AIChatSession
        fields = ['title']
        extra_kwargs = {
            'title': {'required': True, 'allow_blank': False},
        }


class AIChatMessageBatchCreateSerializer(serializers.Serializer):
    """Validate a batch of messages to append to a session."""

    messages = serializers.ListField(
        # `required_keys` was removed here -- not a real DictField kwarg in
        # this DRF version (`Field.__init__()` rejected it with a TypeError
        # at import time, which meant the whole `ai` app failed to load).
        # Required-key checking already happens for real in
        # `validate_messages` below (`msg.get('role')`/`msg.get('content')`),
        # so this constructor was doing nothing even before it started
        # crashing the import.
        child=serializers.DictField(child=serializers.CharField()),
        min_length=1,
        help_text='Array of {role, content} objects. Role must be user/assistant/tool.'
    )

    def validate_messages(self, value):
        valid_roles = {'user', 'assistant', 'tool'}
        for i, msg in enumerate(value):
            role = msg.get('role')
            content = msg.get('content')
            if role not in valid_roles:
                raise serializers.ValidationError(
                    f"Message {i}: invalid role '{role}'. Must be one of: {', '.join(valid_roles)}"
                )
            if not content or not isinstance(content, str):
                raise serializers.ValidationError(
                    f"Message {i}: content must be a non-empty string"
                )
        return value