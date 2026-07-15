from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from .agent import run_aria
from .models import ARIAConversation
from .serializers import ARIAChatRequestSerializer, ARIAConversationSerializer


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def aria_chat(request):
    """
    POST /api/aria/chat/
    Body: { "message": "...", "conversation_id": "<uuid or null>" }
    """
    serializer = ARIAChatRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    user = request.user
    message = serializer.validated_data['message']
    conversation_id = serializer.validated_data.get('conversation_id')

    try:
        result = run_aria(user, conversation_id, message)
        return Response(result)
    except ValueError as e:
        return Response({'error': str(e)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    except Exception as e:
        return Response({'error': 'ARIA encountered an error. Please try again.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def aria_conversations(request):
    """
    GET /api/aria/conversations/
    Returns all conversations for the current user (without messages for performance).
    """
    convos = ARIAConversation.objects.filter(user=request.user).prefetch_related('messages')
    serializer = ARIAConversationSerializer(convos, many=True)
    # Strip messages from list view for speed
    data = []
    for item in serializer.data:
        data.append({
            'id': item['id'],
            'title': item['title'],
            'message_count': item['message_count'],
            'created_at': item['created_at'],
            'updated_at': item['updated_at'],
        })
    return Response(data)


@api_view(['GET', 'DELETE'])
@permission_classes([IsAuthenticated])
def aria_conversation_detail(request, conversation_id):
    """
    GET  /api/aria/conversations/<id>/  — full conversation with messages
    DELETE /api/aria/conversations/<id>/  — delete conversation
    """
    try:
        convo = ARIAConversation.objects.get(id=conversation_id, user=request.user)
    except ARIAConversation.DoesNotExist:
        return Response({'error': 'Conversation not found.'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'DELETE':
        convo.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = ARIAConversationSerializer(convo)
    return Response(serializer.data)
