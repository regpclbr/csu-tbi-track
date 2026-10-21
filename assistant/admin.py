from django.contrib import admin

from accounts.admin import ReadOnlyAdminMixin

from .models import AIChat, AIChatMessage, AIMessageSource, DocumentEmbedding


@admin.register(DocumentEmbedding)
class DocumentEmbeddingAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """View only. Embeddings are written by the indexing task, never by hand."""

    list_display = ("document", "chunk_index", "vector_length", "chunk_preview")
    list_select_related = ("document",)
    search_fields = ("document__title",)
    exclude = ("embedding_vector",)  # 768 numbers are not useful to show
    readonly_fields = ("vector_length",)

    @admin.display(description="Vector length")
    def vector_length(self, embedding: DocumentEmbedding) -> int:
        return len(embedding.embedding_vector)

    @admin.display(description="Chunk")
    def chunk_preview(self, embedding: DocumentEmbedding) -> str:
        return embedding.chunk_text[:80]


class AIChatMessageInline(admin.TabularInline):
    model = AIChatMessage
    extra = 0
    can_delete = False
    fields = ("role", "content", "created_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(AIChat)
class AIChatAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("title", "member", "updated_at")
    inlines = [AIChatMessageInline]


@admin.register(AIChatMessage)
class AIChatMessageAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("chat", "role", "created_at")
    list_filter = ("role",)


@admin.register(AIMessageSource)
class AIMessageSourceAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("message", "document", "chunk_index")
