import { useEffect, useState } from "react";
import BookingFields, { type BookingState } from "./Booking";
import CustomerPicker, { type CustomerChoice } from "./CustomerPicker";
import StaffSelect from "./StaffSelect";
import { Field } from "./ui";
import { api } from "../lib/api";
import { money, svcLabel } from "../lib/format";
import { useApi, useToast } from "../lib/hooks";
import { faDigits, formatJ } from "../lib/jalali";

/** New appointment, or change an existing one (customer, service, staff, time, length, note). */
export default function AppointmentForm({ initial, preset, onDone }: {
  initial?: any; preset?: { service_id?: number; start_at?: string; customer_id?: number; customer?: string }; onDone: (a?: any) => void;
}) {
  const toast = useToast();
  const services = useApi<any[]>("/api/services").data ?? [];
  const startCustomer = initial ?? (preset?.customer_id ? { customer_id: preset.customer_id, customer: preset.customer } : null);
  const [cust, setCust] = useState<CustomerChoice>(startCustomer
    ? { customer_id: startCustomer.customer_id, label: `${startCustomer.customer ?? ""}${initial?.customer_mobile ? " · " + initial.customer_mobile : ""}` } : {});
  const [f, setF] = useState({ service_id: initial?.service_id ?? preset?.service_id ?? 0, staff_id: initial?.staff_id ?? 0,
    start_at: initial?.time_unknown ? "" : initial?.start_at ?? preset?.start_at ?? "", notes: initial?.notes ?? "",
    duration_minutes: initial?.custom_duration ? initial.duration_minutes : 0 });
  const [bs, setBs] = useState<BookingState>({ ready: false, allowOutside: false });
  const [held, setHeld] = useState<any[]>([]);
  const [picked, setPicked] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);

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

  const hasCustomer = !!(cust.customer_id || (cust.customer_name && cust.customer_mobile) || cust.customer_name);
  const customerChanged = !!initial && cust.customer_id !== initial.customer_id;
  // an unchanged booking time doesn't need a new check (e.g. only the note or the customer changes)
  const sameTime = !!initial && f.start_at === initial.start_at && f.service_id === initial.service_id && f.staff_id === (initial.staff_id ?? 0)
    && (f.duration_minutes || 0) === (initial.custom_duration ? initial.duration_minutes : 0);
  const ready = !!f.service_id && !!f.start_at && hasCustomer && (sameTime || bs.ready);

  async function save() {
    setBusy(true);
    try {
      const body: any = { notes: f.notes };
      if (!sameTime || !initial) Object.assign(body, { service_id: f.service_id || null, staff_id: f.staff_id || null, start_at: f.start_at,
        duration_minutes: f.duration_minutes || null, allow_outside_hours: bs.allowOutside });
      let r;
      if (initial) {
        if (customerChanged) {
          body.customer_id = cust.customer_id ?? (await api("/api/customers", { body: { full_name: cust.customer_name, mobile: cust.customer_mobile || null } })).id;
        }
        r = await api(`/api/appointments/${initial.id}`, { method: "PUT", body });
        toast(customerChanged ? `نوبت به نام «${r.customer}» منتقل شد` : "تغییرات نوبت ذخیره شد");
      } else {
        r = await api("/api/appointments", { body: { ...cust, ...body, deposit_ids: picked } });
        toast(`نوبت ${formatJ(r.start_at)} ثبت شد`);
      }
      if (r.warning) toast(r.warning, "info");
      onDone(r);
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <Field label={initial ? "مشتری (برای انتخاب مشتری دیگر «تغییر» را بزنید)" : "۱. مشتری"}>
        <CustomerPicker value={cust} onChange={setCust} />
      </Field>
      {customerChanged && initial?.deposits?.some((d: any) => d.status === "held") && (
        <div className="rounded-xl bg-amber-500/10 p-2.5 text-xs text-amber-700 dark:text-amber-300">بیعانهٔ مشتری قبلی به او تعلق دارد و از این نوبت جدا می‌شود (به‌صورت بیعانهٔ باز همان مشتری می‌ماند).</div>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={initial ? "خدمت" : "۲. خدمت"}>
          <select className="input" value={f.service_id} onChange={(e) => setF({ ...f, service_id: Number(e.target.value) })}>
            <option value={0}>— خدمت را انتخاب کنید —</option>
            {services.map((s) => <option key={s.id} value={s.id}>{svcLabel(s)} ({faDigits(s.duration_minutes)} دقیقه)</option>)}
          </select>
        </Field>
        <Field label="پرسنل (اختیاری)">
          <StaffSelect serviceId={f.service_id || undefined} value={f.staff_id || undefined} emptyLabel="هر پرسنل آزاد"
            onChange={(id) => setF((x) => ({ ...x, staff_id: id ?? 0 }))} />
        </Field>
      </div>
      <Field label={initial ? "زمان نوبت" : "۳. زمان نوبت (روی یکی از زمان‌های خالی بزنید)"}>
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
      <Field label="یادداشت (اختیاری)"><input className="input" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} placeholder="مثلاً: مشتری زودتر می‌آید" /></Field>
      {!ready && (
        <p className="muted text-xs">
          {!hasCustomer ? "اول مشتری را انتخاب کنید." : !f.service_id ? "خدمت را انتخاب کنید." : !f.start_at ? "زمان نوبت را انتخاب کنید." : "زمان انتخاب‌شده آزاد نیست؛ زمان دیگری انتخاب کنید."}
        </p>
      )}
      <button className="btn btn-primary w-full py-3 text-base" disabled={!ready || busy} onClick={save}>
        {initial ? "ذخیره تغییرات" : "ثبت نوبت"}
      </button>
    </div>
  );
}
