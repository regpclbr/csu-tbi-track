from django.urls import path

from config.views import coming_soon

from . import views

app_name = "assistant"

urlpatterns = [
    path("", coming_soon, {"feature": "AI Assistant"}, name="chat"),  # page of a later frontend phase (config.views.COMING_SOON_PAGES)
    path("api/chats/", views.chats_api, name="api_chats"),
    path("api/chats/<int:chat_id>/", views.chat_detail_api, name="api_chat"),
    path("api/chats/<int:chat_id>/ask/", views.ask_api, name="api_ask"),
]
