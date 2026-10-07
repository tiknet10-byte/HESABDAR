import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { type ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";
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

/** A small "!" button that explains a card. Opens on hover (desktop) or tap/click (mobile); the bubble is
 * rendered at <body> so cards with overflow-hidden don't clip it. */
export function HelpTip({ children, title }: { children: ReactNode; title?: string }) {
  const [hover, setHover] = useState(false);
  const [pinned, setPinned] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  const bubble = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number; width: number; above: boolean } | null>(null);
  const open = hover || pinned;
  useLayoutEffect(() => {
    if (!open || !btn.current) return;
    const place = () => {
      const r = btn.current!.getBoundingClientRect();
      const width = Math.min(300, window.innerWidth - 16);
      const left = Math.max(8, Math.min(r.left + r.width / 2 - width / 2, window.innerWidth - width - 8));
      const above = r.bottom + 180 > window.innerHeight && r.top > 200;
      setPos({ top: above ? r.top - 8 : r.bottom + 8, left, width, above });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open]);
  useEffect(() => {
    if (!pinned) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !btn.current?.contains(e.target as Node) && !bubble.current?.contains(e.target as Node)) setPinned(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [pinned]);
  return (
    <>
      <button
        ref={btn}
        type="button"
        aria-label="راهنما"
        aria-expanded={open}
        onClick={(e) => { e.stopPropagation(); setPinned((p) => !p); }}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        className={`inline-grid h-5 w-5 shrink-0 place-items-center rounded-full border text-[11px] font-black leading-none transition ${open ? "border-violet-500 bg-violet-500 text-white" : "border-violet-400/60 text-violet-500 hover:bg-violet-500/10"}`}
      >
        !
      </button>
      {open && pos && createPortal(
        <div
          ref={bubble}
          role="tooltip"
          onMouseEnter={() => setHover(true)}
          onMouseLeave={() => setHover(false)}
          className="fade-up fixed z-[60] rounded-2xl border p-3 text-xs leading-6 shadow-xl"
          style={{ top: pos.top, left: pos.left, width: pos.width, transform: pos.above ? "translateY(-100%)" : undefined, background: "var(--surface-solid)", borderColor: "var(--border)" }}
        >
          {title && <div className="mb-1 font-bold text-violet-600 dark:text-violet-300">{title}</div>}
          <div className="space-y-1">{children}</div>
        </div>,
        document.body,
      )}
    </>
  );
}

export function Stat({ label, value, hint, icon, tone = "violet", help, onClick }: { label: string; value: ReactNode; hint?: ReactNode; icon?: ReactNode; tone?: "violet" | "pink" | "emerald" | "amber" | "sky"; help?: ReactNode; onClick?: () => void }) {
  const tones: Record<string, string> = {
    violet: "from-violet-500 to-purple-600 shadow-violet-500/30",
    pink: "from-pink-500 to-rose-500 shadow-pink-500/30",
    emerald: "from-emerald-500 to-teal-500 shadow-emerald-500/30",
    amber: "from-amber-400 to-orange-500 shadow-amber-500/30",
    sky: "from-sky-500 to-indigo-500 shadow-sky-500/30",
  };
  return (
    <div className={`card fade-up relative overflow-hidden p-5 ${onClick ? "cursor-pointer transition hover:-translate-y-0.5" : ""}`} onClick={onClick}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="muted flex items-center gap-1.5 text-xs font-medium">{label}{help && <HelpTip title={label}>{help}</HelpTip>}</div>
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

const openModals: object[] = []; // stack of open windows: Escape closes only the one on top

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  const me = useRef({});
  useEffect(() => {
    if (!open) return;
    const token = me.current;
    openModals.push(token);
    const h = (e: KeyboardEvent) => e.key === "Escape" && openModals[openModals.length - 1] === token && onClose();
    window.addEventListener("keydown", h);
    return () => {
      window.removeEventListener("keydown", h);
      openModals.splice(openModals.indexOf(token), 1);
    };
  }, [open, onClose]);
  if (!open) return null;
  // rendered at <body>: a parent with backdrop-filter/transform (e.g. .card) would otherwise trap a fixed overlay inside it
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-0 backdrop-blur-sm sm:items-center sm:p-4" onClick={(e) => { e.stopPropagation(); onClose(); }}>
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
    </div>,
    document.body,
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
