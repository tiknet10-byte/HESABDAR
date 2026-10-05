import { Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import JalaliPicker, { SlotChips } from "./JalaliPicker";

/** Suggests the first free times for a service and lets the user pick one or choose any date/time. */
export default function BookingFields({ serviceId, staffId, value, onChange, onStaff }: {
  serviceId?: number; staffId?: number; value: string; onChange: (v: string) => void; onStaff?: (id: number | undefined) => void;
}) {
  const [slots, setSlots] = useState<any[]>([]);
  useEffect(() => {
    if (!serviceId) return setSlots([]);
    api(`/api/appointments/suggest?service_id=${serviceId}${staffId ? `&staff_id=${staffId}` : ""}&count=6`).then(setSlots).catch(() => setSlots([]));
  }, [serviceId, staffId]);
  return (
    <div className="space-y-3">
      {serviceId ? (
        slots.length ? (
          <div className="space-y-2">
            <div className="flex items-center gap-1.5 text-xs font-bold text-violet-600 dark:text-violet-300"><Sparkles size={14} />اولین نوبت‌های خالی پیشنهادی</div>
            <SlotChips slots={slots} value={value} onPick={(s) => { onChange(s.start_at); if (s.staff_id && onStaff && !staffId) onStaff(s.staff_id); }} />
          </div>
        ) : <div className="muted text-xs">در ۶۰ روز آینده نوبت خالی پیدا نشد؛ زمان را دستی انتخاب کنید.</div>
      ) : <div className="muted text-xs">برای پیشنهاد نوبت خالی، خدمت را انتخاب کنید.</div>}
      <JalaliPicker value={value} onChange={onChange} placeholder="یا تاریخ و ساعت دلخواه را انتخاب کنید" clearable />
    </div>
  );
}
