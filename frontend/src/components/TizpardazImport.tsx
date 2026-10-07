import { CheckCircle2, Package, RotateCcw, Search, Upload } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import JalaliPicker from "./JalaliPicker";
import { Badge, Card, Empty, Field } from "./ui";
import { api } from "../lib/api";
import { money } from "../lib/format";
import { useApi, useToast } from "../lib/hooks";
import { faDigits, formatJ, toLocalIso } from "../lib/jalali";

const KINDS = [
  { key: "customers", title: "۱. مشتریان", text: "لیست طرف حساب‌ها: کد حساب، نام، بدهکار، بستانکار" },
  { key: "products", title: "۲. کالاها", text: "کد کالا، نام، تعداد، قیمت خرید و فروش + SKU سایت" },
  { key: "journal", title: "۳. دفتر روزنامه", text: "فروش، خرید و هزینه (هر کدام یک فایل جدا هم می‌شود)" },
] as const;
const STATUS: Record<string, string> = { review: "بررسی", committed: "ثبت شد", undone: "برگشت خورد", discarded: "کنار گذاشته شد" };
const C_STATUS: Record<string, { l: string; cls: string }> = {
  linked: { l: "قبلاً وصل شده", cls: "bg-sky-500/10 text-sky-700 dark:text-sky-300" },
  found: { l: "پیدا شد", cls: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" },
  ambiguous: { l: "چند هم‌نام - انتخاب کنید", cls: "bg-amber-500/15 text-amber-700 dark:text-amber-300" },
  new: { l: "مشتری جدید", cls: "bg-violet-500/10 text-violet-700 dark:text-violet-300" },
  generic: { l: "حساب عمومی (شخص نیست)", cls: "bg-zinc-500/10 text-zinc-600 dark:text-zinc-300" },
};
const P_STATUS: Record<string, { l: string; cls: string }> = {
  linked: { l: "قبلاً وارد شده", cls: "bg-sky-500/10 text-sky-700 dark:text-sky-300" },
  match: { l: "هم‌نام پیدا شد", cls: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" },
  similar: { l: "شبیه دارد - بررسی کنید", cls: "bg-amber-500/15 text-amber-700 dark:text-amber-300" },
  new: { l: "کالای جدید", cls: "bg-violet-500/10 text-violet-700 dark:text-violet-300" },
};
const J_KINDS: Record<string, string> = { sale: "فروش", sale_return: "برگشت از فروش", purchase: "خرید", purchase_return: "برگشت از خرید", expense: "هزینه" };
const PAGE = 100;

/** Bring the clinic's product accounting over from Tizpardaz: customers (by name), products (+ website SKU), journal. */
export default function TizpardazImport() {
  const toast = useToast();
  const history = useApi<any[]>("/api/import/tizpardaz");
  const accounts = (useApi<any[]>("/api/accounts").data ?? []).filter((a) => a.is_active);
  const [kind, setKind] = useState<string>("customers");
  const [unit, setUnit] = useState("toman");
  const [file, setFile] = useState<File | null>(null);
  const [pv, setPv] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<any>(null);

  async function check() {
    if (!file) return;
    setBusy(true);
    setDone(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("kind", kind);
      form.append("unit", unit);
      setPv(await api("/api/import/tizpardaz/preview", { form }));
    } catch (e: any) {
      toast(e.message, "error");
      setPv(null);
    } finally {
      setBusy(false);
    }
  }

  async function commit(body: any, question: string) {
    if (!window.confirm(`${question}\n(در صورت نیاز بعداً از «سابقهٔ انتقال‌ها» قابل برگشت است)`)) return;
    setBusy(true);
    try {
      const r = await api(`/api/import/tizpardaz/${pv.id}/commit`, { body });
      setDone({ kind: pv.kind, ...r.result });
      setPv(null);
      setFile(null);
      toast("اطلاعات تیزپرداز منتقل شد");
      history.reload();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  }

  function cancel() {
    api(`/api/import/tizpardaz/${pv.id}`, { method: "DELETE" }).catch(() => {});
    setPv(null);
  }

  async function undo(b: any) {
    if (!window.confirm(`همه اطلاعاتی که با فایل «${b.file_name}» از تیزپرداز وارد شده برگردد؟`)) return;
    try {
      await api(`/api/import/tizpardaz/${b.id}/undo`, { method: "POST" });
      toast("برگشت انجام شد");
      history.reload();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }

  return (
    <div className="space-y-5">
      <Card title={<span className="flex items-center gap-2"><Package size={18} className="text-pink-500" />انتقال حسابداری محصولات از «تیزپرداز»</span>}>
        <div className="space-y-2 text-sm leading-7">
          <p>از تیزپرداز گزارش‌ها را <b>Excel</b> بگیرید و <b>به همین ترتیب</b> وارد کنید. هر مرحله اول پیش‌نمایش نشان می‌دهد و تا «ثبت» را نزنید چیزی عوض نمی‌شود.</p>
          <ol className="list-inside list-decimal space-y-1 rounded-2xl bg-pink-500/5 p-3">
            <li><b>مشتریان</b>: کد تیزپرداز با کد چهره فرق دارد، پس مشتری فقط با <b>نام و نام خانوادگی</b> پیدا می‌شود («تاجیک(ساناز)» = ساناز تاجیک). اگر چند نفر هم‌نام باشند، از شما می‌پرسد کدام است. بدهی مشتری «بدهی قبلی» و بستانکاری او «بیعانهٔ باز» می‌شود.</li>
            <li><b>کالاها</b>: ملاک، تیزپرداز است؛ کد کالا، نام، قیمت‌ها و <b>موجودی نهایی</b> همان می‌شود و SKU سایت از روی کد کالا وصل می‌شود. اگر کالایی قبلاً اینجا (یا در چهره به‌صورت «خدمت») بوده، می‌پرسد که یکی شود یا نه.</li>
            <li><b>دفتر روزنامه</b> (فروش، خرید، هزینه): فقط <b>سابقه</b> است و در گزارش سود محصولات، پروندهٔ کالا و پروندهٔ مشتری دیده می‌شود. چون مانده‌ها و موجودی در مرحلهٔ ۱ و ۲ آمده، <b>هیچ مبلغی دوباره حساب نمی‌شود</b>. نام مشتری از «شرح» خوانده می‌شود؛ اگر به جای نام، <b>کد حساب تیزپرداز</b> را در شرح بنویسید (مثلاً «… به 1024»)، مستقیم به همان مشتری وصل می‌شود.</li>
          </ol>
          <p className="muted text-xs">مبالغ تیزپرداز معمولاً <b>تومان</b> است. وارد کردن دوبارهٔ یک فایل، چیز تکراری نمی‌سازد.</p>
        </div>
      </Card>

      <Card title="وارد کردن فایل تیزپرداز">
        <div className="space-y-4">
          <div className="grid gap-2 sm:grid-cols-3">
            {KINDS.map((k) => (
              <button key={k.key} onClick={() => { setKind(k.key); setPv(null); setDone(null); }}
                className={`rounded-2xl border p-3 text-right transition ${kind === k.key ? "border-transparent bg-gradient-to-l from-pink-500 to-violet-600 text-white shadow" : "hover:bg-violet-500/5"}`}
                style={kind === k.key ? {} : { borderColor: "var(--border)" }}>
                <div className="font-bold">{k.title}</div>
                <div className={`text-xs ${kind === k.key ? "opacity-90" : "muted"}`}>{k.text}</div>
              </button>
            ))}
          </div>
          {kind === "products" && <SkuMap />}
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="فایل Excel تیزپرداز">
              <input type="file" accept=".xlsx,.xlsm,.xls,.csv,.txt" className="input" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setPv(null); }} />
            </Field>
            <Field label="واحد مبالغ در فایل">
              <div className="flex gap-2">
                {[["toman", "تومان"], ["rial", "ریال"]].map(([v, l]) => (
                  <button key={v} type="button" onClick={() => { setUnit(v); setPv(null); }}
                    className={`flex-1 rounded-xl py-2 text-sm font-semibold ${unit === v ? "bg-violet-600 text-white" : "border"}`} style={unit === v ? {} : { borderColor: "var(--border)" }}>{l}</button>
                ))}
              </div>
            </Field>
          </div>
          <button className="btn btn-primary w-full" disabled={!file || busy} onClick={check}><Upload size={16} />{busy ? "در حال بررسی…" : "بررسی فایل (هنوز چیزی ثبت نمی‌شود)"}</button>
          {done && <Done r={done} />}
        </div>
      </Card>

      <SellerCredits key={history.data?.length ?? 0} />
      {pv?.kind === "customers" && <CustomersPreview pv={pv} accounts={accounts} busy={busy} onCommit={commit} onCancel={cancel} />}
      {pv?.kind === "products" && <ProductsPreview pv={pv} busy={busy} onCommit={commit} onCancel={cancel} />}
      {pv?.kind === "journal" && <JournalPreview pv={pv} busy={busy} onCommit={commit} onCancel={cancel} />}

      <Card title="سابقهٔ انتقال‌ها از تیزپرداز">
        {!history.data?.filter((b) => b.status !== "discarded").length ? <Empty text="هنوز فایلی از تیزپرداز وارد نشده" /> : (
          <div className="space-y-2">
            {history.data.filter((b) => b.status !== "discarded").map((b) => (
              <div key={b.id} className="flex flex-wrap items-center justify-between gap-2 rounded-2xl px-3 py-2 text-sm" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
                <div>
                  <div className="font-semibold">{b.kind_label} · {b.file_name}</div>
                  <div className="muted text-xs">{formatJ(b.created_at)}{b.result ? ` · ${resultText(b.kind, b.result)}` : ""}</div>
                </div>
                <div className="flex items-center gap-2">
                  <Badge>{STATUS[b.status] ?? b.status}</Badge>
                  {b.status === "committed" && <button className="btn btn-sm" onClick={() => undo(b)}><RotateCcw size={14} />برگشت</button>}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

function resultText(kind: string, r: any): string {
  const n = (k: string) => faDigits(r[k] ?? 0);
  if (kind === "customers") return `مشتری جدید ${n("new")} · وصل به موجود ${n("linked")} · بدهی ${n("debts")} · بستانکار ${n("credits")}${r.unresolved ? ` · هم‌نامِ انتخاب‌نشده ${n("unresolved")}` : ""}`;
  if (kind === "products") return `کالای جدید ${n("new")} · یکی‌شده ${n("combined")} · موجودی ${n("units")} عدد${r.chehreh_sales ? ` · ${n("chehreh_sales")} فروش چهره منتقل شد` : ""}`;
  return `فروش ${n("sale")} · خرید ${n("purchase")} · هزینه ${n("expense")}${r.duplicates ? ` · تکراری ${n("duplicates")}` : ""}`;
}

function Done({ r }: { r: any }) {
  return (
    <div className="rounded-2xl bg-emerald-500/10 p-3 text-sm">
      <div className="flex items-center gap-2 font-bold text-emerald-700 dark:text-emerald-300"><CheckCircle2 size={16} />انتقال انجام شد</div>
      <div>{resultText(r.kind, r)}</div>
      {r.kind === "customers" && (r.debt_amount > 0 || r.credit_amount > 0) && (
        <div className="text-xs">جمع بدهی مشتریان: {money(r.debt_amount ?? 0)} · جمع بستانکاری (بیعانهٔ باز): {money(r.credit_amount ?? 0)}. بدهی را از پروندهٔ مشتری با «دریافت بدهی» بگیرید.</div>
      )}
      {r.kind === "customers" && r.balance_already > 0 && <div className="text-xs text-amber-700 dark:text-amber-300">مانده‌ی {faDigits(r.balance_already)} مشتری قبلاً منتقل شده بود و دوباره ثبت نشد.</div>}
      {r.kind === "products" && r.codes_moved > 0 && <div className="text-xs">{faDigits(r.codes_moved)} کالایی که اینجا همان کد را داشت، کد جدید گرفت تا کد تیزپرداز حفظ شود.</div>}
      {r.kind === "journal" && r.without_product > 0 && <div className="text-xs text-amber-700 dark:text-amber-300">{faDigits(r.without_product)} ردیف بدون کالای مشخص ثبت شد (فقط در جمع کل گزارش).</div>}
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: any; tone: string }) {
  const cls: Record<string, string> = { emerald: "bg-emerald-500/10", rose: "bg-rose-500/10", violet: "bg-violet-500/10", amber: "bg-amber-500/10", zinc: "bg-zinc-500/10", sky: "bg-sky-500/10" };
  return (
    <div className={`rounded-xl px-3 py-2 ${cls[tone]}`}>
      <div className="muted text-xs">{label}</div>
      <div className="text-base font-extrabold">{typeof value === "number" ? faDigits(value) : value}</div>
    </div>
  );
}

function Chips({ value, onChange, items }: { value: string; onChange: (v: string) => void; items: { v: string; l: string; n?: number }[] }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((i) => (
        <button key={i.v} type="button" onClick={() => onChange(i.v)}
          className={`rounded-full px-3 py-1 text-xs font-semibold ${value === i.v ? "bg-violet-600 text-white" : "border"}`} style={value === i.v ? {} : { borderColor: "var(--border)" }}>
          {i.l}{i.n !== undefined ? ` (${faDigits(i.n)})` : ""}
        </button>
      ))}
    </div>
  );
}

function Pager({ page, total, onPage }: { page: number; total: number; onPage: (p: number) => void }) {
  if (total <= PAGE) return null;
  return (
    <div className="flex items-center justify-center gap-2 text-sm">
      <button className="btn btn-sm" disabled={page === 0} onClick={() => onPage(page - 1)}>قبلی</button>
      <span className="num">{faDigits(page * PAGE + 1)} تا {faDigits(Math.min(total, (page + 1) * PAGE))} از {faDigits(total)}</span>
      <button className="btn btn-sm" disabled={(page + 1) * PAGE >= total} onClick={() => onPage(page + 1)}>بعدی</button>
    </div>
  );
}

/** Search any customer (when the Tizpardaz name is written differently here). */
function FindCustomer({ onPick }: { onPick: (c: any) => void }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [items, setItems] = useState<any[]>([]);
  useEffect(() => {
    if (q.length < 2) { setItems([]); return; }
    const t = setTimeout(() => api(`/api/customers?q=${encodeURIComponent(q)}&limit=6`).then((r) => setItems(r.items)).catch(() => {}), 250);
    return () => clearTimeout(t);
  }, [q]);
  if (!open) return <button type="button" className="text-xs text-violet-600 hover:underline dark:text-violet-300" onClick={() => setOpen(true)}><Search size={11} className="inline" /> مشتری دیگر…</button>;
  return (
    <div className="mt-1 space-y-1">
      <input autoFocus className="input py-1 text-xs" placeholder="نام، موبایل یا کد مشتری" value={q} onChange={(e) => setQ(e.target.value)} />
      {items.map((c) => (
        <button key={c.id} type="button" className="block w-full rounded-lg px-2 py-1 text-right text-xs hover:bg-violet-500/10" onClick={() => { onPick(c); setOpen(false); setQ(""); }}>
          {c.full_name} <span className="muted num">{c.code ? `کد ${c.code}` : ""} {c.mobile ?? ""}</span>
        </button>
      ))}
    </div>
  );
}

type PreviewProps = { pv: any; busy: boolean; onCommit: (body: any, question: string) => void; onCancel: () => void };

function CustomersPreview({ pv, accounts, busy, onCommit, onCancel }: PreviewProps & { accounts: any[] }) {
  const s = pv.summary;
  const rows: any[] = pv.rows;
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [picked, setPicked] = useState<Record<string, any>>({});
  const [balances, setBalances] = useState(true);
  const [creditAs, setCreditAs] = useState<Record<string, string>>({});
  const creditOf = (r: any) => creditAs[r.row] ?? r.credit_as ?? "deposit";
  const [at, setAt] = useState(toLocalIso(new Date()));
  const [accountId, setAccountId] = useState(0);
  const [filter, setFilter] = useState(s.ambiguous ? "ambiguous" : "");
  const [page, setPage] = useState(0);
  const choiceOf = (r: any) => choices[r.row] ?? (r.customer_id ? String(r.customer_id) : r.status === "ambiguous" ? "" : r.status === "generic" ? "skip" : "new");
  const unresolved = rows.filter((r) => choiceOf(r) === "").length;
  const shown = rows.filter((r) => !filter || (filter === "balance" ? r.debit || r.credit : r.status === filter));
  const count = (st: string) => rows.filter((r) => r.status === st).length;

  function submit() {
    const ch = Object.fromEntries(rows.map((r) => [r.row, choiceOf(r)]).filter(([, v]) => v !== "").map(([k, v]) => [k, /^\d+$/.test(v) ? Number(v) : v]));
    const parts = [`${faDigits(rows.length - unresolved)} مشتری از تیزپرداز ثبت شود؟`];
    if (balances) parts.push(`بدهی‌ها (${money(s.debit)}) و بستانکاری‌ها (${money(s.credit)}) هم به‌عنوان ماندهٔ اول دوره منتقل می‌شود.`);
    if (unresolved) parts.push(`⚠ ${faDigits(unresolved)} مشتری هم‌نام انتخاب نشده و فعلاً ثبت نمی‌شود.`);
    const payables = rows.filter((r) => r.credit > 0 && creditOf(r) === "payable");
    if (balances && payables.length) parts.push(`بستانکاری ${faDigits(payables.length)} نفر «طلب فروشنده» (بدهی ما به او) ثبت می‌شود، نه بیعانه.`);
    onCommit({ choices: ch, balances, at: at || null, account_id: accountId || null,
      credit_as: Object.fromEntries(rows.filter((r) => r.credit > 0).map((r) => [r.row, creditOf(r)])) }, parts.join("\n"));
  }

  return (
    <Card title={`پیش‌نمایش مشتریان تیزپرداز - ${pv.file_name}`}>
      <div className="space-y-4">
        <div className="grid gap-2 text-sm sm:grid-cols-4">
          <Stat label="کل مشتریان فایل" value={s.total} tone="violet" />
          <Stat label="پیدا شد / قبلاً وصل" value={`${faDigits(s.found ?? 0)} / ${faDigits(s.linked ?? 0)}`} tone="emerald" />
          <Stat label="هم‌نام (باید انتخاب کنید)" value={s.ambiguous ?? 0} tone={s.ambiguous ? "amber" : "zinc"} />
          <Stat label="مشتری جدید" value={s.new ?? 0} tone="sky" />
          <Stat label="جمع بدهی مشتریان" value={money(s.debit)} tone="rose" />
          <Stat label="جمع بستانکاری مشتریان" value={money(s.credit)} tone="emerald" />
        </div>
        {unresolved > 0 && <div className="rounded-2xl bg-amber-500/15 p-3 text-sm font-semibold text-amber-800 dark:text-amber-200">⚠ {faDigits(unresolved)} نام در این سیستم چند مشتری دارد. برای هر کدام بگویید کدام است (یا «مشتری جدید»).</div>}
        <Chips value={filter} onChange={(v) => { setFilter(v); setPage(0); }} items={[
          { v: "", l: "همه", n: rows.length }, { v: "ambiguous", l: "هم‌نام", n: count("ambiguous") }, { v: "found", l: "پیدا شد", n: count("found") },
          { v: "new", l: "جدید", n: count("new") }, { v: "linked", l: "قبلاً وصل", n: count("linked") }, { v: "balance", l: "دارای مانده", n: rows.filter((r) => r.debit || r.credit).length },
          ...(count("generic") ? [{ v: "generic", l: "حساب عمومی", n: count("generic") }] : []),
        ]} />
        {count("generic") > 0 && <div className="muted text-xs">حساب‌هایی مثل «متفرقه» یا «صندوق» شخص نیستند و وارد نمی‌شوند؛ اگر لازم است، برایشان «مشتری جدید» را انتخاب کنید.</div>}
        <div className="overflow-x-auto rounded-2xl border" style={{ borderColor: "var(--border)" }}>
          <table className="table text-xs">
            <thead><tr><th>کد تیزپرداز</th><th>نام در تیزپرداز</th><th>بدهکار</th><th>بستانکار</th><th>وضعیت</th><th className="min-w-64">در این سیستم</th></tr></thead>
            <tbody>
              {shown.slice(page * PAGE, (page + 1) * PAGE).map((r) => {
                const v = choiceOf(r);
                const extra = picked[r.row];
                return (
                  <tr key={r.row} className={v === "" ? "bg-amber-500/10" : ""}>
                    <td className="num">{r.code}</td>
                    <td><div className="font-semibold">{r.name}</div>{r.raw !== r.name && <div className="muted text-[10px]">{r.raw}</div>}{r.mobile && <div className="num muted" dir="ltr">{r.mobile}</div>}</td>
                    <td className="num">{r.debit ? money(r.debit) : ""}</td>
                    <td className="num">{r.credit ? <>{money(r.credit)}
                      <select className="input mt-1 py-0.5 text-[11px]" value={creditOf(r)} onChange={(e) => setCreditAs({ ...creditAs, [r.row]: e.target.value })}
                        title="بستانکاری یعنی ما به این شخص بدهکاریم">
                        <option value="deposit">بیعانهٔ مشتری (پیش‌پرداخت او)</option>
                        <option value="payable">طلب فروشنده (بدهی ما به او)</option>
                      </select>{r.seller && <div className="text-[10px] text-sky-600">فروشندهٔ ماست</div>}</> : ""}</td>
                    <td><span className={`badge ${C_STATUS[r.status]?.cls}`}>{C_STATUS[r.status]?.l}</span></td>
                    <td>
                      <select className="input py-1 text-xs" value={v} onChange={(e) => setChoices({ ...choices, [r.row]: e.target.value })}>
                        {v === "" && <option value="">— کدام مشتری؟ —</option>}
                        {r.candidates.map((c: any) => (
                          <option key={c.id} value={c.id}>{c.full_name}{c.code ? ` · کد ${c.code}` : ""}{c.mobile ? ` · ${c.mobile}` : " · بدون موبایل"}{c.spent ? ` · خرید ${money(c.spent)}` : ""}</option>
                        ))}
                        {extra && !r.candidates.some((c: any) => c.id === extra.id) && <option value={extra.id}>{extra.full_name}{extra.code ? ` · کد ${extra.code}` : ""}</option>}
                        <option value="new">➕ مشتری جدید بساز</option>
                        <option value="skip">وارد نشود</option>
                      </select>
                      <FindCustomer onPick={(c) => { setPicked({ ...picked, [r.row]: c }); setChoices({ ...choices, [r.row]: String(c.id) }); }} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <Pager page={page} total={shown.length} onPage={setPage} />
        <div className="space-y-3 rounded-2xl p-3" style={{ background: "var(--surface)" }}>
          <label className="flex items-center gap-2 text-sm font-semibold"><input type="checkbox" checked={balances} onChange={(e) => setBalances(e.target.checked)} />مانده‌ها هم منتقل شود (بدهی = بدهی قبلی مشتری، بستانکاری = بیعانهٔ باز او)</label>
          {balances && (
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="تاریخ مانده‌ها" hint="معمولاً روز آخری که در تیزپرداز کار شده"><JalaliPicker pastOnly withTime={false} value={at} onChange={setAt} /></Field>
              <Field label="بستانکاری‌ها روی کدام حساب بماند؟" hint="پول قبلاً گرفته شده؛ به موجودی این حساب اضافه نمی‌شود">
                <select className="input" value={accountId} onChange={(e) => setAccountId(Number(e.target.value))}>
                  <option value={0}>حساب پیش‌فرض بیعانه</option>
                  {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
              </Field>
            </div>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="btn btn-primary flex-1 py-3" disabled={busy} onClick={submit}>ثبت مشتریان{unresolved ? ` (${faDigits(unresolved)} هم‌نام بی‌جواب ثبت نمی‌شود)` : ""}</button>
          <button className="btn" onClick={onCancel}>انصراف</button>
        </div>
      </div>
    </Card>
  );
}

function SkuMap() {
  const toast = useToast();
  const { data, reload } = useApi<any>("/api/import/tizpardaz/sku-map");
  const [text, setText] = useState<string | null>(null);
  const value = text ?? data?.text ?? "";
  async function save() {
    try {
      await api("/api/import/tizpardaz/sku-map", { method: "PUT", body: { text: value } });
      toast("لیست SKU ذخیره شد");
      setText(null);
      reload();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }
  return (
    <details className="rounded-2xl border p-3 text-sm" style={{ borderColor: "var(--border)" }}>
      <summary className="cursor-pointer font-bold">لیست «کد کالا ← SKU سایت» ({faDigits(data?.count ?? 0)} مورد) - برای دیدن یا اصلاح بزنید</summary>
      <div className="mt-2 space-y-2">
        <div className="muted text-xs">هر خط: کد کالا، فاصله، SKU (مثل <span dir="ltr" className="num">14 LT-EX-MLX-001</span>). اگر فایل کالاها ستون SKU داشته باشد، همان ستون اولویت دارد.</div>
        <textarea className="input num min-h-48 text-xs" dir="ltr" value={value} onChange={(e) => setText(e.target.value)} />
        <button className="btn btn-sm" disabled={text === null} onClick={save}>ذخیرهٔ لیست SKU</button>
      </div>
    </details>
  );
}

function ProductsPreview({ pv, busy, onCommit, onCancel }: PreviewProps) {
  const s = pv.summary;
  const rows: any[] = pv.rows;
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [at, setAt] = useState(toLocalIso(new Date()));
  const [filter, setFilter] = useState(s.similar ? "similar" : "");
  const choiceOf = (r: any) => choices[r.row] ?? r.default;
  const count = (st: string) => rows.filter((r) => r.status === st).length;
  const shown = rows.filter((r) => !filter || (filter === "warn" ? r.warnings.length : r.status === filter));

  function submit() {
    const combine = rows.filter((r) => choiceOf(r) !== "new" && choiceOf(r) !== "skip").length;
    onCommit({ choices: Object.fromEntries(rows.map((r) => [r.row, choiceOf(r)])), at: at || null },
      `${faDigits(rows.filter((r) => choiceOf(r) !== "skip").length)} کالا از تیزپرداز ثبت شود؟\n${faDigits(combine)} کالا با کالا/خدمت موجود یکی می‌شود و موجودی همه برابر تیزپرداز می‌شود.`);
  }

  return (
    <Card title={`پیش‌نمایش کالاهای تیزپرداز - ${pv.file_name}`}>
      <div className="space-y-4">
        <div className="grid gap-2 text-sm sm:grid-cols-4">
          <Stat label="کل کالاها" value={s.total} tone="violet" />
          <Stat label="هم‌نام / شبیه در این سیستم" value={`${faDigits((s.match ?? 0) + (s.linked ?? 0))} / ${faDigits(s.similar ?? 0)}`} tone={s.similar ? "amber" : "emerald"} />
          <Stat label="موجودی (عدد)" value={s.units} tone="sky" />
          <Stat label="ارزش موجودی به قیمت خرید" value={money(s.value)} tone="emerald" />
          {s.no_sku > 0 && <Stat label="بدون SKU" value={s.no_sku} tone="amber" />}
        </div>
        <div className="rounded-2xl bg-violet-500/5 p-3 text-xs leading-6">
          برای هر کالا بگویید با چه چیزی <b>یکی شود</b>: «کالای جدید»، یک <b>کالای موجود</b> (موجودی‌اش برابر تیزپرداز می‌شود)، یا یک <b>خدمت چهره</b> که در واقع فروش محصول بوده (سوابق فروشش به این کالا منتقل و خدمت بایگانی می‌شود).
        </div>
        <Chips value={filter} onChange={setFilter} items={[
          { v: "", l: "همه", n: rows.length }, { v: "similar", l: "شبیه دارد", n: count("similar") }, { v: "match", l: "هم‌نام", n: count("match") },
          { v: "new", l: "جدید", n: count("new") }, { v: "linked", l: "قبلاً وارد شده", n: count("linked") }, { v: "warn", l: "هشدار", n: rows.filter((r) => r.warnings.length).length },
        ]} />
        <div className="overflow-x-auto rounded-2xl border" style={{ borderColor: "var(--border)" }}>
          <table className="table text-xs">
            <thead><tr><th>کد</th><th>نام کالا</th><th>SKU سایت</th><th>موجودی</th><th>خرید</th><th>فروش</th><th>وضعیت</th><th className="min-w-64">در این سیستم</th></tr></thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.row} className={r.status === "similar" && choiceOf(r) === "new" ? "bg-amber-500/10" : ""}>
                  <td className="num font-bold">{r.code}</td>
                  <td><div className="font-semibold">{r.name}</div>{r.group && <div className="muted text-[10px]">{r.group}</div>}{r.warnings.map((w: string) => <div key={w} className="text-[10px] text-amber-600">⚠ {w}</div>)}</td>
                  <td className="num whitespace-nowrap" dir="ltr">{r.sku ?? "—"}</td>
                  <td className="num">{faDigits(r.qty)}</td>
                  <td className="num">{r.buy ? money(r.buy) : ""}</td>
                  <td className="num">{r.sell ? money(r.sell) : ""}</td>
                  <td><span className={`badge ${P_STATUS[r.status]?.cls}`}>{P_STATUS[r.status]?.l}</span></td>
                  <td>
                    <select className="input py-1 text-xs" value={choiceOf(r)} onChange={(e) => setChoices({ ...choices, [r.row]: e.target.value })}>
                      <option value="new">➕ کالای جدید</option>
                      {r.candidates.map((c: any) => c.type === "product"
                        ? <option key={`p${c.id}`} value={`p:${c.id}`}>یکی با کالای «{c.name}» (کد {c.code}{c.sku ? ` · ${c.sku}` : ""} · موجودی {c.stock_qty})</option>
                        : <option key={`s${c.id}`} value={`s:${c.id}`}>یکی با خدمت چهره «{c.name}»{c.line ? ` (${c.line})` : ""} - {c.sold} فروش</option>)}
                      <option value="skip">وارد نشود</option>
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Field label="تاریخ موجودی" hint="موجودی کالاها از این تاریخ برابر تیزپرداز ثبت می‌شود"><JalaliPicker pastOnly withTime={false} value={at} onChange={setAt} /></Field>
        <div className="flex flex-wrap gap-2">
          <button className="btn btn-primary flex-1 py-3" disabled={busy} onClick={submit}>ثبت کالاها</button>
          <button className="btn" onClick={onCancel}>انصراف</button>
        </div>
      </div>
    </Card>
  );
}

function JournalPreview({ pv, busy, onCommit, onCancel }: PreviewProps) {
  const s = pv.summary;
  const rows: any[] = pv.rows;
  const products = useApi<any[]>("/api/products?all=1").data ?? [];
  const [pmap, setPmap] = useState<Record<string, string>>({});
  const [cmap, setCmap] = useState<Record<string, string>>({});
  const [picked, setPicked] = useState<Record<string, any>>({});
  const [filter, setFilter] = useState("");
  const [page, setPage] = useState(0);
  const custDefault = (party: string) => (party.includes("(") || /^[\d۰-۹]+$/.test(party.trim()) ? "new" : "skip");
  const cOf = (u: any) => cmap[u.party] ?? (u.candidates.length === 1 ? String(u.candidates[0].id) : u.generic ? "skip" : custDefault(u.party));
  const pOf = (u: any) => pmap[u.name] ?? (u.similar.length === 1 ? String(u.similar[0].id) : "skip");
  const shown = useMemo(() => rows.filter((r) => (!filter || (filter === "dup" ? r.duplicate : r.kind === filter))), [rows, filter]);
  const kinds = Object.keys(J_KINDS).filter((k) => rows.some((r) => r.kind === k));

  function submit() {
    const product_map = Object.fromEntries(pv.unknown_products.map((u: any) => [u.name, pOf(u)]).map(([k, v]: any) => [k, /^\d+$/.test(v) ? Number(v) : v]));
    const customer_map = Object.fromEntries(pv.unknown_customers.map((u: any) => [u.party, cOf(u)]).map(([k, v]: any) => [k, /^\d+$/.test(v) ? Number(v) : v]));
    onCommit({ product_map, customer_map }, `${faDigits(s.new)} ردیف سابقه از تیزپرداز ثبت شود؟\nاین‌ها فقط سابقه‌اند و موجودی صندوق، کالا و حساب مشتری‌ها را تغییر نمی‌دهند.`);
  }

  return (
    <Card title={`پیش‌نمایش دفتر روزنامهٔ تیزپرداز - ${pv.file_name}`}>
      <div className="space-y-4">
        <div className="grid gap-2 text-sm sm:grid-cols-4">
          {Object.entries(s.counts ?? {}).map(([k, n]: any) => <Stat key={k} label={`${J_KINDS[k]} (${faDigits(n)} ردیف)`} value={money(s.amounts?.[k] ?? 0)} tone={k === "sale" ? "emerald" : k === "expense" ? "rose" : "sky"} />)}
          <Stat label="بازهٔ تاریخ" value={s.first_date ? `${formatJ(s.first_date, false)} تا ${formatJ(s.last_date, false)}` : "—"} tone="violet" />
          {Object.keys(s.doc_counts ?? {}).length > 0 && <Stat label="تعداد فاکتورها" tone="sky"
            value={Object.entries(s.doc_counts).map(([k, n]: any) => `${faDigits(n)} ${J_KINDS[k]}`).join(" · ")} />}
          {s.duplicates > 0 && <Stat label="تکراری (قبلاً وارد شده)" value={s.duplicates} tone="zinc" />}
        </div>
        {Object.keys(s.ignored ?? {}).length > 0 && (
          <div className="muted text-xs">ردیف‌هایی که سابقهٔ کالا یا هزینه نیستند و کنار گذاشته شدند (طرف مشتری/صندوق سند و…): {Object.entries(s.ignored).map(([k, n]: any) => `${k}: ${faDigits(n)}`).join(" · ")}</div>
        )}
        {pv.unknown_products.length > 0 && (
          <div className="space-y-2 rounded-2xl bg-amber-500/10 p-3 text-sm">
            <div className="font-bold">این کالاها در سیستم پیدا نشدند - هر کدام کدام کالاست؟</div>
            <div className="muted text-xs">اگر اول فایل «کالاها» را وارد کرده باشید، بیشتر کالاها خودکار پیدا می‌شوند. «بدون کالا» یعنی فقط در جمع گزارش می‌آید.</div>
            <div className="grid gap-2 sm:grid-cols-2">
              {pv.unknown_products.map((u: any) => (
                <div key={u.name} className="flex items-center gap-2">
                  <span className="w-1/2 truncate font-semibold" title={u.name}>{u.name} <span className="muted text-xs">({faDigits(u.count)})</span></span>
                  <select className="input w-1/2 py-1 text-xs" value={pOf(u)} onChange={(e) => setPmap({ ...pmap, [u.name]: e.target.value })}>
                    <option value="skip">بدون کالا</option>
                    {u.similar.map((x: any) => <option key={`s${x.id}`} value={x.id}>شاید: {x.name}</option>)}
                    {products.map((p) => <option key={p.id} value={p.id}>{p.code} · {p.name}</option>)}
                  </select>
                </div>
              ))}
            </div>
          </div>
        )}
        {pv.unknown_customers.length > 0 && (
          <div className="space-y-2 rounded-2xl bg-sky-500/10 p-3 text-sm">
            <div className="font-bold">این اشخاص (خریدار یا فروشنده) به پروندهٔ مشخصی وصل نشدند</div>
            <div className="muted text-xs">نام‌هایی مثل «تاجیک(ساناز)» یا کد حساب، اگر انتخاب نکنید پروندهٔ جدید می‌گیرند؛ کلمه‌هایی مثل «متفرقه» فقط به‌صورت نام می‌مانند. فروشنده‌ها در هر حال در فهرست «تأمین‌کنندگان» ثبت می‌شوند.</div>
            <div className="grid gap-2 sm:grid-cols-2">
              {pv.unknown_customers.map((u: any) => {
                const extra = picked[u.party];
                return (
                  <div key={u.party}>
                    <div className="flex items-center gap-2">
                      <span className="w-1/2 truncate font-semibold" title={u.party}>{u.name} <span className="muted text-xs">({faDigits(u.count)} فاکتور)</span>{u.seller && <span className="badge mr-1 bg-sky-500/10 text-[10px] text-sky-700">فروشنده</span>}</span>
                      <select className="input w-1/2 py-1 text-xs" value={cOf(u)} onChange={(e) => setCmap({ ...cmap, [u.party]: e.target.value })}>
                        {u.candidates.map((c: any) => <option key={c.id} value={c.id}>{c.full_name}{c.code ? ` · کد ${c.code}` : ""}{c.mobile ? ` · ${c.mobile}` : ""}</option>)}
                        {extra && !u.candidates.some((c: any) => c.id === extra.id) && <option value={extra.id}>{extra.full_name}{extra.code ? ` · کد ${extra.code}` : ""}</option>}
                        <option value="new">➕ مشتری جدید</option>
                        <option value="skip">فقط نام بماند</option>
                      </select>
                    </div>
                    <FindCustomer onPick={(c) => { setPicked({ ...picked, [u.party]: c }); setCmap({ ...cmap, [u.party]: String(c.id) }); }} />
                  </div>
                );
              })}
            </div>
          </div>
        )}
        <Chips value={filter} onChange={(v) => { setFilter(v); setPage(0); }} items={[
          { v: "", l: "همه", n: rows.length }, ...kinds.map((k) => ({ v: k, l: J_KINDS[k], n: rows.filter((r) => r.kind === k).length })),
          ...(s.duplicates ? [{ v: "dup", l: "تکراری", n: s.duplicates }] : []),
        ]} />
        <div className="overflow-x-auto rounded-2xl border" style={{ borderColor: "var(--border)" }}>
          <table className="table text-xs">
            <thead><tr><th>تاریخ</th><th>سند</th><th>نوع</th><th>کالا / حساب</th><th>طرف حساب</th><th>تعداد</th><th>مبلغ</th></tr></thead>
            <tbody>
              {shown.slice(page * PAGE, (page + 1) * PAGE).map((r) => (
                <tr key={r.row} className={r.duplicate ? "opacity-50" : ""}>
                  <td className="num">{formatJ(r.date, false)}</td>
                  <td className="num">{r.doc_no}</td>
                  <td>{J_KINDS[r.kind]}{r.duplicate ? <div className="text-[10px]">تکراری</div> : null}</td>
                  <td>{r.kind === "expense" ? <><b>{r.account}</b><div className="muted text-[10px]">{r.description}</div></> : <>{r.product_name}{r.product_id ? <span className="text-emerald-600"> ✓</span> : <span className="text-amber-600"> ?</span>}</>}</td>
                  <td>{r.party ? <>{r.party}{r.customer_id ? <span className="text-emerald-600"> ✓</span> : null}</> : ""}</td>
                  <td className="num">{r.qty ? faDigits(r.qty) : ""}</td>
                  <td className="num">{money(r.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Pager page={page} total={shown.length} onPage={setPage} />
        <div className="flex flex-wrap gap-2">
          <button className="btn btn-primary flex-1 py-3" disabled={busy || !s.new} onClick={submit}>ثبت {faDigits(s.new)} ردیف سابقه</button>
          <button className="btn" onClick={onCancel}>انصراف</button>
        </div>
      </div>
    </Card>
  );
}

/** Sellers whose Tizpardaz credit balance was brought over as a customer deposit: it is what we owe them. */
function SellerCredits() {
  const toast = useToast();
  const { data, reload } = useApi<any[]>("/api/import/tizpardaz/seller-credits");
  const [picked, setPicked] = useState<Record<number, boolean>>({});
  if (!data?.length) return null;
  const chosen = data.filter((x) => picked[x.deposit_id] ?? true);
  async function fix() {
    if (!window.confirm(`بستانکاری ${faDigits(chosen.length)} فروشنده از «بیعانه» به «بدهی ما به فروشنده» منتقل شود؟`)) return;
    try {
      const r = await api("/api/import/tizpardaz/seller-credits", { body: { deposit_ids: chosen.map((x) => x.deposit_id) } });
      toast(`${faDigits(r.converted ?? 0)} مورد اصلاح شد (${money(r.amount ?? 0)})${r.skipped ? ` · ${faDigits(r.skipped)} مورد دست نخورد` : ""}`);
      reload();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }
  return (
    <Card title={<span className="flex items-center gap-2 text-rose-700 dark:text-rose-300">⚠ بستانکاری فروشندگان به‌اشتباه «بیعانه» ثبت شده</span>}>
      <div className="space-y-3 text-sm">
        <p className="leading-7">این اشخاص در دفتر روزنامهٔ تیزپرداز <b>فروشندهٔ ما</b> هستند (از آن‌ها خرید کرده‌ایم). ماندهٔ بستانکار آن‌ها یعنی <b>ما به آن‌ها بدهکاریم</b>، ولی هنگام ورود مشتریان به‌عنوان «بیعانهٔ مشتری» ثبت شده است. با اصلاح، این مبلغ «بدهی ما به فروشنده» می‌شود و از بخش خرید (یا پروندهٔ همان شخص) قابل پرداخت است.</p>
        <div className="space-y-1.5">
          {data.map((x) => (
            <label key={x.deposit_id} className="flex items-center justify-between gap-2 rounded-xl px-3 py-2" style={{ background: "var(--surface)" }}>
              <span className="flex items-center gap-2"><input type="checkbox" checked={picked[x.deposit_id] ?? true} onChange={(e) => setPicked({ ...picked, [x.deposit_id]: e.target.checked })} />
                <b>{x.name}</b>{x.code && <span className="num muted text-xs">کد {x.code}</span>}<span className="muted text-xs">{faDigits(x.purchases)} فاکتور خرید</span></span>
              <span className="num font-semibold">{money(x.amount)}</span>
            </label>
          ))}
        </div>
        <button className="btn btn-primary w-full" disabled={!chosen.length} onClick={fix}>اصلاح {faDigits(chosen.length)} مورد: «بدهی ما به فروشنده»</button>
      </div>
    </Card>
  );
}
