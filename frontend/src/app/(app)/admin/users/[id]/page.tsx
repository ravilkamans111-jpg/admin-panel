"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  ApiError,
  AuthExpiredError,
  changeUserPassword,
  fetchUserEditData,
  saveUserForm,
  type UserEditData,
  type UserFormValues,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { DualListBox } from "@/components/DualListBox";

// The monolith's Django admin "Change user" page: username, password summary +
// "change password" form, personal info, permissions (active / staff / superuser,
// groups, user permissions) and important dates — saved with SAVE /
// "Save and add another" / "Save and continue editing".

type Errors = Record<string, string[]>;

const input =
  "w-full max-w-xs rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent";

function splitDateTime(value: string | null): { date: string; time: string } {
  if (!value) return { date: "", time: "" };
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return { date: "", time: "" };
  const iso = d.toISOString(); // shown in UTC, same as it is sent back
  return { date: iso.slice(0, 10), time: iso.slice(11, 19) };
}

function joinDateTime(date: string, time: string): string | null {
  return date ? `${date}T${time || "00:00:00"}` : null;
}

function parseApiErrors(err: unknown): { fields: Errors; general: string | null } {
  if (err instanceof ApiError) {
    try {
      const detail = JSON.parse(err.message)?.detail;
      if (detail && typeof detail === "object") return { fields: detail as Errors, general: null };
    } catch {
      /* plain message */
    }
    return { fields: {}, general: err.message };
  }
  return { fields: {}, general: err instanceof Error ? err.message : "Не удалось сохранить." };
}

