import { Package } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { money, num } from "../lib/format";

/** Find a product by its code, the website SKU or its name. */
export default function ProductPicker({ value, onChange, className = "" }: { value?: any; onChange: (p: any) => void; className?: string }) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<any[]>([]);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const t = setTimeout(() => api<any[]>(`/api/products?q=${encodeURIComponent(q)}`).then((r) => setItems(r.slice(0, 30))).catch(() => setItems([])), 200);
    return () => clearTimeout(t);
  }, [q, open]);
  useEffect(() => {
    const close = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  if (value && !open)
    return (
      <button type="button" className={`input flex items-center justify-between gap-2 text-right ${className}`} onClick={() => { setQ(""); setOpen(true); }}>
        <span className="flex min-w-0 items-center gap-2"><Package size={15} className="shrink-0 text-sky-500" /><span className="num muted text-xs">{value.code}</span><span className="truncate font-semibold">{value.name}</span></span>
        <span className="muted shrink-0 text-xs">تغییر</span>
      </button>
    );
  return (
    <div ref={box} className={`relative ${className}`}>
      <input className="input" autoFocus={open} placeholder="کد، SKU یا نام محصول…" value={q} onFocus={() => setOpen(true)} onChange={(e) => { setQ(e.target.value); setOpen(true); }} />
      {open && (
        <div className="absolute z-30 mt-1 max-h-72 w-full overflow-y-auto rounded-2xl border shadow-xl" style={{ background: "var(--surface-solid)", borderColor: "var(--border)" }}>
          {items.length === 0 ? <div className="muted px-4 py-3 text-sm">محصولی پیدا نشد{q ? "" : "؛ ابتدا در صفحهٔ «محصولات» تعریف کنید"}</div> : items.map((p) => (
            <button type="button" key={p.id} onClick={() => { onChange(p); setOpen(false); setQ(""); }}
              className="flex w-full items-center justify-between gap-2 px-3 py-2 text-right text-sm hover:bg-violet-500/10">
              <span className="min-w-0"><span className="num muted ml-2 text-xs">{p.code}</span><b>{p.name}</b>{p.sku && <span className="num muted mr-2 text-xs" dir="ltr">{p.sku}</span>}</span>
              <span className="flex shrink-0 items-center gap-2 text-xs">
                <span className={`num rounded-md px-1.5 ${p.stock_qty <= 0 ? "bg-rose-500/15 text-rose-600" : p.low ? "bg-amber-500/15 text-amber-700" : "muted"}`}>موجودی {num(p.stock_qty)}</span>
                <span className="num font-semibold">{money(p.sale_price, false)}</span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
