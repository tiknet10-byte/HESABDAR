import { CalendarDays, ChevronLeft, ChevronRight, Clock, X } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { faDigits, formatJ, formatJShort, J_MONTHS, J_WEEKDAYS_SHORT, jMonthLength, pad, parseLocal, toGregorian, toJalali, toLocalIso } from "../lib/jalali";

type Props = {
  value: string; // local ISO "YYYY-MM-DDTHH:mm" or ""
  onChange: (v: string) => void;
  withTime?: boolean;
  placeholder?: string;
  minuteStep?: number;
  clearable?: boolean;
  futureOnly?: boolean; // disable past days and, for today, past hours/minutes (bookings)
};

const HOURS = Array.from({ length: 16 }, (_, i) => i + 7); // 07..22

/** Persian (Jalali) date & time picker. */
export default function JalaliPicker({ value, onChange, withTime = true, placeholder = "انتخاب تاریخ", minuteStep = 5, clearable, futureOnly }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const pop = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number; width: number } | null>(null);

  useLayoutEffect(() => {
    if (!open || !ref.current) return;
    const place = () => {
      const r = ref.current!.getBoundingClientRect();
      const width = Math.min(352, window.innerWidth - 16);
      const h = pop.current?.offsetHeight ?? 520;
      const below = window.innerHeight - r.bottom;
      const top = below >= h + 8 || r.top < h + 8 ? Math.min(r.bottom + 6, window.innerHeight - h - 8) : r.top - h - 6;
      const left = Math.min(Math.max(8, r.right - width), window.innerWidth - width - 8);
      setPos({ top: Math.max(8, top), left, width });
    };
    place();
    const t = requestAnimationFrame(place);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => { cancelAnimationFrame(t); window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true); };
  }, [open]);
  const current = value ? parseLocal(value) : null;
  const base = current ?? new Date();
  const [view, setView] = useState(() => {
    const [jy, jm] = toJalali(base.getFullYear(), base.getMonth() + 1, base.getDate());
    return { jy, jm };
  });

  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!ref.current?.contains(t) && !pop.current?.contains(t)) setOpen(false);
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open]);

  useEffect(() => {
    if (open && current) {
      const [jy, jm] = toJalali(current.getFullYear(), current.getMonth() + 1, current.getDate());
      setView({ jy, jm });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const today = new Date();
  const [ty, tm, td] = toJalali(today.getFullYear(), today.getMonth() + 1, today.getDate());
  const sel = current ? toJalali(current.getFullYear(), current.getMonth() + 1, current.getDate()) : null;

  const cells = useMemo(() => {
    const [gy, gm, gd] = toGregorian(view.jy, view.jm, 1);
    const first = (new Date(gy, gm - 1, gd).getDay() + 1) % 7; // 0 = Saturday
    const len = jMonthLength(view.jy, view.jm);
    return [...Array(first).fill(null), ...Array.from({ length: len }, (_, i) => i + 1)];
  }, [view]);

  const move = (delta: number) => {
    let m = view.jm + delta, y = view.jy;
    if (m < 1) { m = 12; y--; }
    if (m > 12) { m = 1; y++; }
    setView({ jy: y, jm: m });
  };

  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const dayIsPast = (jd: number) => {
    if (!futureOnly) return false;
    const [gy, gm, gd] = toGregorian(view.jy, view.jm, jd);
    return new Date(gy, gm - 1, gd).getTime() < startOfToday;
  };
  const selIsToday = !!current && new Date(current.getFullYear(), current.getMonth(), current.getDate()).getTime() === startOfToday;
  const hourIsPast = (h: number) => futureOnly && selIsToday && h < now.getHours();
  const minuteIsPast = (m: number) => futureOnly && selIsToday && !!current && current.getHours() === now.getHours() && m < now.getMinutes();
  // first allowed time today, rounded up to the minute step
  const nextFree = () => {
    const d = new Date(now.getTime() + minuteStep * 60000);
    return new Date(d.getFullYear(), d.getMonth(), d.getDate(), d.getHours(), Math.floor(d.getMinutes() / minuteStep) * minuteStep);
  };

  const pick = (jd: number) => {
    if (dayIsPast(jd)) return;
    const [gy, gm, gd] = toGregorian(view.jy, view.jm, jd);
    const h = current ? current.getHours() : withTime ? 10 : 12;
    const mi = current ? current.getMinutes() : 0;
    let d = new Date(gy, gm - 1, gd, h, mi);
    if (futureOnly && d.getTime() < now.getTime()) d = nextFree();
    onChange(toLocalIso(d));
    if (!withTime) setOpen(false);
  };
  const setTime = (h: number | null, m: number | null) => {
    const d = current ?? new Date();
    let nd = new Date(d.getFullYear(), d.getMonth(), d.getDate(), h ?? d.getHours(), m ?? d.getMinutes());
    if (futureOnly && nd.getTime() < now.getTime()) nd = nextFree();
    onChange(toLocalIso(nd));
  };
  const minutes = Array.from({ length: 60 / minuteStep }, (_, i) => i * minuteStep);

  return (
    <div className="relative" ref={ref}>
      <button type="button" onClick={() => setOpen(!open)} className="input flex items-center justify-between gap-2 text-right">
        <span className={current ? "font-semibold" : "muted"}>{current ? formatJ(current, withTime) : placeholder}</span>
        <span className="flex items-center gap-1">
          {clearable && current && <X size={16} className="muted hover:text-rose-500" onClick={(e) => { e.stopPropagation(); onChange(""); }} />}
          <CalendarDays size={18} className="text-violet-500" />
        </span>
      </button>
      {open && createPortal(
        <div ref={pop} dir="rtl" className="fade-up fixed z-[70] max-h-[calc(100vh-16px)] overflow-y-auto rounded-3xl border shadow-2xl shadow-violet-500/25"
          style={{ background: "var(--surface-solid)", borderColor: "var(--border)", top: pos?.top ?? -9999, left: pos?.left ?? 0, width: pos?.width ?? 352 }}>
          <div className="flex items-center justify-between bg-gradient-to-l from-pink-500 to-violet-600 px-4 py-3 text-white">
            <button type="button" className="rounded-xl p-1.5 hover:bg-white/20" onClick={() => move(-1)} aria-label="ماه قبل"><ChevronRight size={20} /></button>
            <div className="text-center">
              <div className="text-lg font-extrabold">{J_MONTHS[view.jm - 1]}</div>
              <div className="text-xs opacity-90">{faDigits(view.jy)}</div>
            </div>
            <button type="button" className="rounded-xl p-1.5 hover:bg-white/20" onClick={() => move(1)} aria-label="ماه بعد"><ChevronLeft size={20} /></button>
          </div>
          <div className="p-3">
            <div className="mb-1 grid grid-cols-7 text-center text-xs font-bold">
              {J_WEEKDAYS_SHORT.map((w, i) => <div key={w} className={`py-1 ${i === 6 ? "text-rose-500" : "muted"}`}>{w}</div>)}
            </div>
            <div className="grid grid-cols-7 gap-1">
              {cells.map((d, i) => {
                if (d === null) return <div key={`e${i}`} />;
                const isSel = sel && sel[0] === view.jy && sel[1] === view.jm && sel[2] === d;
                const isToday = ty === view.jy && tm === view.jm && td === d;
                const friday = i % 7 === 6;
                const past = dayIsPast(d);
                return (
                  <button type="button" key={d} onClick={() => pick(d)} disabled={past} title={past ? "گذشته" : undefined}
                    className={`aspect-square rounded-xl text-sm font-semibold transition ${past ? "cursor-not-allowed opacity-25 line-through" : isSel ? "bg-gradient-to-br from-pink-500 to-violet-600 text-white shadow-md shadow-violet-500/30" : isToday ? "ring-2 ring-violet-400" : "hover:bg-violet-500/10"} ${!isSel && friday && !past ? "text-rose-500" : ""}`}>
                    {faDigits(d)}
                  </button>
                );
              })}
            </div>
            <div className="mt-2 flex justify-between">
              <button type="button" className="btn btn-ghost btn-sm text-violet-600 dark:text-violet-300" onClick={() => {
                const n = new Date();
                const [jy, jm] = toJalali(n.getFullYear(), n.getMonth() + 1, n.getDate());
                setView({ jy, jm });
                onChange(toLocalIso(futureOnly && withTime ? nextFree() : withTime ? new Date(n.getFullYear(), n.getMonth(), n.getDate(), n.getHours(), Math.floor(n.getMinutes() / minuteStep) * minuteStep) : new Date(n.getFullYear(), n.getMonth(), n.getDate(), 12, 0)));
              }}>امروز</button>
              <button type="button" className="btn btn-sm btn-primary" onClick={() => setOpen(false)}>تأیید</button>
            </div>
          </div>
          {withTime && (
            <div className="border-t p-3" style={{ borderColor: "var(--border)" }}>
              <div className="muted mb-2 flex items-center gap-1 text-xs font-bold"><Clock size={14} />ساعت</div>
              <div className="grid grid-cols-8 gap-1">
                {HOURS.map((h) => (
                  <button type="button" key={h} onClick={() => setTime(h, null)} disabled={hourIsPast(h)}
                    className={`rounded-lg py-1.5 text-sm font-semibold ${hourIsPast(h) ? "cursor-not-allowed opacity-25" : current?.getHours() === h ? "bg-violet-600 text-white" : "hover:bg-violet-500/10"}`}>{faDigits(pad(h))}</button>
                ))}
              </div>
              <div className="muted mb-2 mt-3 text-xs font-bold">دقیقه</div>
              <div className="grid grid-cols-6 gap-1">
                {minutes.map((m) => (
                  <button type="button" key={m} onClick={() => setTime(null, m)} disabled={minuteIsPast(m)}
                    className={`rounded-lg py-1.5 text-sm font-semibold ${minuteIsPast(m) ? "cursor-not-allowed opacity-25" : current?.getMinutes() === m ? "bg-violet-600 text-white" : "hover:bg-violet-500/10"}`}>{faDigits(pad(m))}</button>
                ))}
              </div>
            </div>
          )}
        </div>,
        document.body,
      )}
    </div>
  );
}

/** Suggested free appointment times as clickable chips. */
export function SlotChips({ slots, value, onPick }: { slots: { start_at: string; staff_id?: number | null }[]; value?: string; onPick: (s: { start_at: string; staff_id?: number | null }) => void }) {
  if (!slots.length) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {slots.map((s) => (
        <button type="button" key={s.start_at} onClick={() => onPick(s)}
          className={`rounded-2xl border px-3 py-2 text-xs font-semibold transition ${value === s.start_at ? "border-transparent bg-gradient-to-l from-pink-500 to-violet-600 text-white" : "hover:bg-violet-500/10"}`}
          style={value === s.start_at ? {} : { borderColor: "var(--border)" }}>
          {formatJShort(s.start_at)}
        </button>
      ))}
    </div>
  );
}
