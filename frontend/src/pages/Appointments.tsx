import { AlertTriangle, Ban, CalendarClock, Pencil, Plus, Receipt, Search, Trash2, XCircle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import AppointmentForm from "../components/AppointmentForm";
import AppointmentView from "../components/AppointmentView";
import BookingCalendar from "../components/BookingCalendar";
import { WAITLIST_CHANGED, WaitlistPanel } from "../components/Waitlist";
import { Badge, Card, Empty, Loading, Modal, PageHeader, Tabs } from "../components/ui";
import { isoDate, money } from "../lib/format";
import { useApi } from "../lib/hooks";
import { faDigits, formatJ, J_WEEKDAYS, jWeekday } from "../lib/jalali";

const endTime = (start: string, minutes: number) => {
  const d = new Date(new Date(start.length <= 16 ? start + ":00" : start).getTime() + minutes * 60000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};
const day = (offset: number) => isoDate(new Date(Date.now() + offset * 86400000));

const PERIODS = {
  today: { label: "امروز", range: () => [day(0), day(0)] },
  tomorrow: { label: "فردا", range: () => [day(1), day(1)] },
  week: { label: "۷ روز آینده", range: () => [day(0), day(6)] },
  upcoming: { label: "همهٔ نوبت‌های آینده", range: () => [day(0), day(365)] },
  past: { label: "گذشته (۶۰ روز)", range: () => [day(-60), day(-1)] },
} as const;
type Period = keyof typeof PERIODS;
const STATUSES = {
  active: { label: "فعال", value: "booked" },
  closed: { label: "لغو / نیامده", value: "cancelled,no_show" },
  done: { label: "انجام‌شده", value: "done" },
  all: { label: "همه", value: "" },
} as const;
type StatusKey = keyof typeof STATUSES;

export default function Appointments() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as "calendar" | "list" | "waitlist") || "calendar";
  const period = (params.get("period") as Period) in PERIODS ? params.get("period") as Period : "upcoming";
  const statusKey = (params.get("status") as StatusKey) in STATUSES ? params.get("status") as StatusKey : "active";
  const filter = params.get("filter") ?? ""; // deposit_gone | unknown_time (from the dashboard)
  const setParam = (k: string, v: string) => { const p = new URLSearchParams(params); if (v) p.set(k, v); else p.delete(k); setParams(p, { replace: true }); };

  const [q, setQ] = useState("");
  const [dq, setDq] = useState("");
  useEffect(() => { const t = setTimeout(() => setDq(q.trim()), 300); return () => clearTimeout(t); }, [q]);
  const [start, end] = PERIODS[period].range();
  const qs = new URLSearchParams(dq ? { q: dq, status: STATUSES[statusKey].value } : { start, end, status: STATUSES[statusKey].value });
  if (filter) { qs.set("status", "booked"); if (!dq) { qs.set("start", day(-60)); qs.set("end", day(365)); } }
  const [refresh, setRefresh] = useState(0);
  const { data, reload: reloadList } = useApi<any[]>(tab === "list" ? `/api/appointments?${qs}` : null, [qs.toString(), tab, refresh]);
  const reload = useCallback(() => { reloadList(); setRefresh((n) => n + 1); }, [reloadList]);
  const waiting = useApi<any[]>("/api/waitlist", [refresh]).data?.length ?? 0;
  const [open, setOpen] = useState<false | { service_id?: number; start_at?: string }>(false);
  const [view, setView] = useState<{ id: number; step?: string } | null>(null);

  useEffect(() => {
    window.addEventListener(WAITLIST_CHANGED, reload); // a freed time was given to a waiting customer
    return () => window.removeEventListener(WAITLIST_CHANGED, reload);
  }, [reload]);

  const list = (data ?? []).filter((a) => filter === "deposit_gone" ? a.deposit_gone : filter === "unknown_time" ? a.time_unknown : true);
  const groups = list.reduce((g: Record<string, any[]>, a) => ((g[a.start_at.slice(0, 10)] ||= []).push(a), g), {});
  const days = Object.keys(groups).sort(period === "past" && !dq ? (x, y) => y.localeCompare(x) : undefined);
  const now = Date.now();

  return (
    <div className="space-y-5">
      <PageHeader title="نوبت‌ها" subtitle="ثبت، تغییر، لغو و حذف نوبت‌ها؛ بعد از انجام خدمت از همین‌جا فاکتور صادر کنید" icon={<CalendarClock size={22} />}
        actions={<button className="btn btn-primary" onClick={() => setOpen({})}><Plus size={16} />نوبت جدید</button>} />
      <Tabs value={tab} onChange={(t) => setParams({ tab: t }, { replace: true })} items={[
        { value: "calendar", label: "تقویم نوبت‌دهی" },
        { value: "list", label: "فهرست نوبت‌ها" },
        { value: "waitlist", label: `لیست انتظار / VIP${waiting ? ` (${faDigits(waiting)})` : ""}` },
      ]} />
      {tab === "calendar" && <BookingCalendar refresh={refresh} onBook={(p) => setOpen(p)} onOpen={(a) => setView({ id: a.id })} />}
      {tab === "waitlist" && <WaitlistPanel onBooked={reload} />}
      {tab === "list" && (
        <>
          <Card>
            <div className="space-y-3">
              <div className="relative">
                <Search size={16} className="muted absolute right-3 top-1/2 -translate-y-1/2" />
                <input className="input pr-9" placeholder="جستجوی نوبت با نام، موبایل یا کد مشتری…" value={q} onChange={(e) => setQ(e.target.value)} />
              </div>
              {!dq && !filter && (
                <div className="flex flex-wrap gap-1.5">
                  {(Object.keys(PERIODS) as Period[]).map((k) => (
                    <button key={k} onClick={() => setParam("period", k === "upcoming" ? "" : k)}
                      className={`rounded-xl px-3 py-1.5 text-sm font-semibold ${period === k ? "bg-violet-600 text-white" : "border hover:bg-violet-500/10"}`}
                      style={period === k ? {} : { borderColor: "var(--border)" }}>{PERIODS[k].label}</button>
                  ))}
                </div>
              )}
              {!filter && <Tabs value={statusKey} onChange={(v) => setParam("status", v === "active" ? "" : v)}
                items={(Object.keys(STATUSES) as StatusKey[]).map((k) => ({ value: k, label: STATUSES[k].label }))} />}
              {dq && <div className="muted text-xs">نتیجهٔ جستجو در نوبت‌های یک سال اخیر و آینده</div>}
              {filter && (
                <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-amber-500/10 px-3 py-2 text-sm text-amber-800 dark:text-amber-200">
                  <span className="flex items-center gap-1.5"><AlertTriangle size={15} />
                    {filter === "deposit_gone" ? "نوبت‌های فعالی که بیعانه‌شان پس داده شده یا سوخت شده؛ اگر مشتری نمی‌آید لغوشان کنید" : "نوبت‌هایی که ساعتشان مشخص نیست؛ با «تغییر» ساعت را ثبت کنید"}
                  </span>
                  <button className="btn btn-sm" onClick={() => setParam("filter", "")}>نمایش همه</button>
                </div>
              )}
              <p className="muted text-xs">برای دیدن جزئیات، تغییر، لغو یا حذف، روی هر نوبت بزنید.</p>
            </div>
          </Card>
          {!data ? <Loading /> : days.length === 0 ? (
            <Card><Empty text={dq ? "نوبتی با این جستجو پیدا نشد" : statusKey === "active" ? "در این بازه نوبت فعالی نیست" : "نوبتی پیدا نشد"} /></Card>
          ) : days.map((d) => {
            const items = groups[d];
            const date = new Date(d + "T12:00");
            return (
              <Card key={d} title={<span>{J_WEEKDAYS[jWeekday(date)]} {formatJ(d + "T12:00", false)} <span className="muted text-sm font-normal">· {faDigits(items.length)} نوبت</span></span>}>
                <div className="space-y-2">
                  {items.map((a) => {
                    const past = new Date(a.start_at + ":00").getTime() < now;
                    return (
                      <div key={a.id} onClick={() => setView({ id: a.id })}
                        className={`flex cursor-pointer flex-wrap items-center justify-between gap-2 rounded-2xl px-4 py-3 transition hover:shadow-md ${a.status === "cancelled" ? "opacity-60" : ""}`}
                        style={{ background: "var(--surface)", border: `1px solid ${a.deposit_gone || a.conflict ? "rgb(244 63 94 / .5)" : "var(--border)"}` }}>
                        <div className="flex items-center gap-3">
                          {a.time_unknown ? (
                            <span className="rounded-xl bg-amber-500/15 px-2 py-1 text-center text-[11px] font-bold leading-tight text-amber-700 dark:text-amber-300">ساعت<span className="block">نامشخص</span></span>
                          ) : (
                            <span className="num rounded-xl bg-violet-500/10 px-2 py-1 text-center font-bold leading-tight text-violet-700 dark:text-violet-300">{faDigits(a.start_at.slice(11, 16))}<span className="block text-[10px] font-semibold opacity-70">تا {faDigits(endTime(a.start_at, a.duration_minutes ?? 60))}</span></span>
                          )}
                          <div>
                            <div className="font-semibold">{a.customer} {a.customer_mobile && <span className="num muted text-xs font-normal" dir="ltr">{a.customer_mobile}</span>}</div>
                            <div className="muted text-xs">{a.line ? `${a.line} / ` : ""}{a.service ?? "—"} · {faDigits(a.duration_minutes ?? 60)} دقیقه · {money(a.quoted_price)}</div>
                            {a.staff && <div className="text-xs font-semibold text-violet-600 dark:text-violet-300">پرسنل: {a.staff}</div>}
                            {a.original_start_at && <div className="text-xs text-amber-600">زودتر انجام شد؛ نوبت اصلی {formatJ(a.original_start_at)} آزاد شد</div>}
                            {(a.deposits ?? []).length > 0 && (
                              <div className="mt-0.5 text-xs text-emerald-600">بیعانه: {a.deposits.map((x: any) => `${money(x.amount)}${x.status === "held" ? "" : ` (${x.status === "refunded" ? "مسترد" : x.status === "forfeited" ? "سوخت" : "کسر شده"})`}`).join(" + ")}</div>
                            )}
                            {a.deposit_gone && <div className="text-xs font-semibold text-rose-600">⚠ بیعانه پس داده شده ولی نوبت هنوز فعال است</div>}
                          </div>
                        </div>
                        <div className="flex flex-wrap items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
                          {a.conflict && <span className="badge bg-rose-500/15 text-rose-600" title="این نوبت با نوبت دیگری از همین پرسنل یا مشتری هم‌زمان است؛ یکی را جابه‌جا یا لغو کنید">⚠ تداخل</span>}
                          <Badge status={a.status} />
                          {a.status === "booked" && (
                            <>
                              <Link className="btn btn-sm btn-primary" to={`/invoices?new=1&appointment=${a.id}`} title="خدمت انجام شد: فاکتور صادر شود"><Receipt size={14} />فاکتور</Link>
                              <button className={`btn btn-sm ${a.time_unknown ? "ring-2 ring-amber-400" : ""}`} onClick={() => setView({ id: a.id, step: "edit" })}><Pencil size={14} />{a.time_unknown ? "تعیین ساعت" : "تغییر"}</button>
                              {past && <button className="btn btn-sm" onClick={() => setView({ id: a.id, step: "no_show" })}><Ban size={14} />نیامد</button>}
                              <button className="btn btn-sm" onClick={() => setView({ id: a.id, step: "cancelled" })}><XCircle size={14} />لغو</button>
                            </>
                          )}
                          {a.status !== "done" && !a.invoice_id && (
                            <button className="btn btn-sm text-rose-600" onClick={() => setView({ id: a.id, step: "delete" })} title="نوبت اشتباهی ثبت شده: حذف کامل"><Trash2 size={14} />حذف</button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </Card>
            );
          })}
        </>
      )}
      <Modal open={!!open} onClose={() => setOpen(false)} title="نوبت جدید">
        {open && <AppointmentForm preset={open} onDone={() => { setOpen(false); reload(); }} />}
      </Modal>
      <Modal open={!!view} onClose={() => setView(null)} title="نوبت">
        {view && <AppointmentView key={`${view.id}-${view.step ?? ""}`} id={view.id} initialStep={view.step as any} onChanged={reload} onClose={() => setView(null)} />}
      </Modal>
    </div>
  );
}
