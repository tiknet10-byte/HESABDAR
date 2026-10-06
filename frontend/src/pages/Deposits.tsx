import { CalendarPlus, Flame, HandCoins, Pencil, Plus, Search, Sparkles, Undo2, UserRound } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import BookingFields, { type BookingState } from "../components/Booking";
import StaffSelect from "../components/StaffSelect";
import CustomerPicker, { type CustomerChoice } from "../components/CustomerPicker";
import JalaliPicker from "../components/JalaliPicker";
import { Badge, Card, Empty, Field, Loading, Modal, MoneyInput, PageHeader, Stat, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { ACCOUNT_KINDS, cmoney, money, num, svcLabel } from "../lib/format";
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
  const [bs, setBs] = useState<BookingState>({ ready: false, allowOutside: false });
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
        body: { ...cust, ...f, service_id: f.service_id || null, staff_id: f.staff_id || null, payment_account_id: f.payment_account_id || accounts[0]?.id, book_at: book && bookAt ? bookAt : null, book_duration: book && bookDur ? bookDur : null, book_outside_hours: book && bs.allowOutside },
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
        <Field label="تاریخ و ساعت دریافت"><JalaliPicker pastOnly value={f.received_at} onChange={(v) => setF({ ...f, received_at: v })} /></Field>
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
            {services.map((s) => <option key={s.id} value={s.id}>{svcLabel(s)}</option>)}
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
              onStaff={(id) => setF((x) => ({ ...x, staff_id: id ?? 0 }))} onDuration={setBookDur}
              customerId={cust.customer_id} onState={setBs} />
          </div>
        )}
      </div>
      <button className="btn btn-primary w-full py-3" disabled={busy || !f.amount || !(cust.customer_id || cust.customer_name || cust.customer_mobile) || (book && (!bookAt || !bs.ready))} onClick={save}>ثبت بیعانه</button>
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
  const [bs, setBs] = useState<BookingState>({ ready: false, allowOutside: false });
  return (
    <div className="space-y-3">
      <div className="text-sm">بیعانه <b>{money(deposit.amount)}</b> از <b>{deposit.customer}</b> · دریافت {formatJ(deposit.received_at)}</div>
      <select className="input" value={serviceId} onChange={(e) => setServiceId(Number(e.target.value))}>
        <option value={0}>انتخاب خدمت…</option>
        {services.map((s) => <option key={s.id} value={s.id}>{svcLabel(s)}</option>)}
      </select>
      <BookingFields serviceId={serviceId || undefined} value={at} onChange={setAt} onStaff={setStaffId} onDuration={setDur}
        customerId={deposit.customer_id} onState={setBs} />
      <button className="btn btn-primary w-full" disabled={!at || !serviceId || !bs.ready} onClick={async () => {
        try {
          const r = await api("/api/appointments", { body: { customer_id: deposit.customer_id, service_id: serviceId, staff_id: staffId ?? null, start_at: at, duration_minutes: dur || null, deposit_ids: [deposit.id], allow_outside_hours: bs.allowOutside } });
          toast("نوبت ثبت و به بیعانه وصل شد");
          if (r.warning) toast(r.warning, "info");
          onDone();
        } catch (e: any) { toast(e.message, "error"); }
      }}>ثبت نوبت</button>
    </div>
  );
}

const STATUS_TABS = [
  { value: "held", label: "باز" }, { value: "applied", label: "تسویه‌شده" }, { value: "forfeited", label: "سوخت‌شده" },
  { value: "refunded", label: "مسترد" }, { value: "", label: "همه" },
];
const FILTERS = [
  { v: "", l: "همه" }, { v: "upcoming", l: "نوبت آینده" }, { v: "overdue", l: "نوبت گذشته ولی باز" },
  { v: "no_appointment", l: "بدون نوبت" }, { v: "no_service", l: "بدون خدمت" }, { v: "imported", l: "انتقالی از سیستم قبلی" },
];
const SORTS = [
  { v: "received_desc", l: "جدیدترین دریافت" }, { v: "received_asc", l: "قدیمی‌ترین دریافت" }, { v: "amount_desc", l: "بیشترین مبلغ" },
  { v: "amount_asc", l: "کمترین مبلغ" }, { v: "appointment", l: "نزدیک‌ترین نوبت" }, { v: "code", l: "کد مشتری" }, { v: "customer", l: "نام مشتری" },
];
const PAGE = 50;

