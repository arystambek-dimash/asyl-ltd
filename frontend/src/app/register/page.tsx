"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { apiError } from "@/lib/api";
import { useAuth } from "@/store/auth";
import { registerClient } from "@/lib/portal-actions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PasswordInput } from "@/components/ui/password-input";
import { AuthShell } from "@/components/layout/auth-shell";
import { ArrowLeft, UserPlus } from "lucide-react";

export default function RegisterPage() {
  const router = useRouter();
  const { adoptSession } = useAuth();
  const [f, setF] = useState({
    username: "",
    password: "",
    first_name: "",
    last_name: "",
    company_name: "",
    phone: "",
    iin: "",
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const upd = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const { access, refresh } = await registerClient(f);
      await adoptSession(access, refresh);
      router.replace("/portal/catalog");
    } catch (err) {
      setError(apiError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell
      eyebrow="Кабинет клиента"
      title="Создание аккаунта"
      description="Заполните реквизиты один раз — они будут автоматически использоваться при оформлении заказов."
      wide
    >
      <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5 sm:col-span-2">
          <Label htmlFor="register-first-name">Имя</Label>
          <Input
            id="register-first-name"
            autoComplete="given-name"
            value={f.first_name}
            onChange={upd("first_name")}
            required
          />
        </div>
        <div className="flex flex-col gap-1.5 sm:col-span-2">
          <Label htmlFor="register-last-name">
            Фамилия <span className="font-normal text-[var(--muted-foreground)]">(необязательно)</span>
          </Label>
          <Input id="register-last-name" autoComplete="family-name" value={f.last_name} onChange={upd("last_name")} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="register-company">Название ТОО / ИП</Label>
          <Input
            id="register-company"
            autoComplete="organization"
            value={f.company_name}
            onChange={upd("company_name")}
            placeholder={'Например, ТОО "Сайрам нан"'}
            required
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="register-phone">Телефон</Label>
          <Input id="register-phone" type="tel" autoComplete="tel" value={f.phone} onChange={upd("phone")} required />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="register-iin">ИИН/БИН</Label>
          <Input
            id="register-iin"
            value={f.iin}
            onChange={upd("iin")}
            inputMode="numeric"
            pattern="[0-9]{12}"
            maxLength={12}
            placeholder="12 цифр"
            required
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="register-username">Логин</Label>
          <Input
            id="register-username"
            autoComplete="username"
            value={f.username}
            onChange={upd("username")}
            required
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="register-password">Пароль</Label>
          <PasswordInput
            id="register-password"
            autoComplete="new-password"
            value={f.password}
            onChange={upd("password")}
            minLength={8}
            required
          />
        </div>
        {error && (
          <p
            role="alert"
            className="rounded-xl border border-[var(--soft-red-border)] bg-[var(--soft-red)] px-3 py-2.5 text-sm text-[var(--destructive)] sm:col-span-2"
          >
            {error}
          </p>
        )}
        <Button type="submit" size="lg" disabled={busy} className="mt-1 w-full sm:col-span-2">
          <UserPlus className="size-4" />
          {busy ? "Создаём аккаунт…" : "Создать аккаунт"}
        </Button>
        <Link
          href="/login"
          className="flex items-center justify-center gap-1.5 text-center text-sm font-medium text-[var(--muted-foreground)] transition hover:text-[var(--foreground)] sm:col-span-2"
        >
          <ArrowLeft className="size-3.5" /> Уже есть аккаунт? Войти
        </Link>
      </form>
    </AuthShell>
  );
}
