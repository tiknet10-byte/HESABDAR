import { CalendarClock, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import BookingFields from "../components/Booking";
import CustomerPicker, { type CustomerChoice } from "../components/CustomerPicker";
import { Badge, Card, Empty, Field, Loading, Modal, PageHeader } from "../components/ui";
import { api } from "../lib/api";
import { money } from "../lib/format";
import { useApi, useToast } from "../lib/hooks";
import { faDigits, formatJ } from "../lib/jalali";

function AppointmentForm({ initial, onDone }: { initial?: any; onDone: () => void }) {
  const toast = useToast();
  const services = useApi<any[]>("/api/services").data ?? [];
  const staff = useApi<any[]>("/api/staff").data ?? [];
  const [cust, setCust] = useState<CustomerChoice>(initial ? { customer_id: initial.customer_id, label: initial.customer } : {});
  const [f, setF] = useState({ service_id: initial?.service_id ?? 0, staff_id: initial?.staff_id ?? 0, start_at: initial?.start_at ?? "", notes: initial?.notes ?? "" });
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
      const body = { ...f, service_id: f.service_id || null, staff_id: f.staff_id || null };
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
          <select className="input" value={f.staff_id} onChange={(e) => setF({ ...f, staff_id: Number(e.target.value) })}>
            <option value={0}>هر پرسنل آزاد</option>
            {staff.filter((p) => p.is_active).map((p) => <option key={p.id} value={p.id}>{p.full_name}</option>)}
          </select>
        </Field>
      </div>
      <Field label="زمان نوبت">
        <BookingFields serviceId={f.service_id || undefined} staffId={f.staff_id || undefined} value={f.start_at}
          onChange={(v) => setF((x) => ({ ...x, start_at: v }))} onStaff={(id) => setF((x) => ({ ...x, staff_id: id ?? 0 }))} />
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
      <button className="btn btn-primary w-full" disabled={!f.start_at || (!initial && !(cust.customer_id || cust.customer_name || cust.customer_mobile))} onClick={save}>
        {initial ? "ذخیره تغییرات" : "ثبت نوبت"}
      </button>
    </div>
  );
}

export default function Appointments() {
  const { data, reload } = useApi<any[]>("/api/appointments");
  const [open, setOpen] = useState(false);
  const [edit, setEdit] = useState<any>(null);
  const groups = (data ?? []).reduce((g: Record<string, any[]>, a) => ((g[a.start_at.slice(0, 10)] ||= []).push(a), g), {});

  async function setStatus(id: number, status: string) {
    await api(`/api/appointments/${id}?status=${status}`, { method: "PATCH" });
    reload();
  }

  return (
    <div className="space-y-5">
      <PageHeader title="نوبت‌ها" subtitle="رزروهای پیش رو؛ پس از انجام خدمت، فاکتور صادر و بیعانه کسر می‌شود" icon={<CalendarClock size={22} />}
        actions={<button className="btn btn-primary" onClick={() => setOpen(true)}><Plus size={16} />نوبت جدید</button>} />
      {!data ? <Loading /> : data.length === 0 ? <Card><Empty text="نوبتی ثبت نشده" /></Card> : Object.entries(groups).map(([day, list]) => (
        <Card key={day} title={formatJ(day + "T12:00", false)}>
          <div className="space-y-2">
            {list.map((a) => (
              <div key={a.id} className="flex flex-wrap items-center justify-between gap-2 rounded-2xl px-4 py-3" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
                <div className="flex items-center gap-3">
                  <span className="num rounded-xl bg-violet-500/10 px-2 py-1 font-bold text-violet-700 dark:text-violet-300">{faDigits(a.start_at.slice(11, 16))}</span>
                  <div>
                    <div className="font-semibold">{a.customer}</div>
                    <div className="muted text-xs">{a.service ?? "—"} · {faDigits(a.duration_minutes)} دقیقه · {money(a.quoted_price)}</div>
                    {a.deposits.length > 0 && <div className="mt-0.5 text-xs text-emerald-600">بیعانه: {a.deposits.map((d: any) => money(d.amount)).join(" + ")}</div>}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Badge status={a.status} />
                  {a.status === "booked" && (
                    <>
                      <Link className="btn btn-sm btn-primary" to={`/invoices?new=1&appointment=${a.id}`}>صدور فاکتور</Link>
                      <button className="btn btn-sm" onClick={() => setEdit(a)}>تغییر زمان</button>
                      <button className="btn btn-sm" onClick={() => setStatus(a.id, "no_show")}>نیامد</button>
                      <button className="btn btn-sm" onClick={() => setStatus(a.id, "cancelled")}>لغو</button>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        </Card>
      ))}
      <Modal open={open} onClose={() => setOpen(false)} title="نوبت جدید">
        {open && <AppointmentForm onDone={() => { setOpen(false); reload(); }} />}
      </Modal>
      <Modal open={!!edit} onClose={() => setEdit(null)} title="ویرایش نوبت">
        {edit && <AppointmentForm initial={edit} onDone={() => { setEdit(null); reload(); }} />}
      </Modal>
    </div>
  );
}
