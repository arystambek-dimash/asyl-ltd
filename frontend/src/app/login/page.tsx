"use client";
import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import Link from "next/link";
import { useAuth } from "@/store/auth";
import { homeFor } from "@/lib/can";
import { apiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PasswordInput } from "@/components/ui/password-input";

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
    <div className="flex min-h-screen items-center justify-center bg-[var(--muted)]/40 p-4">
      <div className="w-full max-w-sm animate-fade-up">
        <div className="mb-8 flex flex-col items-center gap-3">
          <Image
            src="/logo.png"
            alt="ASYL-LTD — Мельничный комплекс"
            width={220}
            height={200}
            className="h-auto w-44 object-contain"
            priority
          />
          <div className="text-center">
            <div className="text-xs uppercase tracking-widest text-[var(--muted-foreground)]">Система учёта цеха</div>
          </div>
        </div>
        <div className="rounded-xl border bg-[var(--card)] p-6 shadow-sm">
          <form onSubmit={submit} className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="u">Логин</Label>
              <Input id="u" value={username} autoFocus onChange={(e) => setUsername(e.target.value)} required />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="p">Пароль</Label>
              <PasswordInput id="p" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </div>
            {error && (
              <p className="rounded-md bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]">
                {error}
              </p>
            )}
            <Button type="submit" disabled={busy} className="mt-1">
              {busy ? "Вход…" : "Войти"}
            </Button>
          </form>
          <Link href="/register" className="mt-4 block text-center text-sm text-[var(--muted-foreground)] underline">
            Нет аккаунта? Регистрация
          </Link>
        </div>
      </div>
    </div>
  );
}
