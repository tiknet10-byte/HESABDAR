import { AtSign, Phone, Plus, Users } from "lucide-react";
import { useState } from "react";
import { Badge, Card, Empty, Field, Loading, Modal, PageHeader, Stat } from "../components/ui";
import { api } from "../lib/api";
import { jdate, jdatetime, money, num } from "../lib/format";
import { useApi, useToast } from "../lib/hooks";

function CustomerForm({ initial, onDone }: { initial?: any; onDone: () => void }) {
  const toast = useToast();
  const [f, setF] = useState({ full_name: "", mobile: "", instagram: "", notes: "", ...(initial ?? {}) });
  async function save() {
    try {
      await api(initial ? `/api/customers/${initial.id}` : "/api/customers", { method: initial ? "PUT" : "POST", body: { ...f, tags: f.tags ?? [] } });
      toast("ذخیره شد");
      onDone();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }
  return (
    <div className="space-y-3">
      <Field label="نام و نام خانوادگی"><input className="input" value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="موبایل"><input className="input num" dir="ltr" value={f.mobile ?? ""} onChange={(e) => setF({ ...f, mobile: e.target.value })} placeholder="09xxxxxxxxx" /></Field>
        <Field label="اینستاگرام"><input className="input" dir="ltr" value={f.instagram ?? ""} onChange={(e) => setF({ ...f, instagram: e.target.value })} placeholder="@username" /></Field>
      </div>
      <Field label="یادداشت"><textarea className="input min-h-20" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
      <button className="btn btn-primary w-full" disabled={!f.full_name} onClick={save}>ذخیره</button>
    </div>
  );
}

function CustomerView({ id }: { id: number }) {
  const { data, reload } = useApi<any>(`/api/customers/${id}`);
  const [edit, setEdit] = useState(false);
  if (!data) return <Loading />;
  if (edit) return <CustomerForm initial={data} onDone={() => { setEdit(false); reload(); }} />;
  const b = data.balance;
  return (
    <div className="space-y-4 text-sm">
      <div className="flex items-start justify-between">
        <div>
          <div className="text-lg font-extrabold">{data.full_name}</div>
          <div className="muted mt-1 flex flex-wrap gap-3">
            {data.mobile && <span className="num flex items-center gap-1" dir="ltr"><Phone size={13} />{data.mobile}</span>}
            {data.instagram && <span className="flex items-center gap-1"><AtSign size={13} />{data.instagram}</span>}
            <span>عضویت: {jdate(data.created_at)}</span>
          </div>
        </div>
        <button className="btn btn-sm" onClick={() => setEdit(true)}>ویرایش</button>
      </div>
      <div className="grid grid-cols-3 gap-2">
        <div className="rounded-2xl bg-emerald-500/10 p-3"><div className="muted text-xs">بیعانه نزد سالن</div><div className="num font-bold">{money(b.deposits_held)}</div></div>
        <div className="rounded-2xl bg-amber-500/10 p-3"><div className="muted text-xs">بدهی مشتری</div><div className="num font-bold">{money(b.receivable)}</div></div>
        <div className="rounded-2xl bg-violet-500/10 p-3"><div className="muted text-xs">تعداد مراجعه</div><div className="num font-bold">{num(data.invoices.filter((i: any) => i.status !== "void").length)}</div></div>
      </div>
      {data.notes && <div className="rounded-2xl p-3" style={{ background: "var(--surface)" }}>{data.notes}</div>}
      <div>
        <div className="mb-2 font-bold">سوابق خدمات</div>
        {data.invoices.length === 0 ? <div className="muted">بدون سابقه</div> : (
          <div className="space-y-1.5">
            {data.invoices.map((i: any) => (
              <div key={i.id} className="flex items-center justify-between rounded-xl px-3 py-2" style={{ background: "var(--surface)" }}>
                <span><span className="muted num ml-2">{jdate(i.issued_at)}</span>{i.items.join("، ")}</span>
                <span className="flex items-center gap-2"><span className="num font-semibold">{money(i.total)}</span><Badge status={i.status} /></span>
              </div>
            ))}
          </div>
        )}
      </div>
      {data.deposits.length > 0 && (
        <div>
          <div className="mb-2 font-bold">بیعانه‌ها</div>
          {data.deposits.map((d: any) => (
            <div key={d.id} className="flex justify-between py-1"><span className="muted num">{jdatetime(d.received_at)}</span><span className="flex gap-2"><span className="num">{money(d.amount)}</span><Badge status={d.status} /></span></div>
          ))}
        </div>
      )}
      {data.known_cards?.length > 0 && <div className="muted text-xs">کارت‌های پرداخت‌کننده: <span dir="ltr">{data.known_cards.join(" , ")}</span></div>}
    </div>
  );
}

export default function Customers() {
  const [q, setQ] = useState("");
  const { data, reload } = useApi<any>(`/api/customers?q=${encodeURIComponent(q)}&limit=200`, [q]);
  const [view, setView] = useState<number | null>(null);
  const [create, setCreate] = useState(false);
  return (
    <div className="space-y-5">
      <PageHeader title="مشتریان" subtitle="پرونده کامل هر مشتری: خدمات، بیعانه، بدهی و کانال‌های ارتباطی" icon={<Users size={22} />}
        actions={<button className="btn btn-primary" onClick={() => setCreate(true)}><Plus size={16} />مشتری جدید</button>} />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4"><Stat label="تعداد مشتریان" value={num(data?.total)} tone="sky" /></div>
      <Card pad={false}>
        <div className="p-4"><input className="input max-w-sm" placeholder="جستجوی نام، موبایل یا اینستاگرام…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <div className="overflow-x-auto">
          {!data ? <Loading /> : data.items.length === 0 ? <Empty /> : (
            <table className="table">
              <thead><tr><th>نام</th><th>موبایل</th><th>اینستاگرام</th><th>مجموع خرید</th><th>بیعانه باز</th><th>منبع</th></tr></thead>
              <tbody>
                {data.items.map((c: any) => (
                  <tr key={c.id} className="cursor-pointer" onClick={() => setView(c.id)}>
                    <td className="font-semibold">{c.full_name}</td>
                    <td className="num" dir="ltr">{c.mobile ?? "—"}</td>
                    <td dir="ltr">{c.instagram ? "@" + c.instagram : "—"}</td>
                    <td className="num">{money(c.total_spent)}</td>
                    <td className="num">{c.deposits_held ? money(c.deposits_held) : "—"}</td>
                    <td className="muted text-xs">{c.source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </Card>
      <Modal open={create} onClose={() => setCreate(false)} title="مشتری جدید"><CustomerForm onDone={() => { setCreate(false); reload(); }} /></Modal>
      <Modal open={view !== null} onClose={() => { setView(null); reload(); }} title="پرونده مشتری">{view !== null && <CustomerView id={view} />}</Modal>
    </div>
  );
}
