import { AlertTriangle, Archive, ClipboardCheck, Package, PackagePlus, Pencil, Plus, Save, ShoppingCart, Trash2, TrendingUp, Truck } from "lucide-react";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import JalaliPicker from "../components/JalaliPicker";
import ProductPicker from "../components/ProductPicker";
import { SupplierView, TradeDocView } from "../components/TradeDocs";
import { Badge, Card, Empty, Field, Loading, Modal, MoneyInput, PageHeader, Stat, Tabs } from "../components/ui";
import { api } from "../lib/api";
import { ACCOUNT_KINDS, cmoney, money, num } from "../lib/format";
import { can, useApi, useAuth, useToast } from "../lib/hooks";
import { faDigits, formatJ, J_MONTHS, toGregorian, toJalali, toLocalIso } from "../lib/jalali";
import { share } from "../lib/paysplit";

const iso = (d: Date) => toLocalIso(d).slice(0, 10);
const PERIODS: Record<string, { label: string; start: () => string }> = {
  month: { label: "این ماه", start: () => { const d = new Date(); const [jy, jm] = toJalali(d.getFullYear(), d.getMonth() + 1, d.getDate()); const [y, m, dd] = toGregorian(jy, jm, 1); return iso(new Date(y, m - 1, dd)); } },
  quarter: { label: "۳ ماه اخیر", start: () => iso(new Date(Date.now() - 90 * 86400000)) },
  year: { label: "امسال", start: () => { const d = new Date(); const [jy] = toJalali(d.getFullYear(), d.getMonth() + 1, d.getDate()); const [y, m, dd] = toGregorian(jy, 1, 1); return iso(new Date(y, m - 1, dd)); } },
  all: { label: "همه", start: () => "" },
};
const jmLabel = (key: string) => { const [y, m] = key.split("-").map(Number); return `${J_MONTHS[m - 1]} ${faDigits(y)}`; };
const pct = (x: number | null | undefined) => (x === null || x === undefined ? "—" : `${faDigits(x)}٪`);

// ------------------------------------------------------------------ product form & file
function ProductForm({ initial, onDone }: { initial?: any; onDone: (p: any) => void }) {
  const toast = useToast();
  const [f, setF] = useState<any>({ code: "", sku: "", name: "", brand: "", category: "", unit: "عدد", sale_price: 0, online_price: null, reorder_level: 0, notes: "", is_active: true, ...(initial ?? {}) });
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    try {
      const body = { ...f, code: f.code || null, sku: f.sku || null, online_price: f.online_price || null, reorder_level: Number(f.reorder_level) || 0 };
      const p = await api(initial ? `/api/products/${initial.id}` : "/api/products", { method: initial ? "PUT" : "POST", body });
      toast(initial ? "ذخیره شد" : `«${p.name}» با کد ${p.code} اضافه شد`);
      onDone(p);
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="کد فروش (سیستم)" hint="خالی = خودکار"><input className="input num" dir="ltr" value={f.code ?? ""} placeholder="خودکار" onChange={(e) => setF({ ...f, code: e.target.value })} /></Field>
        <Field label="SKU سایت"><input className="input num" dir="ltr" value={f.sku ?? ""} onChange={(e) => setF({ ...f, sku: e.target.value })} placeholder="مثلاً VC-30" /></Field>
        <Field label="واحد"><input className="input" value={f.unit} onChange={(e) => setF({ ...f, unit: e.target.value })} /></Field>
      </div>
      <Field label="نام محصول"><input className="input" autoFocus value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="برند"><input className="input" value={f.brand} onChange={(e) => setF({ ...f, brand: e.target.value })} /></Field>
        <Field label="دسته"><input className="input" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })} placeholder="مثلاً مراقبت پوست" /></Field>
        <Field label="قیمت فروش حضوری" hint={initial?.last_sale_price ? `آخرین فروش: ${money(initial.last_sale_price)} - فاکتور بعدی با آن پر می‌شود؛ اگر این قیمت را عوض کنید، قیمت جدید ملاک می‌شود` : "فاکتور با آخرین قیمت فروش پر می‌شود"}>
          <MoneyInput value={f.sale_price} onChange={(v) => setF({ ...f, sale_price: v })} /></Field>
        <Field label="قیمت فروش آنلاین (سایت)" hint="خالی = همان قیمت حضوری؛ اگر سایت وصل باشد، قیمت سایت خودکار اینجا می‌آید"><MoneyInput value={f.online_price ?? 0} onChange={(v) => setF({ ...f, online_price: v || null })} /></Field>
        <Field label="حداقل موجودی (هشدار)" hint="وقتی موجودی به این عدد برسد هشدار داده می‌شود"><input type="number" min={0} className="input num" value={f.reorder_level} onChange={(e) => setF({ ...f, reorder_level: e.target.value })} /></Field>
      </div>
      <Field label="یادداشت"><input className="input" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
      <button className="btn btn-primary w-full" disabled={busy || !f.name?.trim()} onClick={save}><Save size={15} />ذخیره</button>
    </div>
  );
}

