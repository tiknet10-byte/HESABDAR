import { X } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { STATUS, fromRial, unitLabel } from "../lib/format";

export function PageHeader({ title, subtitle, icon, actions }: { title: string; subtitle?: string; icon?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-center justify-between gap-3 fade-up">
      <div className="flex items-center gap-3">
        {icon && <div className="grid h-11 w-11 place-items-center rounded-2xl bg-gradient-to-br from-pink-500 to-violet-600 text-white shadow-lg shadow-violet-500/25">{icon}</div>}
        <div>
          <h1 className="text-xl font-extrabold sm:text-2xl">{title}</h1>
          {subtitle && <p className="muted mt-0.5 text-sm">{subtitle}</p>}
        </div>
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Card({ title, actions, children, className = "", pad = true }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; pad?: boolean }) {
  return (
    <section className={`card fade-up ${className}`}>
      {(title || actions) && (
        <div className="flex items-center justify-between gap-2 px-5 pt-4">
          <h3 className="font-bold">{title}</h3>
          {actions}
        </div>
      )}
      <div className={pad ? "p-5" : ""}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, hint, icon, tone = "violet" }: { label: string; value: ReactNode; hint?: ReactNode; icon?: ReactNode; tone?: "violet" | "pink" | "emerald" | "amber" | "sky" }) {
  const tones: Record<string, string> = {
    violet: "from-violet-500 to-purple-600 shadow-violet-500/30",
    pink: "from-pink-500 to-rose-500 shadow-pink-500/30",
    emerald: "from-emerald-500 to-teal-500 shadow-emerald-500/30",
    amber: "from-amber-400 to-orange-500 shadow-amber-500/30",
    sky: "from-sky-500 to-indigo-500 shadow-sky-500/30",
  };
  return (
    <div className="card fade-up relative overflow-hidden p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="muted text-xs font-medium">{label}</div>
          <div className="num mt-2 text-lg font-extrabold leading-snug sm:text-xl xl:text-2xl">{value}</div>
          {hint && <div className="muted mt-1 text-xs">{hint}</div>}
        </div>
        {icon && <div className={`grid h-11 w-11 shrink-0 place-items-center rounded-2xl bg-gradient-to-br text-white shadow-lg ${tones[tone]}`}>{icon}</div>}
      </div>
    </div>
  );
}

export function Badge({ status, children }: { status?: string; children?: ReactNode }) {
  const s = status ? STATUS[status] : undefined;
  return <span className={`badge ${s?.cls ?? "bg-violet-500/10 text-violet-600 dark:text-violet-300"}`}>{children ?? s?.label ?? status}</span>;
}

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-0 backdrop-blur-sm sm:items-center sm:p-4" onClick={onClose}>
      <div
        className={`fade-up max-h-[92vh] w-full overflow-y-auto rounded-t-3xl border sm:rounded-3xl ${wide ? "sm:max-w-4xl" : "sm:max-w-lg"}`}
        style={{ background: "var(--surface-solid)", borderColor: "var(--border)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 z-10 flex items-center justify-between border-b px-5 py-4" style={{ background: "var(--surface-solid)", borderColor: "var(--border)" }}>
          <h3 className="text-lg font-bold">{title}</h3>
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="بستن"><X size={18} /></button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

/** Form field. A <div>, not a <label>: a label re-dispatches clicks to its first button, which broke
 * composite controls (customer picker, date picker) - selecting an item also clicked their "change" button. */
export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="block">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="muted mt-1 block text-xs">{hint}</span>}
    </div>
  );
}

/** Amount input: user types in display unit (Toman by default), value is kept in Rial. */
export function MoneyInput({ value, onChange, placeholder }: { value: number; onChange: (rial: number) => void; placeholder?: string }) {
  const [text, setText] = useState(value ? fromRial(value).toLocaleString("en-US") : "");
  useEffect(() => {
    const cur = parseInt(text.replace(/,/g, "") || "0", 10);
    if (fromRial(value) !== cur) setText(value ? fromRial(value).toLocaleString("en-US") : "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return (
    <div className="relative">
      <input
        className="input num pl-14"
        inputMode="numeric"
        dir="ltr"
        value={text}
        placeholder={placeholder}
        onChange={(e) => {
          const digits = e.target.value.replace(/[۰-۹]/g, (d) => String("۰۱۲۳۴۵۶۷۸۹".indexOf(d))).replace(/[^\d]/g, "");
          const n = parseInt(digits || "0", 10);
          setText(digits ? n.toLocaleString("en-US") : "");
          onChange(n * (unitLabel() === "تومان" ? 10 : 1));
        }}
      />
      <span className="muted pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-xs">{unitLabel()}</span>
    </div>
  );
}

export function Empty({ text = "موردی یافت نشد", icon }: { text?: string; icon?: ReactNode }) {
  return (
    <div className="muted flex flex-col items-center justify-center gap-2 py-12 text-sm">
      {icon}
      {text}
    </div>
  );
}

export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { value: T; label: ReactNode }[] }) {
  return (
    <div className="inline-flex flex-wrap gap-1 rounded-2xl p-1" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
      {items.map((it) => (
        <button
          key={it.value}
          onClick={() => onChange(it.value)}
          className={`rounded-xl px-3 py-1.5 text-sm font-semibold transition ${value === it.value ? "bg-gradient-to-l from-pink-500 to-violet-600 text-white shadow" : "muted hover:bg-violet-500/10"}`}
        >
          {it.label}
        </button>
      ))}
    </div>
  );
}

export function Spinner() {
  return <div className="h-5 w-5 animate-spin rounded-full border-2 border-violet-400 border-t-transparent" />;
}

export function Loading() {
  return (
    <div className="flex justify-center py-16">
      <Spinner />
    </div>
  );
}
