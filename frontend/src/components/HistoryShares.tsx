import { CalendarRange, Check, History, Link2, RotateCcw, Undo2 } from "lucide-react";
import { Fragment, useEffect, useState } from "react";
import { api } from "../lib/api";
import { money, num } from "../lib/format";
import { can, useApi, useAuth, useToast } from "../lib/hooks";
import { faDigits, monthName, type YM } from "../lib/jalali";
import JalaliPicker from "./JalaliPicker";
import { Card, Empty, Loading, Modal } from "./ui";

const pctText = (p: number | null | undefined) => (p === null || p === undefined ? "—" : `${faDigits(String(+Number(p).toFixed(2))).replace(".", "٫")}٪`);

/** Month by month: revenue, treasury, staff share and salon share of every month; a month opens per staff member. */
export function MonthsTable() {
  const [all, setAll] = useState(false);
  const recent = useApi<any>("/api/reports/staff-shares/months", []).data; // the last 12 months (and the first month of all)
  const first = recent?.first as YM | null | undefined;
  const whole = useApi<any>(all && first ? `/api/reports/staff-shares/months?from_jy=${first[0]}&from_jm=${first[1]}` : null, [all, first?.[0], first?.[1]]).data;
  const data = all && first ? whole : recent;
  const [open, setOpen] = useState<string | null>(null);
  if (!data) return <Loading />;
  const rows = data.months as any[];
  return (
    <Card title={<span className="flex items-center gap-2"><CalendarRange size={16} />ماه به ماه</span>} pad={false}
      actions={<label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} />همهٔ ماه‌ها (از اولین کارکرد)</label>}>
      <div className="overflow-x-auto">
        <table className="table">
          <thead><tr><th>ماه</th><th>درآمد خدمات</th><th>بیعانهٔ سوخت‌شده</th><th>خزانه</th><th>سهم پرسنل</th><th>سهم سالن</th><th>پرداخت بابت ماه</th><th></th></tr></thead>
          <tbody>{rows.map((m) => {
            const key = `${m.jy}-${m.jm}`;
            const empty = !m.revenue && !m.forfeits;
            return (
              <Fragment key={key}>
                <tr className={empty ? "muted" : "cursor-pointer"} onClick={() => !empty && setOpen(open === key ? null : key)}>
                  <td className="font-semibold">{monthName([m.jy, m.jm])}
                    {m.history > 0 && <div className="text-[11px] font-normal text-sky-700 dark:text-sky-300">
                      {money(m.history)} از نرم‌افزار قبلی{m.history_unbooked > 0 ? " · هنوز ثبت نشده" : m.history_booked ? " · ثبت شده" : ""}</div>}</td>
                  <td className="num">{money(m.revenue)}</td><td className="num">{m.forfeits ? money(m.forfeits) : "—"}</td>
                  <td className="num text-violet-600">{money(m.treasury)}</td><td className="num font-semibold text-amber-600">{money(m.staff_share)}</td>
                  <td className="num text-emerald-600">{money(m.salon_share)}</td><td className="num">{m.paid_for_month ? money(m.paid_for_month) : "—"}</td>
                  <td className="muted text-xs">{empty ? "" : open === key ? "▲" : "▼"}</td>
                </tr>
                {open === key && (
                  <tr><td colSpan={8} className="p-0">
                    <div className="space-y-2 p-3" style={{ background: "var(--surface)" }}>
                      <table className="table text-xs">
                        <thead><tr><th>پرسنل</th><th>درآمد</th><th>خزانه</th><th>سهم پرسنل</th><th>سهم سالن</th><th>پرداخت بابت این ماه</th></tr></thead>
                        <tbody>{m.staff.map((s: any) => (
                          <tr key={s.staff_id ?? 0}><td className="font-semibold">{s.name}{s.history > 0 && <span className="muted"> (نرم‌افزار قبلی {money(s.history)})</span>}</td>
                            <td className="num">{money(s.revenue + s.forfeits)}</td><td className="num">{money(s.treasury)}</td>
                            <td className="num font-semibold">{money(s.staff_share)}</td><td className="num">{money(s.salon_share)}</td>
                            <td className="num">{s.paid_for_month ? money(s.paid_for_month) : "—"}</td></tr>
                        ))}</tbody>
                      </table>
                      <div className="flex flex-wrap gap-2 text-xs">{m.lines.map((l: any) => (
                        <span key={l.line_id ?? 0} className="badge">{l.name}: درآمد {money(l.revenue + l.forfeits)} · خزانه {money(l.treasury)}</span>
                      ))}</div>
                    </div>
                  </td></tr>
                )}
              </Fragment>
            );
          })}</tbody>
        </table>
      </div>
      <p className="muted px-5 py-3 text-xs leading-6">روی هر ماه بزنید تا سهم هر پرسنل در آن ماه را ببینید. «پرداخت بابت ماه» برداشتی‌ها و تسویه‌هایی است که برای همان ماه ثبت شده (حتی اگر ماه بعد پرداخت شده باشد).</p>
    </Card>
  );
}

