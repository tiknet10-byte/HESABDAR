import { ChevronLeft, ChevronRight, HandCoins, Landmark, Pencil, UsersRound } from "lucide-react";
import { useEffect, useState } from "react";
import { Card, Empty, Field, HelpTip, Loading, Modal, MoneyInput, PageHeader, Stat, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { cmoney, daysAgo, isoDate, money, num } from "../lib/format";
import { can, useApi, useAuth, useToast } from "../lib/hooks";
import { J_MONTHS, faDigits, toJalali } from "../lib/jalali";

const RANGES = { month: "ماهانه", "1": "امروز", "7": "۷ روز", "30": "۳۰ روز", "90": "۳ ماه", "365": "یک سال" } as const;
type Range = keyof typeof RANGES;
type YM = [number, number];

const thisMonth = (): YM => {
  const d = new Date();
  const [jy, jm] = toJalali(d.getFullYear(), d.getMonth() + 1, d.getDate());
  return [jy, jm];
};
const step = ([jy, jm]: YM, by: number): YM => {
  const n = jy * 12 + (jm - 1) + by;
  return [Math.floor(n / 12), (n % 12) + 1];
};
const monthName = ([jy, jm]: YM) => `${J_MONTHS[jm - 1]} ${faDigits(jy)}`;
/** hundredths of a percent -> «۴۲٫۵٪» */
const pct = (bp: number) => `${faDigits(String(+(bp / 100).toFixed(2))).replace(".", "٫")}٪`;
/** what the user typed («۴۲٫۵», "42.5", "42/5") -> hundredths of a percent, or null */
const parseBp = (s: string): number | null => {
  const t = s.replace(/[۰-۹]/g, (d) => String("۰۱۲۳۴۵۶۷۸۹".indexOf(d))).replace(/[٫/,]/g, ".").replace(/[٪%\s]/g, "");
  if (!/^\d{1,3}(\.\d{0,2})?$/.test(t)) return null;
  const bp = Math.round(parseFloat(t) * 100);
  return bp >= 0 && bp <= 10000 ? bp : null;
};

function Payout({ row, month, onDone }: { row: any; month: YM | null; onDone: () => void }) {
  const toast = useToast();
  const accounts = useApi<any[]>("/api/accounts").data ?? [];
  const [amount, setAmount] = useState(Math.max(0, row.balance));
  const [acc, setAcc] = useState(0);
  const [desc, setDesc] = useState(month ? `تسویه ${monthName(month)}` : "");
  return (
    <div className="space-y-3">
      <div className="rounded-2xl bg-violet-500/10 p-3 text-sm">مانده قابل پرداخت به <b>{row.name}</b>: <b className="num">{money(row.balance)}</b></div>
      <Field label="مبلغ پرداخت"><MoneyInput value={amount} onChange={setAmount} /></Field>
      <Field label="پرداخت از">
        <select className="input" value={acc || accounts[0]?.id || 0} onChange={(e) => setAcc(Number(e.target.value))}>
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
      </Field>
      <Field label="توضیح"><input className="input" value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="مثلاً تسویه مهر ماه" /></Field>
      <button className="btn btn-primary w-full" disabled={!amount} onClick={async () => {
        try {
          await api(`/api/staff/${row.staff_id}/payout`, { body: { amount, payment_account_id: acc || accounts[0]?.id, description: desc } });
          toast("پرداخت ثبت شد");
          onDone();
        } catch (e: any) { toast(e.message, "error"); }
      }}>ثبت پرداخت</button>
    </div>
  );
}

/** Set a line's treasury for a month: the preview shows each staff member's share before and after, then save. */
function RateEditor({ rate, month, onDone }: { rate: any; month: YM; onDone: () => void }) {
  const toast = useToast();
  const [text, setText] = useState(rate.bp ? String(rate.bp / 100) : "");
  const bp = parseBp(text);
  const [pv, setPv] = useState<any>(null);
  const [ok, setOk] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setPv(null);
    setOk(false);
    if (bp === null) return;
    const t = setTimeout(() => {
      api("/api/treasury/preview", { body: { line_id: rate.line_id, jy: month[0], jm: month[1], rate_bp: bp } })
        .then(setPv).catch((e: any) => toast(e.message, "error"));
    }, 300);
    return () => clearTimeout(t);
  }, [text]); // eslint-disable-line react-hooks/exhaustive-deps
  const touched = pv?.staff?.filter((s: any) => s.change || s.balance_after !== s.balance) ?? [];
  return (
    <div className="space-y-4">
      <div className="rounded-2xl bg-violet-500/10 p-3 text-sm leading-7">
        خزانهٔ لاین <b>{rate.name}</b> در <b>{monthName(month)}</b>: این درصد اول از درآمد خدمات این لاین در این ماه برداشته می‌شود؛
        باقیمانده با درصد هر پرسنل بین پرسنل و سالن تقسیم می‌شود. معمولاً بین ۴۰ تا ۴۵ درصد است.
      </div>
      <Field label="درصد خزانه" hint="مثلاً ۴۲ یا ۴۲٫۵ (تا دو رقم اعشار)">
        <div className="flex items-center gap-2">
          <input className="input num w-32 text-center text-lg" inputMode="decimal" autoFocus value={text} onChange={(e) => setText(e.target.value)} placeholder="۴۲" />
          <span className="muted">٪</span>
          {text && bp === null && <span className="text-xs text-rose-600">یک عدد بین ۰ تا ۱۰۰ بنویسید</span>}
        </div>
      </Field>
      {pv && (
        <div className="space-y-3 text-sm">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            <div className="rounded-xl p-2" style={{ background: "var(--surface)" }}><div className="muted text-xs">درآمد فاکتورهای این ماه</div><div className="num font-bold">{money(pv.revenue)}</div></div>
            <div className="rounded-xl p-2" style={{ background: "var(--surface)" }}><div className="muted text-xs">خزانه (الان ← بعد)</div><div className="num font-bold">{money(pv.treasury_before)} ← {money(pv.treasury_after)}</div></div>
            <div className="rounded-xl p-2" style={{ background: "var(--surface)" }}><div className="muted text-xs">فاکتورهایی که دوباره حساب می‌شوند</div><div className="num font-bold">{num(pv.invoices)}</div></div>
          </div>
          {pv.staff.length > 0 && (
            <table className="table text-xs">
              <thead><tr><th>پرسنل</th><th>سهم این ماه (الان)</th><th>سهم بعد از تغییر</th><th>تفاوت</th><th>مانده بعد</th></tr></thead>
              <tbody>{pv.staff.map((s: any) => (
                <tr key={s.staff_id}><td className="font-semibold">{s.name}</td><td className="num">{money(s.before)}</td><td className="num">{money(s.after)}</td>
                  <td className={`num font-semibold ${s.change < 0 ? "text-rose-600" : s.change > 0 ? "text-emerald-600" : ""}`}>{s.change ? money(s.change) : "—"}</td>
                  <td className={`num ${s.balance_after < 0 ? "font-bold text-rose-600" : ""}`}>{money(s.balance_after)}</td></tr>
              ))}</tbody>
            </table>
          )}
          {pv.later_kept?.length > 0 && <p className="rounded-xl bg-sky-500/10 p-2 text-xs leading-6">ماه‌های بعدی که فاکتور دارند ({pv.later_kept.map((k: any) => monthName([k.jy, k.jm])).join("، ")}) با همان درصدی که با آن حساب شده‌اند ({pct(pv.old_bp)}) می‌مانند؛ اگر لازم است، درصد آن‌ها را جدا تعیین کنید.</p>}
          {pv.returns_elsewhere?.length > 0 && <p className="rounded-xl bg-amber-500/10 p-2 text-xs leading-6">چند فاکتور این ماه در ماه بعد برگشت خورده‌اند؛ برگشت آن‌ها هم با سهم تازه حساب می‌شود ({pv.returns_elsewhere.map((r: any) => `${monthName([r.jy, r.jm])}: ${money(r.change)}`).join("، ")}).</p>}
          {pv.needs_confirm && (
            <label className="flex items-start gap-2 rounded-xl bg-rose-500/10 p-3 text-xs leading-6">
              <input type="checkbox" className="mt-1" checked={ok} onChange={(e) => setOk(e.target.checked)} />
              <span>
                {touched.some((s: any) => s.paid_since > 0) && <>از اول این ماه به {touched.filter((s: any) => s.paid_since > 0).map((s: any) => s.name).join("، ")} پرداخت شده است. </>}
                {touched.some((s: any) => s.balance_after < 0) && <>مانده‌ی {touched.filter((s: any) => s.balance_after < 0).map((s: any) => s.name).join("، ")} منفی می‌شود (یعنی بیشتر از سهمش گرفته و از سهم ماه‌های بعد کم می‌شود). </>}
                می‌دانم و تغییر را تأیید می‌کنم.
              </span>
            </label>
          )}
        </div>
      )}
      <button className="btn btn-primary w-full py-3" disabled={bp === null || !pv || busy || (pv.needs_confirm && !ok)} onClick={async () => {
        setBusy(true);
        try {
          const r = await api("/api/treasury", { method: "PUT", body: { line_id: rate.line_id, jy: month[0], jm: month[1], rate_bp: bp, expected_bp: pv.old_bp, confirm: ok } });
          toast(`خزانهٔ ${rate.name} در ${monthName(month)}: ${pct(bp!)}${r.invoices ? ` - ${num(r.invoices)} فاکتور دوباره حساب شد` : ""}`);
          onDone();
        } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
      }}>ذخیره</button>
    </div>
  );
}

