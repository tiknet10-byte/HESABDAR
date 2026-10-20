import { BriefcaseBusiness, FileText, HandCoins, Pencil, Plus, Wallet } from "lucide-react";
import { useEffect, useState } from "react";
import MonthNav from "../components/MonthNav";
import { PayoutForm, SettleAll, Statement, kindLabel } from "../components/StaffPay";
import { Card, Empty, Field, Loading, Modal, MoneyInput, PageHeader, Stat } from "../components/ui";
import { api } from "../lib/api";
import { cmoney, money } from "../lib/format";
import { can, useApi, useAuth, useToast } from "../lib/hooks";
import { monthName, sameMonth, thisMonth, type YM } from "../lib/jalali";

/** add or change an employee (name, mobile, job, monthly salary) */
function EmployeeForm({ emp, onDone }: { emp?: any; onDone: () => void }) {
  const toast = useToast();
  const [f, setF] = useState({ full_name: emp?.full_name ?? "", mobile: emp?.mobile ?? "", job_title: emp?.job_title ?? "", salary: emp?.salary ?? 0,
    is_active: emp?.is_active ?? true });
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    try {
      const body = { ...f, kind: "employee", line_id: null, commission_percent: 0, mobile: f.mobile || null };
      await api(emp ? `/api/staff/${emp.id}` : "/api/staff", { method: emp ? "PUT" : "POST", body });
      toast(emp ? "ذخیره شد" : "کارمند اضافه شد");
      onDone();
    } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  }
  return (
    <div className="space-y-3">
      <Field label="نام و نام خانوادگی"><input className="input" autoFocus value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="سمت" hint="مثلاً منشی، نظافت، پذیرش"><input className="input" value={f.job_title} onChange={(e) => setF({ ...f, job_title: e.target.value })} /></Field>
        <Field label="موبایل" hint="برای فرستادن رسید در واتساپ"><input className="input num" dir="ltr" value={f.mobile} onChange={(e) => setF({ ...f, mobile: e.target.value })} placeholder="09121234567" /></Field>
      </div>
      <Field label="حقوق ماهانه" hint="هر ماه همین مبلغ پیشنهاد می‌شود؛ می‌توانید هر ماه عوضش کنید"><MoneyInput value={f.salary} onChange={(v) => setF({ ...f, salary: v })} /></Field>
      {emp && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} />هنوز در کلینیک کار می‌کند (فعال)</label>}
      <button className="btn btn-primary w-full" disabled={!f.full_name.trim() || busy} onClick={save}>ذخیره</button>
    </div>
  );
}

/** the month's salaries of every employee at once (each one: an expense of the clinic, owed to them until paid) */
function SalaryForm({ month, rows, onDone }: { month: YM; rows: any[]; onDone: () => void }) {
  const toast = useToast();
  const todo = rows.filter((r) => r.active && !r.salaries.some((a: any) => a.status === "booked"));
  const [items, setItems] = useState(() => todo.map((r) => ({ staff_id: r.staff_id, name: r.name, on: true, amount: r.salary || 0, description: "" })));
  const [busy, setBusy] = useState(false);
  const total = items.filter((x) => x.on).reduce((a, x) => a + x.amount, 0);
  async function save() {
    setBusy(true);
    try {
      const r: any = await api("/api/employees/salaries", { body: { jy: month[0], jm: month[1], rows: items.filter((x) => x.on && x.amount > 0).map(({ staff_id, amount, description }) => ({ staff_id, amount, description })) } });
      if (r.errors.length) toast(r.errors.map((e: any) => `${e.name ?? ""}: ${e.error}`).join(" · "), "error");
      if (r.done.length) toast(`حقوق ${monthName(month)} برای ${r.done.length} نفر ثبت شد`);
      onDone();
    } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  }
  if (!todo.length) return <Empty text={`حقوق ${monthName(month)} برای همهٔ کارمندان فعال ثبت شده است`} />;
  return (
    <div className="space-y-3">
      <div className="rounded-2xl bg-violet-500/10 p-3 text-sm leading-7">
        حقوق {monthName(month)}: هزینهٔ کلینیک در همین ماه حساب می‌شود (از سود این ماه کم می‌شود) و تا وقتی پرداخت نشده، طلب کارمند است.
        مساعده‌هایی که در طول ماه داده‌اید از آن کم می‌شود.
      </div>
      {items.map((x, i) => (
        <div key={x.staff_id} className="grid items-center gap-2 rounded-xl p-2 sm:grid-cols-[auto_1fr_1fr_1.4fr]" style={{ background: "var(--surface)" }}>
          <input type="checkbox" checked={x.on} onChange={(e) => setItems(items.map((y, j) => (j === i ? { ...y, on: e.target.checked } : y)))} />
          <b>{x.name}</b>
          <MoneyInput value={x.amount} onChange={(v) => setItems(items.map((y, j) => (j === i ? { ...y, amount: v } : y)))} />
          <input className="input" placeholder="توضیح (مثلاً با اضافه‌کار)" value={x.description} onChange={(e) => setItems(items.map((y, j) => (j === i ? { ...y, description: e.target.value } : y)))} />
        </div>
      ))}
      <button className="btn btn-primary w-full py-3" disabled={busy || !total} onClick={save}>ثبت حقوق {monthName(month)} - جمع {money(total)}</button>
    </div>
  );
}