function useDebounced<T>(value: T, ms = 300) {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}

function DepositEditForm({ d, onDone }: { d: any; onDone: () => void }) {
  const toast = useToast();
  const services = useApi<any[]>("/api/services").data ?? [];
  const [f, setF] = useState({ service_id: d.service_id ?? 0, staff_id: d.staff_id ?? 0, reference: d.reference ?? "", notes: d.notes ?? "" });
  async function save() {
    try {
      await api(`/api/deposits/${d.id}`, { method: "PUT", body: { ...(d.status === "held" ? { service_id: f.service_id || null, staff_id: f.staff_id || null } : {}), reference: f.reference || null, notes: f.notes } });
      toast("ذخیره شد");
      onDone();
    } catch (e: any) { toast(e.message, "error"); }
  }
  return (
    <div className="space-y-3 rounded-2xl border p-3" style={{ borderColor: "var(--border)" }}>
      {d.status === "held" && (
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label="خدمت">
            <select className="input" value={f.service_id} onChange={(e) => setF({ ...f, service_id: Number(e.target.value), staff_id: 0 })}>
              <option value={0}>نامشخص</option>
              {services.map((x) => <option key={x.id} value={x.id}>{svcLabel(x)}</option>)}
            </select>
          </Field>
          <Field label="پرسنل"><StaffSelect serviceId={f.service_id || undefined} value={f.staff_id || undefined} onChange={(id) => setF((x) => ({ ...x, staff_id: id ?? 0 }))} /></Field>
        </div>
      )}
      <Field label="شماره پیگیری"><input className="input num" dir="ltr" value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} /></Field>
      <Field label="توضیحات"><input className="input" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
      <button className="btn btn-primary w-full" onClick={save}>ذخیرهٔ تغییرات</button>
    </div>
  );
}

