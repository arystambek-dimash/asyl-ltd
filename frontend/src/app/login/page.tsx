"use client";
import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/store/auth";
import { homeFor } from "@/lib/can";
import { apiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PasswordInput } from "@/components/ui/password-input";
import { AuthShell } from "@/components/layout/auth-shell";
import { ArrowRight, LockKeyhole } from "lucide-react";

export default function LoginPage() {
  const { login, me, loadMe } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    loadMe();
  }, [loadMe]);
  useEffect(() => {
    if (me) router.replace(homeFor(me));
  }, [me, router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const m = await login(username, password);
      router.replace(homeFor(m));
    } catch (err) {
      setError(apiError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell
      eyebrow="Рабочий доступ"
      title="Вход в систему"
      description="Используйте учётную запись сотрудника или клиента. Все действия фиксируются в журнале."
    >
      <div className="mb-5 flex items-center gap-3 rounded-2xl bg-[var(--soft-blue)] p-3.5">
        <span className="flex size-9 items-center justify-center rounded-xl bg-[var(--card)] text-[var(--ring)] shadow-sm">
          <LockKeyhole className="size-[18px]" />
        </span>
        <div>
          <div className="text-xs font-bold">Безопасная сессия</div>
          <div className="text-[11px] text-[var(--muted-foreground)]">Доступ определяется вашей ролью в цехе</div>
        </div>
      </div>
      <form onSubmit={submit} className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="u">Логин</Label>
          <Input
            id="u"
            value={username}
            autoFocus
            autoComplete="username"
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="p">Пароль</Label>
          <PasswordInput
            id="p"
            value={password}
            autoComplete="current-password"
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </div>
        {error && (
          <p
            role="alert"
            className="rounded-xl border border-[var(--soft-red-border)] bg-[var(--soft-red)] px-3 py-2.5 text-sm text-[var(--destructive)]"
          >
            {error}
          </p>
        )}
        <Button type="submit" size="lg" disabled={busy} className="mt-1 w-full">
          {busy ? "Проверяем доступ…" : "Войти в систему"}
          {!busy && <ArrowRight className="size-4" />}
        </Button>
      </form>
      <Link
        href="/register"
        className="mt-5 block text-center text-sm font-medium text-[var(--muted-foreground)] transition hover:text-[var(--ring)]"
      >
        Нет аккаунта? <span className="font-bold text-[var(--foreground)]">Регистрация клиента</span>
      </Link>
    </AuthShell>
  );
}
