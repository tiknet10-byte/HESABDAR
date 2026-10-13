import { Bot, Check, Send, Sparkles, Wrench } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Card, PageHeader, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { money } from "../lib/format";
import { useApi, useAuth, useToast } from "../lib/hooks";

// confirm: a change the assistant proposed; it is done only when the user presses the button
type Call = { tool: string; input?: Record<string, any>; ok?: boolean | null; confirm?: boolean; busy?: boolean; done?: boolean; error?: string };
type Msg = { role: "user" | "assistant"; content: string; tools?: Call[] };
const ACTIONS: Record<string, string> = { create_customer: "ساخت مشتری", register_deposit: "ثبت بیعانه", teach_service_alias: "یاد دادن نام دیگر یک خدمت" };
const ARGS: Record<string, string> = {
  full_name: "نام", mobile: "موبایل", instagram: "اینستاگرام", customer_id: "مشتری", amount_rial: "مبلغ",
  payment_account_id: "حساب دریافت", service_id: "خدمت", reference: "شماره پیگیری", note: "توضیح", alias: "نام دیگر",
};

/** A proposal shows what it really points to (the customer's, account's and service's name), so the person can check
 * it before pressing the button; a number alone could be any customer. */
function ArgValue({ name, value }: { name: string; value: any }) {
  const id = Number(value);
  const cust = useApi<any>(name === "customer_id" && id > 0 ? `/api/customers/${id}` : null);
  const accounts = useApi<any[]>(name === "payment_account_id" ? "/api/accounts" : null);
  const services = useApi<any[]>(name === "service_id" ? "/api/services" : null);
  if (name === "amount_rial") return <b>{money(id)}</b>;
  const found =
    name === "customer_id" ? cust.data && `${cust.data.full_name}${cust.data.mobile ? ` (${cust.data.mobile})` : ""}`
    : name === "payment_account_id" ? accounts.data?.find((a) => a.id === id)?.name
    : name === "service_id" ? services.data?.find((s) => s.id === id)?.name
    : null;
  if (found) return <b>{found}</b>;
  if (["customer_id", "payment_account_id", "service_id"].includes(name)) {
    const loading = cust.loading || accounts.loading || services.loading;
    return <span className={loading ? "muted" : "text-rose-600"}>{loading ? "…" : `شماره ${String(value)} پیدا نشد`}</span>;
  }
  return <>{String(value)}</>;
}
const SUGGEST = [
  "فروش این ماه را با ماه قبل مقایسه کن و دلیل تغییرات را بگو",
  "کدام لاین سودآورتر است و چه پیشنهادی برای رشد داری؟",
  "پیش‌بینی درآمد ماه آینده چقدر است؟",
  "کدام مشتریان وفادار مدتی است نیامده‌اند؟",
  "بیعانه‌های باز را خلاصه کن",
  "رسیدهای مشکوک یا مغایرت‌دار را بررسی کن",
];

