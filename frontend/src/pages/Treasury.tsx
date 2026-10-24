import { Ban, Landmark, Plus, Wallet } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import JalaliPicker from "../components/JalaliPicker";
import MonthNav from "../components/MonthNav";
import { Card, Empty, Field, Loading, Modal, MoneyInput, PageHeader, Stat } from "../components/ui";
import { api } from "../lib/api";
import { cmoney, jdate, money } from "../lib/format";
import { can, useApi, useAuth, useToast } from "../lib/hooks";
import { monthName, thisMonth, toLocalIso, type YM } from "../lib/jalali";
import { QuickAdd } from "./Expenses";

/** The balance a line's treasury had when the program started (owner / admin), or money spent from it before the start. */
function MoveForm({ lines, lineId: line0, kind: kind0, onDone }: { lines: any[]; lineId: number; kind: string; onDone: () => void }) {
  const toast = useToast();
  const { user } = useAuth();
  const openingOk = can(user, "settings"); // the treasury's balance at the start: owner / admin
  const staff = (useApi<any[]>("/api/staff").data ?? []).filter((p) => p.is_active);
  const [kind, setKind] = useState(kind0);
  const [lineId, setLineId] = useState(line0 || lines[0]?.id || 0);
  const [staffId, setStaffId] = useState(0);
  const [amount, setAmount] = useState(0);
  const [negative, setNegative] = useState(false);
  const [at, setAt] = useState(toLocalIso(new Date()));
  const [category, setCategory] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <div className="space-y-3 text-sm">
      <div className="grid grid-cols-2 gap-2">
        {[["opening", "موجودی اول خزانه", "مانده‌ای که خزانهٔ این لاین روز شروع کار با برنامه داشت"],
          ["spent_before", "خرج پیش از شروع برنامه", "خرجی که قبل از شروع کار با برنامه از خزانه شده (از حساب‌ها کم نمی‌شود)"]]
          .filter(([k]) => k !== "opening" || openingOk).map(([k, label, hint]) => (
          <button key={k} type="button" className={`rounded-2xl border p-3 text-right ${kind === k ? "border-violet-500 bg-violet-500/10 font-bold" : ""}`}
            style={kind === k ? undefined : { borderColor: "var(--border)" }} onClick={() => setKind(k)}>
            <div>{label}</div><div className="muted text-[11px] font-normal leading-5">{hint}</div></button>
        ))}
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="لاین"><select className="input" value={lineId} onChange={(e) => { setLineId(Number(e.target.value)); setStaffId(0); }}>{lines.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select></Field>
        <Field label="خزانهٔ پرسنل (اختیاری)"><select className="input" value={staffId} onChange={(e) => setStaffId(Number(e.target.value))}>
          <option value={0}>کل لاین</option>{staff.filter((p) => p.line_id === lineId).map((p) => <option key={p.id} value={p.id}>{p.full_name}</option>)}</select></Field>
        <Field label="مبلغ"><MoneyInput value={amount} onChange={setAmount} autoFocus />
          {kind === "opening" && <label className="mt-1 flex items-center gap-2 text-xs"><input type="checkbox" checked={negative} onChange={(e) => setNegative(e.target.checked)} />منفی بود (بیشتر از خزانه خرج شده بود)</label>}</Field>
        <Field label="تاریخ"><JalaliPicker pastOnly value={at} onChange={setAt} /></Field>
        {kind === "opening" && <p className="muted text-xs leading-6 sm:col-span-2">موجودی اول یعنی خزانهٔ این لاین در همین تاریخ چقدر بود؛
          هرچه پیش از این تاریخ در این لاین بوده (خزانهٔ کارکرد نرم‌افزار قبلی، خرج‌های قبلی) دیگر جدا حساب نمی‌شود، چون در همین مبلغ هست.</p>}
        {kind === "spent_before" && <Field label="برای چه؟"><input className="input" value={category} onChange={(e) => setCategory(e.target.value)} placeholder="مثلاً مواد مصرفی، تعمیر دستگاه" /></Field>}
        <Field label="توضیح (اختیاری)"><input className="input" value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
      </div>
      <button className="btn btn-primary w-full py-3" disabled={busy || !amount || !lineId} onClick={async () => {
        setBusy(true);
        try {
          await api("/api/treasury/moves", { body: { kind, line_id: lineId, staff_id: staffId || null, amount: kind === "opening" && negative ? -amount : amount,
            at, category, description } });
          toast("ثبت شد");
          onDone();
        } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
      }}><Plus size={16} />ثبت</button>
    </div>
  );
}