/** Book one month: each staff member with the percent of that month (today's by default), what they earned. */
function BookMonth({ m, onDone }: { m: any; onDone: () => void }) {
  const toast = useToast();
  const [percents, setPercents] = useState<Record<string, string>>({});
  const body = Object.fromEntries(Object.entries(percents).filter(([, v]) => v.trim() !== "").map(([k, v]) => [k, Number(v.replace(/[٫/]/g, "."))]));
  const valid = Object.values(body).every((v) => Number.isFinite(v) && v >= 0 && v <= 100);
  const key = JSON.stringify(body);
  const [pv, setPv] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!valid) return;
    let alive = true; // an older answer arriving late never replaces the preview of what is typed now
    const t = setTimeout(() => {
      api("/api/history/month", { body: { jy: m.jy, jm: m.jm, percents: body } }).then((r) => { if (alive) setPv(r); })
        .catch((e: any) => { if (alive) toast(e.message, "error"); });
    }, 250);
    return () => { alive = false; clearTimeout(t); };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!pv) return <Loading />;
  return (
    <div className="space-y-3">
      <div className="rounded-2xl bg-sky-500/10 p-3 text-sm leading-7">
        کارکرد <b>{monthName([m.jy, m.jm])}</b> از نرم‌افزار قبلی: {num(pv.rows)} خدمت به مبلغ {money(pv.totals.revenue)}.
        خزانهٔ هر لاین با درصد خزانهٔ همان ماه برداشته می‌شود و از باقیمانده سهم هر پرسنل با درصدش حساب می‌شود.
        با «ثبت»، این سهم‌ها طلب پرسنل از آن ماه می‌شود (برداشتی‌ها و تسویه‌هایی که آن موقع داده‌اید را بعد ثبت کنید) - سود فعلی عوض نمی‌شود.
      </div>
      <table className="table text-xs">
        <thead><tr><th>پرسنل</th><th>خدمت</th><th>درآمد</th><th>خزانه</th><th>درصد آن ماه</th><th>سهم پرسنل</th></tr></thead>
        <tbody>{pv.staff.map((s: any) => (
          <tr key={s.staff_id ?? 0}><td className="font-semibold">{s.name}{s.employee && <span className="muted"> (کارمند: سهم ندارد)</span>}</td>
            <td className="num">{num(s.rows)}</td><td className="num">{money(s.revenue)}</td><td className="num">{money(s.treasury)}</td>
            <td>{s.staff_id && !s.employee ? (
              <input className="input num w-20 py-1 text-center text-xs" inputMode="decimal" placeholder={String(s.percent ?? s.current_percent ?? 0)}
                value={percents[s.staff_id] ?? ""} onChange={(e) => setPercents({ ...percents, [s.staff_id]: e.target.value })} />) : "—"}</td>
            <td className="num font-bold">{money(s.staff_share)}</td></tr>
        ))}</tbody>
      </table>
      <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
        {pv.lines.map((l: any) => <div key={l.line_id ?? 0} className="rounded-xl p-2" style={{ background: "var(--surface)" }}>
          <div className="font-semibold">{l.name}</div><div className="muted">خزانه {pctText(l.bp / 100)}: {money(l.treasury)}</div></div>)}
      </div>
      {!valid && <div className="text-xs text-rose-600">درصد باید عددی بین ۰ تا ۱۰۰ باشد</div>}
      {pv.rows_after_start > 0 && <div className="rounded-xl bg-amber-500/10 p-2 text-xs leading-6">{num(pv.rows_after_start)} خدمتِ این ماه بعد از روز شروع کار با برنامه است و حساب نمی‌شود (فاکتورهای این برنامه آن‌ها را دارند).</div>}
      <p className="muted text-xs leading-6">درصد خالی = درصد فعلی همان پرسنل. درصد خزانهٔ هر لاین را در تب «این ماه» (با رفتن به همان ماه و دکمهٔ مداد) تعیین کنید؛ اگر بعداً عوض شود، این ماه خودکار دوباره حساب می‌شود.</p>
      <button className="btn btn-primary w-full py-3" disabled={busy || !valid} onClick={async () => {
        setBusy(true);
        try {
          const r = await api("/api/history/book", { body: { months: [{ jy: m.jy, jm: m.jm, percents: body }] } });
          if (r.errors.length) toast(r.errors[0].error, "error"); else { toast(`${monthName([m.jy, m.jm])} ثبت شد`); onDone(); }
        } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
      }}><Check size={16} />ثبت سهم پرسنل {monthName([m.jy, m.jm])} ({money(pv.totals.staff_share)})</button>
    </div>
  );
}

