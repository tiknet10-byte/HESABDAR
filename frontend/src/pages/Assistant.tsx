import { Bot, Send, Sparkles, Wrench } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Card, PageHeader, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { useApi } from "../lib/hooks";

type Msg = { role: "user" | "assistant"; content: string; tools?: any[] };
const SUGGEST = [
  "فروش این ماه را با ماه قبل مقایسه کن و دلیل تغییرات را بگو",
  "کدام لاین سودآورتر است و چه پیشنهادی برای رشد داری؟",
  "پیش‌بینی درآمد ماه آینده چقدر است؟",
  "کدام مشتریان وفادار مدتی است نیامده‌اند؟",
  "بیعانه‌های باز را خلاصه کن",
  "رسیدهای مشکوک یا مغایرت‌دار را بررسی کن",
];

export default function Assistant() {
  const [msgs, setMsgs] = useState<Msg[]>(() => {
    try {
      return JSON.parse(sessionStorage.getItem("hesabdar.chat") || "[]");
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
      sessionStorage.setItem("hesabdar.chat", JSON.stringify(msgs.slice(-40)));
    } catch {
      /* ignore */
    }
  }, [msgs]);

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
                {m.tools && m.tools.length > 0 && (
                  <div className="muted mt-2 flex flex-wrap gap-1 text-[11px]">
                    {m.tools.map((t, k) => <span key={k} className="badge bg-violet-500/10"><Wrench size={10} />{t.tool}</span>)}
                  </div>
                )}
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