export default function Treasury() {
  const toast = useToast();
  const { user } = useAuth();
  const allowed = can(user, "reports");
  const { data, reload } = useApi<any>(allowed ? "/api/treasury/fund" : null, []);
  const [lineId, setLineId] = useState<number | null>(null);
  const [month, setMonth] = useState<YM>(thisMonth());
  const [perStaff, setPerStaff] = useState(false);
  const line = data?.lines?.find((l: any) => (l.line_id ?? 0) === lineId) ?? data?.lines?.[0];
  const lid = line?.line_id ?? null;
  const spending = useApi<any>(allowed && line ? `/api/treasury/spending?jy=${month[0]}&jm=${month[1]}${lid ? `&line_id=${lid}` : ""}` : null, [lid, month[0], month[1]]);
  const staffMonths = useApi<any>(allowed && perStaff && line ? `/api/treasury/fund/${lid ?? 0}` : null, [lid, perStaff]);
  const [spend, setSpend] = useState(false);
  const [move, setMove] = useState<string | null>(null);
  const reloadAll = () => { reload(); spending.reload(); staffMonths.reload(); };
  if (!allowed) return <div className="space-y-5"><PageHeader title="خزانهٔ لاین‌ها" icon={<Landmark size={22} />} /><Empty text="این صفحه برای مدیر و حسابدار است." /></div>;
  async function voidRow(r: any) {
    if (!window.confirm(`«${r.category}» ${money(r.amount)} باطل شود؟`)) return;
    try {
      if (r.type === "expense") await api(`/api/expenses/${r.id}/void`, { body: { reason: "از صفحهٔ خزانه" } });
      else await api(`/api/treasury/moves/${r.id}/void`, { method: "POST" });
      toast("باطل شد");
      reloadAll();
    } catch (e: any) { toast(e.message, "error"); }
  }
  return (
    <div className="space-y-5">
      <PageHeader title="خزانهٔ لاین‌ها" subtitle="صندوق خرج‌های هر لاین: درصد خزانه از درآمد هر ماه جمع می‌شود و خرج‌های همان لاین (مواد مصرفی، تعمیر دستگاه…) از آن برداشته می‌شود"
        icon={<Landmark size={22} />}
        actions={<div className="flex flex-wrap gap-2">
          {can(user, "finance") && <button className="btn btn-primary" onClick={() => setSpend(true)}><Wallet size={16} />خرج از خزانه</button>}
          {can(user, "finance") && <button className="btn" onClick={() => setMove(can(user, "settings") ? "opening" : "spent_before")}><Plus size={16} />موجودی اول / خرج قبلی</button>}
        </div>} />
      {!data ? <Loading /> : data.lines.length === 0 ? (
        <Card><Empty text="هنوز خزانه‌ای جمع نشده؛ درصد خزانهٔ هر لاین را در «پرسنل و سهم‌ها» تعیین کنید." /></Card>
      ) : (
        <>
          <div className="rounded-2xl bg-violet-500/10 p-3 text-sm leading-7">
            خرج از خزانه هم هزینهٔ کلینیک است و از سود کم می‌شود (مثل هر هزینهٔ دیگر)؛ این صفحه فقط نشان می‌دهد هر لاین چقدر کنار گذاشته، چقدر خرج کرده و چقدر مانده.
            خزانهٔ کارکردهای نرم‌افزار قبلی هم (با درصد خزانهٔ همان ماه‌ها) حساب می‌شود. خرج‌های قبل از شروع کار با برنامه را با «خرج قبلی» وارد کنید.
          </div>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Stat label="موجودی همهٔ خزانه‌ها" value={cmoney(data.balance)} tone={data.balance < 0 ? "pink" : "violet"} icon={<Landmark size={18} />} />
            {data.lines.slice(0, 3).map((l: any) => (
              <Stat key={l.line_id ?? 0} label={`خزانهٔ ${l.name}`} value={cmoney(l.balance)} tone={l.balance < 0 ? "pink" : "emerald"}
                hint={`این ماه جمع ${cmoney(l.month_accrued)} · خرج ${cmoney(l.month_spent)}`} onClick={() => setLineId(l.line_id ?? 0)} />
            ))}
          </div>
          <div className="flex flex-wrap gap-2">
            {data.lines.map((l: any) => (
              <button key={l.line_id ?? 0} className={`btn btn-sm ${(l.line_id ?? null) === lid ? "btn-primary" : ""}`} onClick={() => setLineId(l.line_id ?? 0)}>
                {l.name}: <span className={`num ${l.balance < 0 ? "text-rose-300" : ""}`}>{cmoney(l.balance)}</span></button>
            ))}
          </div>
          {line && (
            <div className="grid gap-5 lg:grid-cols-[1.3fr_1fr]">
              <Card title={`ماه به ماه - ${line.name}`} pad={false} className="min-w-0"
                actions={<label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={perStaff} onChange={(e) => setPerStaff(e.target.checked)} />به تفکیک پرسنل</label>}>
                <div className="overflow-x-auto">
                  {perStaff ? (!staffMonths.data ? <Loading /> : (
                    <table className="table text-xs">
                      <thead><tr><th>ماه</th>{staffMonths.data.staff.map((p: any) => <th key={p.staff_id ?? 0}>{p.name}</th>)}</tr></thead>
                      <tbody>{staffMonths.data.months.map((m: any) => (
                        <tr key={`${m.jy}-${m.jm}`}><td className="font-semibold">{monthName([m.jy, m.jm])}</td>
                          {m.staff.map((c: any) => <td key={c.staff_id ?? 0} className="num">
                            <div className={c.balance_end < 0 ? "font-bold text-rose-600" : "font-semibold"}>{money(c.balance_end)}</div>
                            {(c.accrued || c.spent) ? <div className="muted">جمع {money(c.accrued)}{c.spent ? ` · خرج ${money(c.spent)}` : ""}</div> : null}</td>)}</tr>
                      ))}</tbody>
                    </table>)) : (
                    <table className="table">
                      <thead><tr><th>ماه</th><th>خزانهٔ ماه</th><th>خرج از خزانه</th><th>ماندهٔ پایان ماه</th></tr></thead>
                      <tbody>{line.months.map((m: any) => (
                        <tr key={`${m.jy}-${m.jm}`} className="cursor-pointer" onClick={() => setMonth([m.jy, m.jm])}>
                          <td className="font-semibold">{monthName([m.jy, m.jm])}{m.opening ? <div className="muted text-[11px] font-normal">موجودی اول {money(m.opening)}</div> : null}</td>
                          <td className="num text-emerald-600">{m.accrued ? money(m.accrued) : "—"}</td>
                          <td className="num text-rose-600">{m.spent ? money(m.spent) : "—"}</td>
                          <td className={`num font-bold ${m.balance_end < 0 ? "text-rose-600" : ""}`}>{money(m.balance_end)}</td>
                        </tr>
                      ))}</tbody>
                    </table>)}
                </div>
              </Card>
              <div className="space-y-5">
                <Card title="خزانهٔ هر پرسنل (تا امروز)" pad={false} className="min-w-0">
                  <table className="table text-sm">
                    <thead><tr><th>پرسنل</th><th>جمع شده</th><th>خرج</th><th>مانده</th></tr></thead>
                    <tbody>{line.staff.map((p: any) => (
                      <tr key={p.staff_id ?? 0}><td className="font-semibold">{p.name}</td><td className="num">{money(p.accrued + p.opening)}</td>
                        <td className="num">{p.spent ? money(p.spent) : "—"}</td><td className={`num font-bold ${p.balance < 0 ? "text-rose-600" : ""}`}>{money(p.balance)}</td></tr>
                    ))}</tbody>
                  </table>
                  <p className="muted px-5 py-3 text-xs leading-6">خزانهٔ هر پرسنل = خزانهٔ خدمات خودش منهای خرج‌هایی که برای او ثبت شده. «عمومی لاین» خرج‌هایی است که برای کل لاین ثبت شده.</p>
                </Card>
                <Card title="خرج‌های خزانه در ماه" pad={false} className="min-w-0">
                  <div className="p-3"><MonthNav month={month} onChange={setMonth} /></div>
                  {!spending.data ? <Loading /> : spending.data.rows.length === 0 ? <Empty text="در این ماه از خزانهٔ این لاین خرجی ثبت نشده" /> : (
                    <div className="space-y-1 p-3">
                      {spending.data.rows.map((r: any) => (
                        <div key={`${r.type}-${r.id}`} className={`flex flex-wrap items-center gap-2 rounded-xl px-3 py-2 text-sm ${r.void ? "opacity-50 line-through" : ""}`} style={{ background: "var(--surface)" }}>
                          <span className="muted text-xs">{jdate(r.at)}</span><b>{r.category}</b><b className="num">{money(r.amount)}</b>
                          {r.staff && <span className="badge text-[10px]">{r.staff}</span>}
                          {r.type !== "expense" && <span className="badge text-[10px]">{r.type === "opening" ? "موجودی اول" : "پیش از شروع برنامه"}</span>}
                          {r.description && <span className="muted text-xs">{r.description}</span>}
                          {!r.void && can(user, "void") && <button className="btn btn-sm mr-auto px-2 text-rose-600" onClick={() => voidRow(r)}><Ban size={13} /></button>}
                        </div>
                      ))}
                    </div>
                  )}
                  <p className="muted px-5 pb-3 text-xs">هزینه‌ها با تیک «از خزانه» در صفحهٔ <Link to="/expenses" className="underline">هزینه‌ها</Link> هم دیده می‌شوند.</p>
                </Card>
              </div>
            </div>
          )}
        </>
      )}
      <Modal open={spend} onClose={() => setSpend(false)} title="خرج از خزانه">
        {spend && <QuickAdd lineId={lid ?? data?.active_lines?.[0]?.id ?? 0} fromTreasury onDone={() => { setSpend(false); reloadAll(); }} />}
      </Modal>
      <Modal open={!!move} onClose={() => setMove(null)} title="موجودی اول / خرج پیش از شروع برنامه">
        {move && <MoveForm lines={data?.active_lines ?? []} lineId={lid ?? 0} kind={move} onDone={() => { setMove(null); reloadAll(); }} />}
      </Modal>
    </div>
  );
}
