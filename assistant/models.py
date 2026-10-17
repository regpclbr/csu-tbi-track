"""RAG assistant: DOCUMENT_EMBEDDING, AI_CHAT, AI_CHAT_MESSAGE and AI_MESSAGE_SOURCE."""

from django.conf import settings
from django.db import models
from pgvector.django import HnswIndex, VectorField


class DocumentEmbedding(models.Model):
    """One chunk of an Approved document and its embedding vector."""

    embedding_id = models.BigAutoField(primary_key=True)
    document = models.ForeignKey("documents.Document", on_delete=models.CASCADE, related_name="embeddings")
    chunk_index = models.PositiveIntegerField()
    chunk_text = models.TextField()
    embedding_vector = VectorField(dimensions=settings.EMBEDDING_DIMENSIONS)

    class Meta:
        db_table = "document_embedding"
        ordering = ["document", "chunk_index"]
        constraints = [
            models.UniqueConstraint(fields=["document", "chunk_index"], name="unique_chunk_per_document"),
        ]
        indexes = [
            # Fast nearest-neighbour search by cosine distance.
            HnswIndex(
                name="embedding_vector_hnsw",
                fields=["embedding_vector"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self) -> str:
        return f"{self.document} #{self.chunk_index}"


class AIChat(models.Model):
    """A conversation between a TBI member and the assistant."""

    chat_id = models.BigAutoField(primary_key=True)
    member = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ai_chats")
    title = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ai_chat"
        ordering = ["-updated_at"]
        verbose_name = "AI chat"

    def __str__(self) -> str:
        return self.title


class AIChatMessage(models.Model):
    """One message in an AI_CHAT, from the user or the assistant."""

    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"

    message_id = models.BigAutoField(primary_key=True)
    chat = models.ForeignKey(AIChat, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=20, choices=Role.choices)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ai_chat_message"
        ordering = ["created_at"]
        verbose_name = "AI chat message"

    def __str__(self) -> str:
        return f"{self.role}: {self.content[:40]}"


class AIMessageSource(models.Model):
    """A document (and chunk) cited by an assistant answer."""

    message_source_id = models.BigAutoField(primary_key=True)
    message = models.ForeignKey(AIChatMessage, on_delete=models.CASCADE, related_name="sources")
    document = models.ForeignKey("documents.Document", on_delete=models.PROTECT, related_name="ai_citations")
    chunk_index = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "ai_message_source"
        verbose_name = "AI message source"

    def __str__(self) -> str:
        return f"{self.document} (chunk {self.chunk_index})"
