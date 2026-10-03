import { BookOpenCheck, CheckCircle2 } from "lucide-react";
import { useState } from "react";
import { Card, Loading, PageHeader, Stat, Tabs } from "../components/ui";
import { jdatetime, money } from "../lib/format";
import { useApi } from "../lib/hooks";

const TYPES: Record<string, string> = { asset: "دارایی", liability: "بدهی", equity: "سرمایه", revenue: "درآمد", expense: "هزینه" };

export default function Ledger() {
  const [tab, setTab] = useState<"tb" | "journal" | "balances">("tb");
  const tb = useApi<any>("/api/ledger/trial-balance");
  const journal = useApi<any[]>(tab === "journal" ? "/api/ledger/journal" : null, [tab]);
  const balances = useApi<any[]>("/api/accounts/balances");
  return (
    <div className="space-y-5">
      <PageHeader title="دفاتر حسابداری" subtitle="حسابداری دوطرفه: هر عملیات یک سند متوازن بدهکار/بستانکار" icon={<BookOpenCheck size={22} />} />
      {tb.data && (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-3">
          <Stat label="جمع بدهکار" value={money(tb.data.total_debit)} />
          <Stat label="جمع بستانکار" value={money(tb.data.total_credit)} />
          <Stat label="توازن دفاتر" value={tb.data.total_debit === tb.data.total_credit ? "متوازن" : "نامتوازن!"} icon={<CheckCircle2 size={20} />} tone={tb.data.total_debit === tb.data.total_credit ? "emerald" : "pink"} />
        </div>
      )}
      <Tabs value={tab} onChange={setTab} items={[{ value: "tb", label: "تراز آزمایشی" }, { value: "balances", label: "موجودی حساب‌ها و کارتخوان‌ها" }, { value: "journal", label: "دفتر روزنامه" }]} />
      <Card pad={false}>
        <div className="overflow-x-auto">
          {tab === "tb" && (!tb.data ? <Loading /> : (
            <table className="table">
              <thead><tr><th>کد</th><th>حساب</th><th>نوع</th><th>بدهکار</th><th>بستانکار</th><th>مانده</th></tr></thead>
              <tbody>{tb.data.rows.map((r: any) => (
                <tr key={r.code}><td className="num">{r.code}</td><td className="font-semibold">{r.name}</td><td className="muted">{TYPES[r.type]}</td>
                  <td className="num">{money(r.debit, false)}</td><td className="num">{money(r.credit, false)}</td><td className="num font-bold">{money(r.balance)}</td></tr>
              ))}</tbody>
            </table>
          ))}
          {tab === "balances" && (!balances.data ? <Loading /> : (
            <table className="table">
              <thead><tr><th>حساب</th><th>بانک</th><th>موجودی دفتری</th></tr></thead>
              <tbody>{balances.data.map((b) => <tr key={b.id}><td className="font-semibold">{b.name}</td><td>{b.bank_name}</td><td className="num font-bold">{money(b.balance)}</td></tr>)}</tbody>
            </table>
          ))}
          {tab === "journal" && (!journal.data ? <Loading /> : (
            <div className="divide-y" style={{ borderColor: "var(--border)" }}>
              {journal.data.map((e) => (
                <div key={e.id} className="p-4 text-sm">
                  <div className="mb-2 flex justify-between"><span className="font-bold">#{e.id} · {e.description}</span><span className="muted num">{jdatetime(e.at)}</span></div>
                  {e.lines.map((l: any, i: number) => (
                    <div key={i} className={`grid grid-cols-3 py-0.5 ${l.credit ? "pr-8" : ""}`}>
                      <span>{l.account}</span><span className="num">{l.debit ? money(l.debit, false) : ""}</span><span className="num">{l.credit ? money(l.credit, false) : ""}</span>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