/** The previous software's work: the day the program started, the months (live until booked), names to match. */
export function HistoryPanel({ onChanged }: { onChanged?: () => void }) {
  const toast = useToast();
  const { user } = useAuth();
  const mayEdit = can(user, "settings");
  const { data, reload } = useApi<any>("/api/history/months", []);
  const names = useApi<any>("/api/history/staff-names", []);
  const [booking, setBooking] = useState<any>(null);
  const [start, setStart] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const changed = () => { reload(); names.reload(); onChanged?.(); };
  if (!data) return <Loading />;
  const unbooked = data.months.filter((m: any) => !m.booked && m.rows > 0);
  async function bookAll() {
    if (!window.confirm(`سهم پرسنل ${faDigits(unbooked.length)} ماه (با درصد فعلی هر پرسنل) ثبت شود؟`)) return;
    setBusy(true);
    try {
      const r = await api("/api/history/book", { body: { months: unbooked.map((m: any) => ({ jy: m.jy, jm: m.jm })) } });
      toast(`${faDigits(r.done.length)} ماه ثبت شد${r.errors.length ? `؛ ${faDigits(r.errors.length)} ماه نه: ${r.errors[0].error}` : ""}`, r.errors.length ? "error" : "ok");
      changed();
    } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  }
  async function act(path: string, body: any, ok: string) {
    try { await api(path, { body }); toast(ok); changed(); } catch (e: any) { toast(e.message, "error"); }
  }
  return (
    <div className="space-y-5">
      <div className="rounded-2xl bg-sky-500/10 p-4 text-sm leading-7">
        <div className="mb-1 flex items-center gap-2 font-bold"><History size={18} />کارکرد پرسنل از نرم‌افزار قبلی (چهره)</div>
        خدماتی که از نرم‌افزار قبلی منتقل شده‌اند، ماه به ماه مثل فاکتورهای همین برنامه تقسیم می‌شوند: خزانهٔ لاین، سهم پرسنل و سهم سالن.
        تا وقتی ماهی «ثبت» نشده فقط نمایش است؛ با ثبت، سهم پرسنل طلب آن‌ها می‌شود و بعد می‌توانید برداشتی‌ها و تسویه‌های همان زمان را
        (در صورت‌حساب هر پرسنل، با تیک «پیش از شروع کار با این برنامه») وارد کنید تا ماندهٔ هر نفر درست شود. سود و موجودی حساب‌ها عوض نمی‌شود.
      </div>
      <Card title="روز شروع کار با این برنامه">
        <div className="flex flex-wrap items-end gap-3">
          <div className="text-sm leading-7">
            کارکردِ <b>قبل از</b> این روز از نرم‌افزار قبلی حساب می‌شود و از این روز به بعد فاکتورهای همین برنامه:
            <b className="num mr-2">{data.start ? new Intl.DateTimeFormat("fa-IR", { dateStyle: "long" }).format(new Date(data.start)) : "هنوز معلوم نیست"}</b>
            {!data.start_set && data.start && <span className="muted text-xs"> (خودکار: روز اولین فاکتور یا موجودی اول)</span>}
          </div>
          {mayEdit && (
            <div className="flex flex-wrap items-end gap-2">
              <div className="w-48"><JalaliPicker pastOnly withTime={false} value={(start ?? data.start) ? `${start ?? data.start}T12:00` : ""} onChange={(v) => setStart(v.slice(0, 10))} /></div>
              <button className="btn btn-sm" disabled={busy || !start || start === data.start} onClick={async () => {
                setBusy(true);
                try { const r = await api("/api/history/start", { method: "PUT", body: { day: start } }); toast(`ذخیره شد${r.rebooked ? ` - ${faDigits(r.rebooked)} ماه دوباره حساب شد` : ""}`); setStart(null); changed(); }
                catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
              }}>ذخیرهٔ روز شروع</button>
              {data.start_set && <button className="btn btn-sm" onClick={() => act("/api/history/start", { day: null }, "به حالت خودکار برگشت").then(() => setStart(null))}>خودکار</button>}
            </div>
          )}
        </div>
      </Card>
      {(names.data?.names?.length > 0 || names.data?.lines?.length > 0) && (
        <Card title={<span className="flex items-center gap-2"><Link2 size={16} />پرسنلی که هنوز معلوم نیست</span>}>
          <p className="muted mb-3 text-xs leading-6">این نام‌ها در فایل نرم‌افزار قبلی بودند ولی با هیچ پرسنلی جور نشدند (یا خدمتی پرسنل نداشت). هر کدام را به پرسنل درستش وصل کنید تا سهمش حساب شود.</p>
          <div className="space-y-2">
            {names.data.names.map((n: any) => <RelinkRow key={n.name} label={`«${n.name}»`} rows={n.rows} revenue={n.revenue} suggest={n.suggest}
              staff={names.data.staff} disabled={!mayEdit} onPick={(sid) => act("/api/history/relink", { name: n.name, staff_id: sid }, `${n.rows} خدمت وصل شد`)} />)}
            {names.data.lines.map((l: any) => <RelinkRow key={`l${l.line_id}`} label={`خدمات بدون پرسنل لاین «${l.line}»`} rows={l.rows} revenue={l.revenue}
              suggest={l.sole_staff ? { id: l.sole_staff.id, name: l.sole_staff.name, why: "تنها پرسنل این لاین" } : null}
              staff={names.data.staff} disabled={!mayEdit} onPick={(sid) => act("/api/history/relink", { line_id: l.line_id, staff_id: sid }, `${l.rows} خدمت وصل شد`)} />)}
          </div>
        </Card>
      )}
      <Card title="ماه‌های نرم‌افزار قبلی" pad={false}
        actions={mayEdit && unbooked.length > 1 ? <button className="btn btn-sm btn-primary" disabled={busy} onClick={bookAll}><Check size={14} />ثبت همهٔ ماه‌ها ({faDigits(unbooked.length)})</button> : undefined}>
        {data.months.length === 0 ? <Empty text="کارکردی از نرم‌افزار قبلی منتقل نشده (تنظیمات ← انتقال از نرم‌افزار قبلی ← فیش‌ها)" /> : (
          <div className="overflow-x-auto">
            <table className="table">
              <thead><tr><th>ماه</th><th>خدمت</th><th>درآمد</th><th>خزانه</th><th>سهم پرسنل</th><th>سهم سالن</th><th>وضعیت</th><th></th></tr></thead>
              <tbody>{[...data.months].reverse().map((m: any) => (
                <tr key={`${m.jy}-${m.jm}`}>
                  <td className="font-semibold">{monthName([m.jy, m.jm] as YM)}
                    {m.without_staff > 0 && <div className="text-[11px] font-normal text-amber-700 dark:text-amber-300">{num(m.without_staff)} خدمت بدون پرسنل</div>}
                    {m.rows_after_start > 0 && <div className="muted text-[11px] font-normal">{num(m.rows_after_start)} خدمت بعد از شروع (حساب نمی‌شود)</div>}</td>
                  <td className="num">{num(m.rows)}</td><td className="num">{money(m.revenue)}</td><td className="num text-violet-600">{money(m.treasury)}</td>
                  <td className="num font-semibold text-amber-600">{money(m.staff_share)}</td><td className="num text-emerald-600">{money(m.salon_share)}</td>
                  <td>{m.booked ? <span className={`badge ${m.stale ? "bg-amber-500/15 text-amber-700" : "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"}`}>{m.stale ? "تغییر کرده" : "ثبت شده"}</span>
                    : m.rows ? <span className="badge">فقط نمایش</span> : null}</td>
                  <td>{mayEdit && m.rows > 0 && (
                    <div className="flex gap-1">
                      <button className={`btn btn-sm ${m.booked && !m.stale ? "" : "btn-primary"}`} onClick={() => setBooking(m)}>
                        {m.booked ? <><RotateCcw size={13} />دوباره</> : <><Check size={13} />ثبت</>}</button>
                      {m.booked && <button className="btn btn-sm px-2" title="برداشتن ثبت (فقط نمایش)" onClick={() => {
                        if (window.confirm("ثبت این ماه برداشته شود؟ سهم پرسنل این ماه از طلبشان کم می‌شود.")) act("/api/history/unbook", { jy: m.jy, jm: m.jm }, "برداشته شد");
                      }}><Undo2 size={13} /></button>}
                    </div>)}</td>
                </tr>
              ))}</tbody>
              <tfoot><tr className="font-bold"><td>جمع</td><td className="num">{num(data.totals.rows)}</td><td className="num">{money(data.totals.revenue)}</td>
                <td className="num">{money(data.totals.treasury)}</td><td className="num">{money(data.totals.staff_share)}</td><td className="num">{money(data.totals.salon_share)}</td><td colSpan={2}></td></tr></tfoot>
            </table>
          </div>
        )}
      </Card>
      <Modal open={!!booking} onClose={() => setBooking(null)} title="ثبت سهم پرسنل ماه" wide>
        {booking && <BookMonth m={booking} onDone={() => { setBooking(null); changed(); }} />}
      </Modal>
    </div>
  );
}

