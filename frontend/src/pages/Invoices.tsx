import { AlertTriangle, Plus, Printer, Receipt, Search, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import StaffSelect from "../components/StaffSelect";
import CustomerPicker, { type CustomerChoice } from "../components/CustomerPicker";
import { Badge, Card, Empty, Field, Loading, Modal, MoneyInput, PageHeader, Stat, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { ACCOUNT_KINDS, cmoney, jdatetime, money, num } from "../lib/format";
import JalaliPicker from "../components/JalaliPicker";
import { faDigits, formatJ, toGregorian, toJalali, toLocalIso } from "../lib/jalali";
import { announceFreed } from "../components/Waitlist";
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
  const [depOverride, setDepOverride] = useState<Record<number, boolean>>({}); // deposits the user ticked/unticked by hand
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

  const day = (iso: string) => iso.slice(0, 10);
  const sameDay = (a: any) => day(a.start_at) === day(issuedAt);
  // default: only an appointment booked for the invoice's day is "done"; other days are kept (changing that needs confirmation)
  const smartDefault = (a: any): "done" | "keep" => {
    if (appointmentId) return appointmentId === a.id ? "done" : "keep";
    if (!sameDay(a)) return "keep";
    const today = appts.filter(sameDay).sort((x, y) => x.start_at.localeCompare(y.start_at));
    if (today.length === 1) return "done";
    const services = new Set(items.map((i) => i.service_id).filter(Boolean));
    return today.find((x) => services.has(x.service_id))?.id === a.id ? "done" : "keep";
  };
  const choice = (a: any) => override[a.id] ?? smartDefault(a);
  const doneIds = appts.filter((a) => choice(a) === "done").map((a) => a.id);
  // appointments on another day than the invoice need an explicit "yes" (keyed by invoice day, so changing the date asks again)
  const [confirmed, setConfirmed] = useState<string[]>([]);
  const [ask, setAsk] = useState<{ list: any[]; then?: () => void } | null>(null);
  const confirmKey = (a: any) => `${a.id}@${day(issuedAt)}`;
  const needsConfirm = (a: any) => !sameDay(a) && !confirmed.includes(confirmKey(a));
  const setChoice = (a: any, c: "done" | "keep") => {
    if (c === "done" && needsConfirm(a)) return setAsk({ list: [a] });
    setOverride((o) => ({ ...o, [a.id]: c }));
  };

  // a "done" appointment brings its service into the invoice (once); un-ticking removes a row it added
  const [autoRows, setAutoRows] = useState<Record<number, number>>({}); // appointment id -> service id it added
  const [seen, setSeen] = useState<number[]>([]);
  const doneKey = doneIds.join(",");
  useEffect(() => {
    const fresh = appts.filter((a) => doneIds.includes(a.id) && !seen.includes(a.id));
    const gone = Object.keys(autoRows).map(Number).filter((id) => !doneIds.includes(id));
    if (!fresh.length && !gone.length) return;
    setSeen((x) => [...x.filter((id) => !gone.includes(id)), ...fresh.map((a) => a.id)]);
    const add = fresh.filter((a) => a.service_id && !items.some((i) => i.service_id === a.service_id));
    setAutoRows((r) => {
      const next = { ...r };
      gone.forEach((id) => delete next[id]);
      add.forEach((a) => (next[a.id] = a.service_id));
      return next;
    });
    setItems((all) => {
      let rows = all.filter((i) => i.service_id || i.description);
      for (const id of gone) {
        const k = rows.findIndex((i) => i.service_id === autoRows[id]);
        if (k >= 0) rows = rows.filter((_, j) => j !== k);
      }
      rows = [...rows, ...add.map((a) => ({ service_id: a.service_id, description: a.service, unit_price: a.quoted_price, quantity: 1, staff_id: a.staff_id ?? undefined }))];
      return rows.length ? rows : [{ unit_price: 0, quantity: 1 }];
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doneKey, appts]);
  useEffect(() => {
    setSeen([]);
    setAutoRows({});
    setConfirmed([]);
  }, [cust.customer_id]);

  useEffect(() => {
    if (!appointmentId) return;
    api(`/api/appointments/${appointmentId}`).then((a) => {
      setCust({ customer_id: a.customer_id, label: `${a.customer}${a.customer_mobile ? " · " + a.customer_mobile : ""}` });
    }).catch(() => {});
  }, [appointmentId]);

  // deposits follow the appointments: a deposit taken for an appointment is applied only when that appointment is
  // settled by this invoice; a deposit without appointment is applied when its service is on the invoice (or it has none)
  const invoiced = new Set(items.map((i) => i.service_id).filter(Boolean));
  const depAppt = (d: any) => appts.find((a) => a.id === d.appointment_id);
  const depDefault = (d: any) => {
    const a = depAppt(d);
    if (a) return doneIds.includes(a.id);
    return d.service_id ? invoiced.has(d.service_id) : true;
  };
  const isPicked = (d: any) => depOverride[d.id] ?? depDefault(d);
  useEffect(() => setDepOverride({}), [cust.customer_id, doneKey]);
  const [busy, setBusy] = useState(false);

  const subtotal = items.reduce((s, i) => s + i.unit_price * i.quantity, 0);
  const total = Math.max(0, subtotal - discount);
  const selected = check.held_deposits.filter(isPicked);
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
    const unconfirmed = appts.filter((a) => doneIds.includes(a.id) && needsConfirm(a));
    if (unconfirmed.length) return setAsk({ list: unconfirmed, then: () => send() });
    return send();
  }

  async function send() {
    setBusy(true);
    try {
      const inv = await api("/api/invoices", {
        body: { ...cust, items: items.filter((i) => i.service_id || i.description), discount, apply_deposits: selected.length > 0, deposit_ids: selected.map((d) => d.id),
          payments: pays.filter((p) => p.amount > 0), appointment_id: appointmentId ?? null, appointment_ids: doneIds, issued_at: issuedAt },
      });
      toast(`فاکتور ${inv.number} ثبت شد${doneIds.length ? ` و ${faDigits(doneIds.length)} نوبت انجام‌شده ثبت شد` : ""}`);
      onDone();
      announceFreed(inv.freed);
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
        <Field label="تاریخ فاکتور"><JalaliPicker pastOnly value={issuedAt} onChange={(v) => setIssuedAt(v || toLocalIso(new Date()))} /></Field>
      </div>

      {appts.length > 0 && (
        <div className="space-y-2 rounded-2xl border p-3 text-sm" style={{ borderColor: "var(--border)", background: "var(--surface)" }}>
          <div className="font-bold">نوبت‌های باز این مشتری</div>
          <div className="muted text-xs">نوبتی که این فاکتور برای آن است «انجام شد» ثبت می‌شود و خدمتش خودکار به فاکتور اضافه می‌شود؛ بقیه برای زمان خودشان حفظ می‌شوند. نوبت‌های روزهای دیگر فقط با تأیید شما انجام‌شده ثبت می‌شوند.</div>
          {appts.map((a) => {
            const c = choice(a);
            return (
              <div key={a.id} className={`flex flex-wrap items-center justify-between gap-2 rounded-xl px-3 py-2 ${c === "done" ? "bg-emerald-500/10" : ""}`}>
                <div>
                  <div className="font-semibold">{formatJ(a.start_at)}</div>
                  {!sameDay(a) && <div className="text-[11px] font-semibold text-amber-600">⚠ روز دیگری غیر از تاریخ فاکتور{day(a.start_at) > day(issuedAt) ? " - اگر زودتر انجام شده، نوبتش آزاد می‌شود" : ""}</div>}
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
          <div className="font-bold">بیعانه‌های باز این مشتری</div>
          <div className="muted text-xs">بیعانهٔ هر نوبت فقط وقتی کسر می‌شود که همان نوبت «انجام شد» باشد؛ بیعانهٔ نوبت‌های حفظ‌شده برای خودشان می‌ماند. در صورت نیاز دستی تغییر دهید.</div>
          {check.held_deposits.map((d) => {
            const a = depAppt(d);
            const kept = !!a && !doneIds.includes(a.id);
            return (
            <label key={d.id} className={`flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl px-2 py-1.5 hover:bg-emerald-500/10 ${isPicked(d) ? "" : "opacity-60"}`}>
              <input type="checkbox" checked={isPicked(d)} onChange={(e) => setDepOverride((o) => ({ ...o, [d.id]: e.target.checked }))} />
              <span className="num font-bold">{money(d.amount)}</span>
              <span className="muted text-xs">دریافت: {formatJ(d.received_at)}</span>
              <span className="text-xs">خدمت: <b>{d.service ?? "نامشخص"}</b></span>
              {d.appointment_at && <span className="text-xs text-violet-600 dark:text-violet-300">نوبت: {formatJ(d.appointment_at)}</span>}
              {a && <span className={`badge ${kept ? "bg-violet-500/10 text-violet-600 dark:text-violet-300" : "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"}`}>{kept ? "نوبتش حفظ می‌شود" : "نوبتش انجام شد"}</span>}
              {kept && isPicked(d) && <span className="w-full text-xs font-semibold text-amber-600">⚠ این بیعانه برای نوبتی است که حفظ می‌شود؛ اگر کسر شود، آن نوبت بدون بیعانه می‌ماند.</span>}
            </label>
            );
          })}
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

      <Modal open={!!ask} onClose={() => setAsk(null)} title="نوبت در روز دیگری است - تأیید کنید">
        {ask && (
          <div className="space-y-4 text-sm">
            {ask.list.map((a) => {
              const early = day(a.start_at) > day(issuedAt);
              return (
                <div key={a.id} className="space-y-1 rounded-2xl bg-amber-500/10 p-3">
                  <div className="flex items-center gap-2 font-bold text-amber-700 dark:text-amber-300"><AlertTriangle size={16} />{a.service ?? "نوبت"}{a.staff ? ` · ${a.staff}` : ""}</div>
                  <div>نوبت این مشتری برای <b>{formatJ(a.start_at)}</b> است، اما تاریخ فاکتور <b>{formatJ(issuedAt, false)}</b> است.</div>
                  {early ? (
                    <div>یعنی خدمت <b>زودتر از نوبت</b> انجام شده. با تأیید، نوبت <b>{formatJ(a.start_at, false)}</b> آزاد می‌شود تا به مشتری بعدی (مثلاً از لیست انتظار VIP) داده شود.</div>
                  ) : (
                    <div>این نوبت در گذشته بوده و هنوز «انجام شد» ثبت نشده؛ با تأیید، همان نوبت انجام‌شده و تسویه‌شده ثبت می‌شود.</div>
                  )}
                </div>
              );
            })}
            <div className="grid gap-2 sm:grid-cols-2">
              <button className="btn btn-primary" onClick={() => {
                const { list, then } = ask;
                setConfirmed((c) => [...c, ...list.map(confirmKey)]);
                setOverride((o) => ({ ...o, ...Object.fromEntries(list.map((a) => [a.id, "done" as const])) }));
                setAsk(null);
                if (then) setTimeout(then, 0);
              }}>{ask.list.some((a) => day(a.start_at) > day(issuedAt)) ? "بله، انجام شد و نوبت آزاد شود" : "بله، همین نوبت انجام شد"}</button>
              <button className="btn" onClick={() => {
                setOverride((o) => ({ ...o, ...Object.fromEntries(ask.list.map((a) => [a.id, "keep" as const])) }));
                setAsk(null);
              }}>خیر، نوبت برای زمان خودش حفظ شود</button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}

function InvoiceView({ id, onChange }: { id: number; onChange: () => void }) {
  const { user } = useAuth();
  const toast = useToast();
  const { data, reload } = useApi<any>(`/api/invoices/${id}`);
  const accounts = useApi<any[]>("/api/accounts").data ?? [];
  const [pay, setPay] = useState<Pay>({ payment_account_id: 0, amount: 0 });
  const [voiding, setVoiding] = useState(false);
  const [payBack, setPayBack] = useState<"refund" | "deposit">("refund");
  const [reason, setReason] = useState("");
  if (!data) return <Loading />;
  const accName = (aid: number) => accounts.find((a) => a.id === aid)?.name ?? aid;
  const cashPaid = data.payments.filter((p: any) => p.amount > 0).reduce((t: number, p: any) => t + p.amount, 0);
  async function doVoid() {
    try {
      await api(`/api/invoices/${id}/void?payments=${payBack}&reason=${encodeURIComponent(reason)}`, { method: "POST" });
      toast("فاکتور باطل شد");
      setVoiding(false);
      reload();
      onChange();
    } catch (e: any) { toast(e.message, "error"); }
  }
  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-2 rounded-2xl bg-gradient-to-l from-pink-500/10 to-violet-600/10 p-4">
        <div>
          <div className="num text-lg font-extrabold">{data.number}</div>
          <div className="font-semibold">{data.customer} {data.customer_code && <span className="num muted text-xs">کد {data.customer_code}</span>}</div>
          <div className="muted text-xs">{data.customer_mobile && <span className="num" dir="ltr">{data.customer_mobile} · </span>}{jdatetime(data.issued_at)}</div>
        </div>
        <div className="flex items-center gap-2">
          <Badge status={data.status} />
          <button className="btn btn-sm" onClick={() => window.open(`/print/invoice/${id}`, "_blank")}><Printer size={14} />چاپ</button>
        </div>
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
      <div className="space-y-1 rounded-2xl p-3" style={{ background: "var(--surface)" }}>
        {data.discount > 0 && <div className="flex justify-between"><span className="muted">تخفیف</span><span className="num">− {money(data.discount)}</span></div>}
        <div className="flex justify-between font-bold"><span>جمع فاکتور</span><span className="num">{money(data.total)}</span></div>
        {data.deposits.map((d: any) => <div key={d.id} className="flex justify-between text-emerald-600"><span>بیعانه ({formatJ(d.received_at, false)})</span><span className="num">{money(d.amount)}</span></div>)}
        {data.payments.map((p: any) => <div key={p.id} className={`flex justify-between ${p.amount < 0 ? "text-rose-600" : ""}`}><span className="muted">{p.amount < 0 ? "استرداد · " : ""}{accName(p.account_id)}</span><span className="num">{money(p.amount)}</span></div>)}
        {data.status !== "void" && <div className="flex justify-between font-bold text-amber-600"><span>مانده</span><span className="num">{money(data.due)}</span></div>}
      </div>
      {data.notes && <div className="muted text-xs">توضیحات: {data.notes}</div>}
      {data.due > 0 && data.status !== "void" && (
        <div className="grid grid-cols-12 gap-2">
          <select className="input col-span-6" value={pay.payment_account_id} onChange={(e) => setPay({ ...pay, payment_account_id: Number(e.target.value) })}>
            <option value={0}>دریافت مانده به حساب…</option>
            {accounts.filter((a) => a.is_active).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
          <div className="col-span-4"><MoneyInput value={pay.amount || data.due} onChange={(v) => setPay({ ...pay, amount: v })} /></div>
          <button className="btn btn-primary btn-sm col-span-2" disabled={!pay.payment_account_id} onClick={async () => {
            try { await api(`/api/invoices/${id}/payments`, { body: { ...pay, amount: pay.amount || data.due } }); toast("دریافت ثبت شد"); reload(); onChange(); } catch (e: any) { toast(e.message, "error"); }
          }}>ثبت</button>
        </div>
      )}
      {can(user, "finance") && data.status !== "void" && (
        voiding ? (
          <div className="space-y-2 rounded-2xl bg-rose-500/10 p-3">
            <div className="font-bold text-rose-700 dark:text-rose-300">ابطال فاکتور {data.number}</div>
            <div className="text-xs">سند فاکتور و سهم پرسنل معکوس می‌شود{data.deposits.length ? "، بیعانه‌های کسرشده دوباره باز می‌شوند" : ""} و نوبت‌های این فاکتور دوباره «رزرو» می‌شوند.</div>
            {cashPaid > 0 && (
              <div className="space-y-1">
                <div className="text-xs font-bold">وجه دریافتی ({money(cashPaid)}):</div>
                <label className="flex items-center gap-2 text-xs"><input type="radio" checked={payBack === "refund"} onChange={() => setPayBack("refund")} />به مشتری برگردانده شد (از همان حساب)</label>
                <label className="flex items-center gap-2 text-xs"><input type="radio" checked={payBack === "deposit"} onChange={() => setPayBack("deposit")} />نزد سالن بماند و بیعانهٔ باز مشتری شود</label>
              </div>
            )}
            <input className="input" placeholder="دلیل ابطال (اختیاری)" value={reason} onChange={(e) => setReason(e.target.value)} />
            <div className="flex gap-2">
              <button className="btn btn-danger flex-1" onClick={doVoid}>تأیید ابطال</button>
              <button className="btn" onClick={() => setVoiding(false)}>انصراف</button>
            </div>
          </div>
        ) : <button className="btn btn-sm text-rose-600" onClick={() => setVoiding(true)}>ابطال فاکتور</button>
      )}
    </div>
  );
}

const STATUS_TABS = [
  { value: "", label: "همه" }, { value: "unpaid", label: "دارای مانده" }, { value: "paid", label: "تسویه" }, { value: "void", label: "باطل" },
];
const SORTS = [
  { v: "date_desc", l: "جدیدترین" }, { v: "date_asc", l: "قدیمی‌ترین" }, { v: "total_desc", l: "بیشترین مبلغ" },
  { v: "total_asc", l: "کمترین مبلغ" }, { v: "due_desc", l: "بیشترین مانده" }, { v: "customer", l: "نام مشتری" },
];
const PERIODS = [{ v: "", l: "همهٔ تاریخ‌ها" }, { v: "today", l: "امروز" }, { v: "week", l: "این هفته" }, { v: "month", l: "این ماه" }, { v: "custom", l: "بازهٔ دلخواه" }];
const PAGE = 50;

function periodRange(p: string, from: string, to: string): [string, string] {
  const d = new Date();
  const iso = (x: Date) => toLocalIso(x).slice(0, 10);
  if (p === "today") return [iso(d), iso(d)];
  if (p === "week") { const s = new Date(d); s.setDate(d.getDate() - ((d.getDay() + 1) % 7)); return [iso(s), iso(d)]; }
  if (p === "month") { const [jy, jm] = toJalali(d.getFullYear(), d.getMonth() + 1, d.getDate()); const [gy, gm, gd] = toGregorian(jy, jm, 1); return [iso(new Date(gy, gm - 1, gd)), iso(d)]; }
  if (p === "custom") return [from.slice(0, 10), to.slice(0, 10)];
  return ["", ""];
}

export default function Invoices() {
  const [params, setParams] = useSearchParams();
  const lines = useApi<any[]>("/api/lines").data ?? [];
  const [status, setStatus] = useState(params.get("status") ?? "");
  const [period, setPeriod] = useState(params.get("period") ?? "");
  const [from, setFrom] = useState(toLocalIso(new Date()));
  const [to, setTo] = useState(toLocalIso(new Date()));
  const [sort, setSort] = useState("date_desc");
  const [lineId, setLineId] = useState(0);
  const [q, setQ] = useState("");
  const [dq, setDq] = useState("");
  useEffect(() => { const t = setTimeout(() => setDq(q), 300); return () => clearTimeout(t); }, [q]);
  const [page, setPage] = useState(0);
  const [start, end] = periodRange(period, from, to);
  useEffect(() => setPage(0), [status, period, sort, lineId, dq, start, end]);
  const qs = new URLSearchParams({ q: dq, status, sort, limit: String(PAGE), offset: String(page * PAGE), ...(start ? { start, end } : {}), ...(lineId ? { line_id: String(lineId) } : {}) });
  const { data, reload } = useApi<any>(`/api/invoices/search?${qs}`, [qs.toString()]);
  const [view, setView] = useState<number | null>(null);
  const open = params.get("new") === "1";
  const st = data?.stats;
  const pages = Math.max(1, Math.ceil((data?.total ?? 0) / PAGE));

  return (
    <div className="space-y-5">
      <PageHeader title="فروش و فاکتور" subtitle="ثبت خدمات ارائه‌شده، کسر بیعانه و دریافت از چند کارتخوان" icon={<Receipt size={22} />}
        actions={<button className="btn btn-primary" onClick={() => setParams({ new: "1" })}><Plus size={16} />فاکتور جدید</button>} />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <button className="text-right" onClick={() => { setPeriod("today"); setStatus(""); }}><Stat label="فروش امروز" value={st ? cmoney(st.today[1]) : "…"} hint={st && `${num(st.today[0])} فاکتور`} tone="pink" /></button>
        <button className="text-right" onClick={() => { setPeriod("week"); setStatus(""); }}><Stat label="فروش این هفته" value={st ? cmoney(st.week[1]) : "…"} hint={st && `${num(st.week[0])} فاکتور`} tone="violet" /></button>
        <button className="text-right" onClick={() => { setPeriod("month"); setStatus(""); }}><Stat label="فروش این ماه" value={st ? cmoney(st.month[1]) : "…"} hint={st && `${num(st.month[0])} فاکتور`} tone="sky" /></button>
        <button className="text-right" onClick={() => { setPeriod(""); setStatus("unpaid"); }}><Stat label="مانده‌های دریافت‌نشده" value={st ? cmoney(st.unpaid[1]) : "…"} hint={st && `${num(st.unpaid[0])} فاکتور`} tone="amber" /></button>
      </div>
      <Card pad={false}>
        <div className="space-y-3 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <Tabs value={status} onChange={setStatus} items={STATUS_TABS} />
            <div className="relative mr-auto">
              <Search size={16} className="muted absolute right-3 top-1/2 -translate-y-1/2" />
              <input className="input w-72 pr-9" placeholder="شماره فاکتور، کد، نام، موبایل یا خدمت…" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {PERIODS.map((p) => (
              <button key={p.v} onClick={() => setPeriod(p.v)}
                className={`rounded-xl px-3 py-1.5 text-xs font-semibold ${period === p.v ? "bg-violet-600 text-white" : "border hover:bg-violet-500/10"}`}
                style={period === p.v ? {} : { borderColor: "var(--border)" }}>{p.l}</button>
            ))}
            {period === "custom" && <>
              <div className="w-44"><JalaliPicker pastOnly withTime={false} value={from} onChange={(v) => v && setFrom(v)} /></div>
              <span className="muted text-xs">تا</span>
              <div className="w-44"><JalaliPicker pastOnly withTime={false} value={to} onChange={(v) => v && setTo(v)} /></div>
            </>}
            <select className="input w-auto py-1.5 text-sm" value={lineId} onChange={(e) => setLineId(Number(e.target.value))} aria-label="لاین">
              <option value={0}>همهٔ لاین‌ها</option>
              {lines.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select>
            <select className="input w-auto py-1.5 text-sm" value={sort} onChange={(e) => setSort(e.target.value)} aria-label="مرتب‌سازی">
              {SORTS.map((o) => <option key={o.v} value={o.v}>مرتب‌سازی: {o.l}</option>)}
            </select>
          </div>
        </div>
        <div className="overflow-x-auto">
          {!data ? <Loading /> : data.items.length === 0 ? <Empty text="فاکتوری با این شرایط پیدا نشد" /> : (
            <table className="table">
              <thead><tr><th>شماره</th><th>کد</th><th>مشتری</th><th>تاریخ</th><th>خدمات</th><th>مبلغ</th><th>مانده</th><th>وضعیت</th></tr></thead>
              <tbody>
                {data.items.map((i: any) => (
                  <tr key={i.id} className={`cursor-pointer ${i.status === "void" ? "opacity-50" : ""}`} onClick={() => setView(i.id)}>
                    <td className="num font-semibold">{i.number}</td>
                    <td className="num muted">{i.code}</td>
                    <td><div className="font-semibold">{i.customer}</div>{i.mobile && <div className="num muted text-xs" dir="ltr">{i.mobile}</div>}</td>
                    <td className="muted num text-xs">{jdatetime(i.issued_at)}</td>
                    <td className="max-w-64 truncate text-xs">{i.items.map((x: any) => x.description + (x.staff ? ` (${x.staff})` : "")).join("، ")}</td>
                    <td className="num font-semibold">{money(i.total)}</td>
                    <td className="num">{i.due > 0 && i.status !== "void" ? <span className="text-amber-600">{money(i.due)}</span> : "—"}</td>
                    <td><Badge status={i.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        {data && (
          <div className="flex flex-wrap items-center justify-between gap-2 border-t p-3 text-sm" style={{ borderColor: "var(--border)" }}>
            <span className="muted">{num(data.total)} فاکتور · فروش <b className="num">{money(data.amount)}</b> · دریافت‌شده <b className="num">{money(data.paid)}</b>{data.due > 0 && <> · مانده <b className="num text-amber-600">{money(data.due)}</b></>}</span>
            {pages > 1 && (
              <div className="flex items-center gap-2">
                <button className="btn btn-sm" disabled={page === 0} onClick={() => setPage(page - 1)}>قبلی</button>
                <span className="num">صفحهٔ {num(page + 1)} از {num(pages)}</span>
                <button className="btn btn-sm" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>بعدی</button>
              </div>
            )}
          </div>
        )}
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
