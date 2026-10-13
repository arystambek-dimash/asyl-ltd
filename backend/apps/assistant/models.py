from django.conf import settings
from django.db import models


class AssistantChat(models.Model):
    """Разговор сотрудника с ИИ-ассистентом; видит его только автор."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="assistant_chats",
    )
    title = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        indexes = [models.Index(fields=["user", "-updated_at"], name="assistant_chat_user_idx")]


class AssistantMessage(models.Model):
    """Вопрос сотрудника или ответ ассистента.

    У ответа хранится ход работы (``trace``) с SQL-запросами, по которым он
    собран, — чтобы человек мог проверить цифры, а владелец видел, что читалось
    из базы.
    """

    USER = "user"
    ASSISTANT = "assistant"
    ROLES = [(USER, "Сотрудник"), (ASSISTANT, "Ассистент")]

    chat = models.ForeignKey(AssistantChat, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=10, choices=ROLES)
    content = models.TextField()
    # Таблица ответа: {"columns", "rows", "truncated", "chart"?} или null.
    table = models.JSONField(null=True, blank=True)
    # Ход работы: шаги (раздумье, поиск, структура, SQL с числом строк) со временем.
    trace = models.JSONField(default=list, blank=True)
    # Время модели и SQL, число вызовов, токены — скорость и цена ответа.
    stats = models.JSONField(default=dict, blank=True)
    # Код сбоя: ответ не получен (ИИ недоступен, вышли лимиты шагов и т.п.).
    error = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