export default function Assistant() {
  const { user } = useAuth();
  const toast = useToast();
  const key = `hesabdar.chat.${user?.id ?? 0}`; // each user's own chat: the next person never sees the previous one's
  const [msgs, setMsgs] = useState<Msg[]>(() => {
    try {
      return JSON.parse(sessionStorage.getItem(key) || "[]");
    } catch {
      return [];
    }
  });
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const dash = useApi<any>("/api/dashboard");

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth" });
    try {
      sessionStorage.setItem(key, JSON.stringify(msgs.slice(-40)));
    } catch {
      /* ignore */
    }
  }, [msgs, key]);

  const patchCall = (mi: number, ti: number, patch: Partial<Call>) =>
    setMsgs((ms) => ms.map((m, i) => (i !== mi ? m : { ...m, tools: m.tools?.map((t, k) => (k !== ti ? t : { ...t, ...patch })) })));

  async function confirmCall(mi: number, ti: number) {
    const t = msgs[mi]?.tools?.[ti];
    if (!t || t.busy || t.done) return;
    patchCall(mi, ti, { busy: true, error: undefined });
    try {
      const r = await api(`/api/ai/tools/${t.tool}`, { body: { arguments: t.input ?? {} } });
      if (r && typeof r === "object" && r.error) throw new Error(String(r.error));
      patchCall(mi, ti, { busy: false, done: true });
      toast("انجام شد");
    } catch (e: any) {
      patchCall(mi, ti, { busy: false, error: e.message });
      toast(`انجام نشد: ${e.message}`, "error");
    }
  }

  async function send(q: string) {
    if (!q.trim() || busy) return;
    const next = [...msgs, { role: "user" as const, content: q }];
    setMsgs(next);
    setText("");
    setBusy(true);
    try {
      const r = await api("/api/ai/chat", { body: { messages: next.map(({ role, content }) => ({ role, content })) } });
      setMsgs([...next, { role: "assistant", content: r.reply, tools: r.tool_calls }]);
    } catch (e: any) {
      setMsgs([...next, { role: "assistant", content: "⚠️ " + e.message }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-[calc(100vh-8rem)] flex-col">
      <PageHeader title="دستیار هوش مصنوعی" subtitle={dash.data?.ai_available ? "متصل به Claude · با دسترسی به داده‌های واقعی سالن" : "حالت آفلاین · برای تحلیل پیشرفته کلید API را تنظیم کنید"} icon={<Bot size={22} />}
        actions={msgs.length ? <button className="btn btn-sm" onClick={() => setMsgs([])}>گفتگوی جدید</button> : null} />
      <Card className="flex min-h-0 flex-1 flex-col" pad={false}>
        <div className="flex-1 space-y-4 overflow-y-auto p-5">
          {msgs.length === 0 && (
            <div className="flex h-full flex-col items-center justify-center gap-5 text-center">
              <div className="grid h-16 w-16 place-items-center rounded-3xl bg-gradient-to-br from-pink-500 to-violet-600 text-white shadow-xl shadow-violet-500/30"><Sparkles size={30} /></div>
              <div><div className="text-lg font-extrabold">از من درباره سالن‌تان بپرسید</div><div className="muted text-sm">تحلیل فروش، پیش‌بینی، مشتریان، بیعانه‌ها و مغایرت‌ها</div></div>
              <div className="grid max-w-2xl gap-2 sm:grid-cols-2">
                {SUGGEST.map((s) => <button key={s} className="btn justify-start text-right text-sm font-medium" onClick={() => send(s)}>{s}</button>)}
              </div>
            </div>
          )}
          {msgs.map((m, i) => (
            <div key={i} className={`flex ${m.role === "user" ? "justify-start" : "justify-end"}`}>
              <div className={`max-w-[85%] rounded-3xl px-4 py-3 text-sm leading-7 ${m.role === "user" ? "bg-gradient-to-l from-pink-500 to-violet-600 text-white" : ""}`}
                style={m.role === "assistant" ? { background: "var(--surface-solid)", border: "1px solid var(--border)" } : {}}>
                <div className="whitespace-pre-wrap">{m.content}</div>
                {m.tools && m.tools.some((t) => !t.confirm) && (
                  <div className="muted mt-2 flex flex-wrap gap-1 text-[11px]">
                    {m.tools.map((t, k) => !t.confirm && <span key={k} className="badge bg-violet-500/10"><Wrench size={10} />{t.tool}</span>)}
                  </div>
                )}
                {m.tools?.map((t, k) => t.confirm && (
                  <div key={k} className="mt-2 rounded-2xl border border-amber-400 bg-amber-500/10 p-3 text-xs leading-6">
                    <div className="font-bold">پیشنهاد دستیار: {ACTIONS[t.tool] ?? t.tool}</div>
                    {Object.entries(t.input ?? {}).map(([a, v]) => (
                      <div key={a}><span className="muted">{ARGS[a] ?? a}: </span><ArgValue name={a} value={v} /></div>
                    ))}
                    {t.done ? (
                      <div className="mt-1 flex items-center gap-1 font-bold text-emerald-600"><Check size={14} />انجام شد</div>
                    ) : (
                      <>
                        <p className="muted mt-1">تا دکمه را نزنید هیچ چیزی ثبت نمی‌شود. اگر درست نیست، دکمه را نزنید.</p>
                        {t.error && <p className="mt-1 text-rose-600">انجام نشد: {t.error}</p>}
                        <button className="btn btn-primary btn-sm mt-1" disabled={t.busy} onClick={() => confirmCall(i, k)}>
                          {t.busy ? <Spinner /> : <Check size={14} />}تأیید و انجام
                        </button>
                      </>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}
          {busy && <div className="flex justify-end"><div className="rounded-3xl px-4 py-3" style={{ background: "var(--surface-solid)" }}><Spinner /></div></div>}
          <div ref={end} />
        </div>
        <form className="flex gap-2 border-t p-3" style={{ borderColor: "var(--border)" }} onSubmit={(e) => { e.preventDefault(); send(text); }}>
          <input className="input" placeholder="سؤال خود را بنویسید…" value={text} onChange={(e) => setText(e.target.value)} />
          <button className="btn btn-primary" disabled={busy || !text.trim()}><Send size={16} /></button>
        </form>
      </Card>
    </div>
  );
}
