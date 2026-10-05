import { CalendarDays, CalendarPlus, Printer } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import JalaliPicker from "./JalaliPicker";
import { Badge, Card, Empty, Field, Modal } from "./ui";
import { api } from "../lib/api";
import { money } from "../lib/format";
import { useApi } from "../lib/hooks";
import { faDigits, formatJ, J_MONTHS, J_WEEKDAYS, J_WEEKDAYS_SHORT, jWeekday, parseLocal, toJalali, toLocalIso } from "../lib/jalali";

type Day = { date: string; status: "past" | "closed" | "empty" | "partial" | "full"; count: number; fill: number; first_free: string | null; booked_minutes: number };
type Cal = { line: string; capacity: number; min_duration: number; open: string; close: string; days: Day[] };

export const DAY_STYLE: Record<Day["status"], { label: string; cell: string; dot: string }> = {
  empty: { label: "کاملاً خالی", cell: "bg-emerald-500/15 text-emerald-800 dark:text-emerald-200 hover:bg-emerald-500/25", dot: "bg-emerald-500" },
  partial: { label: "نوبت دارد، جای خالی هم دارد", cell: "bg-amber-400/25 text-amber-900 dark:text-amber-100 hover:bg-amber-400/40", dot: "bg-amber-400" },
  full: { label: "پر - جای خالی ندارد", cell: "bg-rose-500/80 text-white hover:bg-rose-500", dot: "bg-rose-500" },
  closed: { label: "تعطیل", cell: "bg-zinc-500/10 text-zinc-400 line-through", dot: "bg-zinc-400" },
  past: { label: "گذشته", cell: "text-zinc-400 opacity-50", dot: "bg-zinc-300" },
};

const dateOf = (iso: string) => parseLocal(iso + "T12:00");
const jParts = (iso: string) => { const d = dateOf(iso); return toJalali(d.getFullYear(), d.getMonth() + 1, d.getDate()); };
const hm = (iso: string) => faDigits(iso.slice(11, 16));
const addMin = (iso: string, m: number) => toLocalIso(new Date(parseLocal(iso).getTime() + m * 60000));