function DepositView({ id, onChange }: { id: number; onChange: () => void }) {
  const { user } = useAuth();
  const toast = useToast();
  const { data: d, reload } = useApi<any>(`/api/deposits/${id}`);
  const accounts = (useApi<any[]>("/api/accounts").data ?? []).filter((a) => a.is_active);
  const [edit, setEdit] = useState(false);
  const [booking, setBooking] = useState(false);
  const [refund, setRefund] = useState(false);
  const [refundAcc, setRefundAcc] = useState(0);
  if (!d) return <Loading />;
  const refreshAll = () => { reload(); onChange(); };
  async function close(action: string) {
    if (action === "forfeit" && !confirm("بیعانه سوخت شود و به درآمد منتقل گردد؟ (معمولاً وقتی مشتری نیامده)")) return;
    try {
      await api(`/api/deposits/${id}/close`, { body: { action, refund_account_id: action === "refund" ? (refundAcc || d.payment_account_id) : null } });
      toast(action === "refund" ? "بیعانه مسترد شد" : "بیعانه سوخت شد");
      setRefund(false);
      refreshAll();
    } catch (e: any) { toast(e.message, "error"); }
  }
  const row = (label: string, value: any) => value ? <div className="flex justify-between gap-3 py-1.5"><span className="muted">{label}</span><span className="text-left font-semibold">{value}</span></div> : null;
  const a = d.appointment;
  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-3 rounded-2xl bg-gradient-to-l from-pink-500/10 to-violet-600/10 p-4">
        <div>
          <div className="muted text-xs">مبلغ بیعانه</div>
          <div className="num text-2xl font-extrabold">{money(d.amount)}</div>
          <div className="muted text-xs">دریافت: {formatJ(d.received_at)}</div>
        </div>
        <div className="flex flex-col items-end gap-1">
          <Badge status={d.status} />
          {d.source === "import" && <span className="badge bg-sky-500/10 text-sky-600 dark:text-sky-300">انتقالی از سیستم قبلی</span>}
        </div>
      </div>

      <div className="rounded-2xl border p-3" style={{ borderColor: "var(--border)" }}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <div className="font-bold">{d.customer} {d.customer_code && <span className="num muted text-xs">کد {d.customer_code}</span>}</div>
            {d.customer_mobile && <div className="num muted text-xs" dir="ltr">{d.customer_mobile}</div>}
          </div>
          <Link className="btn btn-sm" to={`/customers?q=${encodeURIComponent(d.customer_code || d.customer)}`}><UserRound size={14} />پروندهٔ مشتری</Link>
        </div>
      </div>

      <div className="divide-y divide-zinc-200 rounded-2xl border px-3 dark:divide-zinc-700/60" style={{ borderColor: "var(--border)" }}>
        {row("حساب دریافت", d.account)}
        {row("شماره پیگیری", d.reference && <span className="num" dir="ltr">{d.reference}</span>)}
        {row("خدمت", d.service ? `${d.line ? d.line + " / " : ""}${d.service}` : <span className="muted">نامشخص</span>)}
        {row("پرسنل", d.staff)}
        {row("توضیحات", d.notes)}
      </div>

      <div className="rounded-2xl border p-3" style={{ borderColor: "var(--border)" }}>
        <div className="mb-2 flex items-center justify-between">
          <span className="font-bold">نوبت</span>
          {!a && d.status === "held" && <button className="btn btn-sm" onClick={() => setBooking(true)}><CalendarPlus size={14} />تعیین نوبت</button>}
        </div>
        {a ? (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <div className="font-semibold">{a.time_unknown ? `${formatJ(a.start_at, false)} · ساعت نامشخص` : formatJ(a.start_at)}</div>
              <div className="muted text-xs">{[a.line, a.service, a.staff].filter(Boolean).join(" · ")}</div>
            </div>
            <div className="flex items-center gap-2">
              <Badge status={a.status} />
              {a.status === "booked" && d.status === "held" && <Link className="btn btn-sm btn-primary" to={`/invoices?new=1&appointment=${a.id}`}>صدور فاکتور</Link>}
            </div>
          </div>
        ) : <div className="muted text-xs">نوبتی به این بیعانه وصل نیست.</div>}
      </div>

      {d.invoice && (
        <div className="rounded-2xl bg-emerald-500/10 p-3">
          <span className="font-bold">کسر شده در فاکتور </span><span className="num">{d.invoice.number}</span>
          <span className="muted text-xs"> · {formatJ(d.invoice.issued_at)} · جمع {money(d.invoice.total)}</span>
        </div>
      )}

      {d.timeline?.length > 0 && (
        <div>
          <div className="mb-1 font-bold">گردش این بیعانه</div>
          <div className="space-y-1 border-r-2 pr-3" style={{ borderColor: "var(--border)" }}>
            {d.timeline.map((t: any, i: number) => <div key={i} className="text-xs"><span className="muted num">{formatJ(t.at)}</span> · {t.description}</div>)}
          </div>
        </div>
      )}

      {d.others?.length > 0 && (
        <details>
          <summary className="cursor-pointer font-bold">بیعانه‌های دیگر این مشتری ({d.others.length})</summary>
          <div className="mt-2 space-y-1">
            {d.others.map((o: any) => (
              <div key={o.id} className="flex items-center justify-between rounded-xl px-3 py-1.5 text-xs" style={{ background: "var(--surface)" }}>
                <span className="num">{formatJ(o.received_at, false)} · {o.service ?? "خدمت نامشخص"}</span>
                <span className="flex items-center gap-2"><span className="num font-semibold">{money(o.amount)}</span><Badge status={o.status} /></span>
              </div>
            ))}
          </div>
        </details>
      )}

      <div className="flex flex-wrap gap-2">
        <button className="btn btn-sm" onClick={() => setEdit(!edit)}><Pencil size={14} />ویرایش جزئیات</button>
        {d.status === "held" && can(user, "finance") && <>
          <button className="btn btn-sm" onClick={() => setRefund(!refund)}><Undo2 size={14} />استرداد به مشتری</button>
          <button className="btn btn-sm text-rose-600" onClick={() => close("forfeit")}><Flame size={14} />سوخت (عدم مراجعه)</button>
        </>}
      </div>
      {refund && (
        <div className="space-y-2 rounded-2xl bg-amber-500/10 p-3">
          <Field label="پرداخت استرداد از حساب">
            <select className="input" value={refundAcc || d.payment_account_id} onChange={(e) => setRefundAcc(Number(e.target.value))}>
              {accounts.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
            </select>
          </Field>
          <button className="btn btn-primary w-full" onClick={() => close("refund")}>تأیید استرداد {money(d.amount)}</button>
        </div>
      )}
      {edit && <DepositEditForm d={d} onDone={() => { setEdit(false); refreshAll(); }} />}
      <Modal open={booking} onClose={() => setBooking(false)} title="تعیین نوبت برای بیعانه">
        {booking && <BookForDeposit deposit={d} onDone={() => { setBooking(false); refreshAll(); }} />}
      </Modal>
    </div>
  );
}

