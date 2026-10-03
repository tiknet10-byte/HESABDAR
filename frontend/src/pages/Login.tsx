import { Lock, Sparkles } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { api, setToken } from "../lib/api";
import { useAuth } from "../lib/hooks";

export default function Login() {
  const { setUser } = useAuth();
  const [setup, setSetup] = useState(false);
  const [f, setF] = useState({ username: "", password: "", full_name: "", salon_name: "" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api("/api/auth/status").then((s) => setSetup(s.needs_setup)).catch(() => {});
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      const r = await api(setup ? "/api/auth/setup" : "/api/auth/login", { body: f });
      setToken(r.access_token);
      setUser(r.user);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-full lg:grid-cols-2">
      <div className="relative hidden overflow-hidden bg-gradient-to-br from-pink-500 via-fuchsia-600 to-violet-700 p-12 text-white lg:flex lg:flex-col lg:justify-between">
        <div className="absolute -left-20 -top-20 h-80 w-80 rounded-full bg-white/10 blur-2xl" />
        <div className="absolute -bottom-24 right-10 h-96 w-96 rounded-full bg-pink-300/20 blur-3xl" />
        <div className="relative flex items-center gap-3 text-2xl font-extrabold"><img src="/favicon.svg" className="h-12 w-12 rounded-2xl ring-2 ring-white/40" alt="" />حسابدار سالن</div>
        <div className="relative space-y-5">
          <h2 className="text-4xl font-extrabold leading-tight">حسابداری زیبا،<br />برای کسب‌وکار زیبایی</h2>
          <ul className="space-y-3 text-white/90">
            {["ثبت فروش، بیعانه و دریافتی همه کارتخوان‌ها و کارت‌ها", "تطبیق خودکار رسید مشتری با واریز بانک", "اسکن دفتر فروش و ثبت خودکار", "دستیار هوش مصنوعی، گزارش و پیش‌بینی"].map((t) => (
              <li key={t} className="flex items-center gap-2"><Sparkles size={18} />{t}</li>
            ))}
          </ul>
        </div>
        <div className="relative text-sm text-white/70">داده‌ها رمزگذاری و به صورت خودکار پشتیبان‌گیری می‌شوند.</div>
      </div>
      <div className="flex items-center justify-center p-6">
        <form onSubmit={submit} className="card fade-up w-full max-w-md space-y-4 p-8">
          <div className="mb-2 flex items-center gap-3">
            <div className="grid h-12 w-12 place-items-center rounded-2xl bg-gradient-to-br from-pink-500 to-violet-600 text-white shadow-lg shadow-violet-500/30"><Lock size={22} /></div>
            <div>
              <h1 className="text-xl font-extrabold">{setup ? "راه‌اندازی اولیه" : "ورود به حساب"}</h1>
              <p className="muted text-sm">{setup ? "حساب مالک سالن را بسازید" : "خوش آمدید 🌸"}</p>
            </div>
          </div>
          {setup && (
            <>
              <input className="input" placeholder="نام سالن" value={f.salon_name} onChange={(e) => setF({ ...f, salon_name: e.target.value })} />
              <input className="input" placeholder="نام و نام خانوادگی شما" value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} />
            </>
          )}
          <input className="input" dir="ltr" autoComplete="username" placeholder="نام کاربری" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} required />
          <input className="input" dir="ltr" type="password" autoComplete={setup ? "new-password" : "current-password"} placeholder="رمز عبور" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} required />
          {setup && <p className="muted text-xs">حداقل ۸ کاراکتر، ترکیب حروف و عدد.</p>}
          {err && <div className="rounded-xl bg-rose-500/10 px-3 py-2 text-sm text-rose-600">{err}</div>}
          <button className="btn btn-primary w-full py-3" disabled={busy}>{setup ? "ساخت حساب و ورود" : "ورود"}</button>
        </form>
      </div>
    </div>
  );
}
