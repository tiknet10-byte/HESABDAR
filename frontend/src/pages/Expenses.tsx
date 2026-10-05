import { Plus, Wallet } from "lucide-react";
import { useState } from "react";
import { Card, Empty, Field, Loading, Modal, MoneyInput, PageHeader } from "../components/ui";
import { api } from "../lib/api";
import JalaliPicker from "../components/JalaliPicker";
import { jdate, money } from "../lib/format";
import { toLocalIso } from "../lib/jalali";
import { useApi, useToast } from "../lib/hooks";

const CATS = ["اجاره", "حقوق و دستمزد", "پورسانت پرسنل", "مواد مصرفی", "تجهیزات", "قبوض", "تبلیغات", "نگهداری و تعمیرات", "مالیات و عوارض", "سایر"];

export default function Expenses() {
  const toast = useToast();
  const { data, reload } = useApi<any[]>("/api/expenses");
  const accounts = useApi<any[]>("/api/accounts").data ?? [];
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ category: CATS[0], amount: 0, payment_account_id: 0, description: "", spent_at: toLocalIso(new Date()) });
  async function save() {
    try {
      await api("/api/expenses", { body: { ...f, payment_account_id: f.payment_account_id || accounts[0]?.id } });
      toast("هزینه ثبت شد");
      setOpen(false);
      reload();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }
  return (
    <div className="space-y-5">
      <PageHeader title="هزینه‌ها" subtitle="اجاره، حقوق، مواد مصرفی و سایر هزینه‌های سالن" icon={<Wallet size={22} />}
        actions={<button className="btn btn-primary" onClick={() => setOpen(true)}><Plus size={16} />ثبت هزینه</button>} />
      <Card pad={false}>
        <div className="overflow-x-auto">
          {!data ? <Loading /> : data.length === 0 ? <Empty /> : (
            <table className="table">
              <thead><tr><th>تاریخ</th><th>دسته</th><th>مبلغ</th><th>از حساب</th><th>شرح</th></tr></thead>
              <tbody>{data.map((e) => (
                <tr key={e.id}><td className="num muted">{jdate(e.spent_at)}</td><td className="font-semibold">{e.category}</td><td className="num">{money(e.amount)}</td>
                  <td>{accounts.find((a) => a.id === e.payment_account_id)?.name}</td><td className="muted">{e.description}</td></tr>
              ))}</tbody>
            </table>
          )}
        </div>
      </Card>
      <Modal open={open} onClose={() => setOpen(false)} title="ثبت هزینه">
        <div className="space-y-3">
          <Field label="دسته"><select className="input" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{CATS.map((c) => <option key={c}>{c}</option>)}</select></Field>
          <Field label="مبلغ"><MoneyInput value={f.amount} onChange={(v) => setF({ ...f, amount: v })} /></Field>
          <Field label="پرداخت از"><select className="input" value={f.payment_account_id || accounts[0]?.id} onChange={(e) => setF({ ...f, payment_account_id: Number(e.target.value) })}>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></Field>
          <Field label="تاریخ"><JalaliPicker pastOnly value={f.spent_at} onChange={(v) => setF({ ...f, spent_at: v })} /></Field>
          <Field label="شرح"><input className="input" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
          <button className="btn btn-primary w-full" disabled={!f.amount} onClick={save}>ثبت</button>
        </div>
      </Modal>
    </div>
  );
}
