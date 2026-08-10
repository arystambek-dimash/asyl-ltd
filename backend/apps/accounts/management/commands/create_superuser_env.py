"""
Создаёт суперпользователя из переменных окружения.

Читает:
  SUPER_ADMIN_EMAIL — email (используется также как username);
  SUPER_ADMIN_PASS  — пароль.

Идемпотентна: если пользователь с таким username/email уже есть, ничего не
делает (опционально обновляет пароль при SUPER_ADMIN_RESET_PASSWORD=1).
Запускается автоматически при подъёме Docker (см. entrypoint.sh).
"""
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

_PLACEHOLDER_PASSWORDS = frozenset({
    "change-me",
    "changeme",
    "password",
    "replace-me",
})


class Command(BaseCommand):
    help = "Создать суперпользователя из SUPER_ADMIN_EMAIL / SUPER_ADMIN_PASS"

    @staticmethod
    def _validate_password(password, user):
        if password.casefold() in _PLACEHOLDER_PASSWORDS:
            raise CommandError(
                "SUPER_ADMIN_PASS не должен быть шаблонным значением."
            )
        try:
            validate_password(password, user=user)
        except ValidationError as exc:
            raise CommandError(
                "SUPER_ADMIN_PASS не прошёл Django password validators: "
                + "; ".join(exc.messages)
            ) from exc

    def handle(self, *args, **options):
        email = os.environ.get("SUPER_ADMIN_EMAIL", "").strip()
        password = os.environ.get("SUPER_ADMIN_PASS", "").strip()

        if not email or not password:
            self.stdout.write(
                self.style.WARNING(
                    "SUPER_ADMIN_EMAIL / SUPER_ADMIN_PASS не заданы — "
                    "суперпользователь не создан."
                )
            )
            return

        User = get_user_model()
        username = email  # username совпадает с email

        user = User.objects.filter(username=username).first() or \
            User.objects.filter(email=email).first()

        if user is not None:
            if os.environ.get("SUPER_ADMIN_RESET_PASSWORD") == "1":
                self._validate_password(password, user)
                user.set_password(password)
                user.is_superuser = True
                user.is_staff = True
                user.save()
                self.stdout.write(
                    self.style.SUCCESS(f"Пароль суперпользователя обновлён: {email}")
                )
            else:
                self.stdout.write(f"Суперпользователь уже существует: {email}")
            return

        self._validate_password(
            password,
            User(username=username, email=email),
        )
        User.objects.create_superuser(
            username=username, email=email, password=password
        )
        self.stdout.write(self.style.SUCCESS(f"Создан суперпользователь: {email}"))
