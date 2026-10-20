import { Ban, Coffee, Landmark, ListChecks, Plus, Search, Settings2, Wallet } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import AccountSelect from "../components/AccountSelect";
import JalaliPicker from "../components/JalaliPicker";
import MonthNav from "../components/MonthNav";
import { Card, Empty, Field, Loading, Modal, MoneyInput, PageHeader, Stat } from "../components/ui";
import { api } from "../lib/api";
import { cmoney, jdate, money } from "../lib/format";
import { can, useApi, useAuth, useToast } from "../lib/hooks";
import { monthName, sameMonth, thisMonth, toLocalIso, type YM } from "../lib/jalali";

/** what usually goes in each quick category (shown under the chips) */
const EXAMPLES: Record<string, string> = {
  "پذیرایی": "چای، قهوه، شیرینی، آب معدنی برای مشتری‌ها و پرسنل",
  "بهداشتی و نظافت": "دستمال کاغذی، مواد شوینده، کیسه زباله، ضدعفونی",
  "مواد مصرفی": "مواد و وسایل یک‌بارمصرف خدمات (دستکش، ملحفه، ژل...)",
  "اجاره": "اجارهٔ ماهانهٔ محل",
  "شارژ ساختمان": "شارژ ماهانه",
  "قبوض": "آب، برق، گاز",
  "اینترنت و تلفن": "اینترنت، تلفن ثابت، شارژ موبایل کلینیک",
  "نگهداری و تعمیرات": "تعمیر دستگاه، سرویس، لوازم یدکی",
  "تجهیزات": "دستگاه و وسیلهٔ تازه",
  "لوازم اداری": "کاغذ، کارتریج، خودکار",
  "تبلیغات": "تبلیغ اینستاگرام، چاپ تراکت",
  "حمل و پیک": "پیک، اسنپ، پست",
  "کارمزد بانکی": "کارمزد کارتخوان و انتقال",
  "مالیات و عوارض": "مالیات، عوارض شهرداری",
};
const SALARY = "حقوق و دستمزد";
const COMMISSION = "پورسانت پرسنل";

