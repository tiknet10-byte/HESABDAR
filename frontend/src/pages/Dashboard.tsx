import { AlertTriangle, Banknote, HandCoins, Lightbulb, Receipt, ScanLine, TrendingUp, Users } from "lucide-react";
import { Link } from "react-router-dom";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, Loading, PageHeader, Stat } from "../components/ui";
import { axis, brand, colorMap, grid, tooltipStyle } from "../lib/chart";
import { cmoney, compactMoney, jshort, money, num } from "../lib/format";
import { useApi, useAuth } from "../lib/hooks";

export default function Dashboard() {
  const { user } = useAuth();
  const { data } = useApi<any>("/api/dashboard");
  const lines = useApi<any[]>("/api/lines");
  if (!data) return <Loading />;
  const t = data.today;
  const m = data.month;
  const lineColor = colorMap((lines.data ?? []).map((l) => l.name));

  return (
    <div className="space-y-6">
      <PageHeader title={`سلام ${user?.full_name || ""} 🌸`} subtitle="خلاصه وضعیت امروز و ۳۰ روز اخیر سالن" icon={<TrendingUp size={22} />}
        actions={<><Link to="/invoices?new=1" className="btn btn-primary"><Receipt size={16} />فاکتور جدید</Link><Link to="/deposits?new=1" className="btn"><HandCoins size={16} />ثبت بیعانه</Link></>} />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="فروش امروز" value={cmoney(t.revenue)} hint={`${num(t.invoice_count)} فاکتور`} icon={<Receipt size={20} />} tone="pink" />
        <Stat label="دریافتی امروز" value={cmoney(t.cash_in)} hint={`بیعانه: ${compactMoney(t.deposits_received)}`} icon={<Banknote size={20} />} tone="emerald" />
        <Stat label="بیعانه‌های باز" value={cmoney(t.deposits_held)} hint={`${num(t.deposits_held_count)} مورد`} icon={<HandCoins size={20} />} tone="amber" />
        <Stat label="مشتریان امروز" value={num(t.customers_served)} hint={`${num(t.new_customers)} مشتری جدید`} icon={<Users size={20} />} tone="sky" />
      </div>

      {(data.reconciliation.receipts_mismatch > 0 || data.alerts.length > 0) && (
        <Card title={<span className="flex items-center gap-2"><AlertTriangle size={18} className="text-rose-500" />هشدارها</span>}
          actions={<Link to="/reconciliation" className="btn btn-sm">بررسی</Link>}>
          <div className="space-y-2">
            {data.alerts.map((a: any) => (
              <div key={a.id} className={`rounded-2xl px-4 py-3 text-sm ${a.level === "danger" ? "bg-rose-500/10" : "bg-amber-500/10"}`}>
                <div className="font-bold">{a.title}</div>
                <div className="muted mt-0.5">{a.message}</div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {m && (
        <>
          <div className="grid gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2" title="روند فروش ۳۰ روز اخیر" actions={<span className="muted text-sm">مجموع: <b className="num">{money(m.revenue)}</b></span>}>
              <div className="h-72" dir="ltr">
                <ResponsiveContainer>
                  <AreaChart data={data.series} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
                    <defs>
                      <linearGradient id="rev" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor={brand()} stopOpacity={0.35} />
                        <stop offset="100%" stopColor={brand()} stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid stroke={grid()} vertical={false} />
                    <XAxis dataKey="date" tickFormatter={jshort} tick={{ fill: axis(), fontSize: 11 }} axisLine={false} tickLine={false} minTickGap={24} reversed />
                    <YAxis orientation="right" tickFormatter={compactMoney} tick={{ fill: axis(), fontSize: 11 }} axisLine={false} tickLine={false} width={70} />
                    <Tooltip {...tooltipStyle()} labelFormatter={(d) => jshort(String(d))} formatter={(v) => [money(Number(v)), "فروش"]} />
                    <Area type="monotone" dataKey="revenue" stroke={brand()} strokeWidth={2} fill="url(#rev)" activeDot={{ r: 5 }} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </Card>
            <Card title="سهم لاین‌ها از فروش">
              <div className="h-48" dir="ltr">
                <ResponsiveContainer>
                  <PieChart>
                    <Pie data={m.by_line} dataKey="value" nameKey="name" innerRadius={50} outerRadius={80} paddingAngle={2} stroke="var(--surface-solid)" strokeWidth={2}>
                      {m.by_line.map((x: any) => <Cell key={x.name} fill={lineColor(x.name)} />)}
                    </Pie>
                    <Tooltip {...tooltipStyle()} formatter={(v, n) => [money(Number(v)), String(n)]} />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <div className="mt-3 space-y-1.5">
                {m.by_line.map((x: any) => (
                  <div key={x.name} className="flex items-center justify-between text-sm">
                    <span className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full" style={{ background: lineColor(x.name) }} />{x.name}</span>
                    <span className="num font-semibold">{m.revenue ? Math.round((x.value / m.revenue) * 100) : 0}٪</span>
                  </div>
                ))}
              </div>
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <Stat label="فروش ۳۰ روز" value={compactMoney(m.revenue)} hint={`میانگین هر فاکتور ${compactMoney(m.avg_ticket)}`} />
            <Stat label="سود خالص ۳۰ روز" value={compactMoney(m.net_profit)} hint={`هزینه‌ها ${compactMoney(m.expenses)}`} />
            <Stat label="مشتریان بازگشتی" value={`${num(m.returning_customers)} از ${num(m.customers_served)}`} hint={`${num(m.new_customers)} مشتری جدید`} />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="دریافتی به تفکیک کارتخوان و کارت">
              <div style={{ height: Math.max(160, m.by_account.length * 44) }} dir="ltr">
                <ResponsiveContainer>
                  <BarChart data={m.by_account} layout="vertical" margin={{ left: 8, right: 8 }}>
                    <CartesianGrid stroke={grid()} horizontal={false} />
                    <XAxis type="number" tickFormatter={compactMoney} tick={{ fill: axis(), fontSize: 11 }} axisLine={false} tickLine={false} reversed />
                    <YAxis type="category" dataKey="name" orientation="right" width={130} tick={{ fill: axis(), fontSize: 12 }} axisLine={false} tickLine={false} />
                    <Tooltip {...tooltipStyle()} cursor={{ fill: "rgba(168,85,247,.06)" }} formatter={(v) => [money(Number(v)), "دریافتی"]} />
                    <Bar dataKey="value" fill={brand()} radius={[4, 0, 0, 4]} barSize={18} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Card>
            <Card title={<span className="flex items-center gap-2"><Lightbulb size={18} className="text-amber-500" />بینش‌های هوشمند</span>}
              actions={<Link to="/assistant" className="btn btn-sm">تحلیل عمیق‌تر با AI</Link>}>
              <div className="space-y-2">
                {data.insights.length === 0 && <div className="muted text-sm">هنوز داده کافی برای تحلیل وجود ندارد.</div>}
                {data.insights.map((x: any, i: number) => (
                  <div key={i} className={`rounded-2xl px-4 py-3 text-sm ${x.level === "danger" ? "bg-rose-500/10" : x.level === "warning" ? "bg-amber-500/10" : x.level === "success" ? "bg-emerald-500/10" : "bg-violet-500/10"}`}>{x.text}</div>
                ))}
                <Link to="/reconciliation" className="flex items-center justify-between rounded-2xl px-4 py-3 text-sm hover:bg-violet-500/10">
                  <span className="flex items-center gap-2"><ScanLine size={16} />رسیدهای در انتظار بانک: <b>{num(data.reconciliation.receipts_pending)}</b></span>
                  <span className="muted">تراکنش تطبیق‌نشده: {num(data.reconciliation.bank_unmatched)}</span>
                </Link>
              </div>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
