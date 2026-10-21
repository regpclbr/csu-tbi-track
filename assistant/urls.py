from django.urls import path

from . import views

app_name = "assistant"

urlpatterns = [
    path("api/chats/", views.chats_api, name="api_chats"),
    path("api/chats/<int:chat_id>/", views.chat_detail_api, name="api_chat"),
    path("api/chats/<int:chat_id>/ask/", views.ask_api, name="api_ask"),
]
