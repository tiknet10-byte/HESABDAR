import { Printer } from "lucide-react";
import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { kindLabel } from "../components/StaffPay";
import { api } from "../lib/api";
import { money, num } from "../lib/format";
import { formatJ, monthName } from "../lib/jalali";

/** One person's month on an A5 sheet: owed at the start, earned, given (advances / settlement), left at the end. */
export default function PrintStaff() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const [st, setSt] = useState<any>(null);
  const [salon, setSalon] = useState<Record<string, any>>({});
  useEffect(() => {
    const q = params.get("jy") && params.get("jm") ? `?jy=${params.get("jy")}&jm=${params.get("jm")}` : "";
    Promise.all([api(`/api/staff/${id}/statement${q}`), api<Record<string, any>>("/api/settings").catch(() => ({}))])
      .then(([s, set]) => { setSalon(set); setSt(s); document.title = `صورت‌حساب ${s.name} - ${s.month}`; })
      .catch(() => setSt({ error: true }));
  }, [id, params]);
  useEffect(() => { if (st && !st.error) setTimeout(() => window.print(), 400); }, [st]);
  if (!st) return <div className="p-8" dir="rtl">در حال آماده‌سازی…</div>;
  if (st.error) return <div className="p-8" dir="rtl">صورت‌حساب پیدا نشد.</div>;
  const earned = [["services", "سهم از خدمات"], ["forfeits", "سهم از بیعانهٔ سوخت‌شده"], ["salary", "حقوق ماه"], ["other", "سایر"]].filter(([k]) => st.earned[k]);
  const rows = [...st.payouts, ...st.later];
  return (
    <div dir="rtl" className="print-sheet min-h-screen bg-white text-black">
      <div className="no-print sticky top-0 flex items-center justify-between border-b bg-white px-6 py-3">
        <span className="text-sm">پیش‌نمایش چاپ صورت‌حساب</span>
        <div className="flex gap-2">
          <button className="btn btn-primary btn-sm" onClick={() => window.print()}><Printer size={14} />چاپ</button>
          <button className="btn btn-sm" onClick={() => window.close()}>بستن</button>
        </div>
      </div>
      <section className="invoice-sheet mx-auto my-6 max-w-[148mm] bg-white px-7 py-6 shadow print:my-0 print:shadow-none">
        <header className="mb-4 border-b-2 border-black pb-3 text-center">
          <div className="text-xl font-extrabold">{salon["salon.name"] || "سالن زیبایی"}</div>
          <div className="mt-1 text-sm">صورت‌حساب {st.employee ? "حقوق" : "سهم"} {monthName([st.jy, st.jm])}</div>
        </header>
        <div className="mb-3 flex justify-between text-sm">
          <div><div>نام: <b>{st.name}</b></div><div>{st.employee ? (st.job_title || "کارمند") : `${st.line ?? ""} · سهم ${num(st.percent)}٪`}</div></div>
          <div className="text-left"><div>تاریخ چاپ: {formatJ(new Date(), false)}</div>{st.current && <div>(ماه هنوز تمام نشده؛ تا امروز)</div>}</div>
        </div>
        <table className="print-table mb-3 w-full border-collapse text-[12px]">
          <tbody>
            <tr><td>ماندهٔ اول ماه (از ماه‌های قبل)</td><td className="num w-36">{money(st.opening)}</td></tr>
            {earned.map(([k, label]) => <tr key={k}><td>{label}</td><td className="num">{money(st.earned[k])}</td></tr>)}
            <tr><td>داده‌شده در این ماه</td><td className="num">− {money(st.given)}</td></tr>
            <tr className="font-bold"><td>ماندهٔ پایان ماه{st.closing < 0 ? " (بدهکار؛ از ماه بعد کم می‌شود)" : ""}</td><td className="num">{money(st.closing)}</td></tr>
            {st.paid_later > 0 && <tr><td>پرداخت بابت همین ماه بعد از پایان آن</td><td className="num">− {money(st.paid_later)}</td></tr>}
            {st.paid_later > 0 && <tr className="font-bold"><td>باقی</td><td className="num">{money(st.remaining)}</td></tr>}
          </tbody>
        </table>
        {st.work && (st.work.revenue || st.work.forfeits) ? (
          <p className="mb-3 text-[11px] leading-6">کارکرد: {num(st.work.services)} خدمت، درآمد {money(st.work.revenue)}{st.work.forfeits ? ` + بیعانهٔ سوخت‌شده ${money(st.work.forfeits)}` : ""}، خزانه {money(st.work.treasury)}، سهم {money(st.work.staff_share)}</p>
        ) : null}
        {rows.length > 0 && (
          <table className="print-table w-full border-collapse text-[11px]">
            <thead><tr><th>تاریخ</th><th>نوع</th><th>مبلغ</th><th>از</th><th>توضیح</th></tr></thead>
            <tbody>{rows.map((p: any) => (
              <tr key={p.id} className={p.status === "void" ? "line-through" : ""}>
                <td>{formatJ(p.paid_at, false)}</td><td>{kindLabel(st.employee, p.kind)}{p.jm !== st.jm || p.jy !== st.jy ? ` (${monthName([p.jy, p.jm])})` : ""}</td>
                <td className="num">{money(p.amount)}</td><td>{p.account ?? ""}</td><td>{[p.reference && `پیگیری ${p.reference}`, p.description, p.status === "void" && "باطل"].filter(Boolean).join(" · ")}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
        <div className="mt-8 flex justify-between text-xs"><span>امضای مدیر</span><span>امضای {st.employee ? "کارمند" : "پرسنل"}</span></div>
      </section>
    </div>
  );
}