/** Six-month booking guide: per line, every day coloured by how booked it is; click a day for its appointments. */
export default function BookingCalendar({ refresh, onBook }: { refresh: number; onBook: (preset: { service_id?: number; start_at?: string }) => void }) {
  const lines = useApi<any[]>("/api/lines").data ?? [];
  const services = useApi<any[]>("/api/services").data ?? [];
  const [lineId, setLineId] = useState<number>(0);
  const [cal, setCal] = useState<Cal | null>(null);
  const [day, setDay] = useState<string>("");
  const [print, setPrint] = useState(false);

  useEffect(() => {
    if (!lineId && lines.length) setLineId(lines[0].id);
  }, [lines, lineId]);
  useEffect(() => {
    if (!lineId) return;
    api<Cal>(`/api/appointments/calendar?line_id=${lineId}&days=182`).then(setCal).catch(() => setCal(null));
  }, [lineId, refresh]);

  // weeks (Saturday first) covering the six months
  const weeks = useMemo(() => {
    if (!cal?.days.length) return [];
    const lead = jWeekday(dateOf(cal.days[0].date));
    const cells: (Day | null)[] = [...Array(lead).fill(null), ...cal.days];
    const out: (Day | null)[][] = [];
    for (let i = 0; i < cells.length; i += 7) out.push(cells.slice(i, i + 7));
    return out;
  }, [cal]);

  const open = cal?.days.filter((d) => d.status !== "past" && d.status !== "closed") ?? [];
  const firstFree = open.find((d) => d.status !== "full");
  const lastBooked = [...open].reverse().find((d) => d.count > 0);
  const fullCount = open.filter((d) => d.status === "full").length;
  const lineServices = services.filter((s) => s.line_id === lineId || s.line === cal?.line);

  return (
    <Card title={<span className="flex items-center gap-2"><CalendarDays size={18} className="text-violet-500" />تقویم نوبت‌دهی ۶ ماه آینده</span>}
      actions={<button className="btn btn-sm" onClick={() => setPrint(true)}><Printer size={14} />چاپ نوبت‌ها (A4)</button>}>
      <div className="mb-3 flex flex-wrap gap-1.5">
        {lines.map((l) => (
          <button key={l.id} onClick={() => { setLineId(l.id); setDay(""); }}
            className={`rounded-xl px-3 py-1.5 text-sm font-semibold transition ${lineId === l.id ? "bg-gradient-to-l from-pink-500 to-violet-600 text-white shadow" : "border hover:bg-violet-500/10"}`}
            style={lineId === l.id ? {} : { borderColor: "var(--border)" }}>{l.name}</button>
        ))}
      </div>

      {!cal ? <div className="muted text-sm">در حال بارگذاری…</div> : cal.capacity === 0 ? <Empty text="این لاین خدمت فعالی ندارد" /> : (
        <>
          <div className="mb-3 grid gap-2 text-xs sm:grid-cols-3">
            <div className="rounded-xl bg-emerald-500/10 px-3 py-2">
              <div className="muted">اولین روز با نوبت خالی</div>
              <div className="font-bold">{firstFree ? `${formatJ(firstFree.date + "T12:00", false)}${firstFree.first_free ? ` از ساعت ${faDigits(firstFree.first_free)}` : ""}` : "تا ۶ ماه آینده جای خالی نیست"}</div>
            </div>
            <div className="rounded-xl bg-violet-500/10 px-3 py-2">
              <div className="muted">آخرین روزی که نوبت دارد</div>
              <div className="font-bold">{lastBooked ? formatJ(lastBooked.date + "T12:00", false) : "نوبتی ثبت نشده"}</div>
            </div>
            <div className="rounded-xl bg-rose-500/10 px-3 py-2">
              <div className="muted">روزهای کاملاً پر</div>
              <div className="font-bold">{faDigits(fullCount)} روز · ظرفیت هم‌زمان: {faDigits(cal.capacity)} نفر · ساعت {faDigits(cal.open)} تا {faDigits(cal.close)}</div>
            </div>
          </div>

          <div className="mb-2 flex flex-wrap gap-3 text-xs">
            {(["empty", "partial", "full", "closed"] as const).map((k) => (
              <span key={k} className="flex items-center gap-1.5"><span className={`h-3 w-3 rounded ${DAY_STYLE[k].dot}`} />{DAY_STYLE[k].label}</span>
            ))}
          </div>

          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <div className="max-h-[560px] overflow-y-auto rounded-2xl border p-2" style={{ borderColor: "var(--border)" }}>
              <div className="sticky top-0 z-10 grid grid-cols-[3.5rem_repeat(7,minmax(0,1fr))] gap-1 pb-1 text-center text-[11px] font-bold" style={{ background: "var(--surface-solid)" }}>
                <div />
                {J_WEEKDAYS_SHORT.map((w, i) => <div key={w} className={i === 6 ? "text-rose-500" : "muted"}>{w}</div>)}
              </div>
              {weeks.map((w, wi) => {
                const first = w.find(Boolean) as Day;
                const newMonth = w.find((d) => d && jParts(d.date)[2] === 1) as Day | undefined;
                const label = wi === 0 ? first : newMonth;
                return (
                  <div key={wi} className="grid grid-cols-[3.5rem_repeat(7,minmax(0,1fr))] gap-1 py-0.5">
                    <div className="flex items-center text-[11px] font-bold text-violet-600 dark:text-violet-300">{label ? J_MONTHS[jParts(label.date)[1] - 1] : ""}</div>
                    {w.map((d, i) => {
                      if (!d) return <div key={i} />;
                      const [, , jd] = jParts(d.date);
                      const st = DAY_STYLE[d.status];
                      const sel = day === d.date;
                      return (
                        <button key={d.date} onClick={() => setDay(d.date)}
                          title={`${formatJ(d.date + "T12:00", false)} - ${st.label}${d.count ? ` - ${d.count} نوبت` : ""}${d.first_free ? ` - اولین زمان خالی ${d.first_free}` : ""}`}
                          className={`relative flex h-11 flex-col items-center justify-center rounded-lg text-xs font-bold transition ${st.cell} ${sel ? "ring-2 ring-violet-600 ring-offset-1" : ""} ${jd === 1 ? "border-r-2 border-violet-500" : ""}`}>
                          <span>{faDigits(jd)}</span>
                          {d.count > 0 && d.status !== "past" && <span className="text-[9px] font-semibold opacity-80">{faDigits(d.count)} نوبت</span>}
                          {d.status === "partial" && <span className="absolute bottom-0.5 left-1 right-1 h-0.5 rounded bg-amber-600/40"><span className="block h-full rounded bg-amber-600" style={{ width: `${Math.max(8, d.fill * 100)}%` }} /></span>}
                        </button>
                      );
                    })}
                  </div>
                );
              })}
            </div>
            <DayPanel lineId={lineId} day={cal.days.find((d) => d.date === day)} services={lineServices} onBook={onBook} refresh={refresh} />
          </div>
        </>
      )}
      <Modal open={print} onClose={() => setPrint(false)} title="چاپ نوبت‌ها روی A4">
        {print && <PrintDialog lines={lines} lineId={lineId} day={day} onDone={() => setPrint(false)} />}
      </Modal>
    </Card>
  );
}

