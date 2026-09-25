from __future__ import annotations

from typing import Self

from django.db import models


class SingletonModel(models.Model):
    """Одна строка на всё приложение (настройки, состояние процесса).

    Уникальное ``singleton=True`` не даёт появиться второй строке; ``load()``
    создаёт её при первом обращении.
    """

    singleton = models.BooleanField(default=True, unique=True, editable=False)

    class Meta:
        abstract = True

    @classmethod
    def load(cls) -> Self:
        row, _ = cls.objects.get_or_create(singleton=True)
        return row
