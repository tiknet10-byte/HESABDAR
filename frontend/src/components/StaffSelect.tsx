import { useEffect, useState } from "react";
import { api } from "../lib/api";

/** Staff picker tied to a service: lists the staff of the service's line and auto-selects when there is only one. */
export default function StaffSelect({ serviceId, value, onChange, className = "input", emptyLabel = "پرسنل" }: {
  serviceId?: number | null; value?: number | null; onChange: (id: number | undefined) => void; className?: string; emptyLabel?: string;
}) {
  const [people, setPeople] = useState<any[]>([]);
  const [all, setAll] = useState<any[]>([]);

  useEffect(() => {
    api<any[]>("/api/staff").then((s) => setAll(s.filter((p) => p.is_active))).catch(() => {});
  }, []);

  useEffect(() => {
    if (!serviceId) return setPeople([]);
    api<any[]>(`/api/staff?service_id=${serviceId}`).then((s) => {
      const active = s.filter((p) => p.is_active);
      setPeople(active);
      // the service belongs to a line: pick that line's only staff member, or clear a choice from another line
      if (active.length === 1 && value !== active[0].id) onChange(active[0].id);
      else if (value && active.length > 1 && !active.some((p) => p.id === value)) onChange(undefined);
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serviceId]);

  const list = serviceId && people.length ? people : all;
  const others = serviceId && people.length ? all.filter((p) => !people.some((x) => x.id === p.id)) : [];
  return (
    <select className={className} value={value ?? ""} onChange={(e) => onChange(Number(e.target.value) || undefined)}
      title={people[0]?.line ? `پرسنل لاین ${people[0].line}` : undefined}>
      <option value="">{serviceId && people.length > 1 ? `انتخاب پرسنل ${people[0].line ?? ""}…` : emptyLabel}</option>
      <optgroup label={serviceId && people.length ? `لاین ${people[0].line ?? ""}` : "همه پرسنل"}>
        {list.map((p) => <option key={p.id} value={p.id}>{p.full_name}</option>)}
      </optgroup>
      {others.length > 0 && (
        <optgroup label="سایر پرسنل">
          {others.map((p) => <option key={p.id} value={p.id}>{p.full_name}{p.line ? ` (${p.line})` : ""}</option>)}
        </optgroup>
      )}
    </select>
  );
}