function DayPanel({ lineId, day, services, onBook, refresh }: { lineId: number; day?: Day; services: any[]; onBook: (p: { service_id?: number; start_at?: string }) => void; refresh: number }) {
  const [list, setList] = useState<any[] | null>(null);
  const [serviceId, setServiceId] = useState<number>(0);
  const [slots, setSlots] = useState<any[]>([]);
  useEffect(() => {
    if (!day) return setList(null);
    api<any[]>(`/api/appointments?line_id=${lineId}&start=${day.date}&end=${day.date}`).then(setList).catch(() => setList([]));
  }, [lineId, day?.date, refresh]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!serviceId && services.length) setServiceId(services[0].id);
    if (serviceId && !services.some((s) => s.id === serviceId)) setServiceId(services[0]?.id ?? 0);
  }, [services, serviceId]);
  useEffect(() => {
    if (!day || !serviceId || day.status === "past") return setSlots([]);
    api<any[]>(`/api/appointments/suggest?service_id=${serviceId}&after=${day.date}T00:00&count=20`)
      .then((s) => setSlots(s.filter((x) => x.start_at.startsWith(day.date)))).catch(() => setSlots([]));
  }, [day?.date, serviceId, refresh]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!day) return (
    <div className="grid place-items-center rounded-2xl border border-dashed p-6 text-center text-sm muted" style={{ borderColor: "var(--border)" }}>
      روی یک روز از تقویم کلیک کنید تا نوبت‌ها و زمان‌های خالی آن روز را ببینید و نوبت ثبت کنید.
    </div>
  );
  const st = DAY_STYLE[day.status];
  const active = (list ?? []).filter((a) => a.status !== "cancelled");
  return (
    <div className="space-y-3 rounded-2xl border p-3" style={{ borderColor: "var(--border)" }}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="font-extrabold">{J_WEEKDAYS[jWeekday(dateOf(day.date))]} {formatJ(day.date + "T12:00", false).split(" ").slice(1).join(" ")}</div>
          <div className="flex items-center gap-1.5 text-xs"><span className={`h-2.5 w-2.5 rounded ${st.dot}`} />{st.label}{day.first_free ? ` · اولین زمان خالی ${faDigits(day.first_free)}` : ""}</div>
        </div>
        {day.status !== "past" && <button className="btn btn-sm btn-primary" onClick={() => onBook({ service_id: serviceId || undefined })}><CalendarPlus size={14} />نوبت جدید</button>}
      </div>

      <div>
        <div className="label">نوبت‌های این روز ({faDigits(active.length)})</div>
        {list === null ? <div className="muted text-xs">…</div> : list.length === 0 ? <div className="muted text-xs">نوبتی ثبت نشده است.</div> : (
          <div className="space-y-1.5">
            {list.map((a) => (
              <div key={a.id} className={`flex items-center justify-between gap-2 rounded-xl px-2.5 py-1.5 text-sm ${a.status === "cancelled" ? "opacity-50" : ""}`} style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
                <div className="flex items-center gap-2">
                  <span className="num rounded-lg bg-violet-500/10 px-1.5 py-0.5 text-xs font-bold text-violet-700 dark:text-violet-300">{hm(a.start_at)}–{hm(addMin(a.start_at, a.duration_minutes ?? 60))}</span>
                  <div>
                    <div className="font-semibold">{a.customer} <span className="muted num text-xs">{a.customer_mobile}</span></div>
                    <div className="muted text-xs">{a.service}{a.staff ? ` · ${a.staff}` : ""}{a.deposits?.length ? ` · بیعانه ${a.deposits.map((d: any) => money(d.amount)).join(" + ")}` : ""}</div>
                  </div>
                </div>
                <Badge status={a.status} />
              </div>
            ))}
          </div>
        )}
      </div>

      {day.status !== "past" && day.status !== "closed" && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="label mb-0">زمان‌های خالی برای</span>
            <select className="input w-auto py-1 text-sm" value={serviceId} onChange={(e) => setServiceId(Number(e.target.value))}>
              {services.map((s) => <option key={s.id} value={s.id}>{s.name} ({faDigits(s.duration_minutes)} دقیقه)</option>)}
            </select>
          </div>
          {slots.length ? (
            <div className="flex flex-wrap gap-1.5">
              {slots.map((s) => (
                <button key={s.start_at} onClick={() => onBook({ service_id: serviceId, start_at: s.start_at })}
                  className="rounded-xl border px-2.5 py-1 text-xs font-bold hover:bg-emerald-500/15" style={{ borderColor: "var(--border)" }}
                  title="ثبت نوبت در این زمان">{hm(s.start_at)}</button>
              ))}
            </div>
          ) : <div className="muted text-xs">برای این خدمت در این روز زمان خالی نیست.</div>}
          <div className="muted text-[11px]">روی هر ساعت کلیک کنید تا فرم نوبت با همان زمان باز شود؛ زمان دلخواه را هم در فرم می‌توانید دستی انتخاب کنید.</div>
        </div>
      )}
    </div>
  );
}

