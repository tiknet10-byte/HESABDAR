import { ArrowDownUp, Download, Layers, Search, Sparkles, UserRound } from "lucide-react";
import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import JalaliPicker from "../components/JalaliPicker";
import { Card, Empty, Loading, Modal, Stat } from "../components/ui";
import { axis, grid, tooltipStyle } from "../lib/chart";
import { compactMoney, money, num } from "../lib/format";
import { useApi } from "../lib/hooks";
import { faDigits, formatJ, toGregorian, toJalali, toLocalIso } from "../lib/jalali";

const PERIODS = [
  { v: "month", l: "این ماه" }, { v: "3m", l: "۳ ماه اخیر" }, { v: "12m", l: "۱۲ ماه اخیر" },
  { v: "year", l: "امسال" }, { v: "all", l: "همهٔ سوابق" }, { v: "custom", l: "بازهٔ دلخواه" },
];
const SOURCES = [{ v: "all", l: "همه" }, { v: "new", l: "فقط این سیستم" }, { v: "old", l: "فقط سیستم قبلی" }];

const iso = (d: Date) => toLocalIso(d).slice(0, 10);
function jalaliMonthStart(monthsBack: number): string {
  const d = new Date();
  let [jy, jm] = toJalali(d.getFullYear(), d.getMonth() + 1, d.getDate());
  jm -= monthsBack;
  while (jm < 1) { jm += 12; jy -= 1; }
  const [gy, gm, gd] = toGregorian(jy, jm, 1);
  return iso(new Date(gy, gm - 1, gd));
}
function range(p: string, from: string, to: string): [string, string] {
  const today = iso(new Date());
  if (p === "month") return [jalaliMonthStart(0), today];
  if (p === "3m") return [jalaliMonthStart(2), today];
  if (p === "12m") return [jalaliMonthStart(11), today];
  if (p === "year") { const d = new Date(); const [jy] = toJalali(d.getFullYear(), d.getMonth() + 1, d.getDate()); const [gy, gm, gd] = toGregorian(jy, 1, 1); return [iso(new Date(gy, gm - 1, gd)), today]; }
  if (p === "custom") return [from.slice(0, 10), to.slice(0, 10)];
  return ["", today];
}

