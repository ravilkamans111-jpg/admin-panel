"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ApiError, AuthExpiredError, createDjangoUser } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// The monolith's Django admin "Add user" form: username, password, password
// confirmation. The new account is active but not staff; staff / superuser,
// names and email are set on its change page (same two-step flow as Django).

type FieldErrors = Partial<Record<"username" | "password1" | "password2", string[]>>;

const HELP_PASSWORD = [
  "Пароль не должен быть слишком похож на другие ваши личные данные.",
  "Пароль должен содержать как минимум 8 символов.",
  "Пароль не должен быть одним из широко распространённых паролей.",
  "Пароль не может состоять только из цифр.",
];

const input =
  "w-full max-w-xs rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent";

function parseErrors(err: unknown): { fields: FieldErrors; general: string | null } {
  if (err instanceof ApiError) {
    try {
      const detail = JSON.parse(err.message)?.detail;
      if (detail && typeof detail === "object") return { fields: detail as FieldErrors, general: null };
    } catch {
      /* not JSON — fall through to the plain message */
    }
    return { fields: {}, general: err.message };
  }
  return { fields: {}, general: err instanceof Error ? err.message : "Не удалось создать пользователя." };
}

export default function AddUserPage() {
  const { logout } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password1, setPassword1] = useState("");
  const [password2, setPassword2] = useState("");
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [general, setGeneral] = useState<string | null>(null);

  async function submit(e: FormEvent, then: "save" | "add" | "continue") {
    e.preventDefault();
    setSaving(true);
    setErrors({});
    setGeneral(null);
    try {
      const created = await createDjangoUser({ username, password1, password2 });
      if (then === "add") {
        setUsername("");
        setPassword1("");
        setPassword2("");
      } else {
        router.push(then === "continue" ? `/admin/users/${created.id}` : "/admin/users");
      }
    } catch (err) {
      if (err instanceof AuthExpiredError) {
        logout();
        router.replace("/login");
        return;
      }
      const parsed = parseErrors(err);
      setErrors(parsed.fields);
      setGeneral(parsed.general);
    } finally {
      setSaving(false);
    }
  }

  const row = (label: string, field: keyof FieldErrors, value: string, set: (v: string) => void, help?: React.ReactNode, type = "text") => (
    <div className="grid gap-1 border-b border-[var(--border)] py-4 sm:grid-cols-[14rem_1fr] sm:gap-4">
      <label className="text-sm font-semibold">{label}:</label>
      <div className="space-y-1">
        {errors[field]?.map((m) => (
          <p key={m} className="text-sm text-red-600">{m}</p>
        ))}
        <input type={type} value={value} onChange={(e) => set(e.target.value)} autoComplete={type === "password" ? "new-password" : "off"} className={input} />
        {help && <div className="text-xs text-[var(--text-muted)]">{help}</div>}
      </div>
    </div>
  );

  return (
    <div className="max-w-3xl space-y-4">
      <div>
        <Link href="/admin/users" className="text-sm text-accent hover:underline">
          ← Пользователи
        </Link>
        <h1 className="mt-1 text-lg font-semibold">Add user</h1>
      </div>
      <p className="text-sm">First, enter a username and password. Then, you&apos;ll be able to edit more user options.</p>
      {general && (
        <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {general}
        </div>
      )}
      <form onSubmit={(e) => submit(e, "save")} className="card px-4">
        {row("Username", "username", username, setUsername, "Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.")}
        {row(
          "Password",
          "password1",
          password1,
          setPassword1,
          <ul className="list-disc space-y-0.5 pl-4">
            {HELP_PASSWORD.map((h) => (
              <li key={h}>{h}</li>
            ))}
          </ul>,
          "password"
        )}
        {row("Password confirmation", "password2", password2, setPassword2, "Enter the same password as before, for verification.", "password")}
        <div className="flex flex-wrap gap-2 py-4">
          <button type="submit" disabled={saving} className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-dark disabled:opacity-60">
            SAVE
          </button>
          <button type="button" disabled={saving} onClick={(e) => submit(e, "add")} className="rounded-md border border-[var(--border)] px-4 py-2 text-sm hover:border-accent disabled:opacity-60">
            Save and add another
          </button>
          <button type="button" disabled={saving} onClick={(e) => submit(e, "continue")} className="rounded-md border border-[var(--border)] px-4 py-2 text-sm hover:border-accent disabled:opacity-60">
            Save and continue editing
          </button>
        </div>
      </form>
    </div>
  );
}
