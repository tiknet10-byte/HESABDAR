// Jalali (Shamsi) <-> Gregorian conversion (same algorithm as the backend).
export function toJalali(gy: number, gm: number, gd: number): [number, number, number] {
  const g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
  const gy2 = gm > 2 ? gy + 1 : gy;
  let days = 355666 + 365 * gy + Math.floor((gy2 + 3) / 4) - Math.floor((gy2 + 99) / 100) + Math.floor((gy2 + 399) / 400) + gd + g_d_m[gm - 1];
  let jy = -1595 + 33 * Math.floor(days / 12053);
  days %= 12053;
  jy += 4 * Math.floor(days / 1461);
  days %= 1461;
  if (days > 365) {
    jy += Math.floor((days - 1) / 365);
    days = (days - 1) % 365;
  }
  const jm = days < 186 ? 1 + Math.floor(days / 31) : 7 + Math.floor((days - 186) / 30);
  const jd = 1 + (days < 186 ? days % 31 : (days - 186) % 30);
  return [jy, jm, jd];
}

export function toGregorian(jy: number, jm: number, jd: number): [number, number, number] {
  jy += 1595;
  let days = -355668 + 365 * jy + Math.floor(jy / 33) * 8 + Math.floor(((jy % 33) + 3) / 4) + jd + (jm < 7 ? (jm - 1) * 31 : (jm - 7) * 30 + 186);
  let gy = 400 * Math.floor(days / 146097);
  days %= 146097;
  if (days > 36524) {
    gy += 100 * Math.floor(--days / 36524);
    days %= 36524;
    if (days >= 365) days++;
  }
  gy += 4 * Math.floor(days / 1461);
  days %= 1461;
  if (days > 365) {
    gy += Math.floor((days - 1) / 365);
    days = (days - 1) % 365;
  }
  let gd = days + 1;
  const leap = (gy % 4 === 0 && gy % 100 !== 0) || gy % 400 === 0;
  const md = [0, 31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  let gm = 1;
  for (; gm <= 12 && gd > md[gm]; gm++) gd -= md[gm];
  return [gy, gm, gd];
}

export const J_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"];
export const J_WEEKDAYS = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"];
export const J_WEEKDAYS_SHORT = ["ش", "ی", "د", "س", "چ", "پ", "ج"];

export function jMonthLength(jy: number, jm: number): number {
  if (jm <= 6) return 31;
  if (jm <= 11) return 30;
  const [gy, gm, gd] = toGregorian(jy + 1, 1, 1);
  const next = new Date(gy, gm - 1, gd);
  const [y2, m2, d2] = toGregorian(jy, 12, 1);
  return Math.round((next.getTime() - new Date(y2, m2 - 1, d2).getTime()) / 86400000);
}

/** index 0 = Saturday */
export const jWeekday = (d: Date) => (d.getDay() + 1) % 7;

export const faDigits = (s: string | number) => String(s).replace(/\d/g, (d) => "۰۱۲۳۴۵۶۷۸۹"[+d]);
export const pad = (n: number) => String(n).padStart(2, "0");

/** local "YYYY-MM-DDTHH:mm" (what the API expects) */
export const toLocalIso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
export const parseLocal = (s: string) => {
  const [date, time = "00:00"] = s.split("T");
  const [y, m, d] = date.split("-").map(Number);
  const [hh, mm] = time.split(":").map(Number);
  return new Date(y, m - 1, d, hh || 0, mm || 0);
};

export function formatJ(s: string | Date | null | undefined, withTime = true): string {
  if (!s) return "";
  const d = typeof s === "string" ? parseLocal(s) : s;
  const [jy, jm, jd] = toJalali(d.getFullYear(), d.getMonth() + 1, d.getDate());
  const date = `${J_WEEKDAYS[jWeekday(d)]} ${faDigits(jd)} ${J_MONTHS[jm - 1]} ${faDigits(jy)}`;
  return withTime ? `${date} · ${faDigits(pad(d.getHours()))}:${faDigits(pad(d.getMinutes()))}` : date;
}

/** compact: "دوشنبه ۱۳ مهر · ۱۴:۳۰" */
export function formatJShort(s: string): string {
  const d = parseLocal(s);
  const [, jm, jd] = toJalali(d.getFullYear(), d.getMonth() + 1, d.getDate());
  return `${J_WEEKDAYS[jWeekday(d)]} ${faDigits(jd)} ${J_MONTHS[jm - 1]} · ${faDigits(pad(d.getHours()))}:${faDigits(pad(d.getMinutes()))}`;
}
