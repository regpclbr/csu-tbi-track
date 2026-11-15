from django.urls import path

from . import page_views, views

app_name = "assistant"

urlpatterns = [
    path("", page_views.chat_page, name="chat"),  # a new, empty chat (created by its first question)
    path("<int:chat_id>/", page_views.chat_page, name="conversation"),
    path("ask/", page_views.ask, name="ask"),
    path("chats/", page_views.chat_list, name="chat_list"),  # partial: the delayed refresh after a first answer
    path("<int:chat_id>/rename/", page_views.rename_chat, name="rename"),
    path("<int:chat_id>/delete/", page_views.delete_chat, name="delete"),
    path("api/chats/", views.chats_api, name="api_chats"),
    path("api/chats/<int:chat_id>/", views.chat_detail_api, name="api_chat"),
    path("api/chats/<int:chat_id>/ask/", views.ask_api, name="api_ask"),
]
