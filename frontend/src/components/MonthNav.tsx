import { ChevronLeft, ChevronRight } from "lucide-react";
import { monthName, sameMonth, stepMonth, thisMonth, type YM } from "../lib/jalali";

/** «ماه قبل · مهر ۱۴۰۵ · ماه بعد» (never past this month) */
export default function MonthNav({ month, onChange }: { month: YM; onChange: (m: YM) => void }) {
  const cur = thisMonth();
  const atCurrent = sameMonth(month, cur);
  return (
    <div className="flex flex-wrap items-center justify-center gap-2">
      <button className="btn btn-sm" onClick={() => onChange(stepMonth(month, -1))} title="ماه قبل"><ChevronRight size={16} />ماه قبل</button>
      <div className="min-w-32 rounded-2xl px-4 py-2 text-center text-lg font-bold sm:min-w-40" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>{monthName(month)}</div>
      <button className="btn btn-sm" disabled={atCurrent} onClick={() => onChange(stepMonth(month, 1))} title="ماه بعد">ماه بعد<ChevronLeft size={16} /></button>
      {!atCurrent && <button className="btn btn-sm" onClick={() => onChange(cur)}>این ماه</button>}
    </div>
  );
}

/** a month chosen from a short list (this month and the 11 before it) */
export function MonthSelect({ value, onChange }: { value: YM; onChange: (m: YM) => void }) {
  const cur = thisMonth();
  const list = Array.from({ length: 12 }, (_, i) => stepMonth(cur, -i));
  if (!list.some((m) => sameMonth(m, value))) list.push(value);
  return (
    <select className="input" value={`${value[0]}-${value[1]}`} onChange={(e) => { const [y, m] = e.target.value.split("-").map(Number); onChange([y, m]); }}>
      {list.map((m) => <option key={`${m[0]}-${m[1]}`} value={`${m[0]}-${m[1]}`}>{monthName(m)}</option>)}
    </select>
  );
}
