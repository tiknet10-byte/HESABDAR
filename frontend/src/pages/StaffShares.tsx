import { FileText, HandCoins, Landmark, Pencil, Upload, UsersRound, Wallet } from "lucide-react";
import { useEffect, useState } from "react";
import MonthNav from "../components/MonthNav";
import { PayoutForm, ReceiptBatch, Statement } from "../components/StaffPay";
import { Card, Empty, Field, HelpTip, Loading, Modal, PageHeader, Stat, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { cmoney, daysAgo, isoDate, money, num } from "../lib/format";
import { can, useApi, useAuth, useToast } from "../lib/hooks";
import { faDigits, monthName, sameMonth, thisMonth, type YM } from "../lib/jalali";

const RANGES = [["month", "ماهانه"], ["1", "امروز"], ["7", "۷ روز"], ["30", "۳۰ روز"], ["90", "۳ ماه"], ["365", "یک سال"]] as const;
type Range = (typeof RANGES)[number][0];
/** hundredths of a percent -> «۴۲٫۵٪» */
const pct = (bp: number) => `${faDigits(String(+(bp / 100).toFixed(2))).replace(".", "٫")}٪`;
/** what the user typed («۴۲٫۵», "42.5", "42/5", «٤٢») -> hundredths of a percent, or null */
const parseBp = (s: string): number | null => {
  const t = s.replace(/[۰-۹]/g, (d) => String("۰۱۲۳۴۵۶۷۸۹".indexOf(d))).replace(/[٠-٩]/g, (d) => String("٠١٢٣٤٥٦٧٨٩".indexOf(d)))
    .replace(/[٫/,]/g, ".").replace(/[٪%\s]/g, "");
  if (!/^(\d{1,3}(\.\d{0,2})?|\.\d{1,2})$/.test(t)) return null;
  const bp = Math.round(parseFloat(t) * 100);
  return bp >= 0 && bp <= 10000 ? bp : null;
};

/** Set a line's treasury for a month: the preview shows each staff member's share before and after, then save. */
function RateEditor({ rate, month, onDone }: { rate: any; month: YM; onDone: () => void }) {
  const toast = useToast();
  const [text, setText] = useState(rate.bp ? String(rate.bp / 100) : "");
  const bp = parseBp(text);
  const [pv, setPv] = useState<any>(null);
  const [ok, setOk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0); // asked again after the server refused (paid meanwhile / changed meanwhile)
  useEffect(() => {
    setPv(null);
    setOk(false);
    if (bp === null) return;
    let alive = true; // an older answer arriving late never replaces the preview of what is typed now
    const t = setTimeout(() => {
      api("/api/treasury/preview", { body: { line_id: rate.line_id, jy: month[0], jm: month[1], rate_bp: bp } })
        .then((r) => { if (alive) setPv(r); }).catch((e: any) => { if (alive) toast(e.message, "error"); });
    }, 300);
    return () => { alive = false; clearTimeout(t); };
  }, [text, refresh]); // eslint-disable-line react-hooks/exhaustive-deps
  const touched = pv?.staff?.filter((s: any) => s.change || s.balance_after !== s.balance) ?? [];
  const changedMeanwhile = pv && pv.old_bp !== rate.bp; // someone else saved this month since the page was opened
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
            <div className="rounded-xl p-2" style={{ background: "var(--surface)" }}><div className="muted text-xs">درآمد فاکتورهای این ماه</div><div className="num font-bold">{money(pv.revenue)}</div>
              {pv.forfeits > 0 && <div className="muted text-[11px]">+ {money(pv.forfeits)} بیعانهٔ سوخت‌شده</div>}</div>
            <div className="rounded-xl p-2" style={{ background: "var(--surface)" }}><div className="muted text-xs">خزانه (الان ← بعد)</div><div className="num font-bold">{money(pv.treasury_before)} ← {money(pv.treasury_after)}</div></div>
            <div className="rounded-xl p-2" style={{ background: "var(--surface)" }}><div className="muted text-xs">فاکتورهایی که دوباره حساب می‌شوند</div><div className="num font-bold">{num(pv.invoices)}{pv.forfeits_changed > 0 && <span className="muted text-xs font-normal"> + {num(pv.forfeits_changed)} بیعانهٔ سوخت‌شده</span>}</div></div>
          </div>
          {pv.staff.length > 0 && (
            <table className="table text-xs">
              <thead><tr><th>پرسنل</th><th>سهم از کارکرد این ماه (الان)</th><th>بعد از تغییر</th><th>تفاوت</th><th>مانده بعد</th></tr></thead>
              <tbody>{pv.staff.map((s: any) => (
                <tr key={s.staff_id}><td className="font-semibold">{s.name}</td><td className="num">{money(s.before)}</td><td className="num">{money(s.after)}</td>
                  <td className={`num font-semibold ${s.change < 0 ? "text-rose-600" : s.change > 0 ? "text-emerald-600" : ""}`}>{s.change ? money(s.change) : "—"}</td>
                  <td className={`num ${s.balance_after < 0 ? "font-bold text-rose-600" : ""}`}>{money(s.balance_after)}</td></tr>
              ))}</tbody>
            </table>
          )}
          {pv.later_kept?.length > 0 && <p className="rounded-xl bg-sky-500/10 p-2 text-xs leading-6">ماه‌های بعدی که فاکتور دارند ({pv.later_kept.map((k: any) => monthName([k.jy, k.jm])).join("، ")}) با همان درصدی که با آن حساب شده‌اند ({pct(pv.old_bp)}) می‌مانند و فاکتورهای تازهٔ آن ماه‌ها هم با همین درصد حساب می‌شوند، نه با درصد تازه؛ درصد آن ماه‌ها را جدا تعیین کنید.</p>}
          {pv.returns_elsewhere?.length > 0 && <p className="rounded-xl bg-amber-500/10 p-2 text-xs leading-6">چند فاکتور این ماه در ماه‌های بعد برگشت خورده‌اند؛ برگشت آن‌ها هم با مبالغ تازه حساب می‌شود، پس ارقام آن ماه‌ها هم کمی عوض می‌شود ({pv.returns_elsewhere.map((r: any) => `${monthName([r.jy, r.jm])}: سهم پرسنل ${money(r.change)}، خزانه ${money(r.treasury_change)}`).join("؛ ")}).</p>}
          {changedMeanwhile && <p className="rounded-xl bg-rose-500/10 p-2 text-xs leading-6">درصد خزانهٔ این ماه همین الان (احتمالاً از کامپیوتر یا صفحهٔ دیگری) عوض شده و الان {pct(pv.old_bp)} است. این پنجره را ببندید تا صفحه تازه شود، بعد دوباره باز کنید.</p>}
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
      <button className="btn btn-primary w-full py-3" disabled={bp === null || !pv || pv.new_bp !== bp || changedMeanwhile || busy || (pv.needs_confirm && !ok)} onClick={async () => {
        setBusy(true);
        try {
          const r = await api("/api/treasury", { method: "PUT", body: { line_id: rate.line_id, jy: month[0], jm: month[1], rate_bp: bp, expected_bp: rate.bp, confirm: ok } });
          toast(`خزانهٔ ${rate.name} در ${monthName(month)}: ${pct(bp!)}${r.invoices ? ` - ${num(r.invoices)} فاکتور دوباره حساب شد` : ""}`);
          onDone();
        } catch (e: any) {
          toast(e.message, "error");
          if (e.status === 409) setRefresh((n) => n + 1); // show what changed (and the confirmation, if now needed)
        } finally { setBusy(false); }
      }}>ذخیره</button>
    </div>
  );
}

