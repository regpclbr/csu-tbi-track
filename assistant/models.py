"""RAG assistant: DOCUMENT_EMBEDDING, AI_CHAT, AI_CHAT_MESSAGE and AI_MESSAGE_SOURCE."""

from django.conf import settings
from django.db import models
from django.db.models import Q
from pgvector.django import HnswIndex, VectorField


def exactly_one_source() -> Q:
    """Exactly one of the three file links is set (ASSUMPTIONS 438): an activity document (any review state), a
    generated report or a submission. Shared by DOCUMENT_EMBEDDING and AI_MESSAGE_SOURCE."""
    return (Q(document__isnull=False, generated_report__isnull=True, submission__isnull=True)
            | Q(document__isnull=True, generated_report__isnull=False, submission__isnull=True)
            | Q(document__isnull=True, generated_report__isnull=True, submission__isnull=False))


class DocumentEmbedding(models.Model):
    """One chunk of an indexed file and its embedding vector: an activity document (any review state), a generated
    report or an approved submission (ASSUMPTIONS 438)."""

    embedding_id = models.BigAutoField(primary_key=True)
    document = models.ForeignKey("documents.Document", on_delete=models.CASCADE, null=True, blank=True,
                                 related_name="embeddings")
    generated_report = models.ForeignKey("reports.GeneratedReport", on_delete=models.CASCADE, null=True, blank=True,
                                         related_name="embeddings")
    submission = models.ForeignKey("approvals.Submission", on_delete=models.CASCADE, null=True, blank=True,
                                   related_name="embeddings")
    chunk_index = models.PositiveIntegerField()
    chunk_text = models.TextField()
    embedding_vector = VectorField(dimensions=settings.EMBEDDING_DIMENSIONS)

    class Meta:
        db_table = "document_embedding"
        ordering = ["document", "generated_report", "submission", "chunk_index"]
        constraints = [
            models.UniqueConstraint(fields=["document", "chunk_index"], condition=Q(document__isnull=False),
                                    name="unique_chunk_per_document"),
            models.UniqueConstraint(fields=["generated_report", "chunk_index"],
                                    condition=Q(generated_report__isnull=False), name="unique_chunk_per_report"),
            models.UniqueConstraint(fields=["submission", "chunk_index"], condition=Q(submission__isnull=False),
                                    name="unique_chunk_per_submission"),
            models.CheckConstraint(condition=exactly_one_source(), name="embedding_exactly_one_source"),
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
        return f"{self.document or self.generated_report or self.submission} #{self.chunk_index}"


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
    """A file (and chunk) cited by an assistant answer: a document, a generated report or a submission (exactly one,
    ASSUMPTIONS 438). PROTECT: a cited file is never deleted (documents.services.CITED_REFUSED)."""

    message_source_id = models.BigAutoField(primary_key=True)
    message = models.ForeignKey(AIChatMessage, on_delete=models.CASCADE, related_name="sources")
    document = models.ForeignKey("documents.Document", on_delete=models.PROTECT, null=True, blank=True,
                                 related_name="ai_citations")
    generated_report = models.ForeignKey("reports.GeneratedReport", on_delete=models.PROTECT, null=True, blank=True,
                                         related_name="ai_citations")
    submission = models.ForeignKey("approvals.Submission", on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="ai_citations")
    chunk_index = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "ai_message_source"
        verbose_name = "AI message source"
        constraints = [models.CheckConstraint(condition=exactly_one_source(), name="source_exactly_one_file")]

    def __str__(self) -> str:
        return f"{self.document or self.generated_report or self.submission} (chunk {self.chunk_index})"
