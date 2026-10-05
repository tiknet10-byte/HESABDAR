import { Clock, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { useApi } from "../lib/hooks";
import { faDigits } from "../lib/jalali";
import JalaliPicker, { SlotChips } from "./JalaliPicker";

const QUICK = [15, 30, 45, 60, 90, 120, 180];

/** Booking time: suggests the first free slots for a service using its own duration (editable per booking). */
export type BookingState = { ready: boolean; allowOutside: boolean; message?: string };

export default function BookingFields({ serviceId, staffId, value, onChange, onStaff, duration, onDuration, customerId, excludeId, onState }: {
  serviceId?: number; staffId?: number; value: string; onChange: (v: string) => void; onStaff?: (id: number | undefined) => void;
  duration?: number; onDuration?: (minutes: number) => void;
  customerId?: number; excludeId?: number; onState?: (s: BookingState) => void;
}) {
  const [check, setCheck] = useState<{ ok: boolean; errors: any[]; overridable: boolean } | null>(null);
  const [allowOutside, setAllowOutside] = useState(false);
  const services = useApi<any[]>("/api/services").data ?? [];
  const svc = services.find((s) => s.id === serviceId);
  const [slots, setSlots] = useState<any[]>([]);
  const [minutes, setMinutes] = useState<number>(duration ?? 0);

  // when the service changes, start from that service's default duration
  // (except the first time when editing a booking that already has its own duration)
  const keepInitial = useRef(!!duration);
  useEffect(() => {
    if (!svc) return;
    if (keepInitial.current) {
      keepInitial.current = false;
      return;
    }
    setMinutes(svc.duration_minutes);
    onDuration?.(svc.duration_minutes);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [svc?.id]);

  useEffect(() => {
    if (!serviceId || !minutes) return setSlots([]);
    const t = setTimeout(() => {
      api(`/api/appointments/suggest?service_id=${serviceId}${staffId ? `&staff_id=${staffId}` : ""}&duration=${minutes}&count=6`)
        .then(setSlots).catch(() => setSlots([]));
    }, 250);
    return () => clearTimeout(t);
  }, [serviceId, staffId, minutes]);

  // live validation of the chosen time against the server's booking rules
  useEffect(() => {
    setAllowOutside(false);
    if (!value || !serviceId) return setCheck(null);
    const t = setTimeout(() => {
      const q = new URLSearchParams({ service_id: String(serviceId), start_at: value, ...(minutes ? { duration: String(minutes) } : {}),
        ...(staffId ? { staff_id: String(staffId) } : {}), ...(customerId ? { customer_id: String(customerId) } : {}),
        ...(excludeId ? { exclude_id: String(excludeId) } : {}) });
      api(`/api/appointments/check?${q}`).then(setCheck).catch(() => setCheck(null));
    }, 250);
    return () => clearTimeout(t);
  }, [value, serviceId, staffId, minutes, customerId, excludeId]);

  useEffect(() => {
    const ready = !!value && !!serviceId && !!check && (check.ok || (check.overridable && allowOutside));
    onState?.({ ready, allowOutside, message: check && !check.ok ? check.errors.map((e: any) => e.message).join(" | ") : undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [check, allowOutside, value, serviceId]);

  const setDur = (m: number) => {
    setMinutes(m);
    onDuration?.(m);
  };

  return (
    <div className="space-y-3">
      {serviceId ? (
        <div className="rounded-2xl p-3" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
          <div className="mb-2 flex flex-wrap items-center gap-2 text-xs font-bold">
            <Clock size={14} className="text-violet-500" />مدت این نوبت
            {svc && <span className="muted font-normal">(پیش‌فرض «{svc.name}»: {faDigits(svc.duration_minutes)} دقیقه)</span>}
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            {QUICK.map((m) => (
              <button type="button" key={m} onClick={() => setDur(m)}
                className={`rounded-xl px-2.5 py-1 text-xs font-semibold ${minutes === m ? "bg-violet-600 text-white" : "border hover:bg-violet-500/10"}`}
                style={minutes === m ? {} : { borderColor: "var(--border)" }}>{faDigits(m)}</button>
            ))}
            <input type="number" min={5} step={5} className="input w-24 py-1 text-sm" value={minutes || ""} onChange={(e) => setDur(Number(e.target.value))} />
            <span className="muted text-xs">دقیقه</span>
          </div>
        </div>
      ) : null}
      {serviceId ? (
        slots.length ? (
          <div className="space-y-2">
            <div className="flex items-center gap-1.5 text-xs font-bold text-violet-600 dark:text-violet-300"><Sparkles size={14} />اولین نوبت‌های خالی پیشنهادی</div>
            <SlotChips slots={slots} value={value} onPick={(s) => { onChange(s.start_at); if (s.staff_id && onStaff && !staffId) onStaff(s.staff_id); }} />
          </div>
        ) : <div className="muted text-xs">در ۶۰ روز آینده نوبت خالی پیدا نشد؛ زمان را دستی انتخاب کنید.</div>
      ) : <div className="muted text-xs">برای پیشنهاد نوبت خالی، خدمت را انتخاب کنید.</div>}
      <JalaliPicker value={value} onChange={onChange} placeholder="یا تاریخ و ساعت دلخواه را انتخاب کنید" clearable futureOnly />
      {value && check && (check.ok ? (
        <div className="rounded-xl bg-emerald-500/10 px-3 py-2 text-xs font-semibold text-emerald-700 dark:text-emerald-300">✓ این زمان آزاد است و قابل ثبت است</div>
      ) : (
        <div className="space-y-2 rounded-xl bg-rose-500/10 px-3 py-2 text-xs font-semibold text-rose-700 dark:text-rose-300">
          {check.errors.map((e: any) => <div key={e.code}>✕ {e.message}</div>)}
          {check.overridable && (
            <label className="flex items-center gap-2 text-amber-700 dark:text-amber-300">
              <input type="checkbox" checked={allowOutside} onChange={(e) => setAllowOutside(e.target.checked)} />
              با این حال ثبت شود (نوبت خارج از ساعت کاری / روز تعطیل)
            </label>
          )}
        </div>
      ))}
    </div>
  );
}