export default function StaffShares() {
  const { user } = useAuth();
  const allowed = can(user, "reports"); // money of the staff: owner, admin, accountant
  const [range, setRange] = useState<Range>("month");
  const [month, setMonth] = useState<YM>(thisMonth());
  const isMonth = range === "month";
  const start = range === "1" ? isoDate(new Date()) : isMonth ? "" : daysAgo(Number(range) - 1);
  const { data, reload } = useApi<any>(!allowed ? null : isMonth ? `/api/reports/staff-shares?jy=${month[0]}&jm=${month[1]}` : `/api/reports/staff-shares?start=${start}&end=${isoDate(new Date())}`, [range, month[0], month[1]]);
  const rates = useApi<any>(allowed && isMonth ? `/api/treasury?jy=${month[0]}&jm=${month[1]}` : null, [range, month[0], month[1]]);
  const [payout, setPayout] = useState<{ row: any; kind: string } | null>(null);
  const [statement, setStatement] = useState<any>(null);
  const [batch, setBatch] = useState(false);
  const [editing, setEditing] = useState<any>(null);
  const t = data?.totals;
  const cur = thisMonth();
  const atCurrent = sameMonth(month, cur);
  const money_ok = can(user, "finance");
  const rateOf = (lineId: number | null) => rates.data?.lines?.find((r: any) => r.line_id === lineId);
  const thisMonthRates = isMonth && atCurrent ? (rates.data?.lines ?? []) : [];
  const inherited = thisMonthRates.filter((r: any) => !r.own); // no rate of its own: the line's latest earlier one
  const kept = thisMonthRates.filter((r: any) => r.own && r.source === "auto"); // kept at what its invoices used
  const mayEdit = can(user, "settings");
  const reloadAll = () => { reload(); rates.reload(); };
  if (!allowed) return (
    <div className="space-y-5">
      <PageHeader title="پرسنل و سهم‌ها" icon={<UsersRound size={22} />} />
      <Empty text="این صفحه (درآمد و سهم پرسنل) فقط برای مدیر و حسابدار است." />
    </div>
  );
  return (
    <div className="space-y-5">
      <PageHeader title="پرسنل و سهم‌ها" subtitle="کارکرد هر پرسنل و هر لاین، خزانهٔ ماه، سهم پرسنل و سالن، برداشتی‌ها و تسویهٔ آخر ماه" icon={<UsersRound size={22} />}
        actions={<div className="flex flex-wrap items-center gap-2">
          {money_ok && <button className="btn btn-sm" onClick={() => setBatch(true)}><Upload size={14} />ثبت چند رسید پرداخت</button>}
          <Tabs value={range} onChange={setRange} items={RANGES.map(([value, label]) => ({ value, label }))} />
        </div>} />
      {isMonth && <MonthNav month={month} onChange={setMonth} />}
      {(inherited.length > 0 || kept.length > 0) && (
        <div className="space-y-1 rounded-2xl bg-amber-500/10 p-3 text-sm leading-7 text-amber-800 dark:text-amber-200">
          {inherited.length > 0 && <div>
            درصد خزانهٔ این ماه برای {inherited.map((r: any) => `«${r.name}»`).join("، ")} هنوز تعیین نشده؛ فعلاً این درصد به کار می‌رود:{" "}
            {inherited.map((r: any) => `${r.name} ${pct(r.bp)} (${r.from ? `از ${monthName(r.from)}` : "هیچ ماهی تعیین نشده"})`).join("، ")}.
          </div>}
          {kept.length > 0 && <div>
            درصد این ماه برای {kept.map((r: any) => `«${r.name}» (${pct(r.bp)})`).join("، ")} همان درصدی است که فاکتورهای این ماه قبلاً با آن حساب شده بودند، نه درصد تازهٔ ماه قبل؛
            فاکتورهای تازه هم با همین درصد حساب می‌شوند تا درصد این ماه را تعیین کنید.
          </div>}
          {mayEdit && <div>در جدول «خزانه و تقسیم سهم»، دکمهٔ مداد کنار هر لاین را بزنید.</div>}
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
              hint={t.forfeits ? `شامل سهم سالن از ${cmoney(t.forfeits)} بیعانهٔ سوخت‌شده` : undefined}
              help="درآمد منهای خزانه و منهای سهم پرسنل. خزانه + سهم پرسنل + سهم سالن = درآمد خدمات + بیعانه‌های سوخت‌شده." />
            <Stat label="مانده با پرسنل (تا امروز)" value={cmoney(t.balance)} hint={`پرداخت‌شده در دوره (برداشتی و تسویه): ${cmoney(t.paid)}`} tone="sky"
              help="جمع چیزی که سالن به پرسنل بدهکار است (سهم‌هایی که هنوز پرداخت نشده)، منهای پرسنلی که بیشتر از سهمشان برداشته‌اند." />
          </div>
          <Card title={isMonth ? `خزانه و تقسیم سهم - ${monthName(month)}` : "به تفکیک لاین"} pad={false}
            actions={<HelpTip title="خزانه چیست؟">برای هر لاین و هر ماه: اول «درصد خزانه» از درآمد خدمات برداشته می‌شود. باقیمانده با درصد هر پرسنل بین پرسنل و سالن تقسیم می‌شود. مثال: درآمد ۱۰۰ میلیون، خزانه ۴۲٪ = ۴۲ میلیون، باقیمانده ۵۸ میلیون؛ پرسنل ۳۰٪ = ۱۷٫۴ میلیون، سالن ۴۰٫۶ میلیون.</HelpTip>}>
            <div className="overflow-x-auto">
              <table className="table">
                <thead><tr><th>لاین</th><th>تعداد خدمت</th><th>درآمد خدمات</th><th>بیعانهٔ سوخت‌شده</th>{isMonth && <th>درصد خزانه</th>}<th>خزانه</th><th>باقیمانده</th><th>سهم پرسنل</th><th>سهم سالن</th></tr></thead>
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
                        <td className="num">{r.forfeits ? money(r.forfeits) : "—"}</td>
                        {isMonth && <td>{rate ? (
                          <span className="inline-flex items-center gap-1">
                            <span className={`badge ${rate.own && rate.source !== "auto" ? "bg-violet-500/15 text-violet-700 dark:text-violet-300" : "bg-amber-500/15 text-amber-700 dark:text-amber-300"}`}
                              title={rate.own ? (rate.source === "auto" ? "با همین درصد حساب شده بود و نگه داشته شد" : "برای همین ماه تعیین شده") : rate.from ? `از ${monthName(rate.from)}` : "تعیین نشده"}>
                              {pct(rate.bp)}{!rate.own && <span className="mr-1 text-[10px]">{rate.from ? "از ماه قبل" : "تعیین نشده"}</span>}
                            </span>
                            {mayEdit && <button className="btn btn-sm px-2 py-0.5" title="تعیین درصد خزانهٔ این ماه" onClick={() => setEditing(rate)}><Pencil size={13} /></button>}
                          </span>) : <span className="muted">—</span>}</td>}
                        <td className="num font-semibold text-violet-600">{money(r.treasury)}</td>
                        <td className="num">{money(r.revenue + r.forfeits - r.treasury)}</td>
                        <td className="num text-amber-600">{money(r.staff_share)}</td><td className="num font-semibold text-emerald-600">{money(r.salon_share)}</td>
                      </tr>
                    );
                  });
                })()}</tbody>
              </table>
            </div>
            <p className="muted px-5 py-3 text-xs leading-6">
              خزانه + سهم پرسنل + سهم سالن = درآمد خدمات + بیعانهٔ سوخت‌شده (در دفاتر تا آخرین ریال یکی است؛ اعداد جدول به تومان گرد شده‌اند و ممکن است ۱ تومان اختلاف دیده شود).
              بیعانهٔ مشتری‌ای که نیامد، مثل درآمد همان لاین است: خزانهٔ ماه از آن برداشته می‌شود و از باقیمانده، پرسنلِ آن بیعانه سهمش را می‌گیرد (اگر بیعانه پرسنل نداشته باشد، باقیمانده مال سالن است). بیعانه‌هایی که پیش از نسخهٔ ۱.۲۲ سوخت شده‌اند همان‌طور که بودند (همه‌اش سهم سالن) می‌مانند.
              {isMonth && mayEdit && " درصد خزانهٔ ماه‌های گذشته را هم می‌توانید عوض کنید؛ قبل از ذخیره نشان می‌دهد سهم هر پرسنل چقدر عوض می‌شود."}
            </p>
          </Card>
          <Card title="به تفکیک پرسنل" pad={false}>
            <div className="overflow-x-auto">
              {data.staff.length === 0 ? <Empty text="پرسنلی ثبت نشده؛ از تنظیمات ← پرسنل و کاربران اضافه کنید" /> : (
                <table className="table">
                  <thead><tr><th>پرسنل</th><th>لاین</th><th>درصد</th><th>تعداد خدمت</th><th>درآمد</th><th>بیعانهٔ سوخت‌شده</th><th>خزانه</th><th>سهم پرسنل</th><th>سهم سالن</th><th>پرداخت در دوره</th><th>مانده تا امروز</th><th></th></tr></thead>
                  <tbody>{data.staff.map((r: any) => (
                    <tr key={r.staff_id ?? 0} className={r.staff_id ? "" : "text-amber-700 dark:text-amber-300"}>
                      <td className="font-semibold">{r.staff_id && money_ok ? <button className="font-semibold text-violet-700 hover:underline dark:text-violet-300" onClick={() => setStatement(r)} title="صورت‌حساب ماه">{r.name}</button> : r.name}{r.active === false && <span className="badge mr-2 bg-slate-500/15 text-[10px] text-slate-500" title="این پرسنل غیرفعال شده ولی هنوز با او حساب داریم">غیرفعال</span>}</td><td className="muted">{r.line ?? "—"}</td>
                      <td className="num" title="درصد فعلی؛ از باقیماندهٔ بعد از خزانه">{faDigits(String(+Number(r.percent || 0).toFixed(2))).replace(".", "٫")}٪</td>
                      <td className="num">{num(r.services)}</td><td className="num">{money(r.revenue)}</td><td className="num">{r.forfeits ? money(r.forfeits) : "—"}</td><td className="num text-violet-600">{money(r.treasury)}</td>
                      <td className="num font-semibold text-amber-600">{money(r.staff_share)}</td><td className="num font-semibold text-emerald-600">{money(r.salon_share)}</td>
                      <td className="num">{money(r.paid)}</td>
                      <td className={`num font-bold ${r.balance < 0 ? "text-rose-600" : ""}`} title={r.balance < 0 ? "بیشتر از سهمش گرفته؛ از سهم ماه‌های بعد کم می‌شود" : undefined}>{money(r.balance)}{r.balance < 0 && <div className="text-[10px] font-normal">بدهکار</div>}</td>
                      <td>{r.staff_id && money_ok && (
                        <div className="flex flex-wrap gap-1">
                          <button className="btn btn-sm" onClick={() => setPayout({ row: r, kind: "advance" })} title="پولی که در طول ماه می‌گیرد"><Wallet size={14} />برداشتی</button>
                          {r.balance > 0 && <button className="btn btn-sm" onClick={() => setPayout({ row: r, kind: "settlement" })} title="باقیماندهٔ سهم"><HandCoins size={14} />تسویه</button>}
                          <button className="btn btn-sm px-2" onClick={() => setStatement(r)} title="صورت‌حساب ماه"><FileText size={14} /></button>
                        </div>)}</td>
                    </tr>
                  ))}</tbody>
                </table>
              )}
            </div>
            {data.staff.some((r: any) => !r.staff_id) && <p className="muted px-5 py-3 text-xs">ردیف «بدون پرسنل» خدماتی است که پرسنلشان مشخص نشده؛ با وصل‌کردن هر پرسنل به لاین خودش، از این به بعد خودکار ثبت می‌شود.</p>}
          </Card>
        </>
      )}
      <Modal open={!!payout} onClose={() => { setPayout(null); reload(); }} title={payout?.kind === "advance" ? `برداشتی - ${payout.row.name}` : `تسویه با ${payout?.row.name ?? ""}`}>
        {payout && <PayoutForm person={{ staff_id: payout.row.staff_id, name: payout.row.name, mobile: payout.row.mobile, balance: payout.row.balance }}
          kind={payout.kind} amount={payout.kind === "settlement" ? Math.max(0, payout.row.balance) : 0} month={isMonth ? month : undefined}
          onDone={() => { setPayout(null); reload(); }} />}
      </Modal>
      <Modal open={!!statement} onClose={() => { setStatement(null); reload(); }} title="صورت‌حساب ماه" wide>
        {statement && <Statement staffId={statement.staff_id} month={isMonth ? month : undefined} onChanged={reload} />}
      </Modal>
      <Modal open={batch} onClose={() => { setBatch(false); reload(); }} title="ثبت چند رسید پرداخت" wide>
        {batch && <ReceiptBatch onDone={() => { setBatch(false); reload(); }} />}
      </Modal>
      <Modal open={!!editing} onClose={() => { setEditing(null); rates.reload(); }} title="درصد خزانهٔ ماه" wide>
        {editing && <RateEditor rate={editing} month={month} onDone={() => { setEditing(null); reloadAll(); }} />}
      </Modal>
    </div>
  );
}
