import { CalendarPlus, HandCoins, Plus, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import BookingFields from "../components/Booking";
import StaffSelect from "../components/StaffSelect";
import CustomerPicker, { type CustomerChoice } from "../components/CustomerPicker";
import JalaliPicker from "../components/JalaliPicker";
import { Badge, Card, Empty, Field, Loading, Modal, MoneyInput, PageHeader, Stat, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { ACCOUNT_KINDS, cmoney, money, num } from "../lib/format";
import { can, useApi, useAuth, useToast } from "../lib/hooks";
import { formatJ, toLocalIso } from "../lib/jalali";

function DepositForm({ onDone }: { onDone: () => void }) {
  const toast = useToast();
  const services = useApi<any[]>("/api/services").data ?? [];
  const accounts = useApi<any[]>("/api/accounts").data ?? [];
  const settings = useApi<any>("/api/settings").data;
  const [cust, setCust] = useState<CustomerChoice>({});
  const [f, setF] = useState({ amount: 0, payment_account_id: 0, service_id: 0, staff_id: 0, reference: "", notes: "", received_at: toLocalIso(new Date()) });
  const [book, setBook] = useState(false);
  const [bookAt, setBookAt] = useState("");
  const [bookDur, setBookDur] = useState(0);
  const [guess, setGuess] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const auto = !!settings?.["booking.auto"];

  useEffect(() => {
    if (!f.amount && !f.notes) return setGuess([]);
    const t = setTimeout(() => {
      const p = new URLSearchParams({ text: f.notes, ...(f.amount ? { amount: String(f.amount) } : {}), ...(cust.customer_id ? { customer_id: String(cust.customer_id) } : {}) });
      api(`/api/services/classify?${p}`).then(setGuess).catch(() => {});
    }, 350);
    return () => clearTimeout(t);
  }, [f.amount, f.notes, cust.customer_id]);

  async function save() {
    setBusy(true);
    try {
      const r = await api("/api/deposits", {
        body: { ...cust, ...f, service_id: f.service_id || null, staff_id: f.staff_id || null, payment_account_id: f.payment_account_id || accounts[0]?.id, book_at: book && bookAt ? bookAt : null, book_duration: book && bookDur ? bookDur : null },
      });
      toast(r.appointment_at ? `بیعانه ثبت و نوبت ${formatJ(r.appointment_at)} رزرو شد` : "بیعانه ثبت شد");
      if (r.warning) toast(r.warning, "info");
      onDone();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
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
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="تاریخ و ساعت دریافت"><JalaliPicker value={f.received_at} onChange={(v) => setF({ ...f, received_at: v })} /></Field>
        <Field label="شماره پیگیری"><input className="input num" dir="ltr" value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} /></Field>
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
              {g.service} · {num(Math.round(g.score * 100))}٪
            </button>
          ))}
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="بیعانه برای کدام خدمت است؟">
          <select className="input" value={f.service_id} onChange={(e) => setF({ ...f, service_id: Number(e.target.value) })}>
            <option value={0}>نامشخص</option>
            {services.map((s) => <option key={s.id} value={s.id}>{s.line} / {s.name}</option>)}
          </select>
        </Field>
        <Field label="پرسنل انجام‌دهنده">
          <StaffSelect serviceId={f.service_id || undefined} value={f.staff_id || undefined} emptyLabel="نامشخص"
            onChange={(id) => setF((x) => ({ ...x, staff_id: id ?? 0 }))} />
        </Field>
      </div>

      <div className="rounded-2xl p-4" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
        <label className="flex items-center gap-2 font-semibold">
          <input type="checkbox" checked={book} onChange={(e) => setBook(e.target.checked)} />
          <CalendarPlus size={16} className="text-violet-500" />همین الان نوبت هم ثبت شود (اختیاری)
        </label>
        {!book && <div className="muted mt-1 text-xs">{auto && f.service_id ? "نوبت‌دهی خودکار فعال است: اولین نوبت خالی به‌صورت خودکار رزرو می‌شود." : "می‌توانید بعداً از صفحه بیعانه‌ها یا نوبت‌ها، نوبت را تعیین و به این بیعانه وصل کنید."}</div>}
        {book && (
          <div className="mt-3 space-y-3">
            <BookingFields serviceId={f.service_id || undefined} staffId={f.staff_id || undefined} value={bookAt} onChange={setBookAt}
              onStaff={(id) => setF((x) => ({ ...x, staff_id: id ?? 0 }))} onDuration={setBookDur} />
          </div>
        )}
      </div>
      <button className="btn btn-primary w-full py-3" disabled={busy || !f.amount || !(cust.customer_id || cust.customer_name || cust.customer_mobile)} onClick={save}>ثبت بیعانه</button>
    </div>
  );
}

