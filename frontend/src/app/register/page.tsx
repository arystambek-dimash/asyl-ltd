"use client";
import { useState } from "react";
import type { AxiosError } from "axios";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Plus } from "lucide-react";
import { apiError } from "@/lib/api";
import { useAuth } from "@/store/auth";
import { registerClient } from "@/lib/portal-actions";
import { DEFAULT_PHONE_COUNTRY, isPhoneComplete, missingPhoneDigits } from "@/lib/phone";
import { OTHER_COUNTRY } from "@/lib/countries";
import { pluralRu } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Field, fieldErrorId } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { PasswordInput } from "@/components/ui/password-input";
import { PhoneInput } from "@/components/ui/phone-input";

const EMPTY_FORM = {
  first_name: "",
  last_name: "",
  phone: "",
  username: "",
  password: "",
  company_name: "",
  iin: "",
};
type FormKey = keyof typeof EMPTY_FORM;
type FieldErrors = Partial<Record<FormKey, string>>;

// Порядок полей на экране: фокус уходит на первое ошибочное.
const FIELD_ORDER = Object.keys(EMPTY_FORM) as FormKey[];
const REQUISITE_FIELDS: FormKey[] = ["company_name", "iin"];
const fieldId = (key: FormKey) => `register-${key.replace("_", "-")}`;

function validate(f: typeof EMPTY_FORM): FieldErrors {
  const errors: FieldErrors = {};
  if (!f.first_name.trim()) errors.first_name = "Введите имя";
  if (!f.phone) errors.phone = "Введите номер телефона";
  else if (!isPhoneComplete(f.phone)) {
    const missing = missingPhoneDigits(f.phone);
    errors.phone = `Номер неполный: ещё ${missing} ${pluralRu(missing, ["цифра", "цифры", "цифр"])}`;
  }
  if (!f.username.trim()) errors.username = "Придумайте логин";
  if (f.password.length < 8) errors.password = "Пароль — минимум 8 символов";
  if (f.iin && f.iin.length !== 12) errors.iin = "ИИН/БИН — 12 цифр";
  return errors;
}

/** Ошибки сервера по полям — чтобы «логин занят» стоял под логином, а не внизу формы. */
function serverFieldErrors(err: unknown): FieldErrors {
  const response = (err as AxiosError<{ detail?: unknown }>).response;
  // Обработчик исключений API кладёт ошибки полей в detail: {"detail": {"username": [...]}}.
  const fields = response?.status === 400 ? response.data?.detail : null;
  if (!fields || typeof fields !== "object") return {};
  const errors: FieldErrors = {};
  for (const key of FIELD_ORDER) {
    const value = (fields as Record<string, unknown>)[key];
    const message = Array.isArray(value) ? value.find((item) => typeof item === "string") : value;
    if (typeof message === "string") errors[key] = message;
  }
  return errors;
}

