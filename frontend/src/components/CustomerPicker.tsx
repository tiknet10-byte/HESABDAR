import { UserPlus, UserRound } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";

export type CustomerChoice = { customer_id?: number; customer_name?: string; customer_mobile?: string; label?: string };

export default function CustomerPicker({ value, onChange }: { value: CustomerChoice; onChange: (c: CustomerChoice) => void }) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<any[]>([]);
  const [mode, setMode] = useState<"search" | "new">("search");

  useEffect(() => {
    if (mode !== "search" || q.length < 2) {
      setItems([]);
      return;
    }
    const t = setTimeout(() => api(`/api/customers?q=${encodeURIComponent(q)}&limit=8`).then((r) => setItems(r.items)).catch(() => {}), 250);
    return () => clearTimeout(t);
  }, [q, mode]);

  if (value.customer_id)
    return (
      <div className="input flex items-center justify-between">
        <span className="flex items-center gap-2 font-semibold"><UserRound size={16} className="text-violet-500" />{value.label}</span>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => onChange({})}>تغییر</button>
      </div>
    );

  return (
    <div className="space-y-2">
      {mode === "search" ? (
        <div className="relative">
          <input className="input" placeholder="جستجوی کد، نام یا موبایل مشتری…" value={q} onChange={(e) => setQ(e.target.value)} />
          {items.length > 0 && (
            <div className="absolute z-20 mt-1 w-full overflow-hidden rounded-2xl border shadow-xl" style={{ background: "var(--surface-solid)", borderColor: "var(--border)" }}>
              {items.map((c) => (
                <button type="button" key={c.id} className="flex w-full items-center justify-between px-4 py-2.5 text-right text-sm hover:bg-violet-500/10"
                  onClick={() => { onChange({ customer_id: c.id, label: `${c.full_name}${c.mobile ? " · " + c.mobile : ""}${c.code ? " · کد " + c.code : ""}` }); setQ(""); }}>
                  <span className="font-semibold">{c.code && <span className="num muted ml-2 text-xs">{c.code}</span>}{c.full_name}</span>
                  <span className="muted num" dir="ltr">{c.mobile}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <input className="input" placeholder="نام و نام خانوادگی" value={value.customer_name ?? ""} onChange={(e) => onChange({ ...value, customer_name: e.target.value })} />
          <input className="input num" dir="ltr" placeholder="09xxxxxxxxx" value={value.customer_mobile ?? ""} onChange={(e) => onChange({ ...value, customer_mobile: e.target.value })} />
        </div>
      )}
      <button type="button" className="btn btn-ghost btn-sm text-violet-600 dark:text-violet-300" onClick={() => { setMode(mode === "search" ? "new" : "search"); onChange({}); }}>
        <UserPlus size={14} /> {mode === "search" ? "مشتری جدید" : "انتخاب از مشتریان"}
      </button>
    </div>
  );
}
