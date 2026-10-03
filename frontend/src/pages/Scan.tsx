import { AlertTriangle, Camera, CheckCircle2, FileUp } from "lucide-react";
import { useState } from "react";
import { Badge, Card, Empty, Loading, PageHeader, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { jdate, jdatetime, money } from "../lib/format";
import { useApi, useToast } from "../lib/hooks";

function Review({ id, onDone }: { id: number; onDone: () => void }) {
  const toast = useToast();
  const { data, setData } = useApi<any>(`/api/plugins/sales_book_ocr/batches/${id}`);
  const [busy, setBusy] = useState(false);
  if (!data) return <Loading />;
  const editable = data.status === "review";
  const toggle = async (i: number) => {
    const rows = data.rows.map((r: any, j: number) => (j === i ? { ...r, include: !r.include } : r));
    setData(await api(`/api/plugins/sales_book_ocr/batches/${id}/rows`, { method: "PUT", body: { rows } }));
  };
  return (
    <Card title={`بررسی: ${data.file_name}`} actions={<Badge status={data.status} />}>
      <div className="mb-4 flex flex-wrap gap-3 text-sm">
        <span className="badge bg-violet-500/10 py-1">ردیف‌ها: {data.summary.rows}</span>
        <span className="badge bg-violet-500/10 py-1">جمع: {money(data.summary.total)}</span>
        <span className="badge bg-sky-500/10 py-1">مشتری جدید: {data.summary.new_customers}</span>
        <span className={`badge py-1 ${data.summary.warnings ? "bg-amber-500/15 text-amber-700" : "bg-emerald-500/15 text-emerald-700"}`}>هشدار: {data.summary.warnings}</span>
      </div>
      {data.summary.page_total_mismatch && <div className="mb-3 flex items-center gap-2 rounded-2xl bg-rose-500/10 p-3 text-sm text-rose-700 dark:text-rose-300"><AlertTriangle size={16} />{data.summary.page_total_mismatch}</div>}
      <div className="overflow-x-auto">
        <table className="table">
          <thead><tr><th></th><th>تاریخ</th><th>مشتری</th><th>خدمات</th><th>مبلغ</th><th>پرداخت</th><th>هشدارها</th></tr></thead>
          <tbody>{data.rows.map((r: any, i: number) => (
            <tr key={i} className={r.include ? "" : "opacity-40"}>
              <td><input type="checkbox" checked={r.include} disabled={!editable} onChange={() => toggle(i)} /></td>
              <td className="num">{jdate(r.date)}</td>
              <td><div className="font-semibold">{r.customer_name}</div><div className="muted num text-xs" dir="ltr">{r.mobile}</div>{r.is_new_customer && <Badge>جدید</Badge>}</td>
              <td>{r.items.map((it: any, k: number) => <div key={k}>{it.service ?? <span className="text-rose-500">{it.text}؟</span>} <span className="muted num text-xs">{money(it.unit_price)}</span></div>)}</td>
              <td className="num font-semibold">{money(r.total)}</td>
              <td>{r.payment_method ?? "—"}</td>
              <td className="text-xs">{r.warnings.map((w: string) => <div key={w} className="text-amber-600">• {w}</div>)}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      {editable && (
        <div className="mt-4 flex gap-2">
          <button className="btn btn-primary" disabled={busy} onClick={async () => {
            setBusy(true);
            try { const r = await api(`/api/plugins/sales_book_ocr/batches/${id}/commit`, { method: "POST" }); toast(`${r.invoices.length} فاکتور ثبت شد`); onDone(); } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
          }}><CheckCircle2 size={16} />تأیید و ثبت در حسابداری</button>
          <button className="btn" onClick={async () => { await api(`/api/plugins/sales_book_ocr/batches/${id}/discard`, { method: "POST" }); onDone(); }}>کنار گذاشتن</button>
        </div>
      )}
    </Card>
  );
}

export default function Scan() {
  const toast = useToast();
  const batches = useApi<any[]>("/api/plugins/sales_book_ocr/batches");
  const [current, setCurrent] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [currency, setCurrency] = useState("toman");

  async function upload(file: File) {
    setBusy(true);
    const fd = new FormData();
    fd.append("file", file);
    fd.append("currency", currency);
    try {
      const b = await api("/api/plugins/sales_book_ocr/upload", { form: fd });
      setCurrent(b.id);
      batches.reload();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <PageHeader title="اسکن دفتر فروش و فاکتور" subtitle="عکس یا فایل دفتر را بدهید؛ سیستم ردیف‌ها را می‌خواند، مغایرت‌ها را نشان می‌دهد و پس از تأیید ثبت می‌کند" icon={<Camera size={22} />} />
      <Card>
        <label className={`flex cursor-pointer flex-col items-center justify-center gap-3 rounded-3xl border-2 border-dashed p-10 text-center transition hover:bg-violet-500/5 ${busy ? "opacity-60" : ""}`} style={{ borderColor: "var(--border)" }}
          onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) upload(f); }}>
          {busy ? <Spinner /> : <FileUp size={40} className="text-violet-500" />}
          <div className="font-bold">عکس، PDF، فایل متنی یا CSV دفتر فروش را اینجا رها کنید</div>
          <div className="muted text-sm">خواندن عکس و دست‌خط با هوش مصنوعی انجام می‌شود · با موبایل می‌توانید مستقیم عکس بگیرید</div>
          <input type="file" className="hidden" accept="image/*,application/pdf,.txt,.csv" capture="environment" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        </label>
        <div className="mt-3 flex items-center gap-2 text-sm"><span className="muted">واحد مبالغ دفتر:</span>
          <select className="input w-auto" value={currency} onChange={(e) => setCurrency(e.target.value)}><option value="toman">تومان</option><option value="rial">ریال</option></select>
        </div>
      </Card>
      {current !== null && <Review key={current} id={current} onDone={() => { setCurrent(null); batches.reload(); }} />}
      <Card title="سوابق اسکن" pad={false}>
        {!batches.data ? <Loading /> : batches.data.length === 0 ? <Empty /> : (
          <table className="table">
            <thead><tr><th>فایل</th><th>زمان</th><th>ردیف</th><th>هشدار</th><th>وضعیت</th></tr></thead>
            <tbody>{batches.data.map((b) => (
              <tr key={b.id} className="cursor-pointer" onClick={() => setCurrent(b.id)}>
                <td className="font-semibold">{b.file_name}</td><td className="num muted">{jdatetime(b.created_at)}</td><td className="num">{b.summary.rows}</td><td className="num">{b.summary.warnings}</td><td><Badge status={b.status} /></td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
