import { AtSign, Eraser, HandCoins, Phone, Plus, Users } from "lucide-react";
import CustomerCleanup, { MOBILE_ISSUE } from "../components/CustomerCleanup";
import { DuplicateCustomers, SameNameHint } from "../components/SameName";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Badge, Card, Empty, Field, Loading, Modal, MoneyInput, PageHeader } from "../components/ui";
import JalaliPicker from "../components/JalaliPicker";
import { api } from "../lib/api";
import { jdate, jdatetime, money, num } from "../lib/format";
import { useApi, useToast } from "../lib/hooks";
import { toLocalIso } from "../lib/jalali";

function CustomerForm({ initial, onDone, onExisting }: { initial?: any; onDone: () => void; onExisting?: (c: any) => void }) {
  const toast = useToast();
  const [f, setF] = useState({ code: "", full_name: "", mobile: "", instagram: "", notes: "", ...(initial ?? {}) });
  async function save() {
    try {
      await api(initial ? `/api/customers/${initial.id}` : "/api/customers", { method: initial ? "PUT" : "POST", body: { ...f, tags: f.tags ?? [] } });
      toast("ذخیره شد");
      onDone();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }
  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="کد مشتری" hint={initial ? undefined : "خالی = شمارهٔ بعدی"}><input className="input num" dir="ltr" value={f.code ?? ""} onChange={(e) => setF({ ...f, code: e.target.value })} placeholder="خودکار" /></Field>
        <div className="sm:col-span-2"><Field label="نام و نام خانوادگی"><input className="input" value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></Field></div>
      </div>
      {!initial && <SameNameHint name={f.full_name} mobile={f.mobile} onPick={(c) => onExisting?.(c)} />}
      {initial?.mobile_issue && <div className="rounded-xl bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">{MOBILE_ISSUE[initial.mobile_issue]?.label}{initial.mobile_raw ? ` («${initial.mobile_raw}»)` : ""} - شمارهٔ درست را وارد کنید.</div>}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="موبایل"><input className="input num" dir="ltr" value={f.mobile ?? ""} onChange={(e) => setF({ ...f, mobile: e.target.value })} placeholder="09xxxxxxxxx" /></Field>
        <Field label="اینستاگرام"><input className="input" dir="ltr" value={f.instagram ?? ""} onChange={(e) => setF({ ...f, instagram: e.target.value })} placeholder="@username" /></Field>
      </div>
      <Field label="یادداشت"><textarea className="input min-h-20" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
      <button className="btn btn-primary w-full" disabled={!f.full_name} onClick={save}>ذخیره</button>
    </div>
  );
}

/** The customer pays what they owe (e.g. a debt brought over from Tizpardaz): unpaid invoices are settled first. */
function ReceiveDebt({ customer, owed, onDone }: { customer: any; owed: number; onDone: () => void }) {
  const toast = useToast();
  const accounts = (useApi<any[]>("/api/accounts").data ?? []).filter((a) => a.is_active);
  const routing = useApi<any>("/api/accounts/routing").data;
  const preferred: number[] = [...(routing?.products?.pos ?? []), ...(routing?.products?.card ?? []), ...(routing?.deposits?.pos ?? []), ...(routing?.deposits?.card ?? [])]
    .filter((id: number) => accounts.some((a) => a.id === id));
  const [f, setF] = useState({ amount: owed, payment_account_id: 0, reference: "", paid_at: toLocalIso(new Date()) });
  const acc = f.payment_account_id || preferred[0] || accounts[0]?.id || 0;
  async function save() {
    try {
      const r = await api(`/api/customers/${customer.id}/receive`, { body: { ...f, payment_account_id: acc, reference: f.reference || null } });
      const inv = r.paid.filter((p: any) => p.invoice).map((p: any) => p.invoice);
      toast(`دریافت شد${inv.length ? ` (تسویهٔ فاکتور ${inv.join("، ")})` : ""}. ماندهٔ بدهی: ${money(r.balance.receivable)}`);
      onDone();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }
  return (
    <div className="space-y-3 text-sm">
      <div className="rounded-2xl bg-amber-500/10 p-3">بدهی فعلی <b>{customer.full_name}</b>: <b className="num">{money(owed)}</b><div className="muted text-xs">اول فاکتورهای پرداخت‌نشدهٔ او (قدیمی‌ترین اول) تسویه می‌شود و بقیه از بدهی قبلی (مثلاً بدهی تیزپرداز) کم می‌شود.</div></div>
      <Field label="مبلغ دریافتی"><MoneyInput value={f.amount} onChange={(v) => setF({ ...f, amount: v })} /></Field>
      <Field label="به کدام حساب واریز شد؟">
        <select className="input" value={acc} onChange={(e) => setF({ ...f, payment_account_id: Number(e.target.value) })}>
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="تاریخ دریافت"><JalaliPicker pastOnly value={f.paid_at} onChange={(v) => setF({ ...f, paid_at: v })} /></Field>
        <Field label="شمارهٔ پیگیری (اختیاری)"><input className="input num" dir="ltr" value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} /></Field>
      </div>
      <button className="btn btn-primary w-full" disabled={!f.amount || f.amount > owed || !acc} onClick={save}>ثبت دریافت {money(f.amount)}</button>
      {f.amount > owed && <div className="text-xs text-rose-600">مبلغ از بدهی بیشتر است؛ مازاد را به‌عنوان بیعانه ثبت کنید.</div>}
    </div>
  );
}