export function QuickAdd({ onDone, lineId: line0 = 0, fromTreasury: ft0 = false }: { onDone: () => void; lineId?: number; fromTreasury?: boolean }) {
  const toast = useToast();
  const cats = useApi<any>("/api/expenses/categories").data;
  const lines = (useApi<any[]>("/api/lines").data ?? []).filter((l) => l.is_active || l.id === line0);
  const staffOfLine = (useApi<any[]>("/api/staff").data ?? []).filter((p) => p.is_active);
  const [lineId, setLineId] = useState(line0);
  const [fromTreasury, setFromTreasury] = useState(ft0 && !!line0);
  const [staffId, setStaffId] = useState(0);
  const [category, setCategory] = useState(ft0 ? "مواد مصرفی" : "");
  const [other, setOther] = useState(false);
  const [amount, setAmount] = useState(0);
  const [acc, setAcc] = useState(0);
  const [at, setAt] = useState(toLocalIso(new Date()));
  const [description, setDescription] = useState("");
  const [dup, setDup] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const quick: string[] = cats?.quick ?? [];
  const used: string[] = (cats?.used ?? []).filter((x: string) => x !== SALARY && x !== COMMISSION);
  async function save(force = false) {
    setBusy(true);
    try {
      const r: any = await api("/api/expenses", { body: { category, amount, payment_account_id: acc, spent_at: at, description, force,
        line_id: lineId || null, from_treasury: fromTreasury && !!lineId, staff_id: fromTreasury && staffId ? staffId : null } });
      toast(`هزینهٔ «${r.category}» ${money(amount)} ثبت شد${fromTreasury && lineId ? " (از خزانه)" : ""}`);
      setAmount(0);
      setDescription("");
      setDup(null);
      setAt(toLocalIso(new Date()));
      onDone();
    } catch (e: any) {
      if (e.status === 409) setDup(e.message); else toast(e.message, "error");
    } finally { setBusy(false); }
  }
  const chip = (c: string) => (
    <button key={c} type="button" onClick={() => { setCategory(c); setOther(false); }}
      className={`rounded-full border px-3 py-1.5 text-sm ${category === c && !other ? "border-violet-500 bg-violet-500/15 font-bold text-violet-700 dark:text-violet-300" : ""}`}
      style={category === c && !other ? undefined : { borderColor: "var(--border)" }}>{c}</button>
  );
  return (
    <div className="space-y-3">
      <Field label="چه خریدید؟ (دسته)">
        <div className="flex flex-wrap gap-2">
          {quick.map(chip)}
          {used.filter((c) => !quick.includes(c)).map(chip)}
          <button type="button" onClick={() => { setOther(true); setCategory(""); }} className={`rounded-full border border-dashed px-3 py-1.5 text-sm ${other ? "border-violet-500" : ""}`}
            style={other ? undefined : { borderColor: "var(--border)" }}>+ دستهٔ دیگر</button>
        </div>
        {other && <input className="input mt-2" autoFocus placeholder="نام دسته (مثلاً گل و تزئینات)" value={category} onChange={(e) => setCategory(e.target.value)} />}
        {EXAMPLES[category] && !other && <div className="muted mt-1 text-xs">{EXAMPLES[category]}</div>}
        {category.trim() === SALARY && <div className="mt-2 rounded-xl bg-amber-500/10 p-2 text-xs leading-6 text-amber-800 dark:text-amber-200">
          حقوق کارمندان ثابت (منشی، نظافت...) را در صفحهٔ <Link to="/employees" className="font-bold underline">کارمندان و حقوق</Link> ثبت کنید تا مساعده‌ها و ماندهٔ هر نفر هم حساب شود؛ اینجا فقط برای دستمزد یک‌باره (مثلاً کارگر روزمزد).</div>}
        {category.trim() === COMMISSION && <div className="mt-2 rounded-xl bg-rose-500/10 p-2 text-xs leading-6 text-rose-700 dark:text-rose-300">
          سهم پرسنل را اینجا ثبت نکنید: سهم هر خدمت خودش حساب می‌شود؛ پرداخت به پرسنل را در صفحهٔ <Link to="/staff" className="font-bold underline">پرسنل و سهم‌ها</Link> با «برداشتی» یا «تسویه» ثبت کنید (وگرنه دو بار از سود کم می‌شود).</div>}
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="برای کدام لاین؟ (اختیاری)" hint="مثلاً مواد مصرفی لاین لیزر">
          <select className="input" value={lineId} onChange={(e) => { const v = Number(e.target.value); setLineId(v); if (!v) setFromTreasury(false); }}>
            <option value={0}>— همهٔ کلینیک —</option>{lines.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
        </Field>
        <Field label="از خزانه؟">
          <label className={`flex items-center gap-2 rounded-xl border p-2 text-sm ${!lineId ? "opacity-50" : ""}`} style={{ borderColor: "var(--border)" }}>
            <input type="checkbox" disabled={!lineId} checked={fromTreasury} onChange={(e) => setFromTreasury(e.target.checked)} />از خزانهٔ همین لاین خرج شد
          </label>
          {fromTreasury && lineId > 0 && (
            <select className="input mt-1 text-sm" value={staffId} onChange={(e) => setStaffId(Number(e.target.value))}>
              <option value={0}>خزانهٔ کل لاین</option>
              {staffOfLine.filter((p) => p.line_id === lineId).map((p) => <option key={p.id} value={p.id}>خزانهٔ {p.full_name}</option>)}
            </select>)}
        </Field>
        <Field label="مبلغ"><MoneyInput value={amount} onChange={setAmount} /></Field>
        <Field label="پرداخت از"><AccountSelect value={acc} onChange={setAcc} remember="expense" /></Field>
        <Field label="تاریخ"><JalaliPicker pastOnly value={at} onChange={setAt} /></Field>
        <Field label="توضیح (اختیاری)"><input className="input" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="مثلاً ۲ بسته قهوه" /></Field>
      </div>
      {dup && (
        <div className="space-y-2 rounded-2xl bg-rose-500/10 p-3 text-sm">
          <div>{dup}</div>
          <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => save(true)}>بله، خرید جداگانه‌ای است؛ ثبت شود</button>
        </div>
      )}
      <button className="btn btn-primary w-full py-3" disabled={!amount || !category.trim() || !acc || busy} onClick={() => save(false)}>
        <Plus size={16} />ثبت هزینه {amount ? money(amount) : ""}
      </button>
    </div>
  );
}

