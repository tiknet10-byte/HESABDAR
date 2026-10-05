import { AlertTriangle, Plus, Printer, Receipt, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import StaffSelect from "../components/StaffSelect";
import CustomerPicker, { type CustomerChoice } from "../components/CustomerPicker";
import { Badge, Card, Empty, Field, Loading, Modal, MoneyInput, PageHeader, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { ACCOUNT_KINDS, jdatetime, money } from "../lib/format";
import JalaliPicker from "../components/JalaliPicker";
import { formatJ, parseLocal, toLocalIso } from "../lib/jalali";
import { can, useApi, useAuth, useToast } from "../lib/hooks";

type Item = { service_id?: number; description?: string; unit_price: number; quantity: number; staff_id?: number };
type Pay = { payment_account_id: number; amount: number };

export function InvoiceForm({ onDone, preset, appointmentId }: { onDone: () => void; preset?: CustomerChoice; appointmentId?: number }) {
  const toast = useToast();
  const services = useApi<any[]>("/api/services").data ?? [];
  const accounts = (useApi<any[]>("/api/accounts").data ?? []).filter((a) => a.is_active);
  const [cust, setCust] = useState<CustomerChoice>(preset ?? {});
  const [items, setItems] = useState<Item[]>([{ unit_price: 0, quantity: 1 }]);
  const [discount, setDiscount] = useState(0);
  const [pays, setPays] = useState<Pay[]>([]);
  const [picked, setPicked] = useState<number[] | null>(null); // selected deposit ids (null = not initialised)
  const [check, setCheck] = useState<{ warnings: string[]; held_deposits: any[] }>({ warnings: [], held_deposits: [] });
  const [issuedAt, setIssuedAt] = useState(toLocalIso(new Date()));
  const [appts, setAppts] = useState<any[]>([]);
  const [override, setOverride] = useState<Record<number, "done" | "keep">>({});

  // the customer's open appointments: settle the one(s) this invoice is for, keep the rest for their own time
  useEffect(() => {
    setOverride({});
    if (!cust.customer_id) return setAppts([]);
    api<any[]>(`/api/appointments?customer_id=${cust.customer_id}&status=booked`).then(setAppts).catch(() => setAppts([]));
  }, [cust.customer_id]);

  const smartDefault = (a: any): "done" | "keep" => {
    if (appointmentId === a.id) return "done";
    const services = new Set(items.map((i) => i.service_id).filter(Boolean));
    if (services.has(a.service_id)) {
      // only the earliest open appointment of a service is settled by this invoice
      const first = appts.filter((x) => x.service_id === a.service_id).sort((x, y) => x.start_at.localeCompare(y.start_at))[0];
      return first?.id === a.id ? "done" : "keep";
    }
    const matchedAny = appts.some((x) => services.has(x.service_id));
    const near = appts.filter((x) => Math.abs(parseLocal(x.start_at).getTime() - parseLocal(issuedAt).getTime()) < 36 * 3600 * 1000);
    return !matchedAny && near.length === 1 && near[0].id === a.id ? "done" : "keep";
  };
  const choice = (a: any) => override[a.id] ?? smartDefault(a);
  const setChoice = (a: any, c: "done" | "keep") => {
    setOverride((o) => ({ ...o, [a.id]: c }));
    if (c === "done" && a.service_id && !items.some((i) => i.service_id === a.service_id)) {
      const row = { service_id: a.service_id, description: a.service, unit_price: a.quoted_price, quantity: 1, staff_id: a.staff_id ?? undefined };
      setItems((all) => (all.length === 1 && !all[0].service_id && !all[0].description ? [row] : [...all, row]));
    }
  };
  const doneIds = appts.filter((a) => choice(a) === "done").map((a) => a.id);

  useEffect(() => {
    if (!appointmentId) return;
    api(`/api/appointments/${appointmentId}`).then((a) => {
      setCust({ customer_id: a.customer_id, label: `${a.customer}${a.customer_mobile ? " · " + a.customer_mobile : ""}` });
      if (a.service_id) setItems([{ service_id: a.service_id, description: a.service, unit_price: a.quoted_price, quantity: 1, staff_id: a.staff_id ?? undefined }]);
      setPicked(a.deposits.filter((d: any) => d.status === "held").map((d: any) => d.id));
    }).catch(() => {});
  }, [appointmentId]);

  useEffect(() => {
    // by default every held deposit of the customer is applied; the user can untick any of them
    if (picked === null && check.held_deposits.length) setPicked(check.held_deposits.map((d) => d.id));
  }, [check.held_deposits, picked]);
  useEffect(() => setPicked(appointmentId ? picked : null), [cust.customer_id]); // eslint-disable-line react-hooks/exhaustive-deps
  const [busy, setBusy] = useState(false);

  const subtotal = items.reduce((s, i) => s + i.unit_price * i.quantity, 0);
  const total = Math.max(0, subtotal - discount);
  const selected = check.held_deposits.filter((d) => (picked ?? []).includes(d.id));
  const heldSum = selected.reduce((s, d) => s + d.amount, 0);
  const due = Math.max(0, total - Math.min(heldSum, total));
  const paid = pays.reduce((s, p) => s + p.amount, 0);

  useEffect(() => {
    const t = setTimeout(() => {
      api("/api/invoices/preview", { body: { customer_id: cust.customer_id, items: items.filter((i) => i.service_id || i.description) } })
        .then(setCheck).catch(() => {});
    }, 300);
    return () => clearTimeout(t);
  }, [cust.customer_id, items]);

  useEffect(() => {
    if (accounts.length && pays.length === 0 && due > 0) setPays([{ payment_account_id: accounts[0].id, amount: due }]);
    else if (pays.length === 1) setPays([{ ...pays[0], amount: due }]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [due, accounts.length]);

  const setItem = (i: number, patch: Partial<Item>) => setItems(items.map((x, j) => (j === i ? { ...x, ...patch } : x)));

  async function save() {
    setBusy(true);
    try {
      const inv = await api("/api/invoices", {
        body: { ...cust, items: items.filter((i) => i.service_id || i.description), discount, apply_deposits: selected.length > 0, deposit_ids: selected.map((d) => d.id),
          payments: pays.filter((p) => p.amount > 0), appointment_id: appointmentId ?? null, appointment_ids: doneIds, issued_at: issuedAt },
      });
      toast(`فاکتور ${inv.number} ثبت شد${doneIds.length ? ` و ${doneIds.length} نوبت انجام‌شده ثبت شد` : ""}`);
      onDone();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="sm:col-span-2"><Field label="مشتری"><CustomerPicker value={cust} onChange={setCust} /></Field></div>
        <Field label="تاریخ فاکتور"><JalaliPicker value={issuedAt} onChange={(v) => setIssuedAt(v || toLocalIso(new Date()))} /></Field>
      </div>

      {appts.length > 0 && (
        <div className="space-y-2 rounded-2xl border p-3 text-sm" style={{ borderColor: "var(--border)", background: "var(--surface)" }}>
          <div className="font-bold">نوبت‌های باز این مشتری</div>
          <div className="muted text-xs">نوبتی که این فاکتور برای آن است «انجام شد» ثبت می‌شود؛ بقیه برای زمان خودشان حفظ می‌شوند.</div>
          {appts.map((a) => {
            const c = choice(a);
            return (
              <div key={a.id} className={`flex flex-wrap items-center justify-between gap-2 rounded-xl px-3 py-2 ${c === "done" ? "bg-emerald-500/10" : ""}`}>
                <div>
                  <div className="font-semibold">{formatJ(a.start_at)}</div>
                  <div className="muted text-xs">{[a.line, a.service, a.staff].filter(Boolean).join(" · ")}{a.deposits?.length ? ` · بیعانه ${a.deposits.map((d: any) => money(d.amount)).join(" + ")}` : ""}</div>
                </div>
                <div className="flex gap-1">
                  <button type="button" onClick={() => setChoice(a, "done")}
                    className={`rounded-xl px-3 py-1.5 text-xs font-bold ${c === "done" ? "bg-emerald-600 text-white" : "border hover:bg-emerald-500/10"}`} style={c === "done" ? {} : { borderColor: "var(--border)" }}>
                    ✓ همین نوبت است (انجام شد)
                  </button>
                  <button type="button" onClick={() => setChoice(a, "keep")}
                    className={`rounded-xl px-3 py-1.5 text-xs font-bold ${c === "keep" ? "bg-violet-600 text-white" : "border hover:bg-violet-500/10"}`} style={c === "keep" ? {} : { borderColor: "var(--border)" }}>
                    حفظ برای زمان خودش
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="space-y-2">
        <div className="label">خدمات</div>
        {items.map((it, i) => (
          <div key={i} className="grid grid-cols-12 gap-2 rounded-2xl p-2" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
            <select className="input col-span-12 sm:col-span-5" value={it.service_id ?? ""} onChange={(e) => {
              const s = services.find((x) => x.id === Number(e.target.value));
              setItem(i, { service_id: s?.id, description: s?.name, unit_price: s ? s.learned_avg_price || s.base_price : 0 });
            }}>
              <option value="">انتخاب خدمت…</option>
              {Object.entries(services.reduce((g: any, s) => ((g[s.line] ||= []).push(s), g), {})).map(([line, list]: any) => (
                <optgroup key={line} label={line}>{list.map((s: any) => <option key={s.id} value={s.id}>{s.name}</option>)}</optgroup>
              ))}
            </select>
            <StaffSelect className="input col-span-6 sm:col-span-3" serviceId={it.service_id} value={it.staff_id}
              onChange={(id) => setItems((all) => all.map((x, j) => (j === i ? { ...x, staff_id: id } : x)))} />
            <div className="col-span-5 sm:col-span-3"><MoneyInput value={it.unit_price} onChange={(v) => setItem(i, { unit_price: v })} /></div>
            <button className="btn btn-ghost btn-sm col-span-1" onClick={() => setItems(items.filter((_, j) => j !== i))} aria-label="حذف"><Trash2 size={16} /></button>
          </div>
        ))}
        <button className="btn btn-sm" onClick={() => setItems([...items, { unit_price: 0, quantity: 1 }])}><Plus size={14} />افزودن خدمت</button>
      </div>

      {check.warnings.length > 0 && (
        <div className="space-y-1 rounded-2xl bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-300">
          {check.warnings.map((w) => <div key={w} className="flex items-center gap-2"><AlertTriangle size={14} />{w}</div>)}
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="تخفیف کل"><MoneyInput value={discount} onChange={setDiscount} /></Field>
      </div>
      {check.held_deposits.length > 0 && (
        <div className="space-y-2 rounded-2xl bg-emerald-500/10 p-3 text-sm">
          <div className="font-bold">بیعانه‌های باز این مشتری (هر کدام که باید از این فاکتور کسر شود را انتخاب کنید):</div>
          {check.held_deposits.map((d) => (
            <label key={d.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl px-2 py-1.5 hover:bg-emerald-500/10">
              <input type="checkbox" checked={(picked ?? []).includes(d.id)} onChange={(e) => setPicked(e.target.checked ? [...(picked ?? []), d.id] : (picked ?? []).filter((x) => x !== d.id))} />
              <span className="num font-bold">{money(d.amount)}</span>
              <span className="muted text-xs">دریافت: {formatJ(d.received_at)}</span>
              <span className="text-xs">خدمت: <b>{d.service ?? "نامشخص"}</b></span>
              {d.appointment_at && <span className="text-xs text-violet-600 dark:text-violet-300">نوبت: {formatJ(d.appointment_at)}</span>}
            </label>
          ))}
        </div>
      )}

      <div className="space-y-2">
        <div className="label">دریافت (قابل تقسیم بین چند کارتخوان/کارت)</div>
        {pays.map((p, i) => (
          <div key={i} className="grid grid-cols-12 gap-2">
            <select className="input col-span-6" value={p.payment_account_id} onChange={(e) => setPays(pays.map((x, j) => (j === i ? { ...x, payment_account_id: Number(e.target.value) } : x)))}>
              {accounts.map((a) => <option key={a.id} value={a.id}>{a.name} ({ACCOUNT_KINDS[a.kind]})</option>)}
            </select>
            <div className="col-span-5"><MoneyInput value={p.amount} onChange={(v) => setPays(pays.map((x, j) => (j === i ? { ...x, amount: v } : x)))} /></div>
            <button className="btn btn-ghost btn-sm col-span-1" onClick={() => setPays(pays.filter((_, j) => j !== i))}><Trash2 size={16} /></button>
          </div>
        ))}
        <button className="btn btn-sm" onClick={() => setPays([...pays, { payment_account_id: accounts[0]?.id, amount: Math.max(0, due - paid) }])}><Plus size={14} />روش پرداخت دیگر</button>
      </div>

      <div className="rounded-2xl p-4 text-sm" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
        <div className="flex justify-between"><span className="muted">جمع خدمات</span><span className="num">{money(subtotal)}</span></div>
        {discount > 0 && <div className="flex justify-between"><span className="muted">تخفیف</span><span className="num">− {money(discount)}</span></div>}
        {heldSum > 0 && <div className="flex justify-between"><span className="muted">بیعانه</span><span className="num">− {money(Math.min(heldSum, total))}</span></div>}
        <div className="mt-2 flex justify-between text-base font-extrabold"><span>قابل پرداخت</span><span className="num gradient-text">{money(due)}</span></div>
        {paid !== due && <div className="mt-1 flex justify-between text-amber-600"><span>مانده پس از دریافت</span><span className="num">{money(due - paid)}</span></div>}
      </div>
      <button className="btn btn-primary w-full py-3" disabled={busy || !subtotal || !(cust.customer_id || cust.customer_name || cust.customer_mobile)} onClick={save}>ثبت فاکتور</button>
    </div>
  );
}

function InvoiceView({ id, onChange }: { id: number; onChange: () => void }) {
  const { user } = useAuth();
  const toast = useToast();
  const { data, reload } = useApi<any>(`/api/invoices/${id}`);
  const accounts = useApi<any[]>("/api/accounts").data ?? [];
  const [pay, setPay] = useState<Pay>({ payment_account_id: 0, amount: 0 });
  if (!data) return <Loading />;
  const accName = (aid: number) => accounts.find((a) => a.id === aid)?.name ?? aid;
  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div><div className="text-lg font-extrabold">{data.number}</div><div className="muted">{data.customer} · {jdatetime(data.issued_at)}</div></div>
        <div className="flex gap-2"><Badge status={data.status} /><button className="btn btn-sm" onClick={() => window.print()}><Printer size={14} />چاپ</button></div>
      </div>
      <table className="table">
        <thead><tr><th>خدمت</th><th>مبلغ</th></tr></thead>
        <tbody>{data.items.map((it: any, i: number) => (
          <tr key={i}>
            <td>{it.description}<div className="muted text-xs">{[it.line, it.staff].filter(Boolean).join(" · ")}</div></td>
            <td className="num">{money(it.amount)}{it.commission_amount ? <div className="muted text-xs">سهم پرسنل: {money(it.commission_amount)}</div> : null}</td>
          </tr>
        ))}</tbody>
      </table>
      <div className="space-y-1">
        {data.discount > 0 && <div className="flex justify-between"><span className="muted">تخفیف</span><span className="num">{money(data.discount)}</span></div>}
        <div className="flex justify-between font-bold"><span>جمع</span><span className="num">{money(data.total)}</span></div>
        {data.deposits.map((d: any) => <div key={d.id} className="flex justify-between text-emerald-600"><span>بیعانه #{d.id}</span><span className="num">{money(d.amount)}</span></div>)}
        {data.payments.map((p: any) => <div key={p.id} className="flex justify-between"><span className="muted">{accName(p.account_id)}</span><span className="num">{money(p.amount)}</span></div>)}
        <div className="flex justify-between font-bold text-amber-600"><span>مانده</span><span className="num">{money(data.due)}</span></div>
      </div>
      {data.due > 0 && data.status !== "void" && (
        <div className="grid grid-cols-12 gap-2">
          <select className="input col-span-6" value={pay.payment_account_id} onChange={(e) => setPay({ ...pay, payment_account_id: Number(e.target.value) })}>
            <option value={0}>حساب دریافت…</option>
            {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
          <div className="col-span-4"><MoneyInput value={pay.amount || data.due} onChange={(v) => setPay({ ...pay, amount: v })} /></div>
          <button className="btn btn-primary btn-sm col-span-2" disabled={!pay.payment_account_id} onClick={async () => {
            try { await api(`/api/invoices/${id}/payments`, { body: { ...pay, amount: pay.amount || data.due } }); toast("دریافت ثبت شد"); reload(); onChange(); } catch (e: any) { toast(e.message, "error"); }
          }}>ثبت</button>
        </div>
      )}
      {can(user, "finance") && data.status !== "void" && data.paid === 0 && (
        <button className="btn btn-danger btn-sm" onClick={async () => {
          if (!confirm("فاکتور باطل شود؟ (سند معکوس ثبت می‌شود)")) return;
          try { await api(`/api/invoices/${id}/void`, { method: "POST" }); toast("فاکتور باطل شد"); reload(); onChange(); } catch (e: any) { toast(e.message, "error"); }
        }}>ابطال فاکتور</button>
      )}
    </div>
  );
}

export default function Invoices() {
  const [params, setParams] = useSearchParams();
  const [status, setStatus] = useState("");
  const { data, reload } = useApi<any[]>(`/api/invoices${status ? `?status=${status}` : ""}`, [status]);
  const [view, setView] = useState<number | null>(null);
  const [q, setQ] = useState("");
  const rows = useMemo(() => (data ?? []).filter((i) => !q || i.customer?.includes(q) || i.number.includes(q)), [data, q]);
  const open = params.get("new") === "1";

  return (
    <div>
      <PageHeader title="فروش و فاکتور" subtitle="ثبت خدمات ارائه‌شده، کسر بیعانه و دریافت از چند کارتخوان" icon={<Receipt size={22} />}
        actions={<button className="btn btn-primary" onClick={() => setParams({ new: "1" })}><Plus size={16} />فاکتور جدید</button>} />
      <Card pad={false}>
        <div className="flex flex-wrap items-center justify-between gap-3 p-4">
          <Tabs value={status} onChange={setStatus} items={[{ value: "", label: "همه" }, { value: "issued", label: "پرداخت‌نشده" }, { value: "partial", label: "جزئی" }, { value: "paid", label: "تسویه" }, { value: "void", label: "باطل" }]} />
          <input className="input max-w-xs" placeholder="جستجو…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="overflow-x-auto">
          {!data ? <Loading /> : rows.length === 0 ? <Empty /> : (
            <table className="table">
              <thead><tr><th>شماره</th><th>مشتری</th><th>تاریخ</th><th>خدمات</th><th>مبلغ</th><th>مانده</th><th>وضعیت</th></tr></thead>
              <tbody>
                {rows.map((i) => (
                  <tr key={i.id} className="cursor-pointer" onClick={() => setView(i.id)}>
                    <td className="num font-semibold">{i.number}</td>
                    <td>{i.customer}</td>
                    <td className="muted num">{jdatetime(i.issued_at)}</td>
                    <td className="max-w-64 truncate">{i.items.map((x: any) => x.description + (x.staff ? ` (${x.staff})` : "")).join("، ")}</td>
                    <td className="num font-semibold">{money(i.total)}</td>
                    <td className="num">{i.due ? money(i.due) : "—"}</td>
                    <td><Badge status={i.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </Card>
      <Modal open={open} onClose={() => setParams({})} title="فاکتور جدید" wide>
        {open && <InvoiceForm key={params.get("appointment") ?? "new"} appointmentId={params.get("appointment") ? Number(params.get("appointment")) : undefined}
          onDone={() => { setParams({}); reload(); }} />}
      </Modal>
      <Modal open={view !== null} onClose={() => setView(null)} title="جزئیات فاکتور">
        {view !== null && <InvoiceView id={view} onChange={reload} />}
      </Modal>
    </div>
  );
}
