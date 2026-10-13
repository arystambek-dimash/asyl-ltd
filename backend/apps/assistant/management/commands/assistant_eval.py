"""Проверить ИИ-ассистента на вопросах с эталоном: сколько ответов верно и как быстро."""

import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.assistant.evaluation import CASES, run_case, summary


class Command(BaseCommand):
    help = "Задать ассистенту вопросы с эталоном из системы и вывести верность и скорость ответов."

    def add_arguments(self, parser):
        parser.add_argument("--case", action="append", help="ключ вопроса; можно несколько раз")
        parser.add_argument("--model", help="модель вместо ASSISTANT_AI_MODEL")
        parser.add_argument("--effort", help="усилие рассуждения вместо ASSISTANT_AI_REASONING_EFFORT («» — без)")
        parser.add_argument("--user", help="логин спрашивающего; по умолчанию первый суперпользователь")
        parser.add_argument("--output", help="файл JSON с ответами, SQL и статистикой")

    def handle(self, *args, **options):
        if not settings.OPENAI_API_KEY:
            raise CommandError("Нет OPENAI_API_KEY.")
        users = get_user_model().objects.filter(is_active=True)
        user = (
            users.filter(username=options["user"]).first() if options["user"]
            else users.filter(is_superuser=True).order_by("pk").first()
        )
        if user is None:
            raise CommandError("Нет пользователя для вопросов.")
        cases = [case for case in CASES if not options["case"] or case.key in options["case"]]
        results = []
        for case in cases:
            result = run_case(case, user, model=options["model"], effort=options["effort"])
            results.append(result)
            stats = result["stats"]
            mark = self.style.SUCCESS("✓") if result["ok"] else self.style.ERROR("✗")
            self.stdout.write(
                f"{mark} {case.key:<24} {result['seconds']:>6} с  "
                f"вызовов {stats.get('model_calls', 0)}  SQL {stats.get('sql_queries', 0)}  "
                f"токенов {stats.get('input_tokens', 0)} (кэш {stats.get('cached_tokens', 0)}) "
                f"→ {stats.get('output_tokens', 0)}"
                + (f"  нет: {', '.join(result['missing'])}" if result["missing"] else "")
            )
        total = summary(results)
        self.stdout.write(json.dumps(total, ensure_ascii=False))
        if options["output"]:
            with open(options["output"], "w", encoding="utf-8") as file:
                json.dump({"summary": total, "results": results}, file, ensure_ascii=False, indent=2)
