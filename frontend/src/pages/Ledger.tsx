import {
  ArrowDownLeft, ArrowUpRight, Banknote, BookOpenCheck, CheckCircle2, ChevronLeft, CreditCard, Globe, Landmark, Scale, Search, TrendingDown, TrendingUp, Wallet,
} from "lucide-react";
import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { Card, Empty, HelpTip, Loading, Modal, PageHeader, Spinner, Stat, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { ACCOUNT_KINDS, compactMoney, daysAgo, isoDate, jdatetime, jlong, money, num, unitLabel } from "../lib/format";
import { useApi } from "../lib/hooks";

const TYPES: Record<string, string> = { asset: "دارایی", liability: "بدهی", equity: "سرمایه", revenue: "درآمد", expense: "هزینه" };
const CASH_PARENT = "1100";

const REF_STYLE: Record<string, string> = {
  deposit: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  deposit_apply: "bg-sky-500/15 text-sky-600 dark:text-sky-400",
  invoice: "bg-violet-500/15 text-violet-600 dark:text-violet-300",
  invoice_void: "bg-zinc-500/15 text-zinc-500",
  payment: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  expense: "bg-rose-500/15 text-rose-600 dark:text-rose-400",
  commission: "bg-fuchsia-500/15 text-fuchsia-600 dark:text-fuchsia-400",
  staff_payout: "bg-orange-500/15 text-orange-600 dark:text-orange-400",
};
const REF_FILTERS: { value: string; label: string }[] = [
  { value: "", label: "همه اسناد" },
  { value: "invoice", label: "فاکتور فروش" },
  { value: "payment", label: "دریافت وجه" },
  { value: "deposit", label: "بیعانه (دریافت/استرداد/سوخت)" },
  { value: "deposit_apply", label: "تسویه بیعانه با فاکتور" },
  { value: "expense", label: "هزینه" },
  { value: "commission", label: "سهم پرسنل" },
  { value: "staff_payout", label: "پرداخت به پرسنل" },
  { value: "invoice_void", label: "ابطال فاکتور" },
];
const RANGES: Record<string, { label: string; days: number | null }> = {
  all: { label: "همه", days: null },
  "1": { label: "امروز", days: 1 },
  "7": { label: "۷ روز", days: 7 },
  "30": { label: "۳۰ روز", days: 30 },
  "90": { label: "۳ ماه", days: 90 },
};
const rangeQuery = (r: string) => {
  const d = RANGES[r]?.days;
  return d ? `start=${daysAgo(d - 1)}&end=${isoDate(new Date())}` : "";
};

const KIND_ICON: Record<string, ReactNode> = {
  pos: <CreditCard size={20} />, card: <CreditCard size={20} />, bank: <Landmark size={20} />, cash: <Banknote size={20} />, gateway: <Globe size={20} />,
};

type Line = { account_id: number; code: string; name: string; type: string; parent: string | null; debit: number; credit: number };
type Entry = { id: number; at: string; description: string; ref_type: string; ref_label: string; detail: string; customer: string | null; amount: number; credit_total: number; lines: Line[] };
type StatementTarget = { id: number; title: string; subtitle?: string };

/** Plain-language sentence for a journal entry, so non-accountants can read the books. */
function explain(e: Entry): string {
  const dr = e.lines.filter((l) => l.debit);
  const cr = e.lines.filter((l) => l.credit);
  const names = (ls: Line[]) => [...new Set(ls.map((l) => `«${l.name}»`))].join(" و ");
  const cashIn = dr.filter((l) => l.parent === CASH_PARENT);
  const cashOut = cr.filter((l) => l.parent === CASH_PARENT);
  const amt = money(e.amount);
  switch (e.ref_type) {
    case "deposit":
      if (cashIn.length) return `${amt} بیعانه از مشتری به ${names(cashIn)} واریز شد. این پول تا انجام خدمت، امانت مشتری (بدهی سالن) است و هنوز درآمد نیست.`;
      if (cashOut.length) return `${amt} بیعانه از ${names(cashOut)} به مشتری پس داده شد و بدهی سالن به مشتری صفر شد.`;
      return `مشتری مراجعه نکرد؛ ${amt} بیعانه او از امانت خارج و به درآمد سالن منتقل شد.`;
    case "deposit_apply":
      return `${amt} از بیعانه قبلی مشتری بابت این فاکتور حساب شد و به همین اندازه از بدهی مشتری کم شد (پول جدیدی جابه‌جا نشده است).`;
    case "invoice":
      return `فروش خدمت به مبلغ ${amt} ثبت شد: مشتری این مبلغ را به سالن بدهکار شد و درآمد ${names(cr)} به همین اندازه زیاد شد.`;
    case "payment":
      return `مشتری ${amt} به ${names(cashIn)} پرداخت کرد و به همین اندازه از بدهی او به سالن کم شد.`;
    case "expense":
      return `${amt} از ${names(cashOut)} بابت ${names(dr)} پرداخت شد؛ موجودی آن حساب کم و هزینه‌های سالن زیاد شد.`;
    case "commission":
      return `سهم پرسنل از این فاکتور (${amt}) به‌عنوان هزینه سالن و طلب پرسنل ثبت شد؛ هنوز پولی پرداخت نشده است.`;
    case "staff_payout":
      return `${amt} از ${names(cashOut)} به پرسنل پرداخت شد و به همین اندازه از طلب پرسنل کم شد.`;
    case "invoice_void":
      return `فاکتور باطل شد؛ همان سند قبلی دقیقاً برعکس ثبت شد تا اثر آن از دفاتر پاک شود.`;
    default:
      return `${names(dr)} بدهکار و ${names(cr)} بستانکار شد.`;
  }
}

export default function Ledger() {
  const [tab, setTab] = useState<"accounts" | "journal" | "tb">("accounts");
  const [statement, setStatement] = useState<StatementTarget | null>(null);
  const tb = useApi<any>("/api/ledger/trial-balance");
  const balances = useApi<any[]>("/api/accounts/balances");
  const d = tb.data;
  const balanced = d && d.balance_debit === d.balance_credit && d.turnover_debit === d.turnover_credit;

  return (
    <div className="space-y-5">
      <PageHeader title="دفاتر حسابداری" subtitle="حسابداری دوطرفه: هر عملیات یک سند متوازن بدهکار/بستانکار" icon={<BookOpenCheck size={22} />} />
      {d && (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-3">
          <Stat label="موجودی صندوق و بانک‌ها" value={money(d.cash)} icon={<Wallet size={20} />} tone="sky" onClick={() => setTab("accounts")}
            hint="کلیک برای جزئیات"
            help={<><p>جمع پولی که طبق دفاتر الان در صندوق نقدی، کارت‌ها، حساب‌های بانکی و کارتخوان‌ها هست.</p><p>هر دریافت (بیعانه، پرداخت فاکتور) آن را زیاد و هر پرداخت (هزینه، سهم پرسنل، استرداد بیعانه) آن را کم می‌کند.</p></>} />
          <Stat label={d.net_profit >= 0 ? "سود خالص (از ابتدا)" : "زیان خالص (از ابتدا)"} value={money(Math.abs(d.net_profit))} icon={d.net_profit >= 0 ? <TrendingUp size={20} /> : <TrendingDown size={20} />}
            tone={d.net_profit >= 0 ? "emerald" : "pink"} hint={`درآمد ${compactMoney(d.by_type.revenue)} − هزینه ${compactMoney(d.by_type.expense)}`}
            help={<><p>جمع درآمدها منهای جمع هزینه‌ها (شامل سهم پرسنل) از اولین سند تا امروز.</p><p>ثبت یک هزینه ۱۰ میلیونی دقیقاً ۱۰ میلیون از این عدد کم می‌کند.</p><p>بیعانه‌ای که هنوز خدمتش انجام نشده درآمد حساب نمی‌شود.</p></>} />
          <Stat label="توازن دفاتر" value={balanced ? "متوازن" : "نامتوازن!"} icon={<CheckCircle2 size={20} />} tone={balanced ? "emerald" : "pink"}
            hint={`${num(d.entries)} سند ثبت‌شده`}
            help={<><p>در حسابداری دوطرفه هر سند باید جمع بدهکار و بستانکارش برابر باشد؛ پس جمع کل دفاتر هم همیشه برابر است.</p><p>«متوازن» یعنی هیچ سند ناقص یا اشتباهی در دفاتر نیست. اگر «نامتوازن» دیدید، فوراً به پشتیبانی اطلاع دهید.</p></>} />
          <Stat label="جمع مانده‌های بدهکار" value={money(d.balance_debit)} icon={<Scale size={20} />}
            hint={`دارایی ${compactMoney(d.by_type.asset)} + هزینه ${compactMoney(d.by_type.expense)}`}
            help={<><p>جمع مانده حساب‌هایی که مانده بدهکار دارند (عمدتاً موجودی نقد و بانک، طلب از مشتریان و هزینه‌ها).</p><p>این عدد با پرداخت هزینه تغییر نمی‌کند: هزینه ۱۰ میلیونی، ۱۰ میلیون از موجودی بانک کم و ۱۰ میلیون به هزینه اضافه می‌کند. فقط وقتی بزرگ می‌شود که واقعاً پول یا طلب جدیدی وارد شود (مثل فروش).</p><p>باید همیشه با «جمع مانده‌های بستانکار» برابر باشد.</p></>} />
          <Stat label="جمع مانده‌های بستانکار" value={money(d.balance_credit)} icon={<Scale size={20} />} tone="pink"
            hint={`بدهی ${compactMoney(d.by_type.liability)} + درآمد ${compactMoney(d.by_type.revenue)}`}
            help={<><p>جمع مانده حساب‌هایی که مانده بستانکار دارند: بیعانه‌های امانی مشتریان، طلب پرسنل، سرمایه و درآمدها.</p><p>همیشه دقیقاً برابر «جمع مانده‌های بدهکار» است.</p></>} />
          <Stat label="گردش کل اسناد" value={money(d.turnover_debit)} icon={<BookOpenCheck size={20} />} tone="amber"
            hint="بدهکار = بستانکار"
            help={<><p>جمع تمام مبالغی که از اول در ستون بدهکار (و برابر آن در ستون بستانکار) ثبت شده است.</p><p>این عدد «حجم کار» دفاتر است، نه موجودی و نه سود؛ برای همین با <b>هر</b> عملیاتی، حتی هزینه، بزرگ‌تر می‌شود. مثلاً هزینه ۱۰ میلیونی هم ۱۰ میلیون بدهکار (هزینه) و هم ۱۰ میلیون بستانکار (بانک) ثبت می‌کند، پس گردش ۱۰ میلیون بالا می‌رود.</p></>} />
        </div>
      )}
      <Tabs value={tab} onChange={setTab} items={[{ value: "accounts", label: "صندوق و بانک‌ها" }, { value: "journal", label: "دفتر روزنامه" }, { value: "tb", label: "تراز آزمایشی" }]} />
      {tab === "accounts" && <AccountsTab data={balances.data} onOpen={setStatement} />}
      {tab === "journal" && <JournalTab onOpenAccount={setStatement} />}
      {tab === "tb" && <TrialBalanceTab data={d} onOpen={setStatement} />}
      <Modal open={!!statement} onClose={() => setStatement(null)} title={statement ? `گردش حساب: ${statement.title}` : ""} wide>
        {statement && <AccountStatement target={statement} />}
      </Modal>
    </div>
  );
}

// ------------------------------------------------------------------ cash, banks, POS
function AccountsTab({ data, onOpen }: { data: any[] | null; onOpen: (t: StatementTarget) => void }) {
  if (!data) return <Loading />;
  if (!data.length) return <Card><Empty text="هنوز صندوق یا حساب بانکی تعریف نشده است" /></Card>;
  const shown = data.filter((b) => b.is_active || b.count);
  const total = shown.reduce((s, b) => s + b.balance, 0);
  return (
    <div className="space-y-3">
      <div className="muted flex items-center gap-1.5 text-sm">
        روی هر حساب کلیک کنید تا ریز همه واریزها و برداشت‌های آن را ببینید.
        <HelpTip title="صندوق و بانک‌ها">
          <p>«واریز» یعنی پولی که وارد این حساب شده (بیعانه، پرداخت فاکتور).</p>
          <p>«برداشت» یعنی پولی که از این حساب خارج شده (هزینه، پرداخت به پرسنل، استرداد بیعانه).</p>
          <p>موجودی = جمع واریزها − جمع برداشت‌ها. اگر منفی شد یعنی بیشتر از پولی که در دفاتر وارد شده، از آن خرج ثبت شده است.</p>
        </HelpTip>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {shown.map((b) => (
          <button key={b.id} type="button" onClick={() => onOpen({ id: b.ledger_account_id, title: b.name, subtitle: [ACCOUNT_KINDS[b.kind], b.bank_name].filter(Boolean).join(" · ") })}
            className="card fade-up group p-5 text-start transition hover:-translate-y-0.5 hover:border-violet-400">
            <div className="flex items-start justify-between gap-3">
              <div className="flex min-w-0 items-center gap-3">
                <div className="grid h-11 w-11 shrink-0 place-items-center rounded-2xl bg-gradient-to-br from-violet-500 to-pink-500 text-white shadow-lg shadow-violet-500/30">{KIND_ICON[b.kind] ?? <Wallet size={20} />}</div>
                <div className="min-w-0">
                  <div className="truncate font-bold">{b.name}{!b.is_active && <span className="muted mr-1 text-xs">(بایگانی)</span>}</div>
                  <div className="muted text-xs">{[ACCOUNT_KINDS[b.kind], b.bank_name].filter(Boolean).join(" · ")}</div>
                </div>
              </div>
              <ChevronLeft size={18} className="muted mt-3 transition group-hover:-translate-x-1" />
            </div>
            <div className={`num mt-4 text-xl font-extrabold ${b.balance < 0 ? "text-rose-500" : ""}`}>{money(b.balance)}</div>
            <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
              <div><div className="muted">واریز</div><div className="num font-semibold text-emerald-600 dark:text-emerald-400">{money(b.total_in, false)}</div></div>
              <div><div className="muted">برداشت</div><div className="num font-semibold text-rose-500">{money(b.total_out, false)}</div></div>
              <div><div className="muted">تراکنش</div><div className="num font-semibold">{num(b.count)}</div></div>
            </div>
          </button>
        ))}
      </div>
      <div className="card flex items-center justify-between p-4 text-sm">
        <span className="font-semibold">جمع موجودی همه حساب‌ها</span>
        <span className={`num text-lg font-extrabold ${total < 0 ? "text-rose-500" : ""}`}>{money(total)}</span>
      </div>
    </div>
  );
}

function AccountStatement({ target }: { target: StatementTarget }) {
  const [range, setRange] = useState("all");
  const q = rangeQuery(range);
  const st = useApi<any>(`/api/ledger/accounts/${target.id}/statement${q ? "?" + q : ""}`, [target.id, range]);
  const s = st.data;
  const isCashLike = s?.account?.type === "asset";
  const inLabel = isCashLike ? "واریز" : "بدهکار";
  const outLabel = isCashLike ? "برداشت" : "بستانکار";
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="muted text-sm">{target.subtitle || (s ? `کد ${s.account.code} · ${TYPES[s.account.type]}` : "")}</div>
        <Tabs value={range} onChange={setRange} items={Object.entries(RANGES).map(([value, r]) => ({ value, label: r.label }))} />
      </div>
      {!s ? <Loading /> : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Mini label="مانده اول دوره" value={money(s.opening)} />
            <Mini label={`جمع ${inLabel}`} value={money(s.total_debit)} cls="text-emerald-600 dark:text-emerald-400" />
            <Mini label={`جمع ${outLabel}`} value={money(s.total_credit)} cls="text-rose-500" />
            <Mini label="مانده پایان دوره" value={money(s.closing)} cls={s.closing < 0 ? "text-rose-500" : ""} />
          </div>
          {!s.rows.length ? <Empty text="در این بازه تراکنشی ثبت نشده است" /> : (
            <div className="overflow-x-auto rounded-2xl border" style={{ borderColor: "var(--border)" }}>
              <table className="table">
                <thead><tr><th>تاریخ</th><th>شرح</th><th>طرف حساب</th><th>{inLabel}</th><th>{outLabel}</th><th>مانده</th></tr></thead>
                <tbody>
                  {[...s.rows].reverse().map((r: any, i: number) => (
                    <tr key={i}>
                      <td className="num muted whitespace-nowrap text-xs">{jdatetime(r.at)}</td>
                      <td className="min-w-[14rem]">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className={`badge ${REF_STYLE[r.ref_type] ?? "bg-violet-500/10 text-violet-600"}`}>{r.ref_label}</span>
                          <span className="font-semibold">{r.description}</span>
                        </div>
                        {(r.detail || r.customer || r.staff) && <div className="muted mt-0.5 text-xs">{[r.detail, r.customer && `مشتری: ${r.customer}`, r.staff && `پرسنل: ${r.staff}`].filter(Boolean).join(" · ")}</div>}
                      </td>
                      <td className="muted text-xs">{r.counterpart}</td>
                      <td className="num font-semibold text-emerald-600 dark:text-emerald-400">{r.debit ? <span className="inline-flex items-center gap-0.5"><ArrowDownLeft size={13} />{money(r.debit, false)}</span> : ""}</td>
                      <td className="num font-semibold text-rose-500">{r.credit ? <span className="inline-flex items-center gap-0.5"><ArrowUpRight size={13} />{money(r.credit, false)}</span> : ""}</td>
                      <td className={`num font-bold ${r.balance < 0 ? "text-rose-500" : ""}`}>{money(r.balance, false)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="muted text-xs">جدیدترین تراکنش بالای جدول است. ستون «مانده» موجودی حساب را درست بعد از همان تراکنش نشان می‌دهد. مبالغ به {unitLabel()}.</div>
        </>
      )}
    </div>
  );
}

function Mini({ label, value, cls = "" }: { label: string; value: ReactNode; cls?: string }) {
  return (
    <div className="rounded-2xl border p-3" style={{ borderColor: "var(--border)", background: "var(--surface)" }}>
      <div className="muted text-xs">{label}</div>
      <div className={`num mt-1 font-extrabold ${cls}`}>{value}</div>
    </div>
  );
}

// ------------------------------------------------------------------ journal
const PAGE = 50;

function JournalTab({ onOpenAccount }: { onOpenAccount: (t: StatementTarget) => void }) {
  const [range, setRange] = useState("30");
  const [refType, setRefType] = useState("");
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [total, setTotal] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setQ(text.trim()), 350);
    return () => clearTimeout(t);
  }, [text]);

  const query = useMemo(() => {
    const p = new URLSearchParams(rangeQuery(range));
    if (refType) p.set("ref_type", refType);
    if (q) p.set("q", q);
    p.set("limit", String(PAGE));
    return p.toString();
  }, [range, refType, q]);

  const load = useCallback(async (offset: number) => {
    const r = await api<{ total: number; entries: Entry[] }>(`/api/ledger/journal?${query}&offset=${offset}`);
    setTotal(r.total);
    setEntries((prev) => (offset ? [...(prev ?? []), ...r.entries] : r.entries));
  }, [query]);

  useEffect(() => {
    setEntries(null);
    load(0).catch((e) => window.dispatchEvent(new CustomEvent("hesabdar:error", { detail: e.message })));
  }, [load]);

  const days = useMemo(() => {
    const out: { day: string; items: Entry[] }[] = [];
    for (const e of entries ?? []) {
      const day = e.at.slice(0, 10);
      if (out.length && out[out.length - 1].day === day) out[out.length - 1].items.push(e);
      else out.push({ day, items: [e] });
    }
    return out;
  }, [entries]);

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative min-w-[12rem] flex-1">
            <Search size={16} className="muted absolute top-1/2 right-3 -translate-y-1/2" />
            <input className="input pr-9" placeholder="جستجو در شرح اسناد (نام مشتری، شماره فاکتور، نوع هزینه…)" value={text} onChange={(e) => setText(e.target.value)} />
          </div>
          <select className="input w-auto" value={refType} onChange={(e) => setRefType(e.target.value)}>
            {REF_FILTERS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
          </select>
          <Tabs value={range} onChange={setRange} items={Object.entries(RANGES).map(([value, r]) => ({ value, label: r.label }))} />
          <HelpTip title="دفتر روزنامه چیست؟">
            <p>همه اسناد حسابداری به ترتیب تاریخ (جدیدترین بالا). هر عملیات (فاکتور، دریافت، هزینه…) یک سند است.</p>
            <p>در هر سند، سطرهای <b className="text-emerald-600">بدهکار</b> اول و سطرهای <b className="text-rose-500">بستانکار</b> با تورفتگی بعد از آن‌ها می‌آیند و جمع دو ستون همیشه برابر است.</p>
            <p>جمله زیر عنوان هر سند، همان سند را به زبان ساده توضیح می‌دهد. با کلیک روی نام هر حساب، گردش کامل آن حساب باز می‌شود.</p>
          </HelpTip>
        </div>
      </Card>
      {!entries ? <Loading /> : !entries.length ? <Card><Empty text="سندی با این شرایط پیدا نشد" /></Card> : (
        <>
          <div className="muted text-xs">{num(total)} سند · نمایش {num(entries.length)} مورد</div>
          {days.map((g) => {
            const dayTotal = g.items.reduce((s, e) => s + e.amount, 0);
            return (
              <div key={g.day} className="space-y-3">
                <div className="flex items-center justify-between px-1 text-sm">
                  <span className="font-bold">{jlong(new Date(g.day + "T12:00:00"))}</span>
                  <span className="muted num text-xs">{num(g.items.length)} سند · گردش {money(dayTotal)}</span>
                </div>
                {g.items.map((e) => <JournalEntryCard key={e.id} e={e} onOpenAccount={onOpenAccount} />)}
              </div>
            );
          })}
          {entries.length < total && (
            <div className="flex justify-center">
              <button className="btn" disabled={loadingMore} onClick={async () => { setLoadingMore(true); try { await load(entries.length); } finally { setLoadingMore(false); } }}>
                {loadingMore ? <Spinner /> : `نمایش اسناد بیشتر (${num(total - entries.length)} سند دیگر)`}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function JournalEntryCard({ e, onOpenAccount }: { e: Entry; onOpenAccount: (t: StatementTarget) => void }) {
  const ok = e.amount === e.credit_total;
  return (
    <div className="card fade-up overflow-hidden">
      <div className="flex flex-wrap items-start justify-between gap-2 border-b px-4 py-3" style={{ borderColor: "var(--border)" }}>
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="num rounded-lg bg-violet-500/10 px-2 py-0.5 text-xs font-bold text-violet-600 dark:text-violet-300">سند {num(e.id)}</span>
            <span className={`badge ${REF_STYLE[e.ref_type] ?? "bg-violet-500/10 text-violet-600"}`}>{e.ref_label}</span>
            <span className="font-bold">{e.description}</span>
          </div>
          <div className="muted text-xs leading-6">{explain(e)}</div>
          {(e.detail || e.customer) && <div className="muted text-xs">{[e.detail && `مرجع: ${e.detail}`, e.customer && `مشتری: ${e.customer}`].filter(Boolean).join(" · ")}</div>}
        </div>
        <div className="text-left">
          <div className="num text-base font-extrabold">{money(e.amount)}</div>
          <div className="muted num text-xs">{jdatetime(e.at)}</div>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="table">
          <thead><tr><th className="w-20">کد</th><th>حساب</th><th className="w-36">بدهکار</th><th className="w-36">بستانکار</th></tr></thead>
          <tbody>
            {e.lines.map((l, i) => (
              <tr key={i}>
                <td className="num muted text-xs">{l.code}</td>
                <td>
                  <button type="button" onClick={() => onOpenAccount({ id: l.account_id, title: l.name, subtitle: `کد ${l.code} · ${TYPES[l.type]}` })}
                    className={`text-start hover:text-violet-600 hover:underline ${l.credit ? "pr-8" : "font-semibold"}`}>
                    {l.credit ? <span className="muted ml-1">به</span> : null}{l.name}
                  </button>
                  <span className="muted mr-2 text-[11px]">{TYPES[l.type]}</span>
                </td>
                <td className="num font-semibold text-emerald-600 dark:text-emerald-400">{l.debit ? money(l.debit, false) : ""}</td>
                <td className="num font-semibold text-rose-500">{l.credit ? money(l.credit, false) : ""}</td>
              </tr>
            ))}
            <tr className="font-bold">
              <td />
              <td className="text-xs">
                <span className={`inline-flex items-center gap-1 ${ok ? "text-emerald-600 dark:text-emerald-400" : "text-rose-500"}`}><CheckCircle2 size={14} />{ok ? "جمع سند (متوازن)" : "سند نامتوازن!"}</span>
              </td>
              <td className="num">{money(e.amount, false)}</td>
              <td className="num">{money(e.credit_total, false)}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ trial balance
function TrialBalanceTab({ data, onOpen }: { data: any; onOpen: (t: StatementTarget) => void }) {
  if (!data) return <Loading />;
  if (!data.rows.length) return <Card><Empty text="هنوز سندی ثبت نشده است" /></Card>;
  return (
    <Card pad={false}>
      <div className="muted flex items-center gap-1.5 border-b px-4 py-3 text-xs" style={{ borderColor: "var(--border)" }}>
        روی هر حساب کلیک کنید تا گردش آن را ببینید.
        <HelpTip title="تراز آزمایشی">
          <p><b>گردش بدهکار/بستانکار:</b> جمع همه مبالغی که تا امروز در هر طرف حساب ثبت شده است.</p>
          <p><b>مانده:</b> تفاوت این دو. هر حساب فقط یا مانده بدهکار دارد یا مانده بستانکار.</p>
          <p>جمع ستون‌های گردش با هم، و جمع ستون‌های مانده با هم، همیشه باید برابر باشند.</p>
        </HelpTip>
      </div>
      <div className="overflow-x-auto">
        <table className="table">
          <thead>
            <tr><th>کد</th><th>حساب</th><th>نوع</th><th>گردش بدهکار</th><th>گردش بستانکار</th><th>مانده بدهکار</th><th>مانده بستانکار</th></tr>
          </thead>
          <tbody>
            {data.rows.map((r: any) => (
              <tr key={r.code} className="cursor-pointer" onClick={() => onOpen({ id: r.id, title: r.name, subtitle: `کد ${r.code} · ${TYPES[r.type]}` })}>
                <td className="num">{r.code}</td>
                <td className="font-semibold">{r.name}</td>
                <td className="muted">{TYPES[r.type]}</td>
                <td className="num">{money(r.debit, false)}</td>
                <td className="num">{money(r.credit, false)}</td>
                <td className="num font-bold text-emerald-600 dark:text-emerald-400">{r.debit_balance ? money(r.debit_balance, false) : "—"}</td>
                <td className="num font-bold text-rose-500">{r.credit_balance ? money(r.credit_balance, false) : "—"}</td>
              </tr>
            ))}
            <tr className="font-extrabold" style={{ background: "var(--surface)" }}>
              <td /><td>جمع کل</td><td />
              <td className="num">{money(data.turnover_debit, false)}</td>
              <td className="num">{money(data.turnover_credit, false)}</td>
              <td className="num">{money(data.balance_debit, false)}</td>
              <td className="num">{money(data.balance_credit, false)}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </Card>
  );
}
