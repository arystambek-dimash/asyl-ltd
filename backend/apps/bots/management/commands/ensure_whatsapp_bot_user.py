"""Создать или вернуть к норме сервисного пользователя WhatsApp-бота."""
from django.core.management.base import BaseCommand

from apps.bots.service_user import BOT_PERMISSION_CODES, ensure_bot_user


class Command(BaseCommand):
    help = "Сервисный пользователь WhatsApp-бота: без входа, только права проведения отчётов о вагонах"

    def handle(self, *args, **options):
        user = ensure_bot_user()
        self.stdout.write(f"{user.username}: права {', '.join(BOT_PERMISSION_CODES)}")
