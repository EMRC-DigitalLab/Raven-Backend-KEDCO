from rest_framework import serializers

from .models import ARIAConversation, ARIAMessage


class ARIAMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ARIAMessage
        fields = ['id', 'role', 'content', 'created_at']


class ARIAConversationSerializer(serializers.ModelSerializer):
    messages = ARIAMessageSerializer(many=True, read_only=True)
    message_count = serializers.IntegerField(source='messages.count', read_only=True)

    class Meta:
        model = ARIAConversation
        fields = ['id', 'title', 'message_count', 'created_at', 'updated_at', 'messages']


class ARIAChatRequestSerializer(serializers.Serializer):
    conversation_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    message = serializers.CharField(max_length=4000)