export default function Deposits() {
  const [params, setParams] = useSearchParams();
  const lines = useApi<any[]>("/api/lines").data ?? [];
  const [status, setStatus] = useState(params.get("status") ?? "held");
  const [filter, setFilter] = useState(params.get("filter") ?? "");
  const [sort, setSort] = useState("received_desc");
  const [lineId, setLineId] = useState(0);
  const [q, setQ] = useState("");
  const dq = useDebounced(q);
  const [page, setPage] = useState(0);
  const [view, setView] = useState<number | null>(null);
  useEffect(() => setPage(0), [status, filter, sort, lineId, dq]);
  const qs = new URLSearchParams({ q: dq, status, filter, sort, limit: String(PAGE), offset: String(page * PAGE), ...(lineId ? { line_id: String(lineId) } : {}) });
  const { data, reload } = useApi<any>(`/api/deposits/search?${qs}`, [qs.toString()]);
  const st = data?.stats;
  const pages = Math.max(1, Math.ceil((data?.total ?? 0) / PAGE));
  const quick = (s: string, f: string) => { setStatus(s); setFilter(f); };

  return (
    <div className="space-y-5">
      <PageHeader title="بیعانه‌ها" subtitle="هر بیعانه جداگانه با تاریخ دریافت، خدمت و نوبت مربوطه" icon={<HandCoins size={22} />}
        actions={<button className="btn btn-primary" onClick={() => setParams({ new: "1" })}><Plus size={16} />ثبت بیعانه</button>} />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <button className="text-right" onClick={() => quick("held", "")}><Stat label="بیعانه‌های باز" value={st ? cmoney(st.held[1]) : "…"} hint={st && `${num(st.held[0])} بیعانه`} tone="emerald" /></button>
        <button className="text-right" onClick={() => quick("held", "overdue")}><Stat label="نوبت گذشته ولی هنوز باز" value={st ? cmoney(st.overdue[1]) : "…"} hint={st && `${num(st.overdue[0])} بیعانه - فاکتور، استرداد یا سوخت؟`} tone="pink" /></button>
        <button className="text-right" onClick={() => quick("held", "no_appointment")}><Stat label="باز بدون نوبت" value={st ? cmoney(st.no_appointment[1]) : "…"} hint={st && `${num(st.no_appointment[0])} بیعانه - نوبت تعیین کنید`} tone="amber" /></button>
        <button className="text-right" onClick={() => quick("", "")}><Stat label="دریافتی این ماه" value={st ? cmoney(st.month[1]) : "…"} hint={st && `${num(st.month[0])} بیعانه`} tone="sky" /></button>
      </div>
      <Card pad={false}>
        <div className="space-y-3 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <Tabs value={status} onChange={setStatus} items={STATUS_TABS} />
            <div className="relative mr-auto">
              <Search size={16} className="muted absolute right-3 top-1/2 -translate-y-1/2" />
              <input className="input w-72 pr-9" placeholder="کد، نام، موبایل یا شماره پیگیری…" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {FILTERS.map((f) => (
              <button key={f.v} onClick={() => setFilter(f.v)}
                className={`rounded-xl px-3 py-1.5 text-xs font-semibold ${filter === f.v ? "bg-violet-600 text-white" : "border hover:bg-violet-500/10"}`}
                style={filter === f.v ? {} : { borderColor: "var(--border)" }}>{f.l}</button>
            ))}
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
          {!data ? <Loading /> : data.items.length === 0 ? <Empty text="بیعانه‌ای با این شرایط پیدا نشد" /> : (
            <table className="table">
              <thead><tr><th>کد</th><th>مشتری</th><th>مبلغ</th><th>تاریخ دریافت</th><th>خدمت</th><th>پرسنل</th><th>نوبت</th><th>وضعیت</th></tr></thead>
              <tbody>
                {data.items.map((d: any) => (
                  <tr key={d.id} className={`cursor-pointer ${d.overdue ? "bg-rose-500/5" : ""}`} onClick={() => setView(d.id)}>
                    <td className="num muted">{d.code}</td>
                    <td><div className="font-semibold">{d.customer}{d.source === "import" && <span className="badge mr-2 bg-sky-500/10 text-[10px] text-sky-600 dark:text-sky-300">انتقالی</span>}</div>{d.mobile && <div className="num muted text-xs" dir="ltr">{d.mobile}</div>}</td>
                    <td className="num font-bold">{money(d.amount)}</td>
                    <td className="num text-xs">{formatJ(d.received_at)}</td>
                    <td className="text-xs">{d.service ? <>{d.service}{d.line && <div className="muted">{d.line}</div>}</> : d.guess ? <span className="muted">حدس: {d.guess}</span> : <span className="muted">نامشخص</span>}</td>
                    <td className="text-xs">{d.staff ?? <span className="muted">—</span>}</td>
                    <td className="text-xs">
                      {d.appointment_at ? <>
                        <div className="num">{d.appointment_time_unknown ? formatJ(d.appointment_at, false) : formatJ(d.appointment_at)}</div>
                        {d.appointment_time_unknown && <span className="text-amber-600">ساعت نامشخص</span>}
                        {d.overdue && <span className="badge bg-rose-500/15 text-rose-600">گذشته</span>}
                      </> : d.status === "held" ? <span className="badge bg-amber-500/15 text-amber-700 dark:text-amber-300">بدون نوبت</span> : <span className="muted">—</span>}
                    </td>
                    <td><Badge status={d.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        {data && (
          <div className="flex flex-wrap items-center justify-between gap-2 border-t p-3 text-sm" style={{ borderColor: "var(--border)" }}>
            <span className="muted">{num(data.total)} بیعانه · جمع <b className="num">{money(data.amount)}</b></span>
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
      <Modal open={params.get("new") === "1"} onClose={() => setParams({})} title="ثبت بیعانه">
        <DepositForm onDone={() => { setParams({}); reload(); }} />
      </Modal>
      <Modal open={view !== null} onClose={() => setView(null)} title="جزئیات بیعانه">
        {view !== null && <DepositView id={view} onChange={reload} />}
      </Modal>
    </div>
  );
}