function csv(name: string, header: string[], rows: (string | number)[][]) {
  const body = [header, ...rows].map((r) => r.map((c) => `"${String(c ?? "").replace(/"/g, '""')}"`).join(",")).join("\n");
  const url = URL.createObjectURL(new Blob(["﻿" + body], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

const toman = (rial: number) => Math.round(rial / 10);

function ShareBar({ pct, color }: { pct: number; color?: string }) {
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 w-24 overflow-hidden rounded-full bg-zinc-500/15"><div className="h-full rounded-full" style={{ width: `${Math.min(100, pct)}%`, background: color ?? "var(--brand, #8b3fe6)" }} /></div>
      <span className="num text-xs">{faDigits(pct)}٪</span>
    </div>
  );
}

function MonthlyChart({ months, lines }: { months: any[]; lines: any[] }) {
  const data = months.map((m) => ({ label: faDigits(m.label), ...m.by_line }));
  return (
    <div className="h-80" dir="ltr">
      <ResponsiveContainer>
        <BarChart data={data} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
          <CartesianGrid stroke={grid()} vertical={false} />
          <XAxis dataKey="label" reversed tick={{ fill: axis(), fontSize: 11 }} axisLine={false} tickLine={false} interval="preserveStartEnd" />
          <YAxis orientation="right" tickFormatter={compactMoney} tick={{ fill: axis(), fontSize: 11 }} axisLine={false} tickLine={false} width={70} />
          <Tooltip {...tooltipStyle()} cursor={{ fill: "rgba(168,85,247,.06)" }} formatter={(v, n) => [money(Number(v)), String(n)]} />
          <Legend wrapperStyle={{ direction: "rtl", fontFamily: "Vazirmatn", fontSize: 12 }} />
          {lines.map((l) => <Bar key={l.name} dataKey={l.name} stackId="a" fill={l.color} maxBarSize={48} />)}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

type SortKey = "code" | "name" | "line" | "count" | "revenue" | "avg" | "last";

function ServicesTable({ rows }: { rows: any[] }) {
  const [sort, setSort] = useState<{ k: SortKey; desc: boolean }>({ k: "revenue", desc: true });
  const [q, setQ] = useState("");
  const shown = useMemo(() => {
    const norm = (s: string) => (s ?? "").replace(/ي/g, "ی").replace(/ك/g, "ک");
    const list = rows.filter((r) => !q || norm(`${r.code ?? ""} ${r.name} ${r.line}`).includes(norm(q)));
    const val = (r: any) => (sort.k === "code" ? Number(r.code ?? 1e9) : r[sort.k] ?? "");
    return [...list].sort((a, b) => {
      const x = val(a), y = val(b);
      const c = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), "fa");
      return sort.desc ? -c : c;
    });
  }, [rows, sort, q]);
  const th = (k: SortKey, label: string) => (
    <th className="cursor-pointer select-none whitespace-nowrap hover:text-violet-600" onClick={() => setSort({ k, desc: sort.k === k ? !sort.desc : k !== "name" && k !== "line" && k !== "code" })}>
      {label} {sort.k === k ? (sort.desc ? "▼" : "▲") : <ArrowDownUp size={11} className="inline opacity-40" />}
    </th>
  );
  return (
    <Card pad={false} title={<span className="flex items-center gap-2"><Sparkles size={18} className="text-pink-500" />درآمد خدمات ({faDigits(rows.length)})</span>}
      actions={<button className="btn btn-sm" onClick={() => csv("درآمد-خدمات.csv", ["کد", "خدمت", "لاین", "تعداد", "درآمد (تومان)", "میانگین (تومان)", "سهم ٪", "آخرین"],
        shown.map((r) => [r.code ?? "", r.name, r.line, r.count, toman(r.revenue), toman(r.avg), r.share, r.last ?? ""]))}><Download size={14} />اکسل</button>}>
      <div className="px-4 pb-3"><div className="relative w-72"><Search size={15} className="muted absolute right-3 top-1/2 -translate-y-1/2" /><input className="input pr-9" placeholder="جستجوی کد یا نام خدمت…" value={q} onChange={(e) => setQ(e.target.value)} /></div></div>
      <div className="max-h-[560px] overflow-auto">
        <table className="table">
          <thead className="sticky top-0 z-10" style={{ background: "var(--surface-solid)" }}><tr>{th("code", "کد")}{th("name", "خدمت")}{th("line", "لاین")}{th("count", "تعداد")}{th("revenue", "درآمد")}<th>سهم</th>{th("avg", "میانگین هر خدمت")}{th("last", "آخرین")}</tr></thead>
          <tbody>
            {shown.map((r, i) => (
              <tr key={i}>
                <td className="num font-bold text-violet-600 dark:text-violet-300">{r.code ?? "—"}</td>
                <td className="font-semibold">{r.name}{r.old > 0 && r.old === r.revenue && <span className="badge mr-2 bg-sky-500/10 text-[10px] text-sky-600">سیستم قبلی</span>}
                  {r.archived && <span className="badge mr-1 bg-slate-500/15 text-[10px] text-slate-500" title="این خدمت حذف (بایگانی) شده؛ در «تنظیمات ← لاین‌ها و خدمات ← بایگانی‌شده» قابل بازگردانی یا ادغام است">بایگانی‌شده</span>}</td>
                <td className="muted text-xs">{r.line}</td>
                <td className="num">{num(r.count)}</td>
                <td className="num font-bold">{money(r.revenue)}</td>
                <td><ShareBar pct={r.share} /></td>
                <td className="num">{money(r.avg)}</td>
                <td className="num text-xs">{r.last ? formatJ(r.last + "T12:00", false) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function StaffDetail({ id, start, end }: { id: number; start: string; end: string }) {
  const [page, setPage] = useState(0);
  const PAGE = 50;
  const qs = new URLSearchParams({ ...(start ? { start } : {}), ...(end ? { end } : {}), limit: String(PAGE), offset: String(page * PAGE) });
  const { data } = useApi<any>(`/api/reports/revenue/staff/${id}?${qs}`, [qs.toString()]);
  if (!data) return <Loading />;
  const t = data.totals;
  const pages = Math.max(1, Math.ceil(data.records_total / PAGE));
  const chart = data.months.map((m: any) => ({ label: faDigits(m.label), value: m.total }));
  return (
    <div className="space-y-4 text-sm">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <div className="rounded-2xl bg-pink-500/10 p-3"><div className="muted text-xs">کل درآمد</div><div className="num font-extrabold">{money(t.revenue)}</div></div>
        <div className="rounded-2xl bg-violet-500/10 p-3"><div className="muted text-xs">تعداد خدمت</div><div className="num font-extrabold">{num(t.count)}</div></div>
        <div className="rounded-2xl bg-emerald-500/10 p-3"><div className="muted text-xs">میانگین ماهانه</div><div className="num font-extrabold">{money(chart.length ? Math.round(t.revenue / chart.length) : 0)}</div></div>
        <div className="rounded-2xl bg-sky-500/10 p-3"><div className="muted text-xs">از سیستم قبلی</div><div className="num font-extrabold">{money(t.old)}</div></div>
      </div>
      {t.inferred_staff > 0 && <div className="rounded-xl bg-amber-500/10 px-3 py-2 text-xs">{faDigits(t.inferred_staff)} خدمت در سوابق پرسنل نداشت؛ چون این لاین فقط یک پرسنل دارد، به {data.staff} نسبت داده شد.</div>}
      {chart.length > 0 && (
        <div className="h-56" dir="ltr">
          <ResponsiveContainer>
            <BarChart data={chart} margin={{ top: 8, right: 8, left: 8 }}>
              <CartesianGrid stroke={grid()} vertical={false} />
              <XAxis dataKey="label" reversed tick={{ fill: axis(), fontSize: 10 }} axisLine={false} tickLine={false} />
              <YAxis orientation="right" tickFormatter={compactMoney} tick={{ fill: axis(), fontSize: 10 }} axisLine={false} tickLine={false} width={60} />
              <Tooltip {...tooltipStyle()} formatter={(v) => [money(Number(v)), "درآمد"]} />
              <Bar dataKey="value" fill="#8b3fe6" radius={[4, 4, 0, 0]} maxBarSize={40} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
      <div>
        <div className="mb-2 font-bold">خدمات این پرسنل</div>
        <div className="space-y-1">
          {data.services.map((s: any, i: number) => (
            <div key={i} className="flex items-center justify-between gap-2 rounded-xl px-3 py-1.5" style={{ background: "var(--surface)" }}>
              <span><span className="num ml-2 text-xs font-bold text-violet-600">{s.code ?? ""}</span>{s.name} <span className="muted text-xs">× {faDigits(s.count)}</span></span>
              <span className="flex items-center gap-3"><ShareBar pct={s.share} /><b className="num">{money(s.revenue)}</b></span>
            </div>
          ))}
        </div>
      </div>
      <div>
        <div className="mb-2 flex items-center justify-between">
          <span className="font-bold">ریز خدمات ({num(data.records_total)})</span>
          <button className="btn btn-sm" onClick={() => csv(`درآمد-${data.staff}.csv`, ["تاریخ", "مشتری", "کد مشتری", "خدمت", "مبلغ (تومان)", "منبع"],
            data.records.map((r: any) => [formatJ(r.at, false), r.customer, r.code ?? "", r.service, toman(r.amount), r.source === "old" ? "سیستم قبلی" : (r.ref ?? "")]))}><Download size={14} />اکسل این صفحه</button>
        </div>
        <div className="max-h-80 overflow-auto rounded-2xl border" style={{ borderColor: "var(--border)" }}>
          <table className="table text-xs">
            <thead><tr><th>تاریخ</th><th>مشتری</th><th>خدمت</th><th>مبلغ</th><th>منبع</th></tr></thead>
            <tbody>{data.records.map((r: any, i: number) => (
              <tr key={i}>
                <td className="num">{formatJ(r.at, false)}</td>
                <td>{r.customer} {r.code && <span className="num muted">({r.code})</span>}</td>
                <td>{r.service}</td>
                <td className="num font-semibold">{money(r.amount)}</td>
                <td>{r.source === "old" ? <span className="badge bg-sky-500/10 text-sky-600">سیستم قبلی</span> : <span className="num muted">{r.ref}</span>}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
        {pages > 1 && (
          <div className="mt-2 flex items-center justify-center gap-2">
            <button className="btn btn-sm" disabled={page === 0} onClick={() => setPage(page - 1)}>قبلی</button>
            <span className="num text-xs">صفحهٔ {num(page + 1)} از {num(pages)}</span>
            <button className="btn btn-sm" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>بعدی</button>
          </div>
        )}
      </div>
    </div>
  );
}

/** Revenue per Jalali month, per line, per staff member and per service (this system + previous software). */
export default function RevenueReport() {
  const [period, setPeriod] = useState("12m");
  const [source, setSource] = useState("all");
  const [lineId, setLineId] = useState(0);
  const [from, setFrom] = useState(toLocalIso(new Date()));
  const [to, setTo] = useState(toLocalIso(new Date()));
  const [staff, setStaff] = useState<any>(null);
  const [start, end] = range(period, from, to);
  const qs = new URLSearchParams({ source, ...(start ? { start } : {}), ...(end ? { end } : {}), ...(lineId ? { line_id: String(lineId) } : {}) });
  const { data } = useApi<any>(`/api/reports/revenue?${qs}`, [qs.toString()]);
  const allLines = useApi<any[]>("/api/lines?all=1").data ?? [];
  const t = data?.totals;
  const monthsN = data?.months.length || 0;

  return (
    <div className="space-y-5">
      <Card>
        <div className="flex flex-wrap items-center gap-2">
          {PERIODS.map((p) => (
            <button key={p.v} onClick={() => setPeriod(p.v)}
              className={`rounded-xl px-3 py-1.5 text-xs font-semibold ${period === p.v ? "bg-violet-600 text-white" : "border hover:bg-violet-500/10"}`}
              style={period === p.v ? {} : { borderColor: "var(--border)" }}>{p.l}</button>
          ))}
          {period === "custom" && <>
            <div className="w-44"><JalaliPicker pastOnly withTime={false} value={from} onChange={(v) => v && setFrom(v)} /></div>
            <span className="muted text-xs">تا</span>
            <div className="w-44"><JalaliPicker pastOnly withTime={false} value={to} onChange={(v) => v && setTo(v)} /></div>
          </>}
          <select className="input w-auto py-1.5 text-sm" value={lineId} onChange={(e) => setLineId(Number(e.target.value))} aria-label="لاین">
            <option value={0}>همهٔ لاین‌ها</option>
            {allLines.map((l) => <option key={l.id} value={l.id}>{l.code ? `${l.code} · ` : ""}{l.name}{l.is_active ? "" : " (بایگانی)"}</option>)}
          </select>
          <select className="input w-auto py-1.5 text-sm" value={source} onChange={(e) => setSource(e.target.value)} aria-label="منبع">
            {SOURCES.map((s) => <option key={s.v} value={s.v}>منبع: {s.l}</option>)}
          </select>
        </div>
        <div className="muted mt-2 text-xs">مبالغ = کل مبلغ خدمات (بدون کسر سهم پرسنل). فیش‌های منتقل‌شده از نرم‌افزار قبلی هم حساب می‌شوند. ماه‌ها شمسی‌اند.</div>
      </Card>

      {!data ? <Loading /> : t.count === 0 ? <Card><Empty text="در این بازه درآمدی ثبت نشده است" /></Card> : (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Stat label="کل درآمد" value={compactMoney(t.revenue)} hint={`${num(t.count)} خدمت`} tone="pink" />
            <Stat label="میانگین ماهانه" value={compactMoney(monthsN ? t.revenue / monthsN : 0)} hint={`${num(monthsN)} ماه`} tone="violet" />
            <Stat label="میانگین هر خدمت" value={compactMoney(t.count ? t.revenue / t.count : 0)} tone="emerald" />
            <Stat label="از سیستم قبلی / این سیستم" value={`${compactMoney(t.old)} / ${compactMoney(t.new)}`} tone="sky" />
          </div>

          <Card title={<span className="flex items-center gap-2"><Layers size={18} className="text-violet-500" />درآمد ماهانهٔ لاین‌ها</span>}>
            <MonthlyChart months={data.months} lines={data.lines} />
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card pad={false} title="درآمد هر لاین"
              actions={<button className="btn btn-sm" onClick={() => csv("درآمد-لاین‌ها.csv", ["کد", "لاین", ...data.months.map((m: any) => m.label), "جمع (تومان)", "تعداد", "سهم ٪"],
                data.lines.map((l: any) => [l.code ?? "", l.name, ...l.months.map(toman), toman(l.revenue), l.count, l.share]))}><Download size={14} />اکسل ماه‌به‌ماه</button>}>
              <table className="table">
                <thead><tr><th>لاین</th><th>درآمد</th><th>سهم</th><th>تعداد</th><th>میانگین</th></tr></thead>
                <tbody>{data.lines.map((l: any) => (
                  <tr key={l.id} className="cursor-pointer" title="فقط همین لاین" onClick={() => setLineId(lineId === l.id ? 0 : l.id)}>
                    <td className="font-semibold"><span className="ml-2 inline-block h-2.5 w-2.5 rounded-full" style={{ background: l.color }} />{l.code && <span className="num muted ml-1 text-xs">{l.code}</span>}{l.name}</td>
                    <td className="num font-bold">{money(l.revenue)}</td>
                    <td><ShareBar pct={l.share} color={l.color} /></td>
                    <td className="num">{num(l.count)}</td>
                    <td className="num text-xs">{money(l.avg)}</td>
                  </tr>
                ))}</tbody>
              </table>
            </Card>
            <Card pad={false} title={<span className="flex items-center gap-2"><UserRound size={18} className="text-emerald-500" />درآمد هر پرسنل</span>}
              actions={<button className="btn btn-sm" onClick={() => csv("درآمد-پرسنل.csv", ["پرسنل", "لاین", ...data.months.map((m: any) => m.label), "جمع (تومان)", "تعداد", "سهم ٪"],
                data.staff.map((p: any) => [p.name, p.line ?? "", ...p.months.map(toman), toman(p.revenue), p.count, p.share]))}><Download size={14} />اکسل ماه‌به‌ماه</button>}>
              <table className="table">
                <thead><tr><th>پرسنل</th><th>درآمد</th><th>سهم</th><th>تعداد</th><th>آخرین</th></tr></thead>
                <tbody>{data.staff.map((p: any) => (
                  <tr key={p.id} className="cursor-pointer" title="جزئیات درآمد" onClick={() => setStaff(p)}>
                    <td><div className="font-semibold">{p.name}</div>{p.line && <div className="muted text-xs">{p.line}</div>}</td>
                    <td className="num font-bold">{money(p.revenue)}</td>
                    <td><ShareBar pct={p.share} /></td>
                    <td className="num">{num(p.count)}</td>
                    <td className="num text-xs">{p.last ? formatJ(p.last + "T12:00", false) : "—"}</td>
                  </tr>
                ))}</tbody>
              </table>
              <div className="muted px-4 pb-3 text-[11px]">برای دیدن جزئیات (ماه‌به‌ماه، خدمات و ریز فیش‌ها) روی هر پرسنل کلیک کنید. سهم پرسنل در بخش «پرسنل و سهم‌ها» است.</div>
            </Card>
          </div>

          <ServicesTable rows={data.services} />
        </>
      )}
      <Modal open={!!staff} onClose={() => setStaff(null)} title={`درآمد ${staff?.name ?? ""}`} wide>
        {staff && <StaffDetail id={staff.id} start={start} end={end} />}
      </Modal>
    </div>
  );
}
