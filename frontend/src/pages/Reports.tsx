import { BarChart3, Download, TrendingUp } from "lucide-react";
import { useState } from "react";
import { Area, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, Card, Loading, PageHeader, Stat, Tabs } from "../components/ui";
import { download } from "../lib/api";
import { axis, brand, colorMap, grid, series, tooltipStyle } from "../lib/chart";
import { compactMoney, daysAgo, isoDate, jshort, money, num } from "../lib/format";
import { useApi } from "../lib/hooks";

const RANGES = { "7": "۷ روز", "30": "۳۰ روز", "90": "۳ ماه", "365": "یک سال" } as const;

function HBar({ data, color }: { data: { name: string; value: number }[]; color: (n: string) => string }) {
  return (
    <div style={{ height: Math.max(140, data.length * 40) }} dir="ltr">
      <ResponsiveContainer>
        <BarChart data={data} layout="vertical" margin={{ left: 8, right: 8 }}>
          <CartesianGrid stroke={grid()} horizontal={false} />
          <XAxis type="number" tickFormatter={compactMoney} tick={{ fill: axis(), fontSize: 11 }} axisLine={false} tickLine={false} reversed />
          <YAxis type="category" dataKey="name" orientation="right" width={120} tick={{ fill: axis(), fontSize: 12 }} axisLine={false} tickLine={false} />
          <Tooltip {...tooltipStyle()} cursor={{ fill: "rgba(168,85,247,.06)" }} formatter={(v) => [money(Number(v)), "مبلغ"]} />
          <Bar dataKey="value" radius={[4, 0, 0, 4]} barSize={16}>
            {data.map((x) => <Cell key={x.name} fill={color(x.name)} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function Reports() {
  const [range, setRange] = useState<keyof typeof RANGES>("30");
  const start = daysAgo(Number(range) - 1);
  const end = isoDate(new Date());
  const s = useApi<any>(`/api/reports/summary?start=${start}&end=${end}`, [range]);
  const daily = useApi<any[]>(`/api/reports/daily?start=${start}&end=${end}`, [range]);
  const fc = useApi<any>("/api/reports/forecast?days=30");
  const ins = useApi<any>("/api/reports/insights");
  const lines = useApi<any[]>("/api/lines");
  const lineColor = colorMap((lines.data ?? []).map((l) => l.name));
  const d = s.data;

  const forecastData = [
    ...(daily.data ?? []).slice(-30).map((x) => ({ date: x.date, actual: x.revenue })),
    ...((fc.data?.points ?? []) as any[]).map((p) => ({ date: p.date, forecast: p.revenue, band: [p.low, p.high] })),
  ];

  return (
    <div className="space-y-5">
      <PageHeader title="گزارش، تحلیل و پیش‌بینی" subtitle="تحلیل فروش به تفکیک لاین، خدمت، پرسنل و حساب؛ پیش‌بینی درآمد" icon={<BarChart3 size={22} />}
        actions={<><Tabs value={range} onChange={setRange} items={Object.entries(RANGES).map(([value, label]) => ({ value: value as keyof typeof RANGES, label }))} />
          <button className="btn" onClick={() => download(`/api/reports/export.csv?start=${start}&end=${end}`, "sales.csv")}><Download size={16} />اکسل</button></>} />
      {!d ? <Loading /> : (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Stat label="فروش" value={compactMoney(d.revenue)} hint={`${num(d.invoice_count)} فاکتور`} tone="pink" />
            <Stat label="سود خالص" value={compactMoney(d.net_profit)} hint={`هزینه ${compactMoney(d.expenses)}`} tone="emerald" />
            <Stat label="میانگین هر فاکتور" value={compactMoney(d.avg_ticket)} hint={`تخفیف‌ها ${compactMoney(d.discounts)}`} />
            <Stat label="عدم مراجعه" value={`${num(Math.round(d.appointments.no_show_rate * 100))}٪`} hint={`${num(d.appointments.total)} نوبت`} tone="amber" />
          </div>

          <Card title={<span className="flex items-center gap-2"><TrendingUp size={18} />فروش واقعی و پیش‌بینی ۳۰ روز آینده</span>}
            actions={fc.data?.points?.length ? <span className="muted text-sm">پیش‌بینی: <b className="num">{money(fc.data.total)}</b>{fc.data.change_vs_last_period != null && <span className={fc.data.change_vs_last_period >= 0 ? "text-emerald-600" : "text-rose-600"}> ({fc.data.change_vs_last_period >= 0 ? "+" : ""}{Math.round(fc.data.change_vs_last_period * 100)}٪)</span>}</span> : null}>
            {fc.data && !fc.data.points.length ? <div className="muted text-sm">{fc.data.message}</div> : (
              <>
                <div className="h-80" dir="ltr">
                  <ResponsiveContainer>
                    <ComposedChart data={forecastData} margin={{ top: 8, right: 8, left: 8 }}>
                      <CartesianGrid stroke={grid()} vertical={false} />
                      <XAxis dataKey="date" tickFormatter={jshort} tick={{ fill: axis(), fontSize: 11 }} axisLine={false} tickLine={false} minTickGap={28} reversed />
                      <YAxis orientation="right" tickFormatter={compactMoney} tick={{ fill: axis(), fontSize: 11 }} axisLine={false} tickLine={false} width={70} />
                      <Tooltip {...tooltipStyle()} labelFormatter={(x) => jshort(String(x))}
                        formatter={(v: any, n) => [Array.isArray(v) ? `${compactMoney(v[0])} تا ${compactMoney(v[1])}` : money(Number(v)), n === "actual" ? "واقعی" : n === "forecast" ? "پیش‌بینی" : "بازه اطمینان"]} />
                      <Area dataKey="band" stroke="none" fill={series(0)} fillOpacity={0.12} />
                      <Line dataKey="actual" stroke={brand()} strokeWidth={2} dot={false} />
                      <Line dataKey="forecast" stroke={series(0)} strokeWidth={2} strokeDasharray="5 4" dot={false} />
                    </ComposedChart>
                  </ResponsiveContainer>
                </div>
                <div className="muted mt-2 flex flex-wrap gap-4 text-xs">
                  <span className="flex items-center gap-1.5"><span className="h-0.5 w-5" style={{ background: brand() }} />فروش واقعی</span>
                  <span className="flex items-center gap-1.5"><span className="h-0.5 w-5 border-t-2 border-dashed" style={{ borderColor: series(0) }} />پیش‌بینی</span>
                  <span className="flex items-center gap-1.5"><span className="h-3 w-5 rounded" style={{ background: series(0), opacity: 0.15 }} />بازه اطمینان ۸۰٪</span>
                  {fc.data?.booked_appointments > 0 && <span>نوبت‌های رزروشده: {num(fc.data.booked_appointments)} ({compactMoney(fc.data.booked_value)})</span>}
                </div>
              </>
            )}
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="فروش به تفکیک لاین"><HBar data={d.by_line} color={lineColor} /></Card>
            <Card title="دریافتی به تفکیک کارتخوان/کارت"><HBar data={d.by_account} color={() => brand()} /></Card>
            <Card title="عملکرد پرسنل"><HBar data={d.by_staff} color={() => series(2)} /></Card>
            <Card title="هزینه‌ها به تفکیک دسته"><HBar data={d.expense_by_category} color={() => series(1)} /></Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="پرفروش‌ترین خدمات" pad={false}>
              <table className="table">
                <thead><tr><th>خدمت</th><th>تعداد</th><th>فروش</th></tr></thead>
                <tbody>{d.top_services.map((x: any) => <tr key={x.name}><td className="font-semibold">{x.name}</td><td className="num">{num(x.count)}</td><td className="num">{money(x.revenue)}</td></tr>)}</tbody>
              </table>
            </Card>
            <Card title="بخش‌بندی مشتریان (RFM)" pad={false}>
              <div className="max-h-96 overflow-y-auto">
                <table className="table">
                  <thead><tr><th>مشتری</th><th>مراجعه</th><th>مجموع</th><th>آخرین</th><th>بخش</th></tr></thead>
                  <tbody>{(ins.data?.customers ?? []).map((c: any) => (
                    <tr key={c.customer_id}><td className="font-semibold">{c.name}</td><td className="num">{num(c.visits)}</td><td className="num">{compactMoney(c.total)}</td><td className="num muted">{num(c.last_visit_days)} روز</td>
                      <td><Badge>{c.segment}</Badge></td></tr>
                  ))}</tbody>
                </table>
              </div>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