function PrintDialog({ lines, lineId, day, onDone }: { lines: any[]; lineId: number; day: string; onDone: () => void }) {
  const today = toLocalIso(new Date()).slice(0, 10);
  const [which, setWhich] = useState<number[]>(lineId ? [lineId] : lines.map((l) => l.id));
  const [from, setFrom] = useState((day || today) + "T12:00");
  const [to, setTo] = useState((day || today) + "T12:00");
  const [cancelled, setCancelled] = useState(false);
  const go = () => {
    const q = new URLSearchParams({ lines: which.join(","), start: from.slice(0, 10), end: (to < from ? from : to).slice(0, 10), ...(cancelled ? { all: "1" } : {}) });
    window.open(`/print/appointments?${q}`, "_blank");
    onDone();
  };
  const setRange = (days: number) => setTo(toLocalIso(new Date(parseLocal(from).getTime() + days * 86400000)));
  return (
    <div className="space-y-4">
      <Field label="لاین‌ها (هر لاین در صفحهٔ جداگانه چاپ می‌شود)">
        <div className="flex flex-wrap gap-1.5">
          {lines.map((l) => {
            const on = which.includes(l.id);
            return <button key={l.id} type="button" onClick={() => setWhich(on ? which.filter((x) => x !== l.id) : [...which, l.id])}
              className={`rounded-xl px-3 py-1.5 text-sm font-semibold ${on ? "bg-violet-600 text-white" : "border"}`} style={on ? {} : { borderColor: "var(--border)" }}>{on ? "✓ " : ""}{l.name}</button>;
          })}
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setWhich(lines.map((l) => l.id))}>همه</button>
        </div>
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="از تاریخ"><JalaliPicker withTime={false} value={from} onChange={(v) => v && setFrom(v)} /></Field>
        <Field label="تا تاریخ"><JalaliPicker withTime={false} value={to} onChange={(v) => v && setTo(v)} /></Field>
      </div>
      <div className="flex flex-wrap gap-1.5 text-xs">
        <button type="button" className="btn btn-sm" onClick={() => setRange(0)}>فقط همان روز</button>
        <button type="button" className="btn btn-sm" onClick={() => setRange(6)}>یک هفته</button>
        <button type="button" className="btn btn-sm" onClick={() => setRange(29)}>یک ماه</button>
      </div>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={cancelled} onChange={(e) => setCancelled(e.target.checked)} />نوبت‌های لغوشده هم چاپ شود</label>
      <button className="btn btn-primary w-full" disabled={!which.length} onClick={go}><Printer size={16} />نمایش و چاپ</button>
    </div>
  );
}