export default function RegisterPage() {
  const router = useRouter();
  const { adoptSession } = useAuth();
  const [f, setF] = useState(EMPTY_FORM);
  const [country, setCountry] = useState(DEFAULT_PHONE_COUNTRY.name);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [showRequisites, setShowRequisites] = useState(false);

  function set(key: FormKey, value: string) {
    setF((current) => ({ ...current, [key]: value }));
    setErrors((current) => (current[key] ? { ...current, [key]: undefined } : current));
  }
  const upd = (key: FormKey) => (e: React.ChangeEvent<HTMLInputElement>) => set(key, e.target.value);

  function showErrors(next: FieldErrors) {
    setErrors(next);
    const first = FIELD_ORDER.find((key) => next[key]);
    if (!first) return;
    if (REQUISITE_FIELDS.includes(first)) setShowRequisites(true);
    requestAnimationFrame(() => document.getElementById(fieldId(first))?.focus());
  }

  // Общие атрибуты ошибки для контрола поля.
  const invalidProps = (key: FormKey) =>
    errors[key] ? { "aria-invalid": true, "aria-describedby": fieldErrorId(fieldId(key)) } : {};

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    const invalid = validate(f);
    if (Object.keys(invalid).length) {
      showErrors(invalid);
      return;
    }
    setBusy(true);
    try {
      const { access, refresh } = await registerClient({
        ...f,
        first_name: f.first_name.trim(),
        last_name: f.last_name.trim(),
        username: f.username.trim(),
        company_name: f.company_name.trim(),
        country: country === OTHER_COUNTRY ? "" : country,
      });
      await adoptSession(access, refresh);
      router.replace("/portal/catalog");
    } catch (err) {
      const fieldErrors = serverFieldErrors(err);
      if (Object.keys(fieldErrors).length) showErrors(fieldErrors);
      else setError(apiError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--muted)]/40 p-4">
      <div className="w-full max-w-sm animate-fade-up">
        <div className="mb-6 text-center">
          <h1 className="text-xl font-bold">Регистрация клиента</h1>
          <p className="mt-1 text-sm text-[var(--muted-foreground)]">Минута — и сразу откроется каталог</p>
        </div>
        <div className="rounded-xl border bg-[var(--card)] p-6 shadow-sm">
          {/* 16px в полях на телефоне: иначе iOS зумит страницу при каждом фокусе. */}
          <form onSubmit={submit} noValidate className="flex flex-col gap-4 [&_input]:text-base sm:[&_input]:text-sm">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Имя" htmlFor={fieldId("first_name")} error={errors.first_name}>
                <Input
                  id={fieldId("first_name")}
                  autoFocus
                  autoComplete="given-name"
                  autoCapitalize="words"
                  value={f.first_name}
                  onChange={upd("first_name")}
                  {...invalidProps("first_name")}
                />
              </Field>
              <Field label="Фамилия" htmlFor={fieldId("last_name")} error={errors.last_name}>
                <Input
                  id={fieldId("last_name")}
                  autoComplete="family-name"
                  autoCapitalize="words"
                  placeholder="по желанию"
                  value={f.last_name}
                  onChange={upd("last_name")}
                  {...invalidProps("last_name")}
                />
              </Field>
            </div>
            <Field label="Телефон" htmlFor={fieldId("phone")} error={errors.phone}>
              <PhoneInput
                id={fieldId("phone")}
                value={f.phone}
                onChange={(value) => set("phone", value)}
                onCountryChange={setCountry}
                {...invalidProps("phone")}
              />
            </Field>
            <Field
              label="Логин"
              htmlFor={fieldId("username")}
              hint="Понадобится для входа — удобно взять e-mail"
              error={errors.username}
            >
              <Input
                id={fieldId("username")}
                autoComplete="username"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                value={f.username}
                onChange={upd("username")}
                {...invalidProps("username")}
              />
            </Field>
            <Field
              label="Пароль"
              htmlFor={fieldId("password")}
              hint="Минимум 8 символов, не только цифры"
              error={errors.password}
            >
              <PasswordInput
                id={fieldId("password")}
                autoComplete="new-password"
                value={f.password}
                onChange={upd("password")}
                {...invalidProps("password")}
              />
            </Field>
            {showRequisites ? (
              <div className="flex flex-col gap-4 border-t pt-4">
                <div className="text-[12px] text-[var(--muted-foreground)]">
                  Реквизиты для счетов — необязательно, можно добавить позже
                </div>
                <Field label="Название ТОО / ИП" htmlFor={fieldId("company_name")} error={errors.company_name}>
                  <Input
                    id={fieldId("company_name")}
                    autoComplete="organization"
                    placeholder={'Например, ТОО "Сайрам нан"'}
                    value={f.company_name}
                    onChange={upd("company_name")}
                    {...invalidProps("company_name")}
                  />
                </Field>
                <Field label="ИИН/БИН" htmlFor={fieldId("iin")} error={errors.iin}>
                  <Input
                    id={fieldId("iin")}
                    inputMode="numeric"
                    placeholder="12 цифр"
                    className="tabular-nums"
                    value={f.iin}
                    onChange={(e) => set("iin", e.target.value.replace(/\D/g, "").slice(0, 12))}
                    {...invalidProps("iin")}
                  />
                </Field>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => {
                  setShowRequisites(true);
                  requestAnimationFrame(() => document.getElementById(fieldId("company_name"))?.focus());
                }}
                className="-my-1 flex items-center gap-1.5 self-start rounded-md py-1 text-sm text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
              >
                <Plus className="size-4" />
                Добавить ТОО / ИП и ИИН/БИН
              </button>
            )}
            {error && (
              <p
                role="alert"
                className="rounded-md bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]"
              >
                {error}
              </p>
            )}
            <Button type="submit" disabled={busy} className="mt-1">
              {busy ? "Регистрация…" : "Зарегистрироваться"}
            </Button>
            <Link href="/login" className="text-center text-sm text-[var(--muted-foreground)] underline">
              Уже есть аккаунт? Войти
            </Link>
          </form>
        </div>
      </div>
    </div>
  );
}