export default function ChangeUserPage() {
  const { id } = useParams<{ id: string }>();
  const { logout } = useAuth();
  const router = useRouter();

  const [data, setData] = useState<UserEditData | null>(null);
  const [form, setForm] = useState<UserFormValues | null>(null);
  const [lastLogin, setLastLogin] = useState({ date: "", time: "" });
  const [joined, setJoined] = useState({ date: "", time: "" });
  const [loadError, setLoadError] = useState<string | null>(null);
  const [errors, setErrors] = useState<Errors>({});
  const [general, setGeneral] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);

  const [showPassword, setShowPassword] = useState(false);
  const [pw1, setPw1] = useState("");
  const [pw2, setPw2] = useState("");
  const [pwErrors, setPwErrors] = useState<Errors>({});
  const [pwDone, setPwDone] = useState(false);

  function handleAuth(err: unknown): boolean {
    if (err instanceof AuthExpiredError) {
      logout();
      router.replace("/login");
      return true;
    }
    return false;
  }

  function load() {
    fetchUserEditData(id)
      .then((d) => {
        setData(d);
        const u = d.user;
        setForm({
          username: u.username, first_name: u.first_name, last_name: u.last_name, email: u.email,
          is_active: u.is_active, is_staff: u.is_staff, is_superuser: u.is_superuser,
          groups: d.groups.chosen, permissions: d.permissions.chosen, last_login: u.last_login, date_joined: u.date_joined,
        });
        setLastLogin(splitDateTime(u.last_login));
        setJoined(splitDateTime(u.date_joined));
      })
      .catch((err) => {
        if (!handleAuth(err)) setLoadError(err instanceof Error ? err.message : "Не удалось загрузить пользователя.");
      });
  }

  useEffect(load, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function save(then: "save" | "add" | "continue") {
    if (!form) return;
    setSaving(true);
    setErrors({});
    setGeneral(null);
    setSaved(false);
    try {
      await saveUserForm(id, {
        ...form,
        last_login: joinDateTime(lastLogin.date, lastLogin.time),
        date_joined: joinDateTime(joined.date, joined.time),
      });
      if (then === "save") router.push("/admin/users");
      else if (then === "add") router.push("/admin/users/new");
      else {
        setSaved(true);
        load();
      }
    } catch (err) {
      if (handleAuth(err)) return;
      const parsed = parseApiErrors(err);
      setErrors(parsed.fields);
      setGeneral(parsed.general);
    } finally {
      setSaving(false);
    }
  }

  async function submitPassword(e: React.FormEvent) {
    e.preventDefault();
    setPwErrors({});
    setPwDone(false);
    try {
      await changeUserPassword(id, pw1, pw2);
      setPw1("");
      setPw2("");
      setPwDone(true);
      setShowPassword(false);
      load();
    } catch (err) {
      if (handleAuth(err)) return;
      setPwErrors(parseApiErrors(err).fields);
    }
  }

  if (loadError) return <p className="text-sm text-red-600">{loadError}</p>;
  if (!data || !form) return <p className="text-sm text-[var(--text-muted)]">Загрузка…</p>;

  const set = <K extends keyof UserFormValues>(key: K, value: UserFormValues[K]) => setForm({ ...form, [key]: value });
  const fieldErr = (name: string) => errors[name]?.map((m) => <p key={m} className="text-sm text-red-600">{m}</p>);
  const row = (label: string, body: React.ReactNode, bold = false) => (
    <div className="grid gap-1 border-b border-[var(--border)] py-3 sm:grid-cols-[12rem_1fr] sm:gap-4">
      <div className={`text-sm ${bold ? "font-semibold" : ""}`}>{label}:</div>
      <div className="space-y-1">{body}</div>
    </div>
  );
  const section = (title: string) => <div className="-mx-4 bg-accent/15 px-4 py-1.5 text-sm font-medium">{title}</div>;
  const check = (field: "is_active" | "is_staff" | "is_superuser", label: string, help: string) => (
    <div className="border-b border-[var(--border)] py-3">
      {fieldErr(field)}
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={form[field]} onChange={(e) => set(field, e.target.checked)} />
        {label}
      </label>
      <p className="mt-1 text-xs text-[var(--text-muted)]">{help}</p>
    </div>
  );
  const dateTime = (value: { date: string; time: string }, setValue: (v: { date: string; time: string }) => void) => (
    <div className="space-y-1 text-sm">
      <div className="flex items-center gap-2">
        <span className="w-12 font-semibold">Date:</span>
        <input type="date" value={value.date} onChange={(e) => setValue({ ...value, date: e.target.value })} className={input} />
      </div>
      <div className="flex items-center gap-2">
        <span className="w-12 font-semibold">Time:</span>
        <input type="time" step={1} value={value.time} onChange={(e) => setValue({ ...value, time: e.target.value })} className={input} />
      </div>
    </div>
  );

  const pwInfo = data.user.password;

  return (
    <div className="max-w-5xl space-y-3">
      <div className="flex items-start justify-between">
        <div>
          <Link href="/admin/users" className="text-sm text-accent hover:underline">
            ← Пользователи
          </Link>
          <h1 className="mt-1 text-lg font-semibold">Change user</h1>
          <p className="font-semibold">{data.user.username}</p>
        </div>
      </div>

      {saved && <div className="rounded-md border border-green-300 bg-green-50 px-3 py-2 text-sm text-green-800 dark:border-green-900 dark:bg-green-950 dark:text-green-300">Изменения сохранены.</div>}
      {pwDone && <div className="rounded-md border border-green-300 bg-green-50 px-3 py-2 text-sm text-green-800 dark:border-green-900 dark:bg-green-950 dark:text-green-300">Пароль изменён.</div>}
      {general && <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">{general}</div>}

      <div className="card px-4">
        {row("Username", <>
          {fieldErr("username")}
          <input value={form.username} onChange={(e) => set("username", e.target.value)} className={input} />
          <p className="text-xs text-[var(--text-muted)]">Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.</p>
        </>, true)}

        {row("Password", <>
          <p className="text-sm">
            {pwInfo.iterations !== undefined ? (
              <>
                <b>algorithm</b>: {pwInfo.algorithm} <b>iterations</b>: {pwInfo.iterations} <b>salt</b>: {pwInfo.salt} <b>hash</b>: {pwInfo.hash}
              </>
            ) : (
              <>
                <b>algorithm</b>: {pwInfo.algorithm} {pwInfo.summary}
              </>
            )}
          </p>
          <p className="text-xs text-[var(--text-muted)]">
            Raw passwords are not stored, so there is no way to see this user&apos;s password, but you can change the password using{" "}
            <button type="button" onClick={() => setShowPassword((v) => !v)} className="text-accent underline">this form</button>.
          </p>
          {showPassword && (
            <form onSubmit={submitPassword} className="mt-2 space-y-2 rounded-md border border-[var(--border)] p-3">
              {(["password1", "password2"] as const).map((f) => (
                <div key={f}>
                  <label className="text-xs font-semibold">{f === "password1" ? "Password" : "Password (again)"}</label>
                  {pwErrors[f]?.map((m) => <p key={m} className="text-sm text-red-600">{m}</p>)}
                  <input type="password" autoComplete="new-password" value={f === "password1" ? pw1 : pw2} onChange={(e) => (f === "password1" ? setPw1 : setPw2)(e.target.value)} className={input} />
                </div>
              ))}
              <button type="submit" className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-dark">Change password</button>
            </form>
          )}
        </>)}

        {section("Personal info")}
        {row("First name", <input value={form.first_name} onChange={(e) => set("first_name", e.target.value)} className={input} />)}
        {row("Last name", <input value={form.last_name} onChange={(e) => set("last_name", e.target.value)} className={input} />)}
        {row("Email address", <input value={form.email} onChange={(e) => set("email", e.target.value)} className={input} />)}

        {section("Permissions")}
        {check("is_active", "Active", "Designates whether this user should be treated as active. Unselect this instead of deleting accounts.")}
        {check("is_staff", "Staff status", "Designates whether the user can log into this admin site.")}
        {check("is_superuser", "Superuser status", "Designates that this user has all permissions without explicitly assigning them.")}
        {row("Groups", <>
          <DualListBox availableTitle="Available groups" chosenTitle="Chosen groups" items={data.groups.available} chosen={form.groups} onChange={(v) => set("groups", v)} />
          <p className="text-xs text-[var(--text-muted)]">The groups this user belongs to. A user will get all permissions granted to each of their groups.</p>
        </>)}
        {row("User permissions", <>
          <DualListBox availableTitle="Available user permissions" chosenTitle="Chosen user permissions" items={data.permissions.available} chosen={form.permissions} onChange={(v) => set("permissions", v)} />
          <p className="text-xs text-[var(--text-muted)]">Specific permissions for this user.</p>
        </>)}

        {section("Important dates")}
        {row("Last login", <>{fieldErr("last_login")}{dateTime(lastLogin, setLastLogin)}</>)}
        {row("Date joined", <>{fieldErr("date_joined")}{dateTime(joined, setJoined)}</>, true)}

        <div className="flex flex-wrap gap-2 py-4">
          <button type="button" disabled={saving} onClick={() => save("save")} className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-dark disabled:opacity-60">SAVE</button>
          <button type="button" disabled={saving} onClick={() => save("add")} className="rounded-md border border-[var(--border)] px-4 py-2 text-sm hover:border-accent disabled:opacity-60">Save and add another</button>
          <button type="button" disabled={saving} onClick={() => save("continue")} className="rounded-md border border-[var(--border)] px-4 py-2 text-sm hover:border-accent disabled:opacity-60">Save and continue editing</button>
        </div>
      </div>
    </div>
  );
}
