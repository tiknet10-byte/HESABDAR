// Money is stored in Rial. The UI shows Toman by default.
let unit: "toman" | "rial" = "toman";
export function setUnit(u: "toman" | "rial") {
  unit = u;
}
export const unitLabel = () => (unit === "toman" ? "تومان" : "ریال");

const nf = new Intl.NumberFormat("fa-IR");
export const num = (n: number | null | undefined) => nf.format(Math.round(n ?? 0));
export const money = (rial: number | null | undefined, withUnit = true) =>
  `${nf.format(Math.round((rial ?? 0) / (unit === "toman" ? 10 : 1)))}${withUnit ? " " + unitLabel() : ""}`;
export const compactMoney = (rial: number) => {
  const v = (rial ?? 0) / (unit === "toman" ? 10 : 1);
  if (Math.abs(v) >= 1e9) return `${nf.format(+(v / 1e9).toFixed(1))} میلیارد`;
  if (Math.abs(v) >= 1e6) return `${nf.format(+(v / 1e6).toFixed(1))} میلیون`;
  if (Math.abs(v) >= 1e3) return `${nf.format(+(v / 1e3).toFixed(0))} هزار`;
  return nf.format(v);
};
/** user types Toman (or Rial) -> Rial for the API */
export const toRial = (v: string | number) => {
  const s = String(v).replace(/[۰-۹]/g, (d) => String("۰۱۲۳۴۵۶۷۸۹".indexOf(d))).replace(/[^\d]/g, "");
  return (parseInt(s || "0", 10) || 0) * (unit === "toman" ? 10 : 1);
};
export const fromRial = (rial: number) => Math.round((rial ?? 0) / (unit === "toman" ? 10 : 1));

const df = new Intl.DateTimeFormat("fa-IR-u-ca-persian", { year: "numeric", month: "2-digit", day: "2-digit" });
const dtf = new Intl.DateTimeFormat("fa-IR-u-ca-persian", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
const sdf = new Intl.DateTimeFormat("fa-IR-u-ca-persian", { month: "short", day: "numeric" });
const wdf = new Intl.DateTimeFormat("fa-IR-u-ca-persian", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
const parse = (d: string | Date) => (typeof d === "string" ? new Date(d.length === 10 ? d + "T12:00:00" : d) : d);
export const jdate = (d?: string | Date | null) => (d ? df.format(parse(d)) : "—");
export const jdatetime = (d?: string | Date | null) => (d ? dtf.format(parse(d)) : "—");
export const jshort = (d?: string | Date | null) => (d ? sdf.format(parse(d)) : "");
export const jlong = (d: Date = new Date()) => wdf.format(d);

export const isoDate = (d: Date) => {
  const z = new Date(d.getTime() - d.getTimezoneOffset() * 60000);
  return z.toISOString().slice(0, 10);
};
export const daysAgo = (n: number) => isoDate(new Date(Date.now() - n * 86400000));

export const STATUS: Record<string, { label: string; cls: string }> = {
  held: { label: "باز", cls: "bg-amber-500/15 text-amber-600 dark:text-amber-400" },
  applied: { label: "تسویه‌شده", cls: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" },
  refunded: { label: "مسترد", cls: "bg-sky-500/15 text-sky-600 dark:text-sky-400" },
  forfeited: { label: "سوخت‌شده", cls: "bg-rose-500/15 text-rose-600 dark:text-rose-400" },
  issued: { label: "پرداخت‌نشده", cls: "bg-amber-500/15 text-amber-600 dark:text-amber-400" },
  partial: { label: "پرداخت جزئی", cls: "bg-orange-500/15 text-orange-600 dark:text-orange-400" },
  paid: { label: "تسویه", cls: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" },
  void: { label: "باطل", cls: "bg-zinc-500/15 text-zinc-500" },
  pending: { label: "در انتظار بانک", cls: "bg-amber-500/15 text-amber-600 dark:text-amber-400" },
  matched: { label: "تطبیق‌شده", cls: "bg-sky-500/15 text-sky-600 dark:text-sky-400" },
  registered: { label: "ثبت شد", cls: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" },
  mismatch: { label: "مغایرت", cls: "bg-rose-500/15 text-rose-600 dark:text-rose-400" },
  rejected: { label: "رد شده", cls: "bg-zinc-500/15 text-zinc-500" },
  unmatched: { label: "تطبیق‌نشده", cls: "bg-amber-500/15 text-amber-600 dark:text-amber-400" },
  ignored: { label: "نادیده", cls: "bg-zinc-500/15 text-zinc-500" },
  booked: { label: "رزرو", cls: "bg-violet-500/15 text-violet-600 dark:text-violet-400" },
  done: { label: "انجام شد", cls: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" },
  cancelled: { label: "لغو", cls: "bg-zinc-500/15 text-zinc-500" },
  no_show: { label: "عدم مراجعه", cls: "bg-rose-500/15 text-rose-600 dark:text-rose-400" },
  review: { label: "در انتظار بررسی", cls: "bg-amber-500/15 text-amber-600 dark:text-amber-400" },
  committed: { label: "ثبت شد", cls: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" },
  discarded: { label: "کنار گذاشته", cls: "bg-zinc-500/15 text-zinc-500" },
};

export const ACCOUNT_KINDS: Record<string, string> = { pos: "کارتخوان", card: "کارت بانکی", bank: "حساب بانکی", cash: "صندوق نقدی", gateway: "درگاه آنلاین" };
export const ROLES: Record<string, string> = {
  owner: "مالک", admin: "مدیر", accountant: "حسابدار", receptionist: "پذیرش", staff: "پرسنل", ai_agent: "عامل هوش مصنوعی",
};
export const CHANNELS: Record<string, string> = { sms: "پیامک", whatsapp: "واتساپ", instagram: "اینستاگرام", manual: "دستی" };
export const cmoney = (rial: number) => `${compactMoney(rial)} ${unitLabel()}`;
