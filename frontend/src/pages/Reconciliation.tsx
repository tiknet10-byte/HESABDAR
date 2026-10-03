import { Landmark, MessageSquareText, ScanLine, Send, Upload } from "lucide-react";
import { useState } from "react";
import { Badge, Card, Empty, Field, Loading, Modal, PageHeader, Stat, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { CHANNELS, jdatetime, money, num } from "../lib/format";
import { useApi, useToast } from "../lib/hooks";

function AssignDialog({ receipt, onDone }: { receipt: any; onDone: () => void }) {
  const toast = useToast();
  const [f, setF] = useState({ full_name: "", mobile: receipt.sender?.startsWith("09") ? receipt.sender : "", force: false });
  return (
    <div className="space-y-3 text-sm">
      <pre className="whitespace-pre-wrap rounded-2xl p-3 text-xs" style={{ background: "var(--surface)" }}>{receipt.raw_text}</pre>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="نام مشتری"><input className="input" value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></Field>
        <Field label="موبایل"><input className="input num" dir="ltr" value={f.mobile} onChange={(e) => setF({ ...f, mobile: e.target.value })} /></Field>
      </div>
      {!receipt.bank_transaction_id && (
        <label className="flex items-center gap-2 rounded-2xl bg-amber-500/10 p-3">
          <input type="checkbox" checked={f.force} onChange={(e) => setF({ ...f, force: e.target.checked })} />
          ثبت بدون تأیید بانک (مسئولیت با کاربر؛ در گزارش ممیزی ثبت می‌شود)
        </label>
      )}
      <button className="btn btn-primary w-full" onClick={async () => {
        try { await api(`/api/plugins/messaging_receipts/receipts/${receipt.id}/assign`, { body: f }); toast("ثبت شد"); onDone(); } catch (e: any) { toast(e.message, "error"); }
      }}>ثبت به نام مشتری</button>
    </div>
  );
}

function Simulator({ onDone }: { onDone: () => void }) {
  const toast = useToast();
  const [f, setF] = useState({ channel: "whatsapp", sender: "", text: "" });
  const [chat, setChat] = useState<{ me: boolean; text: string }[]>([]);
  async function send() {
    if (!f.text) return;
    setChat((c) => [...c, { me: true, text: f.text }]);
    try {
      const r = await api("/api/plugins/messaging_receipts/simulate", { body: f });
      setChat((c) => [...c, ...r.replies.map((t: string) => ({ me: false, text: t }))]);
      setF({ ...f, text: "" });
      onDone();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }
  return (
    <div className="space-y-3">
      <p className="muted text-sm">پیام یا رسیدی که مشتری در واتساپ/اینستاگرام/پیامک فرستاده را اینجا وارد کنید (یا از طریق وبهوک به‌صورت خودکار دریافت شود).</p>
      <div className="grid gap-2 sm:grid-cols-2">
        <select className="input" value={f.channel} onChange={(e) => setF({ ...f, channel: e.target.value })}>{Object.entries(CHANNELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        <input className="input num" dir="ltr" placeholder="شماره یا آیدی فرستنده" value={f.sender} onChange={(e) => setF({ ...f, sender: e.target.value })} />
      </div>
      <div className="min-h-32 space-y-2 rounded-2xl p-3" style={{ background: "var(--surface)" }}>
        {chat.map((m, i) => (
          <div key={i} className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-sm ${m.me ? "bg-emerald-500/15" : "mr-auto bg-violet-500/15"}`}>{m.text}</div>
        ))}
      </div>
      <div className="flex gap-2">
        <textarea className="input min-h-16" placeholder="متن پیام یا رسید…" value={f.text} onChange={(e) => setF({ ...f, text: e.target.value })} />
        <button className="btn btn-primary" disabled={!f.sender} onClick={send}><Send size={16} /></button>
      </div>
    </div>
  );
}

function BankInput({ onDone }: { onDone: () => void }) {
  const toast = useToast();
  const accounts = useApi<any[]>("/api/accounts").data ?? [];
  const [sms, setSms] = useState("");
  const [acc, setAcc] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <div className="font-bold">پیامک بانک (واریز به حساب سالن)</div>
        <p className="muted text-xs">برای خودکارسازی، یک اپ «SMS Forwarder» روی گوشی سالن نصب کنید تا پیامک‌های بانک را به وبهوک امضاشده ارسال کند.</p>
        <select className="input" value={acc} onChange={(e) => setAcc(Number(e.target.value))}>
          <option value={0}>تشخیص خودکار حساب</option>
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <textarea className="input min-h-24" placeholder="متن پیامک بانک را اینجا بچسبانید…" value={sms} onChange={(e) => setSms(e.target.value)} />
        <button className="btn btn-primary" disabled={!sms} onClick={async () => {
          try { const r = await api("/api/plugins/bank_sync/bank-sms", { body: { text: sms, payment_account_id: acc || null } }); toast(r.ok ? `ثبت شد (${r.status === "matched" ? "تطبیق خورد" : "تطبیق‌نشده"})` : r.reason, r.ok ? "ok" : "error"); setSms(""); onDone(); } catch (e: any) { toast(e.message, "error"); }
        }}>ثبت پیامک بانک</button>
      </div>
      <div className="space-y-2 border-t pt-4" style={{ borderColor: "var(--border)" }}>
        <div className="font-bold">ورود صورتحساب بانک (CSV)</div>
        <select className="input" value={acc} onChange={(e) => setAcc(Number(e.target.value))}>
          <option value={0}>انتخاب حساب…</option>
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <input type="file" accept=".csv,text/csv" className="input" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        <button className="btn" disabled={!file || !acc} onClick={async () => {
          const fd = new FormData(); fd.append("payment_account_id", String(acc)); fd.append("file", file!);
          try { const r = await api("/api/plugins/bank_sync/import-statement", { form: fd }); toast(`${r.imported} تراکنش وارد شد، ${r.auto_matched} تطبیق خودکار`); onDone(); } catch (e: any) { toast(e.message, "error"); }
        }}><Upload size={16} />بارگذاری</button>
      </div>
      <div className="border-t pt-4" style={{ borderColor: "var(--border)" }}>
        <button className="btn" onClick={async () => { const r = await api("/api/plugins/bank_sync/sync", { method: "POST" }); toast(`همگام‌سازی ${r.length} حساب متصل به API`, "info"); onDone(); }}>
          <Landmark size={16} />همگام‌سازی با API بانک
        </button>
      </div>
    </div>
  );
}

export default function Reconciliation() {
  const toast = useToast();
  const [tab, setTab] = useState<"receipts" | "bank">("receipts");
  const [status, setStatus] = useState("");
  const receipts = useApi<any[]>(`/api/plugins/messaging_receipts/receipts${status ? `?status=${status}` : ""}`, [status]);
  const bank = useApi<any[]>("/api/plugins/bank_sync/transactions");
  const [assign, setAssign] = useState<any>(null);
  const [modal, setModal] = useState<"sim" | "bank" | null>(null);
  const reload = () => { receipts.reload(); bank.reload(); };
  const r = receipts.data ?? [];

  return (
    <div className="space-y-5">
      <PageHeader title="تطبیق رسید و بانک" subtitle="رسیدهای مشتریان با واریزهای واقعی بانک مقایسه می‌شود؛ در صورت مغایرت هشدار داده می‌شود" icon={<ScanLine size={22} />}
        actions={<><button className="btn" onClick={() => setModal("sim")}><MessageSquareText size={16} />پیام / رسید مشتری</button><button className="btn btn-primary" onClick={() => setModal("bank")}><Landmark size={16} />داده بانک</button></>} />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="در انتظار بانک" value={num(r.filter((x) => x.status === "pending").length)} tone="amber" />
        <Stat label="مغایرت" value={num(r.filter((x) => x.status === "mismatch").length)} tone="pink" />
        <Stat label="ثبت‌شده" value={num(r.filter((x) => x.status === "registered").length)} tone="emerald" />
        <Stat label="تراکنش تطبیق‌نشده" value={num((bank.data ?? []).filter((x) => x.status === "unmatched" && x.direction === "in").length)} tone="sky" />
      </div>
      <Tabs value={tab} onChange={setTab} items={[{ value: "receipts", label: "رسیدهای مشتریان" }, { value: "bank", label: "تراکنش‌های بانک" }]} />
      {tab === "receipts" ? (
        <Card pad={false}>
          <div className="p-4"><Tabs value={status} onChange={setStatus} items={[{ value: "", label: "همه" }, { value: "pending", label: "در انتظار" }, { value: "matched", label: "تطبیق (بی‌نام)" }, { value: "mismatch", label: "مغایرت" }, { value: "registered", label: "ثبت‌شده" }]} /></div>
          <div className="overflow-x-auto">
            {!receipts.data ? <Loading /> : r.length === 0 ? <Empty text="رسیدی دریافت نشده" /> : (
              <table className="table">
                <thead><tr><th>زمان</th><th>کانال</th><th>فرستنده</th><th>مشتری</th><th>مبلغ</th><th>پیگیری</th><th>وضعیت</th><th>توضیح</th><th></th></tr></thead>
                <tbody>{r.map((x) => (
                  <tr key={x.id}>
                    <td className="num muted">{jdatetime(x.created_at)}</td>
                    <td>{CHANNELS[x.channel] ?? x.channel}</td>
                    <td className="num" dir="ltr">{x.sender}</td>
                    <td className="font-semibold">{x.customer ?? "—"}</td>
                    <td className="num font-semibold">{x.amount ? money(x.amount) : "—"}</td>
                    <td className="num">{x.parsed?.reference ?? "—"}</td>
                    <td><Badge status={x.status} /></td>
                    <td className="muted max-w-56 text-xs">{x.note}</td>
                    <td className="whitespace-nowrap">
                      {["pending", "matched", "mismatch"].includes(x.status) && (
                        <div className="flex gap-1">
                          <button className="btn btn-sm" onClick={() => setAssign(x)}>ثبت</button>
                          <button className="btn btn-sm" onClick={async () => { await api(`/api/plugins/messaging_receipts/receipts/${x.id}/recheck`, { method: "POST" }); reload(); }}>بررسی مجدد</button>
                          <button className="btn btn-sm" onClick={async () => { await api(`/api/plugins/messaging_receipts/receipts/${x.id}/reject`, { method: "POST" }); toast("رد شد", "info"); reload(); }}>رد</button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}</tbody>
              </table>
            )}
          </div>
        </Card>
      ) : (
        <Card pad={false}>
          <div className="overflow-x-auto">
            {!bank.data ? <Loading /> : bank.data.length === 0 ? <Empty text="تراکنشی دریافت نشده" /> : (
              <table className="table">
                <thead><tr><th>زمان</th><th>مبلغ</th><th>نوع</th><th>پیگیری</th><th>منبع</th><th>وضعیت</th><th>تطبیق با</th><th></th></tr></thead>
                <tbody>{bank.data.map((t) => (
                  <tr key={t.id}>
                    <td className="num muted">{jdatetime(t.occurred_at)}</td>
                    <td className={`num font-semibold ${t.direction === "in" ? "text-emerald-600" : "text-rose-600"}`}>{t.direction === "in" ? "+" : "−"}{money(t.amount)}</td>
                    <td>{t.direction === "in" ? "واریز" : "برداشت"}</td>
                    <td className="num">{t.reference ?? "—"}</td>
                    <td className="muted text-xs">{t.source}</td>
                    <td><Badge status={t.status} /></td>
                    <td className="muted text-xs">{t.matched_type ? `${t.matched_type} #${t.matched_id}` : ""}</td>
                    <td>{t.status === "unmatched" && <button className="btn btn-sm" onClick={async () => { await api(`/api/plugins/bank_sync/transactions/${t.id}/ignore`, { method: "POST" }); bank.reload(); }}>نادیده</button>}</td>
                  </tr>
                ))}</tbody>
              </table>
            )}
          </div>
        </Card>
      )}
      <Modal open={!!assign} onClose={() => setAssign(null)} title="ثبت رسید به نام مشتری">{assign && <AssignDialog receipt={assign} onDone={() => { setAssign(null); reload(); }} />}</Modal>
      <Modal open={modal === "sim"} onClose={() => setModal(null)} title="پیام / رسید مشتری">{modal === "sim" && <Simulator onDone={reload} />}</Modal>
      <Modal open={modal === "bank"} onClose={() => setModal(null)} title="ورود داده بانک">{modal === "bank" && <BankInput onDone={reload} />}</Modal>
    </div>
  );
}