function BookForDeposit({ deposit, onDone }: { deposit: any; onDone: () => void }) {
  const toast = useToast();
  const services = useApi<any[]>("/api/services").data ?? [];
  const [serviceId, setServiceId] = useState<number>(deposit.service_id ?? 0);
  const [at, setAt] = useState("");
  const [staffId, setStaffId] = useState<number | undefined>();
  const [dur, setDur] = useState(0);
  return (
    <div className="space-y-3">
      <div className="text-sm">بیعانه <b>{money(deposit.amount)}</b> از <b>{deposit.customer}</b> · دریافت {formatJ(deposit.received_at)}</div>
      <select className="input" value={serviceId} onChange={(e) => setServiceId(Number(e.target.value))}>
        <option value={0}>انتخاب خدمت…</option>
        {services.map((s) => <option key={s.id} value={s.id}>{s.line} / {s.name}</option>)}
      </select>
      <BookingFields serviceId={serviceId || undefined} value={at} onChange={setAt} onStaff={setStaffId} onDuration={setDur} />
      <button className="btn btn-primary w-full" disabled={!at || !serviceId} onClick={async () => {
        try {
          const r = await api("/api/appointments", { body: { customer_id: deposit.customer_id, service_id: serviceId, staff_id: staffId ?? null, start_at: at, duration_minutes: dur || null, deposit_ids: [deposit.id] } });
          toast("نوبت ثبت و به بیعانه وصل شد");
          if (r.warning) toast(r.warning, "info");
          onDone();
        } catch (e: any) { toast(e.message, "error"); }
      }}>ثبت نوبت</button>
    </div>
  );
}

export default function Deposits() {
  const { user } = useAuth();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const [status, setStatus] = useState("held");
  const { data, reload } = useApi<any[]>(`/api/deposits${status ? `?status=${status}` : ""}`, [status]);
  const [booking, setBooking] = useState<any>(null);
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
      <PageHeader title="بیعانه‌ها" subtitle="هر بیعانه جداگانه با تاریخ دریافت، خدمت و نوبت مربوطه" icon={<HandCoins size={22} />}
        actions={<button className="btn btn-primary" onClick={() => setParams({ new: "1" })}><Plus size={16} />ثبت بیعانه</button>} />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="تعداد" value={num(data?.length)} />
        <Stat label="جمع مبلغ" value={cmoney(total)} tone="amber" />
      </div>
      <Card pad={false}>
        <div className="p-4">
          <Tabs value={status} onChange={setStatus} items={[{ value: "held", label: "باز" }, { value: "applied", label: "تسویه‌شده" }, { value: "forfeited", label: "سوخت‌شده" }, { value: "refunded", label: "مسترد" }, { value: "", label: "همه" }]} />
        </div>
        <div className="overflow-x-auto">
          {!data ? <Loading /> : data.length === 0 ? <Empty /> : (
            <table className="table">
              <thead><tr><th>مشتری</th><th>مبلغ</th><th>تاریخ دریافت</th><th>برای خدمت</th><th>پرسنل</th><th>نوبت</th><th>وضعیت</th><th></th></tr></thead>
              <tbody>
                {data.map((d) => (
                  <tr key={d.id}>
                    <td className="font-semibold">{d.customer}</td>
                    <td className="num font-semibold">{money(d.amount)}</td>
                    <td className="num text-xs">{formatJ(d.received_at)}</td>
                    <td>{d.service ?? (d.service_guess?.candidates?.[0] ? <span className="muted">حدس: {d.service_guess.candidates[0].service}</span> : <span className="muted">نامشخص</span>)}</td>
                    <td className="text-xs">{d.staff ?? <span className="muted">—</span>}{d.line && <div className="muted">{d.line}</div>}</td>
                    <td className="text-xs">{d.appointment_at ? formatJ(d.appointment_at) : d.status === "held" ? <button className="btn btn-sm" onClick={() => setBooking(d)}><CalendarPlus size={14} />تعیین نوبت</button> : "—"}</td>
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
      <Modal open={!!booking} onClose={() => setBooking(null)} title="تعیین نوبت برای بیعانه">
        {booking && <BookForDeposit deposit={booking} onDone={() => { setBooking(null); reload(); }} />}
      </Modal>
    </div>
  );
}
