import { CalendarClock, Plus } from "lucide-react";
import { useState } from "react";
import CustomerPicker, { type CustomerChoice } from "../components/CustomerPicker";
import { Badge, Card, Empty, Field, Loading, Modal, PageHeader } from "../components/ui";
import { api } from "../lib/api";
import { jlong, money } from "../lib/format";
import { useApi, useToast } from "../lib/hooks";

export default function Appointments() {
  const toast = useToast();
  const { data, reload } = useApi<any[]>("/api/appointments");
  const services = useApi<any[]>("/api/services").data ?? [];
  const staff = useApi<any[]>("/api/staff").data ?? [];
  const [open, setOpen] = useState(false);
  const [cust, setCust] = useState<CustomerChoice>({});
  const [f, setF] = useState({ service_id: 0, staff_id: 0, start_at: "", notes: "" });

  const groups = (data ?? []).reduce((g: Record<string, any[]>, a) => ((g[a.start_at.slice(0, 10)] ||= []).push(a), g), {});
  const svc = (id: number) => services.find((s) => s.id === id)?.name ?? "—";

  async function save() {
    try {
      await api("/api/appointments", { body: { ...cust, service_id: f.service_id || null, staff_id: f.staff_id || null, start_at: f.start_at, notes: f.notes } });
      toast("نوبت ثبت شد");
      setOpen(false);
      reload();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }
  async function setStatus(id: number, status: string) {
    await api(`/api/appointments/${id}?status=${status}`, { method: "PATCH" });
    reload();
  }

  return (
    <div className="space-y-5">
      <PageHeader title="نوبت‌ها" subtitle="رزروهای پیش رو؛ پس از انجام خدمت، فاکتور صادر و بیعانه کسر می‌شود" icon={<CalendarClock size={22} />}
        actions={<button className="btn btn-primary" onClick={() => setOpen(true)}><Plus size={16} />نوبت جدید</button>} />
      {!data ? <Loading /> : data.length === 0 ? <Card><Empty text="نوبتی ثبت نشده" /></Card> : Object.entries(groups).map(([day, list]) => (
        <Card key={day} title={jlong(new Date(day + "T12:00:00"))}>
          <div className="space-y-2">
            {list.map((a) => (
              <div key={a.id} className="flex flex-wrap items-center justify-between gap-2 rounded-2xl px-4 py-3" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
                <div className="flex items-center gap-3">
                  <span className="num rounded-xl bg-violet-500/10 px-2 py-1 font-bold text-violet-700 dark:text-violet-300">{a.start_at.slice(11, 16)}</span>
                  <div><div className="font-semibold">{a.customer}</div><div className="muted text-xs">{svc(a.service_id)} · {money(a.quoted_price)}</div></div>
                </div>
                <div className="flex items-center gap-2">
                  <Badge status={a.status} />
                  {a.status === "booked" && (
                    <>
                      <a className="btn btn-sm btn-primary" href={`/invoices?new=1`}>صدور فاکتور</a>
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
        <div className="space-y-3">
          <Field label="مشتری"><CustomerPicker value={cust} onChange={setCust} /></Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="خدمت">
              <select className="input" value={f.service_id} onChange={(e) => setF({ ...f, service_id: Number(e.target.value) })}>
                <option value={0}>—</option>
                {services.map((s) => <option key={s.id} value={s.id}>{s.line} / {s.name}</option>)}
              </select>
            </Field>
            <Field label="پرسنل">
              <select className="input" value={f.staff_id} onChange={(e) => setF({ ...f, staff_id: Number(e.target.value) })}>
                <option value={0}>—</option>
                {staff.map((p) => <option key={p.id} value={p.id}>{p.full_name}</option>)}
              </select>
            </Field>
          </div>
          <Field label="زمان"><input type="datetime-local" className="input" value={f.start_at} onChange={(e) => setF({ ...f, start_at: e.target.value })} /></Field>
          <Field label="یادداشت"><input className="input" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
          <button className="btn btn-primary w-full" disabled={!f.start_at} onClick={save}>ثبت نوبت</button>
        </div>
      </Modal>
    </div>
  );
}
