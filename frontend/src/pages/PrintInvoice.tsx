import { Printer } from "lucide-react";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../lib/api";
import { money } from "../lib/format";
import { faDigits, formatJ } from "../lib/jalali";

const STATUS: Record<string, string> = { paid: "تسویه شده", partial: "پرداخت جزئی", issued: "پرداخت‌نشده", void: "باطل شده" };

/** A clean A5 invoice sheet for the customer (opens the print dialog by itself). */
export default function PrintInvoice() {
  const { id } = useParams();
  const [inv, setInv] = useState<any>(null);
  const [salon, setSalon] = useState<Record<string, any>>({});
  const [accounts, setAccounts] = useState<Record<number, string>>({});

  useEffect(() => {
    Promise.all([
      api(`/api/invoices/${id}`),
      api<Record<string, any>>("/api/settings").catch(() => ({})),
      api<any[]>("/api/accounts").catch(() => []),
    ]).then(([i, s, a]) => {
      setSalon(s);
      setAccounts(Object.fromEntries((a as any[]).map((x) => [x.id, x.name])));
      setInv(i);
      document.title = `فاکتور ${i.number}`;
    }).catch(() => setInv({ error: true }));
  }, [id]);
  useEffect(() => { if (inv && !inv.error) setTimeout(() => window.print(), 400); }, [inv]);

  if (!inv) return <div className="p-8" dir="rtl">در حال آماده‌سازی…</div>;
  if (inv.error) return <div className="p-8" dir="rtl">فاکتور پیدا نشد.</div>;
  return (
    <div dir="rtl" className="print-sheet min-h-screen bg-white text-black">
      <div className="no-print sticky top-0 flex items-center justify-between border-b bg-white px-6 py-3">
        <span className="text-sm">پیش‌نمایش چاپ فاکتور</span>
        <div className="flex gap-2">
          <button className="btn btn-primary btn-sm" onClick={() => window.print()}><Printer size={14} />چاپ</button>
          <button className="btn btn-sm" onClick={() => window.close()}>بستن</button>
        </div>
      </div>
      <section className="invoice-sheet mx-auto my-6 max-w-[148mm] bg-white px-7 py-6 shadow print:my-0 print:shadow-none">
        <header className="mb-4 border-b-2 border-black pb-3 text-center">
          <div className="text-xl font-extrabold">{salon["salon.name"] || "سالن زیبایی"}</div>
          {(salon["salon.address"] || salon["salon.phone"]) && <div className="mt-1 text-xs">{[salon["salon.address"], salon["salon.phone"] && `تلفن: ${salon["salon.phone"]}`].filter(Boolean).join(" · ")}</div>}
        </header>
        <div className="mb-3 flex justify-between text-sm">
          <div>
            <div>شماره فاکتور: <b className="num">{inv.number}</b></div>
            <div>تاریخ: {formatJ(inv.issued_at)}</div>
          </div>
          <div className="text-left">
            <div>مشتری: <b>{inv.customer}</b></div>
            {inv.customer_code && <div>کد مشتری: <span className="num">{inv.customer_code}</span></div>}
            {inv.customer_mobile && <div className="num" dir="ltr">{inv.customer_mobile}</div>}
          </div>
        </div>
        <table className="print-table mb-3 w-full border-collapse text-[12px]">
          <thead><tr><th className="w-8">#</th><th>خدمت</th><th className="w-28">پرسنل</th><th className="w-28">مبلغ</th></tr></thead>
          <tbody>
            {inv.items.map((it: any, k: number) => (
              <tr key={k}>
                <td className="text-center">{faDigits(k + 1)}</td>
                <td>{it.description}{it.quantity > 1 ? ` × ${faDigits(it.quantity)}` : ""}</td>
                <td>{it.staff ?? ""}</td>
                <td className="num text-left">{money(it.amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="mr-auto w-64 space-y-1 text-sm">
          {inv.discount > 0 && <div className="flex justify-between"><span>تخفیف</span><span className="num">− {money(inv.discount)}</span></div>}
          <div className="flex justify-between border-t border-black pt-1 font-extrabold"><span>جمع کل</span><span className="num">{money(inv.total)}</span></div>
          {inv.deposits.map((d: any) => <div key={d.id} className="flex justify-between"><span>بیعانه ({formatJ(d.received_at, false)})</span><span className="num">− {money(d.amount)}</span></div>)}
          {inv.payments.map((p: any) => <div key={p.id} className="flex justify-between"><span>{p.amount < 0 ? "استرداد" : "پرداخت"} · {accounts[p.account_id] ?? ""}</span><span className="num">{money(Math.abs(p.amount))}</span></div>)}
          {inv.status !== "void" && <div className="flex justify-between font-bold"><span>مانده</span><span className="num">{money(inv.due)}</span></div>}
          <div className="pt-1 text-center text-xs">وضعیت: {STATUS[inv.status] ?? inv.status}</div>
        </div>
        {inv.notes && <div className="mt-3 text-xs">توضیحات: {inv.notes}</div>}
        <footer className="mt-8 text-center text-xs">از انتخاب شما سپاسگزاریم 🌸</footer>
      </section>
    </div>
  );
}
