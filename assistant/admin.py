from django.contrib import admin

from .models import AIChat, AIChatMessage, AIMessageSource, DocumentEmbedding


@admin.register(DocumentEmbedding)
class DocumentEmbeddingAdmin(admin.ModelAdmin):
    list_display = ("document", "chunk_index")
    exclude = ("embedding_vector",)  # 768 numbers are not useful to show


class AIChatMessageInline(admin.TabularInline):
    model = AIChatMessage
    extra = 0


@admin.register(AIChat)
class AIChatAdmin(admin.ModelAdmin):
    list_display = ("title", "member", "updated_at")
    inlines = [AIChatMessageInline]


@admin.register(AIChatMessage)
class AIChatMessageAdmin(admin.ModelAdmin):
    list_display = ("chat", "role", "created_at")


@admin.register(AIMessageSource)
class AIMessageSourceAdmin(admin.ModelAdmin):
    list_display = ("message", "document", "chunk_index")
