import { FileText, Link2, Truck } from "lucide-react";
import { useState } from "react";
import CustomerPicker from "./CustomerPicker";
import { Badge, Empty, Field, Loading, Modal } from "./ui";
import { api } from "../lib/api";
import { money, num } from "../lib/format";
import { useApi, useToast } from "../lib/hooks";
import { formatJ } from "../lib/jalali";

export const SOURCE_FA: Record<string, string> = { tizpardaz: "تیزپرداز", chehreh: "چهره" };

/** An invoice brought over from the previous software (Tizpardaz): its items, prices, discount and what was paid. */
export function TradeDocView({ id }: { id: number }) {
  const { data: d } = useApi<any>(`/api/trade-docs/${id}`);
  if (!d) return <Loading />;
  const sale = d.kind.startsWith("sale");
  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-2 rounded-2xl bg-gradient-to-l from-amber-500/10 to-violet-600/10 p-4">
        <div>
          <div className="text-lg font-extrabold">{d.kind_label} <span className="num">{d.doc_no || "—"}</span></div>
          <div className="font-semibold">{sale ? "خریدار" : "فروشنده"}: {d.person || d.party || "—"}{d.customer_code && <span className="num muted text-xs"> · کد {d.customer_code}</span>}</div>
          <div className="muted text-xs">{formatJ(d.at, false)}</div>
        </div>
        <span className="badge bg-amber-500/15 text-amber-700 dark:text-amber-300">سابقهٔ {SOURCE_FA[d.source] ?? d.source}</span>
      </div>
      <div className="overflow-x-auto rounded-2xl border" style={{ borderColor: "var(--border)" }}>
        <table className="table text-xs [&_td]:px-2 [&_th]:px-2">
          <thead><tr><th>#</th><th>کالا</th><th>تعداد</th><th>فی</th><th>مبلغ</th>{sale && <th>سود</th>}</tr></thead>
          <tbody>
            {d.items.map((i: any, k: number) => (
              <tr key={i.id}>
                <td className="num muted">{num(k + 1)}</td>
                <td>{i.code && <span className="num muted">{i.code} </span>}<b>{i.name}</b>{!i.product_id && <div className="text-[10px] text-amber-600">به کالای سیستم وصل نیست</div>}</td>
                <td className="num">{num(i.qty)}</td>
                <td className="num">{money(i.unit_price, false)}</td>
                <td className="num font-semibold">{money(i.amount, false)}</td>
                {sale && <td className="num">{i.profit != null ? money(i.profit, false) : "—"}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="space-y-1 rounded-2xl p-3" style={{ background: "var(--surface)" }}>
        <div className="flex justify-between"><span className="muted">جمع کالاها</span><span className="num">{money(d.items_total)}</span></div>
        {d.discount > 0 && <div className="flex justify-between text-emerald-700 dark:text-emerald-300"><span>تخفیف</span><span className="num">− {money(d.discount)}</span></div>}
        {d.additions > 0 && <div className="flex justify-between"><span className="muted">هزینه‌های اضافه (حمل، مالیات…)</span><span className="num">+ {money(d.additions)}</span></div>}
        <div className="flex justify-between font-extrabold"><span>مبلغ فاکتور</span><span className="num">{money(d.total)}</span></div>
        {d.paid > 0 && <div className="flex justify-between"><span className="muted">{sale ? "دریافت‌شده" : "پرداخت‌شده"} همان موقع</span><span className="num">{money(d.paid)}</span></div>}
      </div>
      {d.extras?.length > 0 && (
        <details className="text-xs">
          <summary className="muted cursor-pointer">ردیف‌های دیگر این سند در تیزپرداز ({num(d.extras.length)})</summary>
          <div className="mt-1 space-y-0.5">{d.extras.map((x: any, k: number) => (
            <div key={k} className="flex justify-between rounded-lg px-2 py-1" style={{ background: "var(--surface)" }}>
              <span>{x.account}{x.description ? <span className="muted"> · {x.description}</span> : null}</span>
              <span className="num">{x.amount >= 0 ? "بدهکار " : "بستانکار "}{money(Math.abs(x.amount), false)}</span>
            </div>
          ))}</div>
        </details>
      )}
      <div className="muted text-xs">این فاکتور سابقه است: موجودی و حساب‌ها را دوباره تغییر نمی‌دهد (مانده‌ها و موجودی جدا منتقل شده‌اند).</div>
    </div>
  );
}

/** One row of an invoice list (here or brought over) that opens its details. */
export function DocRow({ date, title, sub, amount, badge, onClick, muted }: { date: string; title: React.ReactNode; sub?: React.ReactNode; amount: number; badge?: React.ReactNode; onClick: () => void; muted?: boolean }) {
  return (
    <button type="button" onClick={onClick} className={`flex w-full items-center justify-between gap-2 rounded-xl px-3 py-2 text-right transition hover:bg-violet-500/10 ${muted ? "opacity-60" : ""}`} style={{ background: "var(--surface)" }}>
      <span className="min-w-0"><span className="muted num ml-2 text-xs">{formatJ(date, false)}</span><b>{title}</b>{sub && <span className="muted text-xs"> · {sub}</span>}</span>
      <span className="flex shrink-0 items-center gap-2"><span className="num font-semibold">{money(amount)}</span>{badge}<FileText size={14} className="muted" /></span>
    </button>
  );
}

/** A seller's account: purchases here (what is still owed) and purchase invoices of the previous software. */
export function SupplierView({ id, onOpenPurchase, onChange }: { id: number; onOpenPurchase: (pid: number) => void; onChange?: () => void }) {
  const toast = useToast();
  const { data: s, reload } = useApi<any>(`/api/suppliers/${id}`);
  const [doc, setDoc] = useState<number | null>(null);
  const [linking, setLinking] = useState(false);
  const [person, setPerson] = useState<any>({});
  if (!s) return <Loading />;
  async function link(customer_id: number | null) {
    try {
      await api(`/api/suppliers/${id}`, { method: "PUT", body: { name: s.name, mobile: s.mobile, notes: s.notes, customer_id } });
      toast(customer_id ? "به پروندهٔ این شخص وصل شد" : "اتصال برداشته شد");
      setLinking(false);
      reload();
      onChange?.();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }
  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2 text-lg font-extrabold"><Truck size={18} className="text-sky-500" />{s.name}</div>
          <div className="muted text-xs">{s.mobile && <span className="num" dir="ltr">{s.mobile} · </span>}
            {s.customer_id ? <>همان شخصِ پروندهٔ مشتری «{s.customer}»{s.customer_code && <span className="num"> (کد {s.customer_code})</span>}</> : "به پروندهٔ هیچ مشتری‌ای وصل نیست"}</div>
        </div>
        <button className="btn btn-sm" onClick={() => setLinking(!linking)}><Link2 size={14} />{s.customer_id ? "تغییر شخص" : "وصل به پروندهٔ شخص"}</button>
      </div>
      {linking && (
        <div className="space-y-2 rounded-2xl p-3" style={{ background: "var(--surface)" }}>
          <Field label="این فروشنده همان کدام شخص در فهرست مشتریان است؟" hint="بعد از وصل شدن، خریدها از او در پروندهٔ همان شخص دیده می‌شود">
            <CustomerPicker value={person} onChange={(c) => { setPerson(c); if (c.customer_id) link(c.customer_id); }} />
          </Field>
          {s.customer_id && <button className="btn btn-sm text-rose-600" onClick={() => link(null)}>برداشتن اتصال</button>}
        </div>
      )}
      <div className="grid grid-cols-3 gap-2">
        <div className="rounded-2xl bg-amber-500/10 p-3"><div className="muted text-xs">بدهی ما به او</div><div className="num font-bold">{money(s.owed)}</div></div>
        <div className="rounded-2xl bg-sky-500/10 p-3"><div className="muted text-xs">خرید در این سیستم</div><div className="num font-bold">{money(s.bought)}</div></div>
        <div className="rounded-2xl bg-violet-500/10 p-3"><div className="muted text-xs">خرید در تیزپرداز ({num(s.history_count)} فاکتور)</div><div className="num font-bold">{money(s.history_total)}</div></div>
      </div>
      <div>
        <div className="mb-1 font-bold">فاکتورهای خرید از این فروشنده</div>
        {!s.purchases.length && !s.docs.length ? <Empty text="هنوز خریدی ثبت نشده" /> : (
          <div className="max-h-96 space-y-1.5 overflow-y-auto">
            {s.purchases.map((p: any) => (
              <DocRow key={`p${p.id}`} date={p.at} title={p.items_count ? p.number : "ماندهٔ اول دوره"} sub={p.supplier_ref ? `فاکتور فروشنده ${p.supplier_ref}` : p.items_count ? `${num(p.items_count)} قلم` : p.notes}
                amount={p.total} muted={p.status === "void"} onClick={() => onOpenPurchase(p.id)}
                badge={p.status === "void" ? <Badge status="void" /> : p.due > 0 ? <span className="badge bg-amber-500/15 text-amber-700">مانده {money(p.due, false)}</span> : <Badge status="paid" />} />
            ))}
            {s.docs.map((d: any) => (
              <DocRow key={`d${d.id}`} date={d.at} title={`${d.kind_label} ${d.doc_no}`} amount={d.total} onClick={() => setDoc(d.id)}
                badge={<span className="badge bg-amber-500/15 text-amber-700 dark:text-amber-300">تیزپرداز</span>} />
            ))}
          </div>
        )}
      </div>
      <Modal open={doc !== null} onClose={() => setDoc(null)} title="جزئیات فاکتور" wide>{doc !== null && <TradeDocView id={doc} />}</Modal>
    </div>
  );
}