export default function Employees() {
  const { user } = useAuth();
  const toast = useToast();
  const allowed = can(user, "finance");
  const [month, setMonth] = useState<YM>(thisMonth());
  const { data, reload } = useApi<any>(allowed ? `/api/employees?jy=${month[0]}&jm=${month[1]}` : null, [month[0], month[1]]);
  const all = useApi<any[]>(allowed ? "/api/staff?kind=employee" : null);
  const [editing, setEditing] = useState<any>(null);
  const [salaries, setSalaries] = useState(false);
  const [payout, setPayout] = useState<{ row: any; kind: string } | null>(null);
  const [statement, setStatement] = useState<any>(null);
  const [payAll, setPayAll] = useState(false);
  const reloadAll = () => { reload(); all.reload(); };
  useEffect(() => { document.title = "کارمندان و حقوق"; }, []);
  if (!allowed) return (
    <div className="space-y-5">
      <PageHeader title="کارمندان و حقوق" icon={<BriefcaseBusiness size={22} />} />
      <Empty text="این صفحه (حقوق و پرداخت به کارمندان) فقط برای مدیر و حسابدار است." />
    </div>
  );
  const rows: any[] = data?.rows ?? [];
  const missing = rows.filter((r) => r.active && !r.salaries.some((a: any) => a.status === "booked"));
  const past = !sameMonth(month, thisMonth());
  const mayEdit = can(user, "settings");
  async function voidSalary(a: any, name: string) {
    if (!window.confirm(`حقوق ${monthName(month)} ${name} (${money(a.amount)}) باطل شود؟ بعد می‌توانید مبلغ درست را دوباره ثبت کنید.`)) return;
    try { await api(`/api/employees/salaries/${a.id}/void`, { body: {} }); toast("باطل شد"); reload(); } catch (e: any) { toast(e.message, "error"); }
  }
  return (
    <div className="space-y-5">
      <PageHeader title="کارمندان و حقوق" subtitle="کارمندان کلینیک با حقوق ثابت (منشی، نظافت، ...): حقوق هر ماه هزینهٔ کلینیک است؛ مساعده در طول ماه، پرداخت حقوق آخر ماه"
        icon={<BriefcaseBusiness size={22} />}
        actions={<div className="flex flex-wrap gap-2">
          {mayEdit && <button className="btn" onClick={() => setEditing({})}><Plus size={16} />کارمند جدید</button>}
          {rows.length > 0 && <button className="btn btn-primary" onClick={() => setSalaries(true)}><Wallet size={16} />ثبت حقوق {monthName(month)}</button>}
          {rows.length > 0 && <button className="btn" onClick={() => setPayAll(true)}><HandCoins size={16} />پرداخت حقوق همه</button>}
        </div>} />
      <MonthNav month={month} onChange={setMonth} />
      {!data ? <Loading /> : (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Stat label={`حقوق ${monthName(month)} (هزینه)`} value={cmoney(data.totals.salaries)} tone="pink"
              help="جمع حقوق ثبت‌شدهٔ کارمندان در این ماه؛ هزینهٔ کلینیک است و از سود همین ماه کم می‌شود." />
            <Stat label="داده‌شده در این ماه" value={cmoney(data.totals.given)} tone="amber" hint="مساعده و پرداخت حقوق" />
            <Stat label="ماندهٔ پایان ماه" value={cmoney(data.totals.closing)} tone="sky" help="مثبت: طلب کارمندان (هنوز نگرفته‌اند). منفی: بیشتر از حقوقشان گرفته‌اند." />
            <Stat label="مانده تا امروز" value={cmoney(data.totals.balance_now)} tone="violet" />
          </div>
          {missing.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 rounded-2xl bg-amber-500/10 p-3 text-sm leading-7 text-amber-800 dark:text-amber-200">
              حقوق {monthName(month)} برای {missing.map((r) => r.name).join("، ")} هنوز ثبت نشده{past ? " (این ماه تمام شده؛ تا ثبت نشود سود این ماه بیشتر از واقع نشان داده می‌شود)" : ""}.
              <button className="btn btn-sm" onClick={() => setSalaries(true)}>ثبت حقوق</button>
            </div>
          )}
          <Card pad={false}>
            <div className="overflow-x-auto">
              {rows.length === 0 ? <Empty text={mayEdit ? "هنوز کارمندی ثبت نشده؛ «کارمند جدید» را بزنید" : "هنوز کارمندی ثبت نشده"} /> : (
                <table className="table">
                  <thead><tr><th>کارمند</th><th>سمت</th><th>حقوق ماهانه</th><th>حقوق این ماه</th><th>ماندهٔ اول ماه</th><th>داده‌شده</th><th>ماندهٔ پایان ماه</th><th></th></tr></thead>
                  <tbody>{rows.map((r) => {
                    const booked = r.salaries.filter((a: any) => a.status === "booked");
                    const emp = all.data?.find((p) => p.id === r.staff_id);
                    return (
                      <tr key={r.staff_id} className={r.active ? "" : "opacity-60"}>
                        <td><button className="font-semibold text-violet-700 hover:underline dark:text-violet-300" onClick={() => setStatement(r)}>{r.name}</button>
                          {!r.active && <span className="badge mr-2 text-[10px]">غیرفعال</span>}</td>
                        <td className="muted">{r.job_title ?? "—"}</td>
                        <td className="num">{r.salary ? money(r.salary) : "—"}</td>
                        <td>{booked.length ? booked.map((a: any) => (
                          <span key={a.id} className="inline-flex items-center gap-1"><b className="num">{money(a.amount)}</b>
                            {can(user, "void") && <button className="btn btn-sm px-1.5 py-0 text-[11px]" title="باطل کردن (مبلغ اشتباه)" onClick={() => voidSalary(a, r.name)}>باطل</button>}</span>
                        )) : <span className="text-xs text-amber-600">ثبت نشده</span>}</td>
                        <td className={`num ${r.opening < 0 ? "text-rose-600" : ""}`}>{money(r.opening)}</td>
                        <td className="num">{money(r.given)}{r.advances > 0 && <div className="muted text-[10px]">مساعده {money(r.advances)}</div>}</td>
                        <td className={`num font-bold ${r.closing < 0 ? "text-rose-600" : ""}`}>{money(r.closing)}{r.closing < 0 && <div className="text-[10px] font-normal">بدهکار</div>}</td>
                        <td><div className="flex flex-wrap gap-1">
                          <button className="btn btn-sm" onClick={() => setPayout({ row: r, kind: "advance" })}><Wallet size={14} />{kindLabel(true, "advance")}</button>
                          {Math.min(r.remaining, r.balance_now) > 0 && <button className="btn btn-sm" onClick={() => setPayout({ row: r, kind: "settlement" })}><HandCoins size={14} />{kindLabel(true, "settlement")}</button>}
                          <button className="btn btn-sm px-2" title="صورت‌حساب ماه" onClick={() => setStatement(r)}><FileText size={14} /></button>
                          {mayEdit && emp && <button className="btn btn-sm px-2" title="ویرایش" onClick={() => setEditing(emp)}><Pencil size={14} /></button>}
                        </div></td>
                      </tr>
                    );
                  })}</tbody>
                </table>
              )}
            </div>
            <p className="muted px-5 py-3 text-xs leading-6">
              پرسنلی که از کارکردشان سهم می‌گیرند (لیزر، پوست، ...) در صفحهٔ «پرسنل و سهم‌ها» هستند. اینجا فقط کارمندان با حقوق ثابت: حقوق هر ماه را یک بار ثبت کنید
              (هزینهٔ همان ماه)، مساعده‌ها را هر وقت دادید ثبت کنید، آخر ماه «پرداخت حقوق» باقیمانده را می‌دهد. اگر کسی بیشتر از حقوقش گرفت، بدهکار می‌ماند و از ماه بعد کم می‌شود.
            </p>
          </Card>
        </>
      )}
      <Modal open={!!editing} onClose={() => setEditing(null)} title={editing?.id ? "ویرایش کارمند" : "کارمند جدید"}>
        {editing && <EmployeeForm emp={editing.id ? editing : undefined} onDone={() => { setEditing(null); reloadAll(); }} />}
      </Modal>
      <Modal open={salaries} onClose={() => setSalaries(false)} title={`حقوق ${monthName(month)}`} wide>
        {salaries && <SalaryForm month={month} rows={rows} onDone={() => { setSalaries(false); reload(); }} />}
      </Modal>
      <Modal open={!!payout} onClose={() => { setPayout(null); reload(); }} title={`${payout ? kindLabel(true, payout.kind) : ""} - ${payout?.row.name ?? ""}`}>
        {payout && <PayoutForm person={{ staff_id: payout.row.staff_id, name: payout.row.name, employee: true, mobile: payout.row.mobile, balance: payout.row.balance_now }}
          kind={payout.kind} amount={payout.kind === "settlement" ? Math.max(0, Math.min(payout.row.remaining, payout.row.balance_now)) : 0} month={month} onDone={() => { setPayout(null); reload(); }} />}
      </Modal>
      <Modal open={payAll} onClose={() => { setPayAll(false); reload(); }} title="پرداخت حقوق همهٔ کارمندان" wide>
        {payAll && <SettleAll who="employee" month={month} onDone={() => { setPayAll(false); reload(); }} />}
      </Modal>
      <Modal open={!!statement} onClose={() => { setStatement(null); reload(); }} title="صورت‌حساب ماه" wide>
        {statement && <Statement staffId={statement.staff_id} month={month} onChanged={reload} />}
      </Modal>
    </div>
  );
}
