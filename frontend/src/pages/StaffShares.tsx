import { HandCoins, UsersRound } from "lucide-react";
import { useState } from "react";
import { Card, Empty, Field, Loading, Modal, MoneyInput, PageHeader, Stat, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { cmoney, daysAgo, isoDate, money, num } from "../lib/format";
import { can, useApi, useAuth, useToast } from "../lib/hooks";

const RANGES = { "1": "امروز", "7": "۷ روز", "30": "۳۰ روز", "90": "۳ ماه", "365": "یک سال" } as const;

function Payout({ row, onDone }: { row: any; onDone: () => void }) {
  const toast = useToast();
  const accounts = useApi<any[]>("/api/accounts").data ?? [];
  const [amount, setAmount] = useState(Math.max(0, row.balance));
  const [acc, setAcc] = useState(0);
  const [desc, setDesc] = useState("");
  return (
    <div className="space-y-3">
      <div className="rounded-2xl bg-violet-500/10 p-3 text-sm">مانده قابل پرداخت به <b>{row.name}</b>: <b className="num">{money(row.balance)}</b></div>
      <Field label="مبلغ پرداخت"><MoneyInput value={amount} onChange={setAmount} /></Field>
      <Field label="پرداخت از">
        <select className="input" value={acc || accounts[0]?.id || 0} onChange={(e) => setAcc(Number(e.target.value))}>
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
      </Field>
      <Field label="توضیح"><input className="input" value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="مثلاً تسویه مهر ماه" /></Field>
      <button className="btn btn-primary w-full" disabled={!amount} onClick={async () => {
        try {
          await api(`/api/staff/${row.staff_id}/payout`, { body: { amount, payment_account_id: acc || accounts[0]?.id, description: desc } });
          toast("پرداخت ثبت شد");
          onDone();
        } catch (e: any) { toast(e.message, "error"); }
      }}>ثبت پرداخت</button>
    </div>
  );
}

export default function StaffShares() {
  const { user } = useAuth();
  const [range, setRange] = useState<keyof typeof RANGES>("30");
  const start = range === "1" ? isoDate(new Date()) : daysAgo(Number(range) - 1);
  const { data, reload } = useApi<any>(`/api/reports/staff-shares?start=${start}&end=${isoDate(new Date())}`, [range]);
  const [payout, setPayout] = useState<any>(null);
  const t = data?.totals;
  return (
    <div className="space-y-5">
      <PageHeader title="پرسنل و سهم‌ها" subtitle="درآمد هر پرسنل و هر لاین، سهم پرسنل، سهم سالن و تسویه با پرسنل" icon={<UsersRound size={22} />}
        actions={<Tabs value={range} onChange={setRange} items={Object.entries(RANGES).map(([value, label]) => ({ value: value as keyof typeof RANGES, label }))} />} />
      {!data ? <Loading /> : (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Stat label="درآمد خدمات" value={cmoney(t.revenue)} tone="pink" />
            <Stat label="سهم پرسنل" value={cmoney(t.staff_share)} tone="amber" />
            <Stat label="سهم سالن (مدیریت)" value={cmoney(t.salon_share)} tone="emerald" />
            <Stat label="مانده بدهی به پرسنل" value={cmoney(t.balance)} hint={`پرداخت‌شده در دوره: ${cmoney(t.paid)}`} tone="sky" />
          </div>
          <Card title="به تفکیک پرسنل" pad={false}>
            <div className="overflow-x-auto">
              {data.staff.length === 0 ? <Empty text="پرسنلی ثبت نشده؛ از تنظیمات ← پرسنل و کاربران اضافه کنید" /> : (
                <table className="table">
                  <thead><tr><th>پرسنل</th><th>لاین</th><th>درصد</th><th>تعداد خدمت</th><th>درآمد</th><th>سهم پرسنل</th><th>سهم سالن</th><th>پرداخت در دوره</th><th>مانده</th><th></th></tr></thead>
                  <tbody>{data.staff.map((r: any) => (
                    <tr key={r.staff_id ?? 0} className={r.staff_id ? "" : "text-amber-700 dark:text-amber-300"}>
                      <td className="font-semibold">{r.name}</td><td className="muted">{r.line ?? "—"}</td><td className="num">{num(r.percent)}٪</td>
                      <td className="num">{num(r.services)}</td><td className="num">{money(r.revenue)}</td>
                      <td className="num font-semibold text-amber-600">{money(r.staff_share)}</td><td className="num font-semibold text-emerald-600">{money(r.salon_share)}</td>
                      <td className="num">{money(r.paid)}</td><td className="num font-bold">{money(r.balance)}</td>
                      <td>{r.staff_id && r.balance > 0 && can(user, "finance") && <button className="btn btn-sm" onClick={() => setPayout(r)}><HandCoins size={14} />تسویه</button>}</td>
                    </tr>
                  ))}</tbody>
                </table>
              )}
            </div>
            {data.staff.some((r: any) => !r.staff_id) && <p className="muted px-5 py-3 text-xs">ردیف «بدون پرسنل» خدماتی است که پرسنلشان مشخص نشده؛ با وصل‌کردن هر پرسنل به لاین خودش، از این به بعد خودکار ثبت می‌شود.</p>}
          </Card>
          <Card title="به تفکیک لاین" pad={false}>
            <div className="overflow-x-auto">
              <table className="table">
                <thead><tr><th>لاین</th><th>تعداد خدمت</th><th>درآمد</th><th>سهم پرسنل</th><th>سهم سالن</th></tr></thead>
                <tbody>{data.lines.map((r: any) => (
                  <tr key={r.line_id ?? 0}><td className="font-semibold">{r.name}</td><td className="num">{num(r.services)}</td><td className="num">{money(r.revenue)}</td>
                    <td className="num text-amber-600">{money(r.staff_share)}</td><td className="num font-semibold text-emerald-600">{money(r.salon_share)}</td></tr>
                ))}</tbody>
              </table>
            </div>
          </Card>
        </>
      )}
      <Modal open={!!payout} onClose={() => setPayout(null)} title="تسویه با پرسنل">
        {payout && <Payout row={payout} onDone={() => { setPayout(null); reload(); }} />}
      </Modal>
    </div>
  );
}
