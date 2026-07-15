from django.urls import path

from .views import aria_chat, aria_conversation_detail, aria_conversations

app_name = 'aria'

urlpatterns = [
    path('chat/', aria_chat, name='chat'),
    path('conversations/', aria_conversations, name='conversations'),
    path('conversations/<uuid:conversation_id>/', aria_conversation_detail, name='conversation-detail'),
]
