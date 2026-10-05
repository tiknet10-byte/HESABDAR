import { CalendarClock, Plus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import BookingCalendar from "../components/BookingCalendar";
import BookingFields, { type BookingState } from "../components/Booking";
import StaffSelect from "../components/StaffSelect";
import { announceFreed, WAITLIST_CHANGED, WaitlistPanel } from "../components/Waitlist";
import CustomerPicker, { type CustomerChoice } from "../components/CustomerPicker";
import { Badge, Card, Empty, Field, Loading, Modal, PageHeader, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { money } from "../lib/format";
import { useApi, useToast } from "../lib/hooks";
import { faDigits, formatJ } from "../lib/jalali";

function AppointmentForm({ initial, preset, onDone }: { initial?: any; preset?: { service_id?: number; start_at?: string }; onDone: () => void }) {
  const toast = useToast();
  const services = useApi<any[]>("/api/services").data ?? [];
  const [cust, setCust] = useState<CustomerChoice>(initial ? { customer_id: initial.customer_id, label: initial.customer } : {});
  const [f, setF] = useState({ service_id: initial?.service_id ?? preset?.service_id ?? 0, staff_id: initial?.staff_id ?? 0,
    start_at: initial?.start_at ?? preset?.start_at ?? "", notes: initial?.notes ?? "",
    duration_minutes: initial?.custom_duration ? initial.duration_minutes : 0 });
  const [bs, setBs] = useState<BookingState>({ ready: false, allowOutside: false });
  const [held, setHeld] = useState<any[]>([]);
  const [picked, setPicked] = useState<number[]>([]);

  useEffect(() => {
    if (initial || !cust.customer_id) return setHeld([]);
    api(`/api/deposits?status=held&customer_id=${cust.customer_id}`).then((d: any[]) => {
      const free = d.filter((x) => !x.appointment_id);
      setHeld(free);
      setPicked(free.map((x) => x.id));
      const withService = free.find((x) => x.service_id);
      if (withService && !f.service_id) setF((v) => ({ ...v, service_id: withService.service_id }));
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cust.customer_id]);

  async function save() {
    try {
      const body = { ...f, service_id: f.service_id || null, staff_id: f.staff_id || null, duration_minutes: f.duration_minutes || null,
        allow_outside_hours: bs.allowOutside };
      const r = initial
        ? await api(`/api/appointments/${initial.id}`, { method: "PUT", body })
        : await api("/api/appointments", { body: { ...cust, ...body, deposit_ids: picked } });
      toast(initial ? "نوبت ویرایش شد" : "نوبت ثبت شد");
      if (r.warning) toast(r.warning, "info");
      onDone();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }

  return (
    <div className="space-y-4">
      {!initial && <Field label="مشتری"><CustomerPicker value={cust} onChange={setCust} /></Field>}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="خدمت">
          <select className="input" value={f.service_id} onChange={(e) => setF({ ...f, service_id: Number(e.target.value) })}>
            <option value={0}>—</option>
            {services.map((s) => <option key={s.id} value={s.id}>{s.line} / {s.name} ({faDigits(s.duration_minutes)} دقیقه)</option>)}
          </select>
        </Field>
        <Field label="پرسنل">
          <StaffSelect serviceId={f.service_id || undefined} value={f.staff_id || undefined} emptyLabel="هر پرسنل آزاد"
            onChange={(id) => setF((x) => ({ ...x, staff_id: id ?? 0 }))} />
        </Field>
      </div>
      <Field label="زمان نوبت">
        <BookingFields serviceId={f.service_id || undefined} staffId={f.staff_id || undefined} value={f.start_at}
          onChange={(v) => setF((x) => ({ ...x, start_at: v }))} onStaff={(id) => setF((x) => ({ ...x, staff_id: id ?? 0 }))}
          duration={f.duration_minutes || undefined} onDuration={(m) => setF((x) => ({ ...x, duration_minutes: m }))}
          customerId={initial?.customer_id ?? cust.customer_id} excludeId={initial?.id} onState={setBs} />
      </Field>
      {held.length > 0 && (
        <div className="space-y-2 rounded-2xl bg-emerald-500/10 p-3 text-sm">
          <div className="font-bold">بیعانه‌های این مشتری که به این نوبت وصل می‌شوند:</div>
          {held.map((d) => (
            <label key={d.id} className="flex items-center gap-2">
              <input type="checkbox" checked={picked.includes(d.id)} onChange={(e) => setPicked(e.target.checked ? [...picked, d.id] : picked.filter((x) => x !== d.id))} />
              <span className="num font-semibold">{money(d.amount)}</span>
              <span className="muted text-xs">{formatJ(d.received_at)} · {d.service ?? "خدمت نامشخص"}</span>
            </label>
          ))}
        </div>
      )}
      <Field label="یادداشت"><input className="input" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
      <button className="btn btn-primary w-full" disabled={!f.start_at || !f.service_id || !bs.ready || (!initial && !(cust.customer_id || cust.customer_name || cust.customer_mobile))} onClick={save}>
        {initial ? "ذخیره تغییرات" : "ثبت نوبت"}
      </button>
    </div>
  );
}

const endTime = (start: string, minutes: number) => {
  const d = new Date(new Date(start.length <= 16 ? start + ":00" : start).getTime() + minutes * 60000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};

export default function Appointments() {
  const { data, reload: reloadList } = useApi<any[]>("/api/appointments");
  const [refresh, setRefresh] = useState(0);
  const reload = useCallback(() => { reloadList(); setRefresh((n) => n + 1); }, [reloadList]);
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as "calendar" | "list" | "waitlist") || "calendar";
  const waiting = useApi<any[]>("/api/waitlist", [refresh]).data?.length ?? 0;
  const [open, setOpen] = useState<false | { service_id?: number; start_at?: string }>(false);
  const [edit, setEdit] = useState<any>(null);
  const groups = (data ?? []).reduce((g: Record<string, any[]>, a) => ((g[a.start_at.slice(0, 10)] ||= []).push(a), g), {});

  const toast = useToast();
  useEffect(() => {
    window.addEventListener(WAITLIST_CHANGED, reload); // a freed time was given to a waiting customer
    return () => window.removeEventListener(WAITLIST_CHANGED, reload);
  }, [reload]);
  async function setStatus(a: any, status: string) {
    if (status === "cancelled" && !window.confirm(`نوبت ${a.customer} (${formatJ(a.start_at)}) لغو شود؟`)) return;
    try {
      const r = await api(`/api/appointments/${a.id}?status=${status}`, { method: "PATCH" });
      toast(status === "cancelled" ? "نوبت لغو شد" : "ثبت شد");
      announceFreed([r.freed]);
    } catch (e: any) {
      toast(e.message, "error");
    }
    reload();
  }

  return (
    <div className="space-y-5">
      <PageHeader title="نوبت‌ها" subtitle="رزروهای پیش رو؛ پس از انجام خدمت، فاکتور صادر و بیعانه کسر می‌شود" icon={<CalendarClock size={22} />}
        actions={<button className="btn btn-primary" onClick={() => setOpen({})}><Plus size={16} />نوبت جدید</button>} />
      <Tabs value={tab} onChange={(t) => setParams({ tab: t }, { replace: true })} items={[
        { value: "calendar", label: "تقویم نوبت‌دهی" },
        { value: "list", label: `نوبت‌های پیش رو${data ? ` (${faDigits(data.filter((a) => a.status === "booked").length)})` : ""}` },
        { value: "waitlist", label: `لیست انتظار / VIP${waiting ? ` (${faDigits(waiting)})` : ""}` },
      ]} />
      {tab === "calendar" && <BookingCalendar refresh={refresh} onBook={(p) => setOpen(p)} />}
      {tab === "waitlist" && <WaitlistPanel onBooked={reload} />}
      {tab !== "list" ? null : !data ? <Loading /> : data.length === 0 ? <Card><Empty text="نوبتی ثبت نشده" /></Card> : Object.entries(groups).map(([day, list]) => (
        <Card key={day} title={formatJ(day + "T12:00", false)}>
          <div className="space-y-2">
            {list.map((a) => (
              <div key={a.id} className="flex flex-wrap items-center justify-between gap-2 rounded-2xl px-4 py-3" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
                <div className="flex items-center gap-3">
                  <span className="num rounded-xl bg-violet-500/10 px-2 py-1 text-center font-bold leading-tight text-violet-700 dark:text-violet-300">{faDigits(a.start_at.slice(11, 16))}<span className="block text-[10px] font-semibold opacity-70">تا {faDigits(endTime(a.start_at, a.duration_minutes ?? 60))}</span></span>
                  <div>
                    <div className="font-semibold">{a.customer}</div>
                    <div className="muted text-xs">{a.line ? `${a.line} / ` : ""}{a.service ?? "—"} · {faDigits(a.duration_minutes ?? 60)} دقیقه · {money(a.quoted_price)}</div>
                    {a.staff && <div className="text-xs font-semibold text-violet-600 dark:text-violet-300">پرسنل: {a.staff}</div>}
                    {a.original_start_at && <div className="text-xs text-amber-600">زودتر انجام شد؛ نوبت اصلی {formatJ(a.original_start_at)} آزاد شد</div>}
                    {(a.deposits ?? []).length > 0 && <div className="mt-0.5 text-xs text-emerald-600">بیعانه: {a.deposits.map((d: any) => money(d.amount)).join(" + ")}</div>}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {a.conflict && <span className="badge bg-rose-500/15 text-rose-600" title="این نوبت با نوبت دیگری از همین پرسنل یا مشتری هم‌زمان است؛ یکی را جابه‌جا یا لغو کنید">⚠ تداخل</span>}
                  <Badge status={a.status} />
                  {a.status === "booked" && (
                    <>
                      <Link className="btn btn-sm btn-primary" to={`/invoices?new=1&appointment=${a.id}`}>صدور فاکتور</Link>
                      <button className="btn btn-sm" onClick={() => setEdit(a)}>تغییر زمان</button>
                      <button className="btn btn-sm" onClick={() => setStatus(a, "no_show")}>نیامد</button>
                      <button className="btn btn-sm" onClick={() => setStatus(a, "cancelled")}>لغو</button>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        </Card>
      ))}
      <Modal open={!!open} onClose={() => setOpen(false)} title="نوبت جدید">
        {open && <AppointmentForm preset={open} onDone={() => { setOpen(false); reload(); }} />}
      </Modal>
      <Modal open={!!edit} onClose={() => setEdit(null)} title="ویرایش نوبت">
        {edit && <AppointmentForm initial={edit} onDone={() => { setEdit(null); reload(); }} />}
      </Modal>
    </div>
  );
}
