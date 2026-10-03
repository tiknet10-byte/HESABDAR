import { HandCoins, Plus, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import CustomerPicker, { type CustomerChoice } from "../components/CustomerPicker";
import { Badge, Card, Empty, Field, Loading, Modal, MoneyInput, PageHeader, Stat, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { ACCOUNT_KINDS, jdatetime, money, num } from "../lib/format";
import { can, useApi, useAuth, useToast } from "../lib/hooks";

function DepositForm({ onDone }: { onDone: () => void }) {
  const toast = useToast();
  const services = useApi<any[]>("/api/services").data ?? [];
  const accounts = (useApi<any[]>("/api/accounts").data ?? []).filter((a) => a.is_active);
  const [cust, setCust] = useState<CustomerChoice>({});
  const [f, setF] = useState({ amount: 0, payment_account_id: 0, service_id: 0, reference: "", notes: "" });
  const [guess, setGuess] = useState<any[]>([]);

  useEffect(() => {
    if (!f.amount && !f.notes) return setGuess([]);
    const t = setTimeout(() => {
      const p = new URLSearchParams({ text: f.notes, ...(f.amount ? { amount: String(f.amount) } : {}), ...(cust.customer_id ? { customer_id: String(cust.customer_id) } : {}) });
      api(`/api/services/classify?${p}`).then(setGuess).catch(() => {});
    }, 350);
    return () => clearTimeout(t);
  }, [f.amount, f.notes, cust.customer_id]);

  async function save() {
    try {
      await api("/api/deposits", { body: { ...cust, ...f, service_id: f.service_id || null, payment_account_id: f.payment_account_id || accounts[0]?.id } });
      toast("بیعانه ثبت شد");
      onDone();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }

  return (
    <div className="space-y-4">
      <Field label="مشتری"><CustomerPicker value={cust} onChange={setCust} /></Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="مبلغ بیعانه"><MoneyInput value={f.amount} onChange={(v) => setF({ ...f, amount: v })} /></Field>
        <Field label="واریز به">
          <select className="input" value={f.payment_account_id || accounts[0]?.id || 0} onChange={(e) => setF({ ...f, payment_account_id: Number(e.target.value) })}>
            {accounts.map((a) => <option key={a.id} value={a.id}>{a.name} ({ACCOUNT_KINDS[a.kind]})</option>)}
          </select>
        </Field>
      </div>
      <Field label="توضیحات / متن پیام مشتری" hint="سیستم از روی متن و مبلغ حدس می‌زند بیعانه برای کدام خدمت است.">
        <input className="input" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} placeholder="مثلاً: برای کاشت ناخن پنجشنبه" />
      </Field>
      {guess.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Sparkles size={16} className="text-violet-500" />
          {guess.map((g) => (
            <button key={g.service_id} type="button" onClick={() => setF({ ...f, service_id: g.service_id })}
              className={`badge cursor-pointer py-1 ${f.service_id === g.service_id ? "bg-violet-600 text-white" : "bg-violet-500/10 text-violet-700 dark:text-violet-300"}`} title={g.reasons?.join("، ")}>
              {g.service} · {Math.round(g.score * 100)}٪
            </button>
          ))}
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="خدمت">
          <select className="input" value={f.service_id} onChange={(e) => setF({ ...f, service_id: Number(e.target.value) })}>
            <option value={0}>نامشخص</option>
            {services.map((s) => <option key={s.id} value={s.id}>{s.line} / {s.name}</option>)}
          </select>
        </Field>
        <Field label="شماره پیگیری"><input className="input num" dir="ltr" value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} /></Field>
      </div>
      <button className="btn btn-primary w-full py-3" disabled={!f.amount || !(cust.customer_id || cust.customer_name || cust.customer_mobile)} onClick={save}>ثبت بیعانه</button>
    </div>
  );
}

export default function Deposits() {
  const { user } = useAuth();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const [status, setStatus] = useState("held");
  const { data, reload } = useApi<any[]>(`/api/deposits${status ? `?status=${status}` : ""}`, [status]);
  const services = useApi<any[]>("/api/services").data ?? [];
  const svc = (id?: number) => services.find((s) => s.id === id)?.name;
  const total = (data ?? []).reduce((s, d) => s + d.amount, 0);

  async function close(id: number, action: string) {
    if (!confirm(action === "refund" ? "بیعانه به مشتری مسترد شود؟" : "بیعانه سوخت شود و به درآمد منتقل گردد؟")) return;
    try {
      await api(`/api/deposits/${id}/close`, { body: { action } });
      toast("انجام شد");
      reload();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }

  return (
    <div className="space-y-5">
      <PageHeader title="بیعانه‌ها" subtitle="پیش‌پرداخت مشتریان تا زمان ارائه خدمت به‌عنوان بدهی نگهداری می‌شود" icon={<HandCoins size={22} />}
        actions={<button className="btn btn-primary" onClick={() => setParams({ new: "1" })}><Plus size={16} />ثبت بیعانه</button>} />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="تعداد" value={num(data?.length)} />
        <Stat label="جمع مبلغ" value={money(total)} tone="amber" />
      </div>
      <Card pad={false}>
        <div className="p-4">
          <Tabs value={status} onChange={setStatus} items={[{ value: "held", label: "باز" }, { value: "applied", label: "تسویه‌شده" }, { value: "forfeited", label: "سوخت‌شده" }, { value: "refunded", label: "مسترد" }, { value: "", label: "همه" }]} />
        </div>
        <div className="overflow-x-auto">
          {!data ? <Loading /> : data.length === 0 ? <Empty /> : (
            <table className="table">
              <thead><tr><th>مشتری</th><th>مبلغ</th><th>تاریخ</th><th>خدمت</th><th>منبع</th><th>وضعیت</th><th></th></tr></thead>
              <tbody>
                {data.map((d) => (
                  <tr key={d.id}>
                    <td className="font-semibold">{d.customer}</td>
                    <td className="num font-semibold">{money(d.amount)}</td>
                    <td className="muted num">{jdatetime(d.received_at)}</td>
                    <td>{svc(d.service_id) ?? (d.service_guess?.candidates?.[0] ? <span className="muted">حدس: {d.service_guess.candidates[0].service}</span> : "—")}</td>
                    <td className="muted text-xs">{d.source}</td>
                    <td><Badge status={d.status} /></td>
                    <td className="whitespace-nowrap">
                      {d.status === "held" && can(user, "finance") && (
                        <div className="flex gap-1">
                          <button className="btn btn-sm" onClick={() => close(d.id, "refund")}>استرداد</button>
                          <button className="btn btn-sm" onClick={() => close(d.id, "forfeit")}>سوخت</button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </Card>
      <Modal open={params.get("new") === "1"} onClose={() => setParams({})} title="ثبت بیعانه">
        <DepositForm onDone={() => { setParams({}); reload(); }} />
      </Modal>
    </div>
  );
}
