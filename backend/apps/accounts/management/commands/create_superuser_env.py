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
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Создать суперпользователя из SUPER_ADMIN_EMAIL / SUPER_ADMIN_PASS"

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

        User.objects.create_superuser(
            username=username, email=email, password=password
        )
        self.stdout.write(self.style.SUCCESS(f"Создан суперпользователь: {email}"))