function CustomerView({ id }: { id: number }) {
  const { data, reload } = useApi<any>(`/api/customers/${id}`);
  const [edit, setEdit] = useState(false);
  const [receive, setReceive] = useState(false);
  if (!data) return <Loading />;
  if (edit) return <CustomerForm initial={data} onDone={() => { setEdit(false); reload(); }} />;
  const b = data.balance;
  if (receive) return (
    <div className="space-y-3">
      <button className="btn btn-sm" onClick={() => setReceive(false)}>بازگشت به پرونده</button>
      <ReceiveDebt customer={data} owed={b.receivable} onDone={() => { setReceive(false); reload(); }} />
    </div>
  );
  return (
    <div className="space-y-4 text-sm">
      <div className="flex items-start justify-between">
        <div>
          <div className="text-lg font-extrabold">{data.full_name} {data.code && <span className="num rounded-lg bg-violet-500/10 px-2 py-0.5 text-sm text-violet-700 dark:text-violet-300">کد {data.code}</span>}</div>
          <div className="muted mt-1 flex flex-wrap gap-3">
            {data.mobile && <span className="num flex items-center gap-1" dir="ltr"><Phone size={13} />{data.mobile}</span>}
            {data.other_mobiles?.map((m: string) => <span key={m} className="num flex items-center gap-1" dir="ltr" title="شمارهٔ دیگر همین مشتری"><Phone size={13} />{m}</span>)}
            {data.other_codes?.length > 0 && <span title="کدهای پرونده‌های یکی‌شده">کدهای دیگر: <span className="num">{data.other_codes.join("، ")}</span></span>}
            {data.tp_code && <span title="کد حساب این مشتری در تیزپرداز">کد تیزپرداز: <span className="num">{data.tp_code}</span></span>}
            {data.instagram && <span className="flex items-center gap-1"><AtSign size={13} />{data.instagram}</span>}
            <span>عضویت: {jdate(data.created_at)}</span>
            {data.mobile_issue && <span className={`badge ${MOBILE_ISSUE[data.mobile_issue]?.cls}`}>{MOBILE_ISSUE[data.mobile_issue]?.label}{data.mobile_raw ? `: ${data.mobile_raw}` : ""}</span>}
          </div>
        </div>
        <button className="btn btn-sm" onClick={() => setEdit(true)}>ویرایش</button>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <div className="rounded-2xl bg-sky-500/10 p-3"><div className="muted text-xs">مجموع خرید</div><div className="num font-bold">{money((data.history ?? []).reduce((t: number, h: any) => t + (h.amount || 0), 0))}</div></div>
        <div className="rounded-2xl bg-emerald-500/10 p-3"><div className="muted text-xs">بیعانه نزد سالن</div><div className="num font-bold">{money(b.deposits_held)}</div></div>
        <div className="rounded-2xl bg-amber-500/10 p-3"><div className="muted text-xs">بدهی مشتری</div><div className="num font-bold">{money(b.receivable)}</div>
          {b.receivable > 0 && <button className="btn btn-sm mt-1 w-full py-1 text-xs" onClick={() => setReceive(true)}><HandCoins size={13} />دریافت بدهی</button>}</div>
        <div className="rounded-2xl bg-violet-500/10 p-3"><div className="muted text-xs">تعداد مراجعه</div><div className="num font-bold">{num(new Set((data.history ?? []).map((h: any) => h.date.slice(0, 10))).size)}</div>{data.history?.[0] && <div className="muted text-[11px]">آخرین: {jdate(data.history[0].date)}</div>}</div>
      </div>
      {data.notes && <div className="rounded-2xl p-3" style={{ background: "var(--surface)" }}>{data.notes}</div>}
      {data.upcoming?.length > 0 && (
        <div>
          <div className="mb-2 font-bold">نوبت‌های آینده</div>
          {data.upcoming.map((a: any) => (
            <div key={a.id} className="flex justify-between rounded-xl bg-violet-500/10 px-3 py-2"><span>{a.service ?? "—"}{a.staff ? ` · ${a.staff}` : ""}</span><span className="num">{jdatetime(a.start_at)}</span></div>
          ))}
        </div>
      )}
      <div>
        <div className="mb-2 font-bold">سوابق خدمات ({num(data.history?.length ?? 0)})</div>
        {!data.history?.length ? <div className="muted">بدون سابقه</div> : (
          <div className="max-h-80 space-y-1.5 overflow-y-auto">
            {data.history.map((h: any, k: number) => (
              <div key={k} className="flex items-center justify-between gap-2 rounded-xl px-3 py-2" style={{ background: "var(--surface)" }}>
                <span><span className="muted num ml-2">{jdate(h.date)}</span><b>{h.service}</b>{h.staff ? <span className="muted"> · {h.staff}</span> : null}</span>
                <span className="flex items-center gap-2">
                  {h.amount ? <span className="num text-xs">{money(h.amount)}</span> : null}
                  {h.product && <span className="badge bg-pink-500/10 text-pink-600 dark:text-pink-300">محصول</span>}
                  {h.source === "tizpardaz" ? <span className="badge bg-amber-500/10 text-amber-700 dark:text-amber-300" title={h.notes ?? undefined}>تیزپرداز</span>
                    : h.source === "import" ? <span className="badge bg-sky-500/10 text-sky-600 dark:text-sky-300">سیستم قبلی</span>
                    : h.invoice_number ? <span className="badge bg-violet-500/10 text-violet-600 dark:text-violet-300">{h.invoice_number}</span> : null}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
      {data.invoices.length > 0 && (
        <details>
          <summary className="mb-2 cursor-pointer font-bold">فاکتورها ({num(data.invoices.length)})</summary>
          <div className="space-y-1.5">
            {data.invoices.map((i: any) => (
              <div key={i.id} className="flex items-center justify-between rounded-xl px-3 py-2" style={{ background: "var(--surface)" }}>
                <span><span className="muted num ml-2">{jdate(i.issued_at)}</span>{i.number}</span>
                <span className="flex items-center gap-2"><span className="num font-semibold">{money(i.total)}</span><Badge status={i.status} /></span>
              </div>
            ))}
          </div>
        </details>
      )}
      {data.deposits.length > 0 && (
        <div>
          <div className="mb-2 font-bold">بیعانه‌ها</div>
          {data.deposits.map((d: any) => (
            <div key={d.id} className="flex justify-between py-1"><span className="muted num">{jdatetime(d.received_at)}</span><span className="flex gap-2"><span className="num">{money(d.amount)}</span><Badge status={d.status} /></span></div>
          ))}
        </div>
      )}
      {data.known_cards?.length > 0 && <div className="muted text-xs">کارت‌های پرداخت‌کننده: <span dir="ltr">{data.known_cards.join(" , ")}</span></div>}
    </div>
  );
}

const SORT_OPTIONS = [
  { v: "code", l: "کد مشتری" }, { v: "recent", l: "تازه‌ترین ثبت" }, { v: "name", l: "نام" },
  { v: "spent", l: "بیشترین خرید" }, { v: "visits", l: "بیشترین مراجعه" }, { v: "last_visit", l: "آخرین مراجعه" },
  { v: "debt", l: "بیشترین بدهی" },
];
const FILTERS = [
  { v: "", l: "همه" }, { v: "held", l: "دارای بیعانه باز" }, { v: "debt", l: "بدهکار" }, { v: "mobile_issue", l: "موبایل اشتباه/تکراری" },
  { v: "no_mobile", l: "بدون موبایل" }, { v: "no_history", l: "بدون سابقه" },
];
const PAGE = 100;

export default function Customers() {
  const [params] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const [sort, setSort] = useState("code");
  const [filter, setFilter] = useState("");
  const [page, setPage] = useState(0);
  const { data, reload } = useApi<any>(`/api/customers?q=${encodeURIComponent(q)}&sort=${sort}&filter=${filter}&limit=${PAGE}&offset=${page * PAGE}`, [q, sort, filter, page]);
  const [view, setView] = useState<number | null>(null);
  const [create, setCreate] = useState(false);
  const [clean, setClean] = useState(false);
  const [dupes, setDupes] = useState(false);
  const pages = Math.max(1, Math.ceil((data?.total ?? 0) / PAGE));
  // opened from another page with ?q=code: go straight to that customer's file
  const [autoOpened, setAutoOpened] = useState(false);
  useEffect(() => {
    if (!autoOpened && params.get("q") && data?.items?.length === 1) {
      setView(data.items[0].id);
      setAutoOpened(true);
    }
  }, [data, autoOpened, params]);
  return (
    <div className="space-y-5">
      <PageHeader title="مشتریان" subtitle="پرونده کامل هر مشتری: خدمات، بیعانه، بدهی و کانال‌های ارتباطی" icon={<Users size={22} />}
        actions={<>
          <button className="btn" onClick={() => setDupes(true)}><Users size={16} />مشتریان هم‌نام</button>
          <button className="btn" onClick={() => setClean(true)}><Eraser size={16} />پاک‌سازی مشتریان مشکل‌دار</button>
          <button className="btn btn-primary" onClick={() => setCreate(true)}><Plus size={16} />مشتری جدید</button>
        </>} />
      <Card pad={false}>
        <div className="flex flex-wrap items-center gap-2 p-4">
          <input className="input max-w-sm" placeholder="جستجوی کد، نام، موبایل یا اینستاگرام…" value={q} onChange={(e) => { setQ(e.target.value); setPage(0); }} />
          <select className="input w-auto" value={sort} onChange={(e) => { setSort(e.target.value); setPage(0); }} aria-label="مرتب‌سازی">
            {SORT_OPTIONS.map((o) => <option key={o.v} value={o.v}>مرتب‌سازی: {o.l}</option>)}
          </select>
          <div className="flex flex-wrap gap-1">
            {FILTERS.map((f) => (
              <button key={f.v} onClick={() => { setFilter(f.v); setPage(0); }}
                className={`rounded-xl px-3 py-1.5 text-xs font-semibold ${filter === f.v ? "bg-violet-600 text-white" : "border hover:bg-violet-500/10"}`}
                style={filter === f.v ? {} : { borderColor: "var(--border)" }}>{f.l}</button>
            ))}
          </div>
          <span className="muted mr-auto text-sm">{num(data?.total)} مشتری</span>
        </div>
        <div className="overflow-x-auto">
          {!data ? <Loading /> : data.items.length === 0 ? <Empty /> : (
            <table className="table">
              <thead><tr><th>کد</th><th>نام</th><th>موبایل</th><th>مراجعه</th><th>آخرین مراجعه</th><th>مجموع خرید</th><th>بیعانه باز</th><th>بدهی</th></tr></thead>
              <tbody>
                {data.items.map((c: any) => (
                  <tr key={c.id} className="cursor-pointer" onClick={() => setView(c.id)}>
                    <td className="num muted">{c.code}</td>
                    <td className="font-semibold">{c.full_name}{c.source === "import" && <span className="badge mr-2 bg-sky-500/10 text-[10px] text-sky-600 dark:text-sky-300">انتقالی</span>}{c.source === "tizpardaz" && <span className="badge mr-2 bg-amber-500/10 text-[10px] text-amber-700 dark:text-amber-300">تیزپرداز</span>}{c.source === "woocommerce" && <span className="badge mr-2 bg-sky-500/10 text-[10px] text-sky-700 dark:text-sky-300">سایت</span>}</td>
                    <td className="num" dir="ltr">{c.mobile ?? (c.mobile_issue && c.mobile_issue !== "missing"
                      ? <span className={`badge ${MOBILE_ISSUE[c.mobile_issue].cls}`} title={MOBILE_ISSUE[c.mobile_issue].label}>{c.mobile_raw} ⚠</span> : "—")}</td>
                    <td className="num">{c.visits ? num(c.visits) : "—"}</td>
                    <td className="num text-xs">{c.last_visit ? jdate(c.last_visit) : "—"}</td>
                    <td className="num">{c.total_spent ? money(c.total_spent) : "—"}{c.spent_old > 0 && c.spent_old < c.total_spent && <div className="muted text-[10px]">سیستم قبلی: {money(c.spent_old)}</div>}</td>
                    <td className="num">{c.deposits_held ? money(c.deposits_held) : "—"}</td>
                    <td className={`num ${c.debt ? "font-semibold text-amber-700 dark:text-amber-300" : ""}`}>{c.debt ? money(c.debt) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        {pages > 1 && (
          <div className="flex items-center justify-center gap-2 p-3 text-sm">
            <button className="btn btn-sm" disabled={page === 0} onClick={() => setPage(page - 1)}>قبلی</button>
            <span className="num">صفحهٔ {num(page + 1)} از {num(pages)}</span>
            <button className="btn btn-sm" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>بعدی</button>
          </div>
        )}
      </Card>
      <Modal open={clean} onClose={() => setClean(false)} title="پاک‌سازی مشتریان مشکل‌دار" wide>{clean && <CustomerCleanup onDone={reload} />}</Modal>
      <Modal open={create} onClose={() => setCreate(false)} title="مشتری جدید">
        <CustomerForm onDone={() => { setCreate(false); reload(); }}
          onExisting={(c) => { setCreate(false); setView(c.id); reload(); }} />
      </Modal>
      <Modal open={dupes} onClose={() => setDupes(false)} title="مشتریان هم‌نام (نام و نام خانوادگی یکسان)" wide>{dupes && <DuplicateCustomers onDone={reload} />}</Modal>
      <Modal open={view !== null} onClose={() => { setView(null); reload(); }} title="پرونده مشتری">{view !== null && <CustomerView id={view} />}</Modal>
    </div>
  );
}
