import { AlertTriangle, Banknote, CalendarClock, CalendarX, ClipboardList, Clock, HandCoins, Lightbulb, Receipt, ScanLine, TrendingDown, TrendingUp, Users } from "lucide-react";
import { Link } from "react-router-dom";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, Card, Loading, Modal, PageHeader, Stat, Tabs } from "../components/ui";
import { useState } from "react";
import { axis, brand, colorMap, grid, tooltipStyle } from "../lib/chart";
import { cmoney, compactMoney, jshort, money, num } from "../lib/format";
import { useApi, useAuth } from "../lib/hooks";
import { faDigits, formatJShort } from "../lib/jalali";

function growth(now: number, before: number) {
  if (!before) return null;
  const pct = Math.round(((now - before) / before) * 100);
  return { pct, up: pct >= 0 };
}

const MONEY_KIND: Record<string, { label: string; cls: string }> = {
  payment: { label: "دریافت فاکتور", cls: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" },
  deposit: { label: "بیعانه", cls: "bg-violet-500/10 text-violet-700 dark:text-violet-300" },
  refund: { label: "استرداد", cls: "bg-rose-500/10 text-rose-700 dark:text-rose-300" },
  void_cancel: { label: "لغو (فاکتور باطل)", cls: "bg-zinc-500/10 text-zinc-500" },
};

/** What is behind the 'today' cards: invoices, money movements and customers of the day. */
function DayDetails({ initial }: { initial: "sales" | "money" | "customers" }) {
  const [tab, setTab] = useState(initial);
  const { data } = useApi<any>("/api/dashboard/day");
  if (!data) return <Loading />;
  return (
    <div className="space-y-4 text-sm">
      <Tabs value={tab} onChange={setTab} items={[
        { value: "sales", label: `فروش (${faDigits(data.invoice_count)})` },
        { value: "money", label: `دریافت و پرداخت (${faDigits(data.money.length)})` },
        { value: "customers", label: `مشتریان (${faDigits(data.customers.length)})` },
      ]} />
      {tab === "sales" && (data.invoices.length === 0 ? <div className="muted">امروز فاکتوری صادر نشده است.</div> : (
        <>
          <div className="rounded-xl bg-pink-500/10 px-3 py-2 font-bold">جمع فروش امروز: <span className="num">{money(data.sales)}</span></div>
          <div className="space-y-1.5">{data.invoices.map((i: any) => (
            <Link key={i.id} to={`/invoices?period=today`} className={`flex items-center justify-between gap-2 rounded-xl px-3 py-2 hover:bg-violet-500/5 ${i.status === "void" ? "opacity-50 line-through" : ""}`} style={{ background: "var(--surface)" }}>
              <span><span className="num ml-2 text-xs font-bold">{i.number}</span><b>{i.customer}</b><span className="muted text-xs"> · {i.items.join("، ")}</span></span>
              <span className="flex items-center gap-2"><span className="num muted text-xs">{faDigits(i.at.slice(11, 16))}</span><b className="num">{money(i.total)}</b><Badge status={i.status} /></span>
            </Link>
          ))}</div>
        </>
      ))}
      {tab === "money" && (data.money.length === 0 ? <div className="muted">امروز پولی دریافت یا پرداخت نشده است.</div> : (
        <>
          <div className="grid gap-2 sm:grid-cols-2">
            {data.by_account.map((a: any) => (
              <div key={a.name} className="flex justify-between rounded-xl px-3 py-2" style={{ background: "var(--surface)" }}><span>{a.name}</span><b className={`num ${a.value < 0 ? "text-rose-600" : ""}`}>{money(a.value)}</b></div>
            ))}
          </div>
          <div className="rounded-xl bg-emerald-500/10 px-3 py-2 font-bold">جمع خالص امروز: <span className="num">{money(data.money_total)}</span></div>
          <div className="space-y-1.5">{data.money.map((r: any, k: number) => (
            <div key={k} className="flex flex-wrap items-center justify-between gap-2 rounded-xl px-3 py-2" style={{ background: "var(--surface)" }}>
              <span className="flex items-center gap-2"><span className={`badge ${MONEY_KIND[r.kind]?.cls}`}>{MONEY_KIND[r.kind]?.label}</span><b>{r.customer}</b>{r.ref && <span className="num muted text-xs">{r.ref}</span>}</span>
              <span className="flex items-center gap-2"><span className="muted text-xs">{r.account}</span><span className="num muted text-xs">{faDigits(r.at.slice(11, 16))}</span><b className={`num ${r.amount < 0 ? "text-rose-600" : ""}`}>{money(r.amount)}</b></span>
            </div>
          ))}</div>
        </>
      ))}
      {tab === "customers" && (data.customers.length === 0 ? <div className="muted">امروز مشتری‌ای مراجعه یا ثبت نشده است.</div> : (
        <div className="space-y-1.5">{data.customers.map((c: any) => (
          <Link key={c.id} to={`/customers?q=${encodeURIComponent(c.code || c.name)}`} className="flex flex-wrap items-center justify-between gap-2 rounded-xl px-3 py-2 hover:bg-violet-500/5" style={{ background: "var(--surface)" }}>
            <span><b>{c.name}</b> {c.code && <span className="num muted text-xs">کد {c.code}</span>}{c.new && <span className="badge mr-2 bg-emerald-500/10 text-emerald-600">جدید</span>}
              <span className="muted block text-xs">{c.services.length ? c.services.join("، ") : c.appointment ? (c.appointment.time_unknown ? "نوبت امروز (ساعت نامشخص)" : `نوبت ساعت ${faDigits(c.appointment.at.slice(11, 16))}`) : "ثبت‌نام امروز"}</span></span>
            <span className="flex items-center gap-2">{c.appointment && <Badge status={c.appointment.status} />}{c.total > 0 && <b className="num">{money(c.total)}</b>}</span>
          </Link>
        ))}</div>
      ))}
    </div>
  );
}

function Agenda({ a }: { a: any }) {
  const todo = a.todo;
  const items = [
    { n: todo.overdue_deposits[0], amt: todo.overdue_deposits[1], text: "بیعانهٔ باز با نوبت گذشته", hint: "فاکتور، استرداد یا سوخت", to: "/deposits?filter=overdue", tone: "bg-rose-500/10 text-rose-700 dark:text-rose-300", icon: <HandCoins size={16} /> },
    { n: todo.past_open_appointments, text: "نوبت گذشته که وضعیتش ثبت نشده", hint: "انجام شد / نیامد / لغو", to: "/appointments?tab=list", tone: "bg-amber-500/10 text-amber-700 dark:text-amber-300", icon: <CalendarX size={16} /> },
    { n: todo.unpaid_invoices[0], amt: todo.unpaid_invoices[1], text: "فاکتور با مانده پرداخت‌نشده", hint: "پیگیری دریافت", to: "/invoices?status=unpaid", tone: "bg-orange-500/10 text-orange-700 dark:text-orange-300", icon: <Receipt size={16} /> },
    { n: todo.deposits_without_appointment[0], amt: todo.deposits_without_appointment[1], text: "بیعانهٔ باز بدون نوبت", hint: "تعیین نوبت", to: "/deposits?filter=no_appointment", tone: "bg-violet-500/10 text-violet-700 dark:text-violet-300", icon: <CalendarClock size={16} /> },
    { n: todo.unknown_time_appointments, text: "نوبت با ساعت نامشخص", hint: "تعیین ساعت", to: "/appointments?tab=list", tone: "bg-sky-500/10 text-sky-700 dark:text-sky-300", icon: <Clock size={16} /> },
  ].filter((x) => x.n > 0);
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card className="lg:col-span-2" title={<span className="flex items-center gap-2"><CalendarClock size={18} className="text-violet-500" />نوبت‌های امروز ({faDigits(a.today.length)})</span>}
        actions={<Link to="/appointments" className="btn btn-sm">همهٔ نوبت‌ها</Link>}>
        {a.today.length === 0 ? <div className="muted text-sm">امروز نوبتی ثبت نشده است.{a.tomorrow_count ? ` فردا ${faDigits(a.tomorrow_count)} نوبت دارید.` : ""}</div> : (
          <div className="max-h-80 space-y-1.5 overflow-y-auto">
            {a.today.map((x: any) => (
              <div key={x.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl px-3 py-2 text-sm" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
                <div className="flex items-center gap-3">
                  <span className={`num rounded-lg px-2 py-1 text-xs font-bold ${x.time_unknown ? "bg-amber-500/15 text-amber-700" : "bg-violet-500/10 text-violet-700 dark:text-violet-300"}`}>{x.time_unknown ? "نامشخص" : faDigits(x.start_at.slice(11, 16))}</span>
                  <div><div className="font-semibold">{x.customer}</div><div className="muted text-xs">{[x.line, x.service, x.staff].filter(Boolean).join(" · ")}</div></div>
                </div>
                <div className="flex items-center gap-2">
                  <Badge status={x.status} />
                  {x.status === "booked" && <Link className="btn btn-sm btn-primary" to={`/invoices?new=1&appointment=${x.id}`}>فاکتور</Link>}
                </div>
              </div>
            ))}
          </div>
        )}
        {a.upcoming.length > 0 && (
          <div className="mt-3 border-t pt-3" style={{ borderColor: "var(--border)" }}>
            <div className="muted mb-1.5 text-xs font-bold">نوبت‌های بعدی{a.tomorrow_count ? ` · فردا ${faDigits(a.tomorrow_count)} نوبت` : ""}</div>
            <div className="flex flex-wrap gap-1.5">
              {a.upcoming.map((x: any) => (
                <span key={x.id} className="rounded-xl border px-2.5 py-1 text-xs" style={{ borderColor: "var(--border)" }}>
                  <b>{x.customer}</b> · {x.time_unknown ? formatJShort(x.start_at).split(" · ")[0] : formatJShort(x.start_at)}{x.service ? ` · ${x.service}` : ""}
                </span>
              ))}
            </div>
          </div>
        )}
      </Card>
      <Card title={<span className="flex items-center gap-2"><ClipboardList size={18} className="text-amber-500" />کارهای مانده</span>}>
        {items.length === 0 ? <div className="rounded-2xl bg-emerald-500/10 px-4 py-3 text-sm text-emerald-700 dark:text-emerald-300">✓ همه چیز مرتب است؛ کار مانده‌ای نیست.</div> : (
          <div className="space-y-2">
            {items.map((x) => (
              <Link key={x.text} to={x.to} className={`flex items-center justify-between gap-2 rounded-2xl px-3 py-2.5 text-sm transition hover:opacity-80 ${x.tone}`}>
                <span className="flex items-center gap-2">{x.icon}<span><b className="num">{faDigits(x.n)}</b> {x.text}<span className="block text-[11px] opacity-75">{x.hint}</span></span></span>
                {x.amt ? <span className="num text-xs font-bold">{compactMoney(x.amt)}</span> : null}
              </Link>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

export default function Dashboard() {
  const { user } = useAuth();
  const { data } = useApi<any>("/api/dashboard");
  const [detail, setDetail] = useState<null | "sales" | "money" | "customers">(null);
  const lines = useApi<any[]>("/api/lines");
  if (!data) return <Loading />;
  const t = data.today;
  const m = data.month;
  const lineColor = colorMap((lines.data ?? []).map((l) => l.name));
  const g = m && data.previous ? growth(m.revenue, data.previous.revenue) : null;

  return (
    <div className="space-y-6">
      <PageHeader title={`سلام ${user?.full_name || ""} 🌸`} subtitle="خلاصه وضعیت امروز و ۳۰ روز اخیر سالن" icon={<TrendingUp size={22} />}
        actions={<><Link to="/invoices?new=1" className="btn btn-primary"><Receipt size={16} />فاکتور جدید</Link><Link to="/deposits?new=1" className="btn"><HandCoins size={16} />ثبت بیعانه</Link></>} />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <button className="text-right" onClick={() => setDetail("sales")}><Stat label="فروش امروز" value={cmoney(t.revenue)} hint={`${num(t.invoice_count)} فاکتور · برای جزئیات کلیک کنید`} icon={<Receipt size={20} />} tone="pink" /></button>
        <button className="text-right" onClick={() => setDetail("money")}><Stat label="دریافتی امروز" value={cmoney(t.cash_in)} hint={`از فاکتورها ${compactMoney(t.cash_in - t.deposits_received)} · بیعانه ${compactMoney(t.deposits_received)}`} icon={<Banknote size={20} />} tone="emerald" /></button>
        <Link to="/deposits"><Stat label="بیعانه‌های باز" value={cmoney(t.deposits_held)} hint={`${num(t.deposits_held_count)} مورد`} icon={<HandCoins size={20} />} tone="amber" /></Link>
        <button className="text-right" onClick={() => setDetail("customers")}><Stat label="مشتریان امروز" value={num(t.customers_served)} hint={`${num(t.new_customers)} مشتری جدید · ${num(data.agenda?.today?.length ?? 0)} نوبت امروز`} icon={<Users size={20} />} tone="sky" /></button>
      </div>

      {data.agenda && <Agenda a={data.agenda} />}

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
            <Stat label="فروش ۳۰ روز" value={compactMoney(m.revenue)} icon={g ? (g.up ? <TrendingUp size={20} /> : <TrendingDown size={20} />) : undefined} tone={g && !g.up ? "pink" : "emerald"}
              hint={<>{g && <b className={g.up ? "text-emerald-600" : "text-rose-600"}>{g.up ? "▲" : "▼"} {faDigits(Math.abs(g.pct))}٪ نسبت به ۳۰ روز قبل · </b>}میانگین هر فاکتور {compactMoney(m.avg_ticket)}</>} />
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
      <Modal open={!!detail} onClose={() => setDetail(null)} title="جزئیات امروز" wide>
        {detail && <DayDetails initial={detail} />}
      </Modal>
    </div>
  );
}
