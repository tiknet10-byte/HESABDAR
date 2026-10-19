import { ArrowDownCircle, ArrowUpCircle, Coins, Gem, History, Plus, Scale, Search, Truck, UserRoundCheck } from "lucide-react";
import { useEffect, useState } from "react";
import JalaliPicker from "../components/JalaliPicker";
import { Badge, Card, Empty, Field, Loading, Modal, MoneyInput, PageHeader, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { money, num } from "../lib/format";
import { formatJ, toGregorian, toJalali, toLocalIso } from "../lib/jalali";
import { can, useApi, useAuth, useToast } from "../lib/hooks";

const UNIT_BY_KIND: Record<string, string> = { gold: "گرم", coin: "عدد", currency: "دلار", other: "عدد" };
const MOVE_KINDS: { value: string; label: string; hint: string }[] = [
  { value: "buy", label: "خریدم", hint: "با پول صندوق یا کارت خریدید" },
  { value: "sell", label: "فروختم", hint: "پولش به صندوق یا کارت آمد" },
  { value: "owner_in", label: "خودم آوردم", hint: "از پول شخصی شما؛ سرمایهٔ شما بیشتر می‌شود" },
  { value: "owner_out", label: "خودم بردم", hint: "برای خودتان برداشتید؛ از سرمایهٔ شما کم می‌شود" },
  { value: "opening", label: "از قبل داشتیم", hint: "قبل از شروع کار با این برنامه خریده شده بود" },
];

function yearStart(): string {
  const d = new Date();
  const [jy] = toJalali(d.getFullYear(), d.getMonth() + 1, d.getDate());
  const [gy, gm, gd] = toGregorian(jy, 1, 1);
  return `${gy}-${String(gm).padStart(2, "0")}-${String(gd).padStart(2, "0")}`;
}

function Row({ label, value, strong, tone, sub }: { label: React.ReactNode; value: number; strong?: boolean; tone?: string; sub?: React.ReactNode }) {
  return (
    <div className={`flex items-center justify-between gap-2 rounded-xl px-3 py-2 ${strong ? "font-extrabold" : ""}`} style={{ background: "var(--surface)" }}>
      <span>{label}{sub && <span className="muted mr-1 text-xs font-normal">{sub}</span>}</span>
      <span className={`num ${tone ?? ""}`}>{money(value)}</span>
    </div>
  );
}

export default function Capital() {
  const { user } = useAuth();
  if (!can(user, "capital")) return <Empty text="این صفحه فقط برای مدیر است." />;
  return <CapitalPage />;
}

function CapitalPage() {
  const [range, setRange] = useState<"year" | "all">("year");
  const start = range === "year" ? yearStart() : "2000-01-01";
  const { data: p, reload } = useApi<any>(`/api/capital/position?start=${start}`);
  const { data: convertible, reload: reloadConv } = useApi<any[]>("/api/capital/convertible");
  const [modal, setModal] = useState<null | { kind: string; holding?: any }>(null);
  const refresh = () => { reload(); reloadConv(); };
  const close = () => setModal(null);
  const done = () => { close(); refresh(); };

  if (!p) return <Loading />;
  const flow: any[] = p.flow;
  const suggested = (convertible ?? []).filter((c) => c.suggested);
  return (
    <div className="space-y-5">
      <PageHeader title="سرمایه و دارایی‌ها" subtitle="با یک نگاه: چقدر گذاشته‌اید و برداشته‌اید، پول کجاست و کسب‌وکار چقدر می‌ارزد" icon={<Gem size={22} />}
        actions={<div className="flex flex-wrap gap-2">
          <button className="btn btn-primary" onClick={() => setModal({ kind: "add" })}><ArrowDownCircle size={16} />پول گذاشتم</button>
          <button className="btn" onClick={() => setModal({ kind: "withdraw" })}><ArrowUpCircle size={16} />پول برداشتم</button>
          <button className="btn" onClick={() => setModal({ kind: "supplier" })}><Truck size={16} />از جیب خودم به فروشنده دادم</button>
        </div>} />

      {suggested.length > 0 && (
        <div className="card space-y-2 border-2 border-violet-400/50 p-4">
          <div className="flex items-center gap-2 font-bold"><UserRoundCheck size={18} className="text-violet-500" />این‌ها احتمالاً سرمایهٔ خود شماست</div>
          {suggested.map((c) => (
            <div key={c.customer_id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl p-2" style={{ background: "var(--surface)" }}>
              <span>در فهرست مشتریان «<b>{c.name}</b>» {c.code && <span className="num muted">(کد {c.code})</span>} طلبی برابر <b className="num">{money(c.total)}</b> دارد
                (بیعانه یا طلب از خرید). اگر این پولی است که خودتان برای کار گذاشته‌اید، آن را به «سرمایهٔ مدیر» تبدیل کنید.</span>
              <button className="btn btn-sm btn-primary" onClick={() => setModal({ kind: "convert", holding: c })}>بررسی و تبدیل به سرمایه</button>
            </div>
          ))}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="card relative overflow-hidden p-5 lg:col-span-1">
          <div className="muted text-sm">ارزش خالص کسب‌وکار شما (امروز)</div>
          <div className="num gradient-text mt-1 text-3xl font-black">{money(p.worth)}</div>
          {p.unrealized !== 0 && <div className="mt-1 text-sm">با قیمت روز طلا و ارز: <b className="num">{money(p.worth_market)}</b></div>}
          {p.missing_price?.length > 0 && <div className="mt-1 text-xs text-amber-600">قیمت روزِ {p.missing_price.join("، ")} وارد نشده است.</div>}
          <div className="muted mt-3 text-xs leading-6">یعنی همهٔ دارایی‌ها (پول حساب‌ها، طلب از مشتری‌ها، کالای انبار، طلا و ارز) منهای همهٔ بدهی‌ها (بیعانهٔ مشتری‌ها، سهم پرسنل، بدهی به فروشنده‌ها). این مقدار مال شماست.</div>
          <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
            <div className="rounded-xl bg-violet-500/10 p-2"><div className="muted">سرمایه‌ای که خودتان گذاشته‌اید (منهای برداشت‌ها)</div><div className="num font-bold">{money(p.equity.capital)}</div></div>
            <div className="rounded-xl bg-emerald-500/10 p-2"><div className="muted">سود از اول تا امروز</div><div className="num font-bold">{money(p.equity.profit_to_date)}</div></div>
            <div className="col-span-2 rounded-xl bg-amber-500/10 p-2"><div className="muted">مانده‌های اول دوره (پول، طلب و کالایی که هنگام شروع داشتید منهای بدهی‌ها)</div><div className="num font-bold">{money(p.equity.opening)}</div></div>
          </div>
        </div>

        <Card className="lg:col-span-2" title={<span className="flex items-center gap-2"><Scale size={18} />سرمایه چطور تغییر کرد؟</span>}
          actions={<Tabs value={range} onChange={setRange} items={[{ value: "year", label: "امسال" }, { value: "all", label: "از اول" }]} />}>
          <div className="space-y-1.5 text-sm">
            {flow.map((f) => (
              <Row key={f.key} label={f.key === "start" ? (range === "year" ? "ارزش خالص در اول سال" : "ارزش خالص در شروع") : f.label} value={f.value}
                tone={f.key === "start" ? "" : f.value < 0 ? "text-rose-600" : "text-emerald-600"} />
            ))}
            <Row strong label="= ارزش خالص امروز" value={p.worth} />
          </div>
          {p.check !== 0 && <div className="mt-2 text-xs text-rose-600">⚠ دفاتر تراز نیست ({money(p.check)}). با پشتیبانی تماس بگیرید.</div>}
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="پول و دارایی‌ها کجاست؟">
          <div className="space-y-1.5 text-sm">
            {p.assets.map((a: any) => <Row key={a.key} label={a.label} value={a.value} />)}
            <Row strong label="جمع دارایی‌ها" value={p.assets_total} />
          </div>
          <details className="mt-3 text-sm">
            <summary className="muted cursor-pointer">موجودی هر صندوق، کارت و حساب</summary>
            <div className="mt-2 space-y-1">{p.cash.map((c: any) => <Row key={c.id} label={c.name} value={c.balance} tone={c.balance < 0 ? "text-rose-600" : ""} />)}</div>
          </details>
        </Card>
        <Card title="بدهی‌ها (پولی که مال دیگران است)">
          <div className="space-y-1.5 text-sm">
            {p.liabilities.map((a: any) => <Row key={a.key} label={a.label} value={a.value} />)}
            <Row strong label="جمع بدهی‌ها" value={p.liabilities_total} />
          </div>
        </Card>
      </div>

      <Card title={<span className="flex items-center gap-2"><Coins size={18} className="text-amber-500" />طلا، سکه و ارز</span>}
        actions={<button className="btn btn-sm" onClick={() => setModal({ kind: "new_holding" })}><Plus size={14} />دارایی جدید (طلا، سکه، دلار…)</button>}>
        {p.holdings.length === 0 ? <Empty text="هنوز طلا یا ارزی ثبت نشده. «دارایی جدید» را بزنید (مثلاً طلای آب‌شده یا دلار)." /> : (
          <div className="overflow-x-auto">
            <table className="table text-sm">
              <thead><tr><th>نام</th><th>مقدار</th><th>به قیمت خرید</th><th>قیمت روز (هر واحد)</th><th>ارزش امروز</th><th>سود/زیان</th><th></th></tr></thead>
              <tbody>{p.holdings.map((h: any) => (
                <tr key={h.id}>
                  <td className="font-semibold">{h.name} <span className="muted text-xs">{h.kind_label}</span></td>
                  <td className="num">{h.qty} {h.unit}</td>
                  <td className="num">{money(h.cost)}</td>
                  <td><button className="num text-violet-600 underline-offset-4 hover:underline" onClick={() => setModal({ kind: "price", holding: h })}>
                    {h.market_price != null ? money(h.market_price) : "وارد کنید"}</button>
                    {h.market_at && <div className="muted text-[10px]">{formatJ(h.market_at, false)}</div>}</td>
                  <td className="num">{h.market_value != null ? money(h.market_value) : "—"}</td>
                  <td className={`num ${h.unrealized > 0 ? "text-emerald-600" : h.unrealized < 0 ? "text-rose-600" : ""}`}>{h.unrealized != null ? money(h.unrealized) : "—"}</td>
                  <td className="whitespace-nowrap">
                    <button className="btn btn-sm btn-primary" onClick={() => setModal({ kind: "holding_move", holding: h })}>ثبت خرید/فروش</button>
                    <button className="btn btn-ghost btn-sm" onClick={() => setModal({ kind: "holding_history", holding: h })}><History size={14} /></button>
                  </td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
        <div className="muted mt-2 text-xs">طلا و ارز به قیمتی که خریده شده در حساب‌ها می‌ماند؛ «قیمت روز» فقط برای دیدن ارزش امروز است. سود واقعی وقتی فروختید ثبت می‌شود.</div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={`سود و زیان ${range === "year" ? "امسال" : "از اول"} (از روی دفاتر)`}>
          <div className="space-y-1.5 text-sm">
            {p.profit.rows.length === 0 ? <Empty text="هنوز درآمد یا هزینه‌ای نیست" /> : p.profit.rows.map((r: any) => (
              <Row key={r.key} label={r.label} value={r.value} tone={r.value < 0 ? "text-rose-600" : "text-emerald-600"} />
            ))}
            <Row strong label="سود خالص" value={p.profit.total} />
          </div>
        </Card>
        <Card title="گذاشتن و برداشتن سرمایه در این دوره" actions={<button className="btn btn-ghost btn-sm" onClick={() => setModal({ kind: "convert_search" })}><Search size={14} />تبدیل طلب یک شخص به سرمایه</button>}>
          {p.moves.length === 0 ? <Empty text="در این دوره پولی گذاشته یا برداشته نشده" /> : (
            <div className="max-h-96 space-y-1.5 overflow-y-auto text-sm">
              {p.moves.map((m: any) => <MoveRow key={m.id} m={m} onChange={refresh} />)}
            </div>
          )}
        </Card>
      </div>

      <Modal open={modal?.kind === "add" || modal?.kind === "withdraw"} onClose={close} title={modal?.kind === "add" ? "پول گذاشتم (سرمایه)" : "پول برداشتم (برای خودم)"}>
        {(modal?.kind === "add" || modal?.kind === "withdraw") && <MoveForm kind={modal.kind} onDone={done} />}
      </Modal>
      <Modal open={modal?.kind === "supplier"} onClose={close} title="از جیب خودم به فروشنده دادم">{modal?.kind === "supplier" && <SupplierForm onDone={done} />}</Modal>
      <Modal open={modal?.kind === "new_holding"} onClose={close} title="دارایی جدید">{modal?.kind === "new_holding" && <HoldingForm onDone={done} />}</Modal>
      <Modal open={modal?.kind === "price"} onClose={close} title={`قیمت روز ${modal?.holding?.name ?? ""}`}>{modal?.kind === "price" && <PriceForm h={modal.holding} onDone={done} />}</Modal>
      <Modal open={modal?.kind === "holding_move"} onClose={close} title={modal?.holding?.name ?? ""}>{modal?.kind === "holding_move" && <HoldingMoveForm h={modal.holding} onDone={done} />}</Modal>
      <Modal open={modal?.kind === "holding_history"} onClose={close} title={`گردش ${modal?.holding?.name ?? ""}`} wide>{modal?.kind === "holding_history" && <HoldingHistory h={modal.holding} onChange={refresh} />}</Modal>
      <Modal open={modal?.kind === "convert"} onClose={close} title="تبدیل طلب به سرمایهٔ مدیر" wide>{modal?.kind === "convert" && <ConvertForm person={modal.holding} onDone={done} />}</Modal>
      <Modal open={modal?.kind === "convert_search"} onClose={close} title="تبدیل طلب یک شخص به سرمایه" wide>{modal?.kind === "convert_search" && <ConvertSearch onDone={done} />}</Modal>
    </div>
  );
}

function MoveRow({ m, onChange }: { m: any; onChange: () => void }) {
  const toast = useToast();
  async function voidIt() {
    if (!confirm("این مورد اشتباه ثبت شده و باطل شود؟ (مثل این است که هیچ‌وقت ثبت نشده)")) return;
    try { await api(`/api/capital/moves/${m.id}/void`, { body: {} }); toast("باطل شد"); onChange(); } catch (e: any) { toast(e.message, "error"); }
  }
  return (
    <div className={`flex flex-wrap items-center justify-between gap-2 rounded-xl px-3 py-2 ${m.status === "void" ? "opacity-50" : ""}`} style={{ background: "var(--surface)" }}>
      <span><span className="muted num ml-2 text-xs">{formatJ(m.at, false)}</span><b>{m.kind_label}</b>
        <span className="muted text-xs"> · {[m.account, m.supplier && `فروشنده: ${m.supplier}`, m.purchase, m.customer, m.notes].filter(Boolean).join(" · ")}</span></span>
      <span className="flex items-center gap-2">
        <span className={`num font-bold ${m.signed < 0 ? "text-rose-600" : "text-emerald-600"}`}>{money(m.signed)}</span>
        {m.status === "void" ? <Badge status="void" /> : m.kind === "staff"
          ? <span className="muted text-[11px]" title="از صورت‌حساب همان شخص در «پرسنل و سهم‌ها» یا «کارمندان و حقوق» باطل می‌شود">از صفحهٔ پرسنل</span>
          : <button className="btn btn-ghost btn-sm text-rose-600" onClick={voidIt}>باطل</button>}
      </span>
    </div>
  );
}

function AccountSelect({ value, onChange }: { value: number; onChange: (id: number) => void }) {
  const accounts = (useApi<any[]>("/api/accounts").data ?? []).filter((a) => a.is_active);
  const cash = accounts.find((a) => a.kind === "cash");
  const v = value || cash?.id || accounts[0]?.id || 0;
  useEffect(() => { if (!value && v) onChange(v); }, [value, v]); // eslint-disable-line react-hooks/exhaustive-deps
  return <select className="input" value={v} onChange={(e) => onChange(Number(e.target.value))}>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select>;
}

function MoveForm({ kind, onDone }: { kind: "add" | "withdraw"; onDone: () => void }) {
  const toast = useToast();
  const [f, setF] = useState({ amount: 0, payment_account_id: 0, at: toLocalIso(new Date()), notes: "" });
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    try { await api("/api/capital/moves", { body: { kind, ...f } }); toast("ثبت شد"); onDone(); } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  }
  return (
    <div className="space-y-3">
      <div className="muted text-sm">{kind === "add" ? "پولی که از جیب خودتان برای کار گذاشتید (مثلاً برای خرید محصول) به کدام صندوق یا حساب رفت؟" : "پولی که برای خودتان از کدام صندوق یا حساب برداشتید؟"}</div>
      <Field label="مبلغ"><MoneyInput value={f.amount} onChange={(v) => setF({ ...f, amount: v })} /></Field>
      <Field label={kind === "add" ? "به حساب" : "از حساب"}><AccountSelect value={f.payment_account_id} onChange={(id) => setF((x) => ({ ...x, payment_account_id: id }))} /></Field>
      <Field label="تاریخ"><JalaliPicker pastOnly value={f.at} onChange={(v) => setF({ ...f, at: v })} /></Field>
      <Field label="توضیح (اختیاری)"><input className="input" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
      <button className="btn btn-primary w-full" disabled={!f.amount || busy} onClick={save}>ثبت</button>
    </div>
  );
}

function SupplierForm({ onDone }: { onDone: () => void }) {
  const toast = useToast();
  const { data: list } = useApi<any[]>("/api/purchases?status=unpaid");
  const [pid, setPid] = useState(0);
  const [f, setF] = useState({ amount: 0, at: toLocalIso(new Date()), notes: "" });
  const [busy, setBusy] = useState(false);
  const pur = (list ?? []).find((x) => x.id === pid);
  async function save() {
    setBusy(true);
    try { await api("/api/capital/supplier", { body: { purchase_id: pid, ...f } }); toast("ثبت شد: بدهی به فروشنده کم و سرمایهٔ شما زیاد شد"); onDone(); }
    catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  }
  if (!list) return <Loading />;
  if (!list.length) return <Empty text="خرید پرداخت‌نشده‌ای نیست" />;
  return (
    <div className="space-y-3">
      <div className="muted text-sm">اگر پول خرید کالا را خودتان (نه از صندوق یا کارت کسب‌وکار) به فروشنده دادید، اینجا ثبت کنید: بدهی به فروشنده کم می‌شود و همین مبلغ سرمایهٔ شما حساب می‌شود.</div>
      <Field label="کدام خرید؟">
        <select className="input" value={pid} onChange={(e) => { const id = Number(e.target.value); setPid(id); const x = list.find((y) => y.id === id); setF((v) => ({ ...v, amount: x?.due ?? 0 })); }}>
          <option value={0}>انتخاب کنید…</option>
          {list.map((x) => <option key={x.id} value={x.id}>{x.number} · {x.supplier ?? "بدون نام"} · مانده {money(x.due)}</option>)}
        </select>
      </Field>
      {pur && <>
        <Field label="مبلغی که دادید" hint={`حداکثر ${money(pur.due)}`}><MoneyInput value={f.amount} onChange={(v) => setF({ ...f, amount: v })} /></Field>
        <Field label="تاریخ"><JalaliPicker pastOnly value={f.at} onChange={(v) => setF({ ...f, at: v })} /></Field>
        <Field label="توضیح (اختیاری)"><input className="input" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
      </>}
      <button className="btn btn-primary w-full" disabled={!pid || !f.amount || busy} onClick={save}>ثبت</button>
    </div>
  );
}

function HoldingForm({ onDone }: { onDone: () => void }) {
  const toast = useToast();
  const [f, setF] = useState({ name: "", kind: "gold", unit: "گرم", notes: "" });
  async function save() {
    try { await api("/api/capital/holdings", { body: f }); toast("اضافه شد؛ حالا «ثبت خرید/فروش» را بزنید"); onDone(); } catch (e: any) { toast(e.message, "error"); }
  }
  return (
    <div className="space-y-3">
      <Field label="نوع">
        <select className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value, unit: UNIT_BY_KIND[e.target.value] })}>
          <option value="gold">طلا</option><option value="coin">سکه</option><option value="currency">ارز (دلار، یورو…)</option><option value="other">سایر</option>
        </select>
      </Field>
      <Field label="نام" hint="مثلاً: طلای آب‌شده، سکهٔ امامی، دلار"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
      <Field label="واحد" hint="گرم، عدد، دلار، یورو…"><input className="input" value={f.unit} onChange={(e) => setF({ ...f, unit: e.target.value })} /></Field>
      <button className="btn btn-primary w-full" disabled={!f.name.trim()} onClick={save}>ذخیره</button>
    </div>
  );
}

function PriceForm({ h, onDone }: { h: any; onDone: () => void }) {
  const toast = useToast();
  const [price, setPrice] = useState<number>(h.market_price ?? 0);
  async function save() {
    try { await api(`/api/capital/holdings/${h.id}/price`, { body: { price } }); toast("قیمت روز ذخیره شد"); onDone(); } catch (e: any) { toast(e.message, "error"); }
  }
  return (
    <div className="space-y-3">
      <Field label={`قیمت امروز هر ${h.unit}`} hint="فقط برای دیدن ارزش امروز؛ در حساب‌ها چیزی ثبت نمی‌شود"><MoneyInput value={price} onChange={setPrice} /></Field>
      {price > 0 && <div className="text-sm">ارزش امروز {h.qty} {h.unit}: <b className="num">{money(Math.floor((h.qty_m * price) / 1000))}</b></div>}
      <button className="btn btn-primary w-full" onClick={save}>ذخیره</button>
    </div>
  );
}

function HoldingMoveForm({ h, onDone }: { h: any; onDone: () => void }) {
  const toast = useToast();
  const [f, setF] = useState({ kind: "buy", qty: "", amount: 0, payment_account_id: 0, at: toLocalIso(new Date()), notes: "" });
  const [busy, setBusy] = useState(false);
  const k = MOVE_KINDS.find((x) => x.value === f.kind)!;
  const needsAccount = f.kind === "buy" || f.kind === "sell";
  const amountLabel = { buy: "مبلغی که پرداختید", sell: "مبلغی که گرفتید", owner_in: "ارزش آن (به قیمت روز)", opening: "قیمت خرید آن", owner_out: "" }[f.kind] ?? "";
  async function save() {
    setBusy(true);
    try {
      await api(`/api/capital/holdings/${h.id}/moves`, { body: { ...f, payment_account_id: needsAccount ? f.payment_account_id : null } });
      toast("ثبت شد"); onDone();
    } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  }
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5">
        {MOVE_KINDS.map((x) => <button key={x.value} type="button" className={`btn btn-sm ${f.kind === x.value ? "btn-primary" : ""}`} onClick={() => setF({ ...f, kind: x.value })}>{x.label}</button>)}
      </div>
      <div className="muted text-xs">{k.hint}. موجودی فعلی: <b className="num">{h.qty} {h.unit}</b></div>
      <Field label={`مقدار (${h.unit})`} hint="اعشار مجاز است، مثلاً 12.5"><input className="input num" dir="ltr" inputMode="decimal" value={f.qty} onChange={(e) => setF({ ...f, qty: e.target.value })} /></Field>
      {f.kind !== "owner_out" && <Field label={amountLabel}><MoneyInput value={f.amount} onChange={(v) => setF({ ...f, amount: v })} /></Field>}
      {f.kind === "owner_out" && <div className="text-xs">به همان قیمتی که خریده شده از دارایی‌ها و از سرمایهٔ شما کم می‌شود.</div>}
      {needsAccount && <Field label={f.kind === "buy" ? "پرداخت از" : "پولش به"}><AccountSelect value={f.payment_account_id} onChange={(id) => setF((x) => ({ ...x, payment_account_id: id }))} /></Field>}
      <Field label="تاریخ"><JalaliPicker pastOnly value={f.at} onChange={(v) => setF({ ...f, at: v })} /></Field>
      <Field label="توضیح (اختیاری)"><input className="input" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
      <button className="btn btn-primary w-full" disabled={!f.qty || (f.kind !== "owner_out" && !f.amount) || busy} onClick={save}>ثبت</button>
    </div>
  );
}

function HoldingHistory({ h, onChange }: { h: any; onChange: () => void }) {
  const toast = useToast();
  const { data, reload } = useApi<any[]>(`/api/capital/holdings/${h.id}/moves`);
  if (!data) return <Loading />;
  const lastActive = data.find((m) => m.status === "active");
  async function voidIt(id: number) {
    if (!confirm("این گردش اشتباه ثبت شده و باطل شود؟")) return;
    try { await api(`/api/capital/holding-moves/${id}/void`, { body: {} }); toast("باطل شد"); reload(); onChange(); } catch (e: any) { toast(e.message, "error"); }
  }
  return data.length === 0 ? <Empty /> : (
    <div className="overflow-x-auto">
      <table className="table text-sm">
        <thead><tr><th>تاریخ</th><th>نوع</th><th>مقدار</th><th>مبلغ</th><th>به قیمت خرید</th><th>سود/زیان</th><th>حساب</th><th></th></tr></thead>
        <tbody>{data.map((m) => (
          <tr key={m.id} className={m.status === "void" ? "opacity-50" : ""}>
            <td className="num muted">{formatJ(m.at, false)}</td><td className="font-semibold">{m.kind_label}</td>
            <td className="num" dir="ltr">{m.qty}</td><td className="num">{money(m.amount)}</td><td className="num">{money(m.cost)}</td>
            <td className={`num ${m.gain > 0 ? "text-emerald-600" : m.gain < 0 ? "text-rose-600" : ""}`}>{m.gain ? money(m.gain) : "—"}</td>
            <td>{m.account ?? "—"}</td>
            <td>{m.status === "void" ? <Badge status="void" /> : m.id === lastActive?.id ? <button className="btn btn-ghost btn-sm text-rose-600" onClick={() => voidIt(m.id)}>باطل</button> : null}</td>
          </tr>
        ))}</tbody>
      </table>
      <div className="muted mt-2 text-xs">فقط آخرین گردش را می‌شود باطل کرد (قیمت تمام‌شدهٔ گردش‌های بعدی به قبلی‌ها بستگی دارد).</div>
    </div>
  );
}

function ConvertForm({ person, onDone }: { person: any; onDone: () => void }) {
  const toast = useToast();
  const [deps, setDeps] = useState<number[]>(person.deposits.map((d: any) => d.id));
  const [purs, setPurs] = useState<number[]>(person.purchases.map((p: any) => p.id));
  const [busy, setBusy] = useState(false);
  const total = person.deposits.filter((d: any) => deps.includes(d.id)).reduce((s: number, d: any) => s + d.amount, 0)
    + person.purchases.filter((p: any) => purs.includes(p.id)).reduce((s: number, p: any) => s + p.due, 0);
  const toggle = (list: number[], set: (x: number[]) => void, id: number) => set(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  async function save() {
    setBusy(true);
    try {
      const r = await api<any>("/api/capital/convert", { body: { deposit_ids: deps, purchase_ids: purs } });
      toast(`${num(r.converted)} مورد به مبلغ ${money(r.amount)} سرمایهٔ مدیر شد`); onDone();
    } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  }
  return (
    <div className="space-y-3 text-sm">
      <div>«<b>{person.name}</b>» این طلب‌ها را دارد. اگر این پولِ خود شما برای کار است، تیک بزنید تا از «بدهی به مشتری/فروشنده» به «سرمایهٔ مدیر» منتقل شود (با همان تاریخ خودش). بعداً هم می‌شود باطلش کرد.</div>
      {person.deposits.map((d: any) => (
        <label key={`d${d.id}`} className="flex items-center gap-2 rounded-xl p-2" style={{ background: "var(--surface)" }}>
          <input type="checkbox" checked={deps.includes(d.id)} onChange={() => toggle(deps, setDeps, d.id)} />
          <span className="num font-bold">{money(d.amount)}</span><span className="muted text-xs">بیعانه/طلب · {formatJ(d.at, false)} · {d.notes}</span>
        </label>
      ))}
      {person.purchases.map((p: any) => (
        <label key={`p${p.id}`} className="flex items-center gap-2 rounded-xl p-2" style={{ background: "var(--surface)" }}>
          <input type="checkbox" checked={purs.includes(p.id)} onChange={() => toggle(purs, setPurs, p.id)} />
          <span className="num font-bold">{money(p.due)}</span><span className="muted text-xs">طلب از خرید {p.number} · {formatJ(p.at, false)} · {p.notes}</span>
        </label>
      ))}
      <button className="btn btn-primary w-full" disabled={!total || busy} onClick={save}>تبدیل {money(total)} به سرمایهٔ مدیر</button>
    </div>
  );
}

function ConvertSearch({ onDone }: { onDone: () => void }) {
  const [q, setQ] = useState("");
  const [list, setList] = useState<any[] | null>(null);
  const [pick, setPick] = useState<any | null>(null);
  async function search() { setList(await api<any[]>(`/api/capital/convertible?q=${encodeURIComponent(q)}`)); }
  if (pick) return <ConvertForm person={pick} onDone={onDone} />;
  return (
    <div className="space-y-3 text-sm">
      <div className="muted">نام شخصی را بنویسید که طلبش (بیعانه یا طلب از خرید) در واقع پول خود شماست.</div>
      <div className="flex gap-2"><input className="input" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search()} placeholder="نام…" />
        <button className="btn" onClick={search}><Search size={14} />جستجو</button></div>
      {list && (list.length === 0 ? <Empty text="کسی با طلب باز پیدا نشد" /> : list.map((c) => (
        <button key={c.customer_id} className="flex w-full items-center justify-between rounded-xl p-2 text-right hover:bg-violet-500/10" style={{ background: "var(--surface)" }} onClick={() => setPick(c)}>
          <span><b>{c.name}</b> {c.code && <span className="num muted text-xs">کد {c.code}</span>}</span><span className="num font-bold">{money(c.total)}</span>
        </button>
      )))}
    </div>
  );
}
