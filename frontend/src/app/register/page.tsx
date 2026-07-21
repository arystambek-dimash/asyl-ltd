"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { setTokens, apiError } from "@/lib/api";
import { useAuth } from "@/store/auth";
import { registerClient } from "@/lib/portal-actions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PasswordInput } from "@/components/ui/password-input";

export default function RegisterPage() {
  const router = useRouter();
  const { loadMe } = useAuth();
  const [f, setF] = useState({ username: "", password: "", first_name: "",
    last_name: "", company_name: "", phone: "", iin: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const upd = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setF({ ...f, [k]: e.target.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const { access, refresh } = await registerClient(f);
      setTokens(access, refresh);
      await loadMe();
      router.replace("/portal/catalog");
    } catch (err) { setError(apiError(err)); }
    finally { setBusy(false); }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--muted)]/40 p-4">
      <div className="w-full max-w-sm animate-fade-up">
        <h1 className="mb-6 text-center text-xl font-bold">Регистрация клиента</h1>
        <div className="rounded-xl border bg-[var(--card)] p-6 shadow-sm">
          <form onSubmit={submit} className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5"><Label>Имя</Label>
              <Input value={f.first_name} onChange={upd("first_name")} required /></div>
            <div className="flex flex-col gap-1.5"><Label>Фамилия <span className="font-normal text-[var(--muted-foreground)]">(необязательно)</span></Label>
              <Input value={f.last_name} onChange={upd("last_name")} /></div>
            <div className="flex flex-col gap-1.5"><Label>Название ТОО / ИП</Label>
              <Input value={f.company_name} onChange={upd("company_name")}
                placeholder={'Например, ТОО "Сайрам нан"'} required /></div>
            <div className="flex flex-col gap-1.5"><Label>Телефон</Label>
              <Input value={f.phone} onChange={upd("phone")} required /></div>
            <div className="flex flex-col gap-1.5"><Label>ИИН/БИН</Label>
              <Input value={f.iin} onChange={upd("iin")} inputMode="numeric"
                pattern="[0-9]{12}" maxLength={12} placeholder="12 цифр" required /></div>
            <div className="flex flex-col gap-1.5"><Label>Логин</Label>
              <Input value={f.username} onChange={upd("username")} required /></div>
            <div className="flex flex-col gap-1.5"><Label>Пароль</Label>
              <PasswordInput value={f.password} onChange={upd("password")}
                minLength={8} required /></div>
            {error && <p className="rounded-md bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]">{error}</p>}
            <Button type="submit" disabled={busy} className="mt-1">
              {busy ? "Регистрация…" : "Зарегистрироваться"}</Button>
            <Link href="/login" className="text-center text-sm text-[var(--muted-foreground)] underline">
              Уже есть аккаунт? Войти</Link>
          </form>
        </div>
      </div>
    </div>
  );
}