export default function StaffShares() {
  const { user } = useAuth();
  const [range, setRange] = useState<Range>("month");
  const [month, setMonth] = useState<YM>(thisMonth());
  const isMonth = range === "month";
  const start = range === "1" ? isoDate(new Date()) : isMonth ? "" : daysAgo(Number(range) - 1);
  const { data, reload } = useApi<any>(isMonth ? `/api/reports/staff-shares?jy=${month[0]}&jm=${month[1]}` : `/api/reports/staff-shares?start=${start}&end=${isoDate(new Date())}`, [range, month[0], month[1]]);
  const rates = useApi<any>(isMonth ? `/api/treasury?jy=${month[0]}&jm=${month[1]}` : null, [range, month[0], month[1]]);
  const [payout, setPayout] = useState<any>(null);
  const [editing, setEditing] = useState<any>(null);
  const t = data?.totals;
  const cur = thisMonth();
  const atCurrent = month[0] === cur[0] && month[1] === cur[1];
  const rateOf = (lineId: number | null) => rates.data?.lines?.find((r: any) => r.line_id === lineId);
  const unset = isMonth && atCurrent ? (rates.data?.lines ?? []).filter((r: any) => !r.own || r.source === "auto") : [];
  const mayEdit = can(user, "settings");
  const reloadAll = () => { reload(); rates.reload(); };
  return (
    <div className="space-y-5">
      <PageHeader title="پرسنل و سهم‌ها" subtitle="درآمد هر پرسنل و هر لاین، خزانهٔ ماه، سهم پرسنل، سهم سالن و تسویه با پرسنل" icon={<UsersRound size={22} />}
        actions={<Tabs value={range} onChange={setRange} items={Object.entries(RANGES).map(([value, label]) => ({ value: value as Range, label }))} />} />
      {isMonth && (
        <div className="flex items-center justify-center gap-2">
          <button className="btn btn-sm" onClick={() => setMonth(step(month, -1))} title="ماه قبل"><ChevronRight size={16} />ماه قبل</button>
          <div className="min-w-40 rounded-2xl px-4 py-2 text-center text-lg font-bold" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>{monthName(month)}</div>
          <button className="btn btn-sm" disabled={atCurrent} onClick={() => setMonth(step(month, 1))} title="ماه بعد">ماه بعد<ChevronLeft size={16} /></button>
          {!atCurrent && <button className="btn btn-sm" onClick={() => setMonth(cur)}>این ماه</button>}
        </div>
      )}
      {unset.length > 0 && (
        <div className="rounded-2xl bg-amber-500/10 p-3 text-sm leading-7 text-amber-800 dark:text-amber-200">
          درصد خزانهٔ این ماه برای {unset.map((r: any) => `«${r.name}»`).join("، ")} هنوز تعیین نشده؛ فعلاً {unset.map((r: any) => `${r.name}: ${pct(r.bp)}${r.from ? ` (از ${monthName(r.from)})` : ""}`).join("، ")} به کار می‌رود.
          {mayEdit && " از جدول «خزانه و تقسیم سهم» پایین همین صفحه، دکمهٔ مداد را بزنید."}
        </div>
      )}
      {!data ? <Loading /> : (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
            <Stat label="درآمد خدمات" value={cmoney(t.revenue)} tone="pink" hint="فقط خدمات؛ فروش محصولات جداست"
              help="جمع خدمات انجام‌شده در این بازه (بعد از تخفیف). فروش محصولات پرسنل و سهم ندارد و در «گزارش‌ها ← فروش محصولات» است. خدمتی که بعداً برگشت خورده، در روز برگشت کم می‌شود." />
            <Stat label="خزانه" value={cmoney(t.treasury)} tone="violet" icon={<Landmark size={18} />}
              help="درصدی از درآمد هر لاین که اول برداشته می‌شود (مال سالن است)؛ سهم پرسنل از باقیماندهٔ آن حساب می‌شود. درصد هر لاین برای هر ماه جدا تعیین می‌شود." />
            <Stat label="سهم پرسنل" value={cmoney(t.staff_share)} tone="amber" />
            <Stat label="سهم سالن (مدیریت)" value={cmoney(t.salon_share)} tone="emerald"
              hint={t.forfeits ? `به‌علاوهٔ ${cmoney(t.forfeits)} بیعانهٔ سوخت‌شده` : undefined}
              help="درآمد منهای خزانه و منهای سهم پرسنل. خزانه + سهم پرسنل + سهم سالن = درآمد خدمات." />
            <Stat label="مانده بدهی به پرسنل" value={cmoney(t.balance)} hint={`پرداخت‌شده در دوره: ${cmoney(t.paid)}`} tone="sky" />
          </div>
          <Card title={isMonth ? `خزانه و تقسیم سهم - ${monthName(month)}` : "به تفکیک لاین"} pad={false}
            actions={<HelpTip title="خزانه چیست؟">برای هر لاین و هر ماه: اول «درصد خزانه» از درآمد خدمات برداشته می‌شود. باقیمانده با درصد هر پرسنل بین پرسنل و سالن تقسیم می‌شود. مثال: درآمد ۱۰۰ میلیون، خزانه ۴۲٪ = ۴۲ میلیون، باقیمانده ۵۸ میلیون؛ پرسنل ۳۰٪ = ۱۷٫۴ میلیون، سالن ۴۰٫۶ میلیون.</HelpTip>}>
            <div className="overflow-x-auto">
              <table className="table">
                <thead><tr><th>لاین</th><th>تعداد خدمت</th><th>درآمد</th>{isMonth && <th>درصد خزانه</th>}<th>خزانه</th><th>باقیمانده</th><th>سهم پرسنل</th><th>سهم سالن</th><th>بیعانهٔ سوخت‌شده</th></tr></thead>
                <tbody>{(() => {
                  const rows = [...data.lines];
                  if (isMonth) for (const r of rates.data?.lines ?? []) if (!rows.some((x: any) => x.line_id === r.line_id))
                    rows.push({ line_id: r.line_id, name: r.name, services: 0, revenue: 0, treasury: 0, staff_share: 0, salon_share: 0, forfeits: 0, returned: 0 });
                  return rows.map((r: any) => {
                    const rate = rateOf(r.line_id);
                    return (
                      <tr key={r.line_id ?? 0}>
                        <td className="font-semibold">{r.name}{r.returned > 0 && <div className="muted text-[11px] font-normal">شامل {money(r.returned)} برگشتی از فروش‌های قبل</div>}</td>
                        <td className="num">{num(r.services)}</td><td className="num">{money(r.revenue)}</td>
                        {isMonth && <td>{rate ? (
                          <span className="inline-flex items-center gap-1">
                            <span className={`badge ${rate.own && rate.source !== "auto" ? "bg-violet-500/15 text-violet-700 dark:text-violet-300" : "bg-amber-500/15 text-amber-700 dark:text-amber-300"}`}
                              title={rate.own ? (rate.source === "auto" ? "با همین درصد حساب شده بود و نگه داشته شد" : "برای همین ماه تعیین شده") : rate.from ? `از ${monthName(rate.from)}` : "تعیین نشده"}>
                              {pct(rate.bp)}{!rate.own && <span className="mr-1 text-[10px]">{rate.from ? "از ماه قبل" : "تعیین نشده"}</span>}
                            </span>
                            {mayEdit && <button className="btn btn-sm px-2 py-0.5" title="تعیین درصد خزانهٔ این ماه" onClick={() => setEditing(rate)}><Pencil size={13} /></button>}
                          </span>) : <span className="muted">—</span>}</td>}
                        <td className="num font-semibold text-violet-600">{money(r.treasury)}</td>
                        <td className="num">{money(r.revenue - r.treasury)}</td>
                        <td className="num text-amber-600">{money(r.staff_share)}</td><td className="num font-semibold text-emerald-600">{money(r.salon_share)}</td>
                        <td className="num text-emerald-600">{r.forfeits ? money(r.forfeits) : "—"}</td>
                      </tr>
                    );
                  });
                })()}</tbody>
              </table>
            </div>
            <p className="muted px-5 py-3 text-xs leading-6">
              خزانه + سهم پرسنل + سهم سالن = درآمد خدمات (تا آخرین ریال). بیعانهٔ مشتری‌ای که نیامد، درآمد همان لاین است و همه‌اش سهم سالن است (پرسنل از آن سهمی ندارد و خزانه هم از آن برداشته نمی‌شود).
              {isMonth && mayEdit && " درصد خزانهٔ ماه‌های گذشته را هم می‌توانید عوض کنید؛ قبل از ذخیره نشان می‌دهد سهم هر پرسنل چقدر عوض می‌شود."}
            </p>
          </Card>
          <Card title="به تفکیک پرسنل" pad={false}>
            <div className="overflow-x-auto">
              {data.staff.length === 0 ? <Empty text="پرسنلی ثبت نشده؛ از تنظیمات ← پرسنل و کاربران اضافه کنید" /> : (
                <table className="table">
                  <thead><tr><th>پرسنل</th><th>لاین</th><th>درصد</th><th>تعداد خدمت</th><th>درآمد</th><th>خزانه</th><th>سهم پرسنل</th><th>سهم سالن</th><th>پرداخت در دوره</th><th>مانده</th><th></th></tr></thead>
                  <tbody>{data.staff.map((r: any) => (
                    <tr key={r.staff_id ?? 0} className={r.staff_id ? "" : "text-amber-700 dark:text-amber-300"}>
                      <td className="font-semibold">{r.name}{r.active === false && <span className="badge mr-2 bg-slate-500/15 text-[10px] text-slate-500" title="این پرسنل غیرفعال شده ولی هنوز با او حساب داریم">غیرفعال</span>}</td><td className="muted">{r.line ?? "—"}</td>
                      <td className="num" title="درصد فعلی؛ از باقیماندهٔ بعد از خزانه">{faDigits(String(+Number(r.percent || 0).toFixed(2))).replace(".", "٫")}٪</td>
                      <td className="num">{num(r.services)}</td><td className="num">{money(r.revenue)}</td><td className="num text-violet-600">{money(r.treasury)}</td>
                      <td className="num font-semibold text-amber-600">{money(r.staff_share)}</td><td className="num font-semibold text-emerald-600">{money(r.salon_share)}</td>
                      <td className="num">{money(r.paid)}</td>
                      <td className={`num font-bold ${r.balance < 0 ? "text-rose-600" : ""}`} title={r.balance < 0 ? "بیشتر از سهمش گرفته (پیش‌پرداخت)؛ از سهم ماه‌های بعد کم می‌شود" : undefined}>{money(r.balance)}{r.balance < 0 && <div className="text-[10px] font-normal">پیش‌پرداخت</div>}</td>
                      <td>{r.staff_id && r.balance > 0 && can(user, "finance") && <button className="btn btn-sm" onClick={() => setPayout(r)}><HandCoins size={14} />تسویه</button>}</td>
                    </tr>
                  ))}</tbody>
                </table>
              )}
            </div>
            {data.staff.some((r: any) => !r.staff_id) && <p className="muted px-5 py-3 text-xs">ردیف «بدون پرسنل» خدماتی است که پرسنلشان مشخص نشده؛ با وصل‌کردن هر پرسنل به لاین خودش، از این به بعد خودکار ثبت می‌شود.</p>}
          </Card>
        </>
      )}
      <Modal open={!!payout} onClose={() => setPayout(null)} title="تسویه با پرسنل">
        {payout && <Payout row={payout} month={isMonth ? month : null} onDone={() => { setPayout(null); reload(); }} />}
      </Modal>
      <Modal open={!!editing} onClose={() => setEditing(null)} title="درصد خزانهٔ ماه" wide>
        {editing && <RateEditor rate={editing} month={month} onDone={() => { setEditing(null); reloadAll(); }} />}
      </Modal>
    </div>
  );
}