function ProductFile({ id, onChange }: { id: number; onChange: () => void }) {
  const toast = useToast();
  const { user } = useAuth();
  const { data: p, reload } = useApi<any>(`/api/products/${id}`);
  const [step, setStep] = useState<"" | "edit" | "opening" | "count">("");
  const [qty, setQty] = useState(0);
  const [unitCost, setUnitCost] = useState(0);
  const [at, setAt] = useState(toLocalIso(new Date()));
  if (!p) return <Loading />;
  const done = () => { reload(); onChange(); setStep(""); };
  const finance = can(user, "finance");

  async function post(path: string, body: any, msg: (r: any) => string) {
    try {
      const r = await api(`/api/products/${id}/${path}`, { body });
      toast(msg(r));
      done();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }
  if (step === "edit") return <ProductForm initial={p} onDone={done} />;
  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-lg font-extrabold">{p.name} {!p.is_active && <Badge>بایگانی</Badge>}</div>
          <div className="muted text-xs">کد <span className="num">{p.code}</span>{p.sku && <> · SKU <span className="num" dir="ltr">{p.sku}</span></>}{p.brand && ` · ${p.brand}`}{p.category && ` · ${p.category}`}</div>
        </div>
        {finance && <div className="flex flex-wrap gap-1.5">
          <button className="btn btn-sm" onClick={() => setStep("edit")}><Pencil size={14} />ویرایش</button>
          <button className="btn btn-sm" onClick={() => { setQty(0); setUnitCost(p.last_purchase_cost ?? 0); setStep("opening"); }}><PackagePlus size={14} />موجودی اول دوره</button>
          <button className="btn btn-sm" onClick={() => { setQty(Math.max(0, p.stock_qty)); setStep("count"); }}><ClipboardCheck size={14} />شمارش انبار</button>
        </div>}
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <div className={`rounded-2xl p-3 ${p.stock_qty < 0 ? "bg-rose-500/10" : p.low ? "bg-amber-500/10" : "bg-sky-500/10"}`}><div className="muted text-xs">موجودی</div><div className="num font-bold">{num(p.stock_qty)} {p.unit}</div></div>
        <div className="rounded-2xl bg-violet-500/10 p-3"><div className="muted text-xs">ارزش موجودی</div><div className="num font-bold">{money(p.stock_value)}</div></div>
        <div className="rounded-2xl bg-emerald-500/10 p-3"><div className="muted text-xs">بهای تمام‌شدهٔ هر واحد</div><div className="num font-bold">{p.unit_cost != null ? money(p.unit_cost) : "—"}</div></div>
        <div className="rounded-2xl bg-pink-500/10 p-3"><div className="muted text-xs">سود هر واحد (حضوری)</div><div className="num font-bold">{p.margin != null ? money(p.margin) : "—"}</div><div className="muted text-[11px]">{pct(p.margin_pct)}</div></div>
      </div>
      {p.stock_qty < 0 && <div className="flex items-start gap-2 rounded-xl bg-rose-500/10 p-2.5 text-xs text-rose-700 dark:text-rose-300"><AlertTriangle size={15} className="shrink-0" />بیشتر از موجودی فروخته شده. خرید یا «موجودی اول دوره» را ثبت کنید؛ بهای واقعی فروش‌ها خودکار اصلاح می‌شود.</div>}

      {(step === "opening" || step === "count") && (
        <div className="space-y-3 rounded-2xl border p-3" style={{ borderColor: "var(--border)" }}>
          <div className="font-bold">{step === "opening" ? "موجودی اول دوره (کالایی که از قبل در قفسه بوده)" : "شمارش انبار"}</div>
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label={step === "opening" ? "تعداد" : "تعداد شمرده‌شده"}><input type="number" min={0} className="input num" value={qty} onChange={(e) => setQty(Number(e.target.value))} /></Field>
            {step === "opening" && <Field label="بهای خرید هر واحد"><MoneyInput value={unitCost} onChange={setUnitCost} /></Field>}
            <Field label="تاریخ"><JalaliPicker pastOnly value={at} onChange={(v) => setAt(v || toLocalIso(new Date()))} /></Field>
          </div>
          {step === "count" && <div className="muted text-xs">موجودی سیستم: {num(p.stock_qty)}؛ اختلاف به‌عنوان {qty < p.stock_qty ? "کسری انبار (هزینه)" : qty > p.stock_qty ? "اضافهٔ انبار" : "—"} ثبت می‌شود.</div>}
          <div className="flex gap-2">
            <button className="btn btn-primary" onClick={() => step === "opening"
              ? post("opening", { qty, unit_cost: unitCost, at }, () => "موجودی اول دوره ثبت شد")
              : post("count", { counted: qty, at }, (r) => r.difference ? `اختلاف ${faDigits(r.difference)} ${p.unit} به ارزش ${money(r.difference_cost)} ثبت شد` : "موجودی درست بود")}>ثبت</button>
            <button className="btn" onClick={() => setStep("")}>انصراف</button>
          </div>
        </div>
      )}

      <div>
        <div className="mb-1 font-bold">کارت کالا (گردش موجودی)</div>
        {!p.moves.length ? <Empty text="هنوز گردشی ندارد؛ خرید یا موجودی اول دوره ثبت کنید" /> : (
          <div className="max-h-80 overflow-y-auto">
            <table className="table text-xs [&_td]:px-2 [&_th]:px-2">
              <thead><tr><th>تاریخ</th><th>نوع</th><th>تعداد</th><th>بهای واحد</th><th>بهای کل</th><th>مانده</th><th>ارزش مانده</th></tr></thead>
              <tbody>{p.moves.map((m: any) => (
                <tr key={m.id}>
                  <td className="num whitespace-nowrap">{formatJ(m.at)}</td>
                  <td>{m.label}{m.invoice && <span className="num muted"> · {m.invoice}</span>}{m.estimated && <span className="badge mr-1 bg-amber-500/15 text-amber-700" title="کالا قبل از ثبت خرید فروخته شده؛ با ثبت خرید اصلاح می‌شود">تخمینی</span>}</td>
                  <td className={`num ${m.qty < 0 ? "text-rose-600" : "text-emerald-600"}`}>{m.qty > 0 ? "+" : ""}{num(m.qty)}</td>
                  <td className="num">{money(m.unit_cost, false)}</td>
                  <td className="num">{money(m.cost, false)}</td>
                  <td className="num">{num(m.balance_qty)}</td>
                  <td className="num">{money(m.balance_value, false)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </div>
      {p.history?.length > 0 && <TradeHistoryList rows={p.history} />}
      {finance && p.is_active && (
        <button className="btn btn-sm text-rose-600" onClick={async () => {
          if (!confirm(`«${p.name}» حذف شود؟ (اگر گردش دارد بایگانی می‌شود)`)) return;
          const r = await api(`/api/products/${id}`, { method: "DELETE" });
          toast(r.archived ? "بایگانی شد" : "حذف شد");
          onChange();
        }}><Trash2 size={14} />حذف</button>
      )}
    </div>
  );
}

const H_KINDS: Record<string, string> = { sale: "فروش", sale_return: "برگشت از فروش", purchase: "خرید", purchase_return: "برگشت از خرید" };
const H_SOURCE: Record<string, string> = { tizpardaz: "تیزپرداز", chehreh: "چهره" };

/** Sales and purchases of this product brought over from Tizpardaz / Chehreh (history only: stock came over as a count). */
function TradeHistoryList({ rows }: { rows: any[] }) {
  const [doc, setDoc] = useState<number | null>(null);
  const sales = rows.filter((h) => h.kind === "sale" || h.kind === "sale_return");
  const sign = (h: any) => (h.kind === "sale_return" ? -1 : 1);
  const qty = sales.reduce((t, h) => t + sign(h) * h.qty, 0);
  const revenue = sales.reduce((t, h) => t + sign(h) * h.amount, 0);
  const cost = sales.reduce((t, h) => t + sign(h) * (h.cost ?? 0), 0);
  return (
    <details open={!rows.length || rows.length < 30}>
      <summary className="mb-1 cursor-pointer font-bold">سوابق خرید و فروش در نرم‌افزار قبلی ({num(rows.length)})</summary>
      <div className="muted mb-2 text-xs">فروش: {num(qty)} عدد · {money(revenue)} · سود ناخالص {money(revenue - cost)}. این‌ها فقط سابقه‌اند؛ موجودی بالا همان موجودی نهایی تیزپرداز است.</div>
      <div className="max-h-72 overflow-y-auto">
        <table className="table text-xs [&_td]:px-2 [&_th]:px-2">
          <thead><tr><th>تاریخ</th><th>نوع</th><th>طرف حساب</th><th>تعداد</th><th>مبلغ</th><th>سود</th></tr></thead>
          <tbody>{rows.map((h: any, k: number) => (
            <tr key={k} className={h.doc_id ? "cursor-pointer hover:bg-violet-500/10" : ""} title={h.doc_id ? "دیدن فاکتور" : undefined}
              onClick={() => h.doc_id && setDoc(h.doc_id)}>
              <td className="num whitespace-nowrap">{formatJ(h.at, false)}</td>
              <td>{H_KINDS[h.kind] ?? h.kind}<span className="muted"> · {H_SOURCE[h.source] ?? h.source}{h.doc_no ? ` · سند ${h.doc_no}` : ""}</span></td>
              <td>{h.customer ?? h.party ?? ""}</td>
              <td className="num">{num(h.qty)}</td>
              <td className="num">{money(h.amount, false)}</td>
              <td className="num">{h.kind === "sale" || h.kind === "sale_return" ? money(sign(h) * (h.amount - (h.cost ?? 0)), false) : ""}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      <Modal open={doc !== null} onClose={() => setDoc(null)} title="جزئیات فاکتور" wide>{doc !== null && <TradeDocView id={doc} />}</Modal>
    </details>
  );
}

function ProductList({ onChanged }: { onChanged: () => void }) {
  const { user } = useAuth();
  const [q, setQ] = useState("");
  const [show, setShow] = useState<"active" | "low" | "all">("active");
  const { data, reload } = useApi<any[]>(`/api/products?q=${encodeURIComponent(q)}${show === "all" ? "&all=1" : ""}${show === "low" ? "&low=1" : ""}`, [q, show]);
  const [view, setView] = useState<number | null>(null);
  const [create, setCreate] = useState(false);
  const refresh = () => { reload(); onChanged(); };
  return (
    <Card pad={false} title="محصولات" actions={can(user, "finance") && <button className="btn btn-sm btn-primary" onClick={() => setCreate(true)}><Plus size={14} />محصول جدید</button>}>
      <div className="flex flex-wrap items-center gap-2 px-5 pt-3">
        <input className="input max-w-xs py-1.5 text-sm" placeholder="جستجو: کد، SKU، نام، برند…" value={q} onChange={(e) => setQ(e.target.value)} />
        <Tabs value={show} onChange={setShow} items={[{ value: "active", label: "همه" }, { value: "low", label: "کم‌موجودی" }, { value: "all", label: "با بایگانی‌شده‌ها" }]} />
      </div>
      {!data ? <Loading /> : !data.length ? <Empty icon={<Package size={28} />} text={q ? "محصولی پیدا نشد" : "هنوز محصولی تعریف نشده؛ «محصول جدید» را بزنید"} /> : (
        <div className="mt-3 overflow-x-auto">
          <table className="table [&_td]:px-2.5 [&_th]:px-2.5">
            <thead><tr><th>کد</th><th>محصول</th><th>SKU</th><th>موجودی</th><th>بهای تمام‌شده</th><th>قیمت حضوری</th><th>قیمت آنلاین</th><th>سود هر واحد</th></tr></thead>
            <tbody>{data.map((p) => (
              <tr key={p.id} className={`cursor-pointer ${p.is_active ? "" : "opacity-60"}`} onClick={() => setView(p.id)}>
                <td className="num font-bold text-sky-600">{p.code}</td>
                <td><div className="font-semibold">{p.name}</div><div className="muted text-xs">{[p.brand, p.category].filter(Boolean).join(" · ")}</div></td>
                <td className="num text-xs" dir="ltr">{p.sku ?? "—"}</td>
                <td><span className={`num badge ${p.stock_qty < 0 ? "bg-rose-500/15 text-rose-600" : p.low ? "bg-amber-500/15 text-amber-700" : "bg-sky-500/10 text-sky-700 dark:text-sky-300"}`}>{num(p.stock_qty)} {p.unit}</span></td>
                <td className="num">{p.unit_cost != null ? money(p.unit_cost, false) : "—"}</td>
                <td className="num">{money(p.sale_price, false)}{p.last_sale_price && p.last_sale_price !== p.sale_price ? <div className="muted text-[10px]">آخرین فروش: {money(p.last_sale_price, false)}</div> : null}</td>
                <td className="num">{p.online_price ? money(p.online_price, false) : <span className="muted">همان</span>}{p.last_online_price && p.last_online_price !== p.online_price ? <div className="muted text-[10px]">آخرین فروش سایت: {money(p.last_online_price, false)}</div> : null}</td>
                <td className="num">{p.margin != null ? <>{money(p.margin, false)} <span className="muted text-xs">({pct(p.margin_pct)})</span></> : "—"}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      <Modal open={create} onClose={() => setCreate(false)} title="محصول جدید">{create && <ProductForm onDone={(p) => { setCreate(false); refresh(); setView(p.id); }} />}</Modal>
      <Modal open={view !== null} onClose={() => setView(null)} title="پروندهٔ محصول" wide>{view !== null && <ProductFile id={view} onChange={refresh} />}</Modal>
    </Card>
  );
}

// ------------------------------------------------------------------ purchases
type Row = { product?: any; quantity: number; unit_price: number };

function PurchaseForm({ onDone }: { onDone: () => void }) {
  const toast = useToast();
  const suppliers = useApi<any[]>("/api/suppliers").data ?? [];
  const accounts = (useApi<any[]>("/api/accounts").data ?? []).filter((a) => a.is_active);
  const routing = useApi<any>("/api/accounts/routing").data;
  const [supplierId, setSupplierId] = useState(0);
  const [supplierName, setSupplierName] = useState("");
  const [ref, setRef] = useState("");
  const [at, setAt] = useState(toLocalIso(new Date()));
  const [rows, setRows] = useState<Row[]>([{ quantity: 1, unit_price: 0 }]);
  const [discount, setDiscount] = useState(0);
  const [shipping, setShipping] = useState(0);
  const [payNow, setPayNow] = useState(true);
  const [payAcc, setPayAcc] = useState(0);
  const [payAmount, setPayAmount] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const valid = rows.filter((r) => r.product && r.quantity > 0);
  const gross = valid.map((r) => r.quantity * r.unit_price);
  const subtotal = gross.reduce((s, x) => s + x, 0);
  const total = Math.max(0, subtotal - discount + shipping);
  // landed cost of each row: its share of the discount and of the shipping (same split as the server)
  const landed = useMemo(() => {
    const d = share(discount, gross);
    const s = share(shipping, subtotal ? gross : valid.map((r) => r.quantity));
    return valid.map((r, i) => gross[i] - d[i] + s[i]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [discount, shipping, JSON.stringify(gross)]);
  const defaultAcc = payAcc || routing?.products?.card?.[0] || accounts.find((a) => a.kind !== "pos")?.id || accounts[0]?.id;
  const set = (i: number, patch: Partial<Row>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  async function save() {
    setBusy(true);
    try {
      const amount = payAmount ?? total;
      const r = await api("/api/purchases", { body: {
        supplier_id: supplierId || null, supplier_name: supplierId ? null : supplierName || null, supplier_ref: ref, at, discount, shipping,
        items: valid.map((x) => ({ product_id: x.product.id, quantity: x.quantity, unit_price: x.unit_price })),
        payments: payNow && amount > 0 ? [{ payment_account_id: defaultAcc, amount }] : [] } });
      toast(`خرید ${r.number} ثبت شد${r.due ? ` · مانده بدهی ${money(r.due)}` : ""}`);
      onDone();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4 text-sm">
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="تأمین‌کننده">
          <select className="input" value={supplierId} onChange={(e) => setSupplierId(Number(e.target.value))}>
            <option value={0}>➕ تأمین‌کنندهٔ جدید…</option>
            {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          {!supplierId && <input className="input mt-1.5" placeholder="نام تأمین‌کننده" value={supplierName} onChange={(e) => setSupplierName(e.target.value)} />}
        </Field>
        <Field label="شمارهٔ فاکتور فروشنده"><input className="input num" value={ref} onChange={(e) => setRef(e.target.value)} /></Field>
        <Field label="تاریخ خرید"><JalaliPicker pastOnly value={at} onChange={(v) => setAt(v || toLocalIso(new Date()))} /></Field>
      </div>
      <div className="space-y-2">
        <div className="label">کالاها</div>
        {rows.map((r, i) => {
          const k = valid.indexOf(r);
          return (
            <div key={i} className="grid grid-cols-12 items-start gap-2 rounded-2xl p-2" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
              <ProductPicker className="col-span-12 sm:col-span-5" value={r.product} onChange={(p) => set(i, { product: p, unit_price: r.unit_price || p.last_purchase_cost || 0 })} />
              <input type="number" min={1} className="input num col-span-3 sm:col-span-2" value={r.quantity} onChange={(e) => set(i, { quantity: Number(e.target.value) })} title="تعداد" />
              <div className="col-span-8 sm:col-span-4"><MoneyInput value={r.unit_price} onChange={(v) => set(i, { unit_price: v })} placeholder="قیمت خرید هر واحد" /></div>
              <button className="btn btn-ghost btn-sm col-span-1" onClick={() => setRows(rows.filter((_, j) => j !== i))}><Trash2 size={16} /></button>
              {k >= 0 && (discount > 0 || shipping > 0) && <div className="muted col-span-12 text-xs">بهای تمام‌شدهٔ هر واحد با سهم تخفیف و حمل: <b className="num">{money(Math.floor(landed[k] / r.quantity))}</b></div>}
            </div>
          );
        })}
        <button className="btn btn-sm" onClick={() => setRows([...rows, { quantity: 1, unit_price: 0 }])}><Plus size={14} />کالای دیگر</button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="تخفیف کل خرید"><MoneyInput value={discount} onChange={setDiscount} /></Field>
        <Field label="هزینهٔ حمل (به بهای کالا اضافه می‌شود)"><MoneyInput value={shipping} onChange={setShipping} /></Field>
      </div>
      <div className="space-y-2 rounded-2xl p-3" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
        <label className="flex items-center gap-2 font-semibold"><input type="checkbox" checked={payNow} onChange={(e) => setPayNow(e.target.checked)} />همین حالا پرداخت شد</label>
        {payNow ? (
          <div className="grid grid-cols-12 gap-2">
            <select className="input col-span-7" value={defaultAcc} onChange={(e) => setPayAcc(Number(e.target.value))}>
              {accounts.map((a) => <option key={a.id} value={a.id}>{a.name} ({ACCOUNT_KINDS[a.kind]})</option>)}
            </select>
            <div className="col-span-5"><MoneyInput value={payAmount ?? total} onChange={setPayAmount} /></div>
          </div>
        ) : <div className="muted text-xs">کل مبلغ به‌عنوان بدهی به تأمین‌کننده ثبت می‌شود و بعداً از فهرست خریدها پرداخت می‌کنید.</div>}
      </div>
      <div className="rounded-2xl p-3" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
        <div className="flex justify-between"><span className="muted">جمع کالاها</span><span className="num">{money(subtotal)}</span></div>
        {discount > 0 && <div className="flex justify-between"><span className="muted">تخفیف</span><span className="num">− {money(discount)}</span></div>}
        {shipping > 0 && <div className="flex justify-between"><span className="muted">حمل</span><span className="num">+ {money(shipping)}</span></div>}
        <div className="mt-1 flex justify-between font-extrabold"><span>جمع خرید</span><span className="num">{money(total)}</span></div>
        {payNow && (payAmount ?? total) < total && <div className="flex justify-between text-amber-600"><span>مانده بدهی</span><span className="num">{money(total - (payAmount ?? total))}</span></div>}
      </div>
      <button className="btn btn-primary w-full py-3" disabled={busy || !valid.length || (!supplierId && !supplierName.trim())} onClick={save}><ShoppingCart size={16} />ثبت خرید</button>
    </div>
  );
}

export function PurchaseView({ id, onChange }: { id: number; onChange: () => void }) {
  const toast = useToast();
  const { user } = useAuth();
  const { data: p, reload } = useApi<any>(`/api/purchases/${id}`);
  const accounts = (useApi<any[]>("/api/accounts").data ?? []).filter((a) => a.is_active);
  const [acc, setAcc] = useState(0);
  const [amount, setAmount] = useState(0);
  if (!p) return <Loading />;
  const done = () => { reload(); onChange(); };
  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div><b className="num">{p.number}</b> · {p.supplier ?? "—"}{p.supplier_ref && <span className="muted"> · فاکتور فروشنده {p.supplier_ref}</span>}<div className="muted text-xs">{formatJ(p.at)}</div></div>
        <Badge status={p.status === "open" ? "issued" : p.status} />
      </div>
      {!p.items.length && <div className="rounded-xl bg-sky-500/10 p-3 text-xs">ماندهٔ اول دوره: مبلغی که از قبل (مثلاً در تیزپرداز) به این فروشنده بدهکار بودیم. کالایی ندارد و مثل بقیهٔ خریدها پرداخت می‌شود.{p.notes && <div className="muted mt-1">{p.notes}</div>}</div>}
      {p.items.length > 0 && <table className="table text-xs [&_td]:px-2 [&_th]:px-2">
        <thead><tr><th>کالا</th><th>تعداد</th><th>قیمت خرید</th><th>بهای تمام‌شدهٔ واحد</th><th>جمع</th></tr></thead>
        <tbody>{p.items.map((i: any, k: number) => (
          <tr key={k}><td><span className="num muted">{i.code}</span> {i.product}</td><td className="num">{num(i.quantity)}</td><td className="num">{money(i.unit_price, false)}</td><td className="num">{money(i.unit_cost, false)}</td><td className="num">{money(i.cost, false)}</td></tr>
        ))}</tbody>
      </table>}
      {(p.discount > 0 || p.shipping > 0) && <div className="muted flex flex-wrap gap-4 text-xs"><span>جمع اقلام: <span className="num">{money(p.subtotal)}</span></span>
        {p.discount > 0 && <span>تخفیف: <span className="num">{money(p.discount)}</span></span>}{p.shipping > 0 && <span>حمل: <span className="num">{money(p.shipping)}</span></span>}</div>}
      <div className="rounded-xl p-3" style={{ background: "var(--surface)" }}>
        <div className="flex justify-between"><span className="muted">جمع خرید</span><b className="num">{money(p.total)}</b></div>
        <div className="flex justify-between"><span className="muted">پرداخت‌شده</span><span className="num">{money(p.paid)}</span></div>
        {p.due > 0 && <div className="flex justify-between font-bold text-amber-600"><span>مانده بدهی به تأمین‌کننده</span><span className="num">{money(p.due)}</span></div>}
        {p.payments.map((x: any) => <div key={x.id} className="muted flex justify-between text-xs"><span>{x.amount < 0 ? "برگشت · " : ""}{x.account} · {formatJ(x.paid_at)}</span><span className="num">{money(x.amount)}</span></div>)}
      </div>
      {p.status !== "void" && p.due > 0 && can(user, "finance") && (
        <div className="grid grid-cols-12 gap-2">
          <select className="input col-span-6" value={acc || accounts[0]?.id} onChange={(e) => setAcc(Number(e.target.value))}>
            {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
          <div className="col-span-4"><MoneyInput value={amount || p.due} onChange={setAmount} /></div>
          <button className="btn btn-primary btn-sm col-span-2" onClick={async () => {
            try { await api(`/api/purchases/${id}/pay`, { body: { payment_account_id: acc || accounts[0]?.id, amount: amount || p.due } }); toast("پرداخت ثبت شد"); done(); } catch (e: any) { toast(e.message, "error"); }
          }}>پرداخت</button>
        </div>
      )}
      {p.status !== "void" && can(user, "void") && (
        <button className="btn btn-sm text-rose-600" onClick={async () => {
          if (!confirm("این خرید باطل شود؟ کالاهایش از موجودی خارج و پرداخت‌هایش برگشت می‌خورد.")) return;
          try { await api(`/api/purchases/${id}/void`, { method: "POST" }); toast("خرید باطل شد"); done(); } catch (e: any) { toast(e.message, "error"); }
        }}><Archive size={14} />ابطال خرید (اشتباه ثبت شده)</button>
      )}
    </div>
  );
}

function Purchases({ onChanged }: { onChanged: () => void }) {
  const { user } = useAuth();
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const list = !["history", "suppliers"].includes(status);
  const { data, reload } = useApi<any[]>(list ? `/api/purchases?status=${status}` : "", [status]);
  const hist = useApi<any[]>(status === "history" ? `/api/trade-docs?kind=purchase&q=${encodeURIComponent(q)}` : "", [status, q]).data;
  const sups = useApi<any[]>(status === "suppliers" ? "/api/suppliers" : "", [status]);
  const [create, setCreate] = useState(false);
  const [view, setView] = useState<number | null>(null);
  const [doc, setDoc] = useState<number | null>(null);
  const [sup, setSup] = useState<number | null>(null);
  const refresh = () => { reload(); onChanged(); };
  const shownSups = (sups.data ?? []).filter((x) => !q || x.name.includes(q) || (x.customer ?? "").includes(q));
  return (
    <Card pad={false} title="خرید از تأمین‌کننده" actions={can(user, "finance") && <button className="btn btn-sm btn-primary" onClick={() => setCreate(true)}><Truck size={14} />خرید جدید</button>}>
      <div className="flex flex-wrap items-center gap-2 px-5 pt-3">
        <Tabs value={status} onChange={(v) => { setStatus(v); setQ(""); }} items={[{ value: "", label: "همه" }, { value: "unpaid", label: "پرداخت‌نشده" }, { value: "paid", label: "تسویه" },
          { value: "void", label: "باطل" }, { value: "suppliers", label: "تأمین‌کنندگان" }, { value: "history", label: "سوابق تیزپرداز" }]} />
        {!list && <input className="input max-w-xs py-1.5 text-sm" placeholder={status === "history" ? "نام فروشنده یا شمارهٔ فاکتور…" : "نام تأمین‌کننده…"} value={q} onChange={(e) => setQ(e.target.value)} />}
      </div>
      {status === "suppliers" ? (!sups.data ? <Loading /> : !shownSups.length ? <Empty icon={<Truck size={28} />} text="تأمین‌کننده‌ای نیست" /> : (
        <div className="mt-3 overflow-x-auto">
          <table className="table [&_td]:px-2.5 [&_th]:px-2.5">
            <thead><tr><th>تأمین‌کننده</th><th>پروندهٔ شخص</th><th>خرید در این سیستم</th><th>خرید در تیزپرداز</th><th>بدهی ما به او</th></tr></thead>
            <tbody>{shownSups.map((x) => (
              <tr key={x.id} className="cursor-pointer" onClick={() => setSup(x.id)}>
                <td className="font-semibold">{x.name}{x.mobile && <div className="num muted text-xs" dir="ltr">{x.mobile}</div>}</td>
                <td className="text-xs">{x.customer ? <>{x.customer}{x.customer_code && <span className="num muted"> · کد {x.customer_code}</span>}</> : <span className="muted">وصل نیست</span>}</td>
                <td className="num">{x.bought ? money(x.bought, false) : "—"}</td>
                <td className="num">{x.history_count ? <>{money(x.history_total, false)}<div className="muted text-[10px]">{num(x.history_count)} فاکتور</div></> : "—"}</td>
                <td className={`num ${x.owed > 0 ? "font-bold text-amber-600" : ""}`}>{x.owed > 0 ? money(x.owed, false) : "—"}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )) : status === "history" ? (!hist ? <Loading /> : !hist.length ? <Empty icon={<Truck size={28} />} text="فاکتور خریدی از تیزپرداز منتقل نشده" /> : (
        <div className="mt-3 overflow-x-auto">
          <table className="table [&_td]:px-2.5 [&_th]:px-2.5">
            <thead><tr><th>فاکتور</th><th>تاریخ</th><th>فروشنده</th><th>جمع کالاها</th><th>تخفیف</th><th>مبلغ فاکتور</th></tr></thead>
            <tbody>{hist.map((d) => (
              <tr key={d.id} className="cursor-pointer" onClick={() => setDoc(d.id)}>
                <td className="font-bold">{d.kind_label} <span className="num">{d.doc_no}</span></td><td className="num">{formatJ(d.at, false)}</td>
                <td>{d.person || d.party || "—"}</td><td className="num">{money(d.items_total, false)}</td>
                <td className="num">{d.discount ? money(d.discount, false) : "—"}</td><td className="num font-semibold">{money(d.total, false)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )) : !data ? <Loading /> : !data.length ? <Empty icon={<Truck size={28} />} text="خریدی ثبت نشده" /> : (
        <div className="mt-3 overflow-x-auto">
          <table className="table [&_td]:px-2.5 [&_th]:px-2.5">
            <thead><tr><th>شماره</th><th>تاریخ</th><th>تأمین‌کننده</th><th>اقلام</th><th>جمع</th><th>پرداخت‌شده</th><th>مانده</th><th></th></tr></thead>
            <tbody>{data.map((p) => (
              <tr key={p.id} className={`cursor-pointer ${p.status === "void" ? "opacity-50" : ""}`} onClick={() => setView(p.id)}>
                <td className="num font-bold">{p.items_count ? p.number : <span className="text-xs">مانده اول دوره</span>}</td><td className="num">{formatJ(p.at, false)}</td><td>{p.supplier ?? "—"}</td>
                <td className="num">{num(p.items_count)}</td><td className="num">{money(p.total, false)}</td><td className="num">{money(p.paid, false)}</td>
                <td className={`num ${p.due > 0 ? "font-bold text-amber-600" : ""}`}>{p.due > 0 ? money(p.due, false) : "—"}</td>
                <td><Badge status={p.status === "open" ? "issued" : p.status} /></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      <Modal open={create} onClose={() => setCreate(false)} title="خرید جدید" wide>{create && <PurchaseForm onDone={() => { setCreate(false); refresh(); }} />}</Modal>
      <Modal open={view !== null} onClose={() => setView(null)} title="جزئیات خرید" wide>{view !== null && <PurchaseView id={view} onChange={refresh} />}</Modal>
      <Modal open={doc !== null} onClose={() => setDoc(null)} title="جزئیات فاکتور خرید (تیزپرداز)" wide>{doc !== null && <TradeDocView id={doc} />}</Modal>
      <Modal open={sup !== null} onClose={() => { setSup(null); sups.reload(); }} title="حساب تأمین‌کننده" wide>
        {sup !== null && <SupplierView id={sup} onOpenPurchase={(pid) => setView(pid)} onChange={() => sups.reload()} />}</Modal>
    </Card>
  );
}

// ------------------------------------------------------------------ profit
function Profit() {
  const [period, setPeriod] = useState("month");
  const [channel, setChannel] = useState("");
  const [sort, setSort] = useState<"profit" | "revenue" | "margin" | "qty">("profit");
  const start = PERIODS[period].start();
  const { data } = useApi<any>(`/api/reports/products?${start ? `start=${start}&` : ""}channel=${channel}`, [period, channel]);
  const rows = useMemo(() => [...(data?.products ?? [])].sort((a, b) => (b[sort] ?? -1e18) - (a[sort] ?? -1e18)), [data, sort]);
  if (!data) return <Loading />;
  const t = data.totals;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Tabs value={period} onChange={setPeriod} items={Object.entries(PERIODS).map(([value, p]) => ({ value, label: p.label }))} />
        <Tabs value={channel} onChange={setChannel} items={[{ value: "", label: "همهٔ فروش‌ها" }, { value: "in_person", label: "حضوری" }, { value: "online", label: "آنلاین (سایت)" }, { value: "history", label: "سوابق تیزپرداز/چهره" }]} />
      </div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="فروش محصولات (پس از تخفیف)" value={cmoney(t.revenue)} hint={`${num(t.qty)} عدد فروخته شده`} tone="sky" />
        <Stat label="بهای تمام‌شدهٔ کالای فروش‌رفته" value={cmoney(t.cogs)} hint={data.method_label} tone="amber" />
        <Stat label="سود ناخالص" value={cmoney(t.profit)} tone={t.profit < 0 ? "pink" : "emerald"} icon={<TrendingUp size={20} />} />
        <Stat label="حاشیهٔ سود" value={pct(t.margin)} hint="سود ناخالص ÷ فروش" />
      </div>
      {t.unknown_cost > 0 && <div className="flex items-start gap-2 rounded-xl bg-amber-500/10 p-2.5 text-xs text-amber-800 dark:text-amber-200"><AlertTriangle size={15} className="shrink-0" />{money(t.unknown_cost)} از فروش‌های سوابق، کالای مشخص ندارد و بهایش معلوم نیست؛ سود آن کامل حساب شده است. برای دقت، فایل دفتر روزنامه را برگردانید و دوباره وارد کنید و کالای آن ردیف‌ها را انتخاب کنید.</div>}
      {t.expenses > 0 && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat label="هزینه‌های ثبت‌شده در تیزپرداز" value={cmoney(t.expenses)} hint="از دفتر روزنامهٔ هزینه (سابقه)" tone="pink" />
          <Stat label="سود خالص (سود ناخالص − این هزینه‌ها)" value={cmoney(t.net)} tone={t.net < 0 ? "pink" : "emerald"} />
        </div>
      )}
      {t.estimated > 0 && <div className="flex items-start gap-2 rounded-xl bg-amber-500/10 p-2.5 text-xs text-amber-800 dark:text-amber-200"><AlertTriangle size={15} className="shrink-0" />بعضی محصولات قبل از ثبت خرید فروخته شده‌اند؛ بهای آن‌ها فعلاً تخمینی است و با ثبت خرید یا موجودی اول دوره دقیق می‌شود.</div>}
      <Card pad={false} title="سود هر محصول">
        <div className="px-5 pt-1"><Tabs value={sort} onChange={setSort} items={[{ value: "profit", label: "بیشترین سود" }, { value: "revenue", label: "بیشترین فروش" }, { value: "margin", label: "حاشیهٔ سود" }, { value: "qty", label: "تعداد" }]} /></div>
        {!rows.length ? <Empty text="در این بازه محصولی فروخته نشده" /> : (
          <div className="mt-2 overflow-x-auto">
            <table className="table [&_td]:px-2.5 [&_th]:px-2.5">
              <thead><tr><th>کد</th><th>محصول</th><th>تعداد</th><th>فروش</th><th>بهای تمام‌شده</th><th>سود ناخالص</th><th>حاشیه</th></tr></thead>
              <tbody>{rows.map((r: any) => (
                <tr key={r.id ?? 0}>
                  <td className="num text-sky-600">{r.code}</td>
                  <td><span className="font-semibold">{r.name}</span>{r.online > 0 && <span className="muted text-xs"> · {num(r.online)} آنلاین</span>}{r.history ? <span className="muted text-xs"> · {num(r.history)} از سوابق</span> : null}{r.estimated && <span className="badge mr-1 bg-amber-500/15 text-amber-700">تخمینی</span>}</td>
                  <td className="num">{num(r.qty)}</td><td className="num">{money(r.revenue, false)}</td><td className="num">{r.unknown_cost ? <span className="text-amber-600">نامعلوم</span> : money(r.cogs, false)}</td>
                  <td className={`num font-bold ${r.profit < 0 ? "text-rose-600" : ""}`}>{r.unknown_cost ? "—" : money(r.profit, false)}</td><td className="num">{r.unknown_cost ? "—" : pct(r.margin)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="ماه به ماه">
          {!data.months.length ? <Empty text="—" /> : (
            <table className="table text-sm [&_td]:px-2 [&_th]:px-2">
              <thead><tr><th>ماه</th><th>فروش</th><th>بهای تمام‌شده</th><th>سود</th><th>حاشیه</th></tr></thead>
              <tbody>{data.months.map((m: any) => <tr key={m.key}><td>{jmLabel(m.key)}</td><td className="num">{money(m.revenue, false)}</td><td className="num">{money(m.cogs, false)}</td><td className="num font-bold">{money(m.profit, false)}</td><td className="num">{pct(m.margin)}</td></tr>)}</tbody>
            </table>
          )}
        </Card>
        <Card title="حضوری، آنلاین و سوابق">
          {!data.channels.length ? <Empty text="—" /> : (
            <table className="table text-sm [&_td]:px-2 [&_th]:px-2">
              <thead><tr><th>کانال</th><th>تعداد</th><th>فروش</th><th>سود</th><th>حاشیه</th></tr></thead>
              <tbody>{data.channels.map((c: any) => <tr key={c.channel}><td>{c.channel === "online" ? "آنلاین (سایت)" : c.channel === "history" ? "سوابق تیزپرداز/چهره" : "حضوری"}</td><td className="num">{num(c.qty)}</td><td className="num">{money(c.revenue, false)}</td><td className="num font-bold">{money(c.profit, false)}</td><td className="num">{pct(c.margin)}</td></tr>)}</tbody>
            </table>
          )}
        </Card>
      </div>
      <p className="muted text-xs leading-6">سود ناخالص = فروش (پس از تخفیف) − بهای تمام‌شدهٔ همان کالاها به روش «{data.method_label}». روش را در «تنظیمات ← محصولات» عوض کنید؛ همهٔ فروش‌های گذشته با روش جدید دوباره حساب می‌شوند.</p>
    </div>
  );
}

export default function Products() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "list";
  const [refresh, setRefresh] = useState(0);
  const sum = useApi<any>("/api/products/summary", [refresh]).data;
  const { user } = useAuth();
  return (
    <div className="space-y-5">
      <PageHeader title="محصولات" subtitle="فروش حضوری و آنلاین محصولات، موجودی، خرید از تأمین‌کننده و سود هر محصول" icon={<Package size={22} />} />
      {sum && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat label="ارزش موجودی انبار" value={cmoney(sum.stock_value)} hint={`${num(sum.units)} عدد · ${num(sum.count)} محصول`} tone="sky" />
          <Stat label="کم‌موجودی" value={num(sum.low)} hint={sum.negative ? `${num(sum.negative)} محصول منفی` : "به حداقل رسیده‌اند"} tone={sum.low ? "amber" : "emerald"} />
          <Stat label="بدهی به تأمین‌کنندگان" value={cmoney(sum.owed_to_suppliers)} tone="pink" />
          <Stat label="روش محاسبهٔ بهای تمام‌شده" value={<span className="text-base">{sum.method_label}</span>} />
        </div>
      )}
      <Tabs value={tab} onChange={(t) => setParams({ tab: t }, { replace: true })} items={[
        { value: "list", label: "محصولات و موجودی" },
        { value: "purchases", label: "خرید از تأمین‌کننده" },
        ...(can(user, "reports") ? [{ value: "profit", label: "سود محصولات" }] : []),
      ]} />
      {tab === "list" && <ProductList onChanged={() => setRefresh((n) => n + 1)} />}
      {tab === "purchases" && <Purchases onChanged={() => setRefresh((n) => n + 1)} />}
      {tab === "profit" && <Profit />}
    </div>
  );
}