/** an expense entered before: which line (and staff member) it was for and whether it came from that line's treasury */
function TagExpense({ e, onDone }: { e: any; onDone: () => void }) {
  const toast = useToast();
  const lines = (useApi<any[]>("/api/lines").data ?? []).filter((l) => l.is_active || l.id === e.line_id);
  const staff = (useApi<any[]>("/api/staff").data ?? []).filter((p) => p.is_active || p.id === e.staff_id);
  const [lineId, setLineId] = useState<number>(e.line_id ?? 0);
  const [fromTreasury, setFromTreasury] = useState<boolean>(!!e.from_treasury);
  const [staffId, setStaffId] = useState<number>(e.staff_id ?? 0);
  return (
    <div className="space-y-3 text-sm">
      <div className="rounded-2xl p-3" style={{ background: "var(--surface)" }}>{e.category} · <b className="num">{money(e.amount)}</b> · {jdate(e.spent_at)}</div>
      <Field label="برای لاین">
        <select className="input" value={lineId} onChange={(ev) => { const v = Number(ev.target.value); setLineId(v); if (!v) setFromTreasury(false); }}>
          <option value={0}>— همهٔ کلینیک —</option>{lines.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
        </select>
      </Field>
      <label className="flex items-center gap-2"><input type="checkbox" disabled={!lineId} checked={fromTreasury} onChange={(ev) => setFromTreasury(ev.target.checked)} />از خزانهٔ همین لاین خرج شد</label>
      {fromTreasury && <Field label="خزانهٔ کدام پرسنل؟">
        <select className="input" value={staffId} onChange={(ev) => setStaffId(Number(ev.target.value))}>
          <option value={0}>خزانهٔ کل لاین</option>{staff.filter((p) => p.line_id === lineId).map((p) => <option key={p.id} value={p.id}>{p.full_name}</option>)}
        </select></Field>}
      <p className="muted text-xs leading-6">مبلغ، تاریخ و حساب عوض نمی‌شود و هزینه همان‌طور از سود کم می‌شود؛ فقط معلوم می‌شود از خزانهٔ کدام لاین بوده.</p>
      <button className="btn btn-primary w-full" onClick={async () => {
        try { await api(`/api/expenses/${e.id}/treasury`, { body: { line_id: lineId || null, from_treasury: fromTreasury && !!lineId, staff_id: staffId || null } }); toast("ذخیره شد"); onDone(); }
        catch (err: any) { toast(err.message, "error"); }
      }}>ذخیره</button>
    </div>
  );
}

function CategoryEditor({ onDone }: { onDone: () => void }) {
  const toast = useToast();
  const cats = useApi<any>("/api/expenses/categories").data;
  const [text, setText] = useState<string | null>(null);
  const value = text ?? (cats?.quick ?? []).join("\n");
  return (
    <div className="space-y-3">
      <div className="muted text-sm leading-7">هر خط یک دسته. این‌ها دکمه‌های سریع صفحهٔ هزینه‌ها هستند (هر دسته در دفاتر حساب جدای خودش را دارد). دسته‌هایی که قبلاً استفاده شده‌اند همیشه می‌مانند.</div>
      <textarea className="input h-64" value={value} onChange={(e) => setText(e.target.value)} />
      <button className="btn btn-primary w-full" onClick={async () => {
        try { await api("/api/expenses/categories", { method: "PUT", body: { names: value.split("\n") } }); toast("ذخیره شد"); onDone(); } catch (e: any) { toast(e.message, "error"); }
      }}>ذخیره</button>
    </div>
  );
}

export default function Expenses() {
  const toast = useToast();
  const { user } = useAuth();
  const [month, setMonth] = useState<YM>(thisMonth());
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("");
  const { data, reload } = useApi<any>(`/api/expenses?jy=${month[0]}&jm=${month[1]}&q=${encodeURIComponent(q)}&category=${encodeURIComponent(cat)}`, [month[0], month[1], q, cat]);
  const [adding, setAdding] = useState(false);
  const [editCats, setEditCats] = useState(false);
  const [tagging, setTagging] = useState<any>(null);
  const profit = data?.profit?.total ?? 0;
  const general = (data?.total ?? 0) - (data?.salaries ?? 0);
  const biggest = data?.by_category?.find((c: any) => c.name !== SALARY); // the general expenses' biggest (not salaries)
  async function voidOne(e: any) {
    const reason = window.prompt(`هزینهٔ «${e.category}» ${money(e.amount)} باطل شود؟ (مثلاً دو بار ثبت شده) دلیل:`, "");
    if (reason === null) return;
    try { await api(`/api/expenses/${e.id}/void`, { body: { reason } }); toast("باطل شد؛ پول به همان حساب برگشت"); reload(); } catch (err: any) { toast(err.message, "error"); }
  }
  return (
    <div className="space-y-5">
      <PageHeader title="هزینه‌های کلینیک" subtitle="قهوه و پذیرایی، دستمال کاغذی، اجاره، قبوض، تعمیرات... همه از سود کم می‌شوند" icon={<Wallet size={22} />}
        actions={<div className="flex flex-wrap gap-2">
          {can(user, "settings") && <button className="btn" onClick={() => setEditCats(true)}><Settings2 size={16} />دسته‌ها</button>}
          <button className="btn btn-primary" onClick={() => setAdding(true)}><Plus size={16} />ثبت هزینه</button>
        </div>} />
      <MonthNav month={month} onChange={setMonth} />
      {!data ? <Loading /> : (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Stat label={`هزینه‌های ${monthName(month)}`} value={cmoney(data.total)} tone="pink" icon={<Coffee size={18} />}
              help="جمع همهٔ هزینه‌های این ماه در دفاتر: خریدهای کلینیک و حقوق کارمندان (هزینه‌ای که باطل شده حساب نمی‌شود)." />
            <Stat label="هزینه‌های عمومی" value={cmoney(general)} tone="amber" hint={[biggest ? `بیشترین: ${biggest.name}` : "", data.treasury_spent ? `از خزانهٔ لاین‌ها: ${cmoney(data.treasury_spent)}` : ""].filter(Boolean).join(" · ") || undefined} />
            <Link to="/employees"><Stat label="حقوق کارمندان" value={cmoney(data.salaries)} tone="sky" hint="از صفحهٔ کارمندان" /></Link>
            <Stat label={`سود ${monthName(month)}`} value={cmoney(profit)} tone={profit < 0 ? "pink" : "emerald"}
              hint={sameMonth(month, thisMonth()) ? "تا امروز" : undefined}
              help="درآمد این ماه (خدمات، محصولات، بیعانهٔ سوخت‌شده) منهای سهم پرسنل، بهای کالاها و همهٔ هزینه‌ها؛ همان سود دفاتر." />
          </div>
          <div className="grid gap-5 lg:grid-cols-[1fr_1.6fr]">
            <Card title="به تفکیک دسته" className="min-w-0">
              {data.by_category.length === 0 ? <Empty text="هزینه‌ای در این ماه نیست" /> : (
                <div className="space-y-2">
                  {data.by_category.map((c: any) => (
                    <button key={c.name} className={`block w-full text-right ${cat === c.name ? "font-bold" : ""}`} onClick={() => setCat(cat === c.name ? "" : c.name)}>
                      <div className="flex justify-between text-sm"><span>{c.name}</span><span className="num">{money(c.value)}</span></div>
                      <div className="mt-1 h-2 overflow-hidden rounded-full" style={{ background: "var(--surface)" }}>
                        <div className="h-full rounded-full bg-violet-500" style={{ width: `${Math.max(2, Math.round((c.value / (data.total || 1)) * 100))}%` }} />
                      </div>
                    </button>
                  ))}
                  <p className="muted pt-1 text-xs">روی هر دسته بزنید تا فقط هزینه‌های آن را ببینید.</p>
                </div>
              )}
            </Card>
            <Card className="min-w-0" title={<span className="flex items-center gap-2"><ListChecks size={16} />هزینه‌های ثبت‌شده{cat && <span className="badge">{cat} <button className="mr-1" onClick={() => setCat("")}>✕</button></span>}</span>} pad={false}
              actions={<div className="relative"><Search size={14} className="muted absolute right-2 top-2.5" /><input className="input w-32 py-1.5 pr-7 text-sm sm:w-40" placeholder="جستجو در شرح" value={q} onChange={(e) => setQ(e.target.value)} /></div>}>
              <div className="overflow-x-auto">
                {cat === SALARY && (
                  <p className="m-4 rounded-xl bg-sky-500/10 p-3 text-xs leading-6">
                    حقوق کارمندانی که در صفحهٔ <Link to="/employees" className="font-bold underline">کارمندان و حقوق</Link> ثبت می‌شود اینجا جدا فهرست نمی‌شود
                    (جمعش در کادر «به تفکیک دسته» هست)؛ جزئیات هر نفر در همان صفحه است.{data.rows.length ? " ردیف‌های زیر دستمزدهایی است که همین‌جا ثبت شده‌اند." : ""}
                  </p>)}
                {data.rows.length === 0 ? (cat === SALARY ? null : <Empty text="موردی نیست" />) : (
                  <table className="table">
                    <thead><tr><th>تاریخ</th><th>دسته</th><th>مبلغ</th><th>از حساب</th><th>شرح</th><th></th></tr></thead>
                    <tbody>{data.rows.map((e: any) => (
                      <tr key={e.id} className={e.voided_at ? "opacity-50" : ""}>
                        <td className="num muted">{jdate(e.spent_at)}</td>
                        <td className="font-semibold">{e.category}
                          {(e.line || e.from_treasury) && <div className="text-[11px] font-normal">{e.line && <span className="muted">{e.line}</span>}
                            {e.from_treasury && <span className="badge mr-1 bg-violet-500/15 text-[10px] text-violet-700 dark:text-violet-300">از خزانه</span>}</div>}</td>
                        <td className={`num ${e.voided_at ? "line-through" : ""}`}>{money(e.amount)}</td>
                        <td>{e.account ?? "—"}</td>
                        <td className="muted">{e.description}{e.voided_at && <div className="text-xs text-rose-600">باطل شد{e.void_reason ? `: ${e.void_reason}` : ""}</div>}</td>
                        <td><div className="flex gap-1">
                          {!e.voided_at && e.category !== SALARY && <button className="btn btn-sm px-2" title="لاین و خزانه" onClick={() => setTagging(e)}><Landmark size={13} /></button>}
                          {!e.voided_at && can(user, "void") && <button className="btn btn-sm px-2 text-rose-600" title="باطل کردن (اشتباه ثبت شده)" onClick={() => voidOne(e)}><Ban size={13} /></button>}
                        </div></td>
                      </tr>
                    ))}</tbody>
                  </table>
                )}
              </div>
              <p className="muted px-5 py-3 text-xs leading-6">هزینه‌ای که اشتباه ثبت شده را «باطل» کنید (در همان تاریخ خودش برمی‌گردد) و درستش را دوباره ثبت کنید. سهم پرسنل و حقوق کارمندان در صفحه‌های خودشان ثبت می‌شوند.</p>
            </Card>
          </div>
        </>
      )}
      <Modal open={adding} onClose={() => setAdding(false)} title="ثبت هزینه">
        {adding && <QuickAdd onDone={() => reload()} />}
      </Modal>
      <Modal open={!!tagging} onClose={() => setTagging(null)} title="این هزینه برای کدام لاین بود؟">
        {tagging && <TagExpense e={tagging} onDone={() => { setTagging(null); reload(); }} />}
      </Modal>
      <Modal open={editCats} onClose={() => setEditCats(false)} title="دسته‌های هزینه">
        {editCats && <CategoryEditor onDone={() => setEditCats(false)} />}
      </Modal>
    </div>
  );
}