function RelinkRow({ label, rows, revenue, suggest, staff, disabled, onPick }:
  { label: string; rows: number; revenue: number; suggest: any; staff: any[]; disabled: boolean; onPick: (sid: number) => void }) {
  const [sid, setSid] = useState<number>(suggest?.id ?? 0);
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl px-3 py-2 text-sm" style={{ background: "var(--surface)" }}>
      <span className="font-semibold">{label}</span>
      <span className="muted text-xs">{num(rows)} خدمت · {money(revenue)}</span>
      {suggest && <span className="badge bg-emerald-500/15 text-[10px] text-emerald-700 dark:text-emerald-300">پیشنهاد: {suggest.name} ({suggest.why})</span>}
      <span className="mr-auto flex items-center gap-2">
        <select className="input py-1 text-sm" value={sid} disabled={disabled} onChange={(e) => setSid(Number(e.target.value))}>
          <option value={0}>— کدام پرسنل؟ —</option>
          {staff.map((p) => <option key={p.id} value={p.id}>{p.name}{p.active ? "" : " (غیرفعال)"}</option>)}
        </select>
        <button className="btn btn-sm" disabled={disabled || !sid} onClick={() => onPick(sid)}>وصل کن</button>
      </span>
    </div>
  );
}

export function UnbookedNote({ amount, onOpen }: { amount: number; onOpen: () => void }) {
  if (!amount) return null;
  return (
    <div className="rounded-2xl bg-sky-500/10 p-3 text-sm leading-7">
      {money(amount)} از سهم پرسنل این دوره از کارکرد نرم‌افزار قبلی است و هنوز «ثبت» نشده (فقط نمایش؛ در ماندهٔ پرسنل نیست).
      <button className="btn btn-sm mr-2" onClick={onOpen}>رفتن به «نرم‌افزار قبلی»</button>
    </div>
  );
}

