import { AlertTriangle, CheckCircle2, Globe, KeyRound, Link2, RefreshCw, ShoppingCart } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import JalaliPicker from "./JalaliPicker";
import ProductPicker from "./ProductPicker";
import { Card, Empty, Field, Tabs } from "./ui";
import { api } from "../lib/api";
import { money, num } from "../lib/format";
import { useApi, useToast } from "../lib/hooks";
import { faDigits, formatJ, toLocalIso } from "../lib/jalali";

const STATE: Record<string, { l: string; cls: string }> = {
  booked: { l: "ثبت شد", cls: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" },
  cancelled: { l: "لغو / مسترد", cls: "bg-rose-500/10 text-rose-700 dark:text-rose-300" },
  waiting: { l: "منتظر پرداخت", cls: "bg-amber-500/10 text-amber-700 dark:text-amber-300" },
  skipped: { l: "ثبت نشد", cls: "bg-zinc-500/10 text-zinc-600 dark:text-zinc-300" },
  local_void: { l: "در سیستم باطل شده", cls: "bg-zinc-500/10 text-zinc-600 dark:text-zinc-300" },
  error: { l: "خطا", cls: "bg-rose-500/15 text-rose-700 dark:text-rose-300" },
};
const OUT_STATUS: Record<string, { l: string; cls: string }> = {
  pending: { l: "در صف", cls: "bg-amber-500/10 text-amber-700 dark:text-amber-300" },
  done: { l: "ارسال شد", cls: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" },
  skipped: { l: "ارسال نشد", cls: "bg-zinc-500/10 text-zinc-600 dark:text-zinc-300" },
  error: { l: "خطا", cls: "bg-rose-500/15 text-rose-700 dark:text-rose-300" },
};

const ago = (iso?: string | null) => (iso ? formatJ(iso) : "—");

/** The clinic's WordPress / WooCommerce shop: connection, automatic sync of orders and stock, comparison. */
export default function WooSync() {
  const toast = useToast();
  const { data, reload } = useApi<any>("/api/woo/config");
  const accounts = (useApi<any[]>("/api/accounts").data ?? []).filter((a) => a.is_active);
  const [f, setF] = useState<any>(null);
  const [test, setTest] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<"products" | "orders" | "outbox" | "log">("products");
  const [tick, setTick] = useState(0);
  useEffect(() => { if (data && !f) setF({ ...data.config, start: data.config.start ?? toLocalIso(new Date()).slice(0, 10) }); }, [data, f]);
  if (!data || !f) return <Card><div className="muted text-sm">در حال بارگذاری…</div></Card>;
  const st = data.status;
  const set = (k: string, v: any) => setF({ ...f, [k]: v });

  async function save(extra: any = {}) {
    setBusy(true);
    try {
      const r = await api("/api/woo/config", { method: "PUT", body: { ...f, ...extra } });
      setF({ ...r.config, start: r.config.start ?? f.start });
      toast("تنظیمات سایت ذخیره شد");
      reload();
      return true;
    } catch (e: any) {
      toast(e.message, "error");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function check() {
    if (!(await save())) return;
    setBusy(true);
    try {
      const r = await api("/api/woo/test", { method: "POST" });
      setTest(r);
      reload();
    } finally {
      setBusy(false);
    }
  }

  async function syncNow() {
    setBusy(true);
    try {
      const r = await api("/api/woo/sync", { method: "POST" });
      if (r.busy) toast("هماهنگی در حال انجام است؛ چند لحظه بعد دوباره ببینید");
      else if (r.error) toast(r.error, "error");
      else {
        const o = r.orders ?? {};
        const newOnes = (o.booked ?? 0) + (o.rebooked ?? 0);
        toast(`هماهنگ شد: ${faDigits(newOnes)} سفارش جدید، ${faDigits(o.paid ?? 0)} پرداخت، ${faDigits(o.cancelled ?? 0)} لغو، ${faDigits(r.stock?.sent ?? 0)} تغییر موجودی به سایت`);
      }
      reload();
      setTick((t) => t + 1);
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  }

  const methods = Object.entries(st.methods ?? {});
  return (
    <div className="space-y-5">
      <Card title={<span className="flex items-center gap-2"><Globe size={18} className="text-sky-500" />اتصال به فروشگاه سایت (وردپرس / ووکامرس)</span>}>
        <div className="space-y-2 text-sm leading-7">
          <p>بعد از اتصال، برنامه هر چند دقیقه یک بار از سایت می‌پرسد چه خبر است:</p>
          <ul className="list-inside list-disc space-y-1 rounded-2xl bg-sky-500/5 p-3">
            <li><b>هر سفارش سایت</b> خودکار یک <b>فاکتور فروش آنلاین</b> می‌شود؛ مشتری با <b>شماره موبایل</b> پیدا می‌شود و اگر نبود، مشتری جدید با همان موبایل ساخته می‌شود. پول روی حسابی که برای درگاه انتخاب می‌کنید می‌نشیند.</li>
            <li>سفارشی که در سایت <b>لغو یا مسترد</b> شود، فاکتورش اینجا باطل و پولش برگشت داده می‌شود. پرداخت کارت‌به‌کارت یا در محل، وقتی در سایت «پرداخت‌شده» شود اینجا ثبت می‌شود.</li>
            <li><b>خرید، فروش حضوری و شمارش انبار</b> این‌جا، موجودی همان کالا (با همان SKU) را در سایت هم عوض می‌کند.</li>
          </ul>
          <details className="rounded-2xl border p-3" style={{ borderColor: "var(--border)" }}>
            <summary className="cursor-pointer font-bold"><KeyRound size={14} className="inline" /> کلید API را از کجا بیاورم؟ (یک بار، چند دقیقه)</summary>
            <ol className="mt-2 list-inside list-decimal space-y-1">
              <li>وارد پیشخوان وردپرس سایت شوید.</li>
              <li>منوی <b>ووکامرس ← پیکربندی (تنظیمات) ← پیشرفته ← REST API</b> را باز کنید.</li>
              <li><b>«افزودن کلید»</b> را بزنید. توضیح: «حسابداری کلینیک»، کاربر: مدیر سایت، دسترسی: <b>خواندن/نوشتن (Read/Write)</b>.</li>
              <li><b>«ایجاد کلید API»</b> را بزنید. دو کد نشان داده می‌شود: <b dir="ltr">Consumer key</b> (با ck_ شروع می‌شود) و <b dir="ltr">Consumer secret</b> (با cs_). هر دو را این‌جا کپی کنید. (رمز فقط همان یک بار نشان داده می‌شود.)</li>
            </ol>
          </details>
        </div>
      </Card>

      <Card title="۱. اتصال">
        <div className="space-y-3">
          <Field label="آدرس سایت" hint="مثلاً https://clinic.ir"><input className="input" dir="ltr" value={f.url} onChange={(e) => set("url", e.target.value)} placeholder="https://" /></Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Consumer key"><input className="input num" dir="ltr" value={f.key} onFocus={(e) => e.target.select()} onChange={(e) => set("key", e.target.value)} placeholder="ck_…" /></Field>
            <Field label="Consumer secret" hint={f.has_key ? "ذخیره شده؛ برای عوض کردن، رمز جدید را بنویسید" : undefined}>
              <input className="input num" dir="ltr" type="password" value={f.secret} onFocus={(e) => e.target.select()} onChange={(e) => set("secret", e.target.value)} placeholder="cs_…" /></Field>
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn btn-primary" disabled={busy || !f.url} onClick={check}><Link2 size={15} />ذخیره و آزمایش اتصال</button>
          </div>
          {test && (test.ok ? (
            <div className="rounded-2xl bg-emerald-500/10 p-3 text-sm">
              <div className="flex items-center gap-2 font-bold text-emerald-700 dark:text-emerald-300"><CheckCircle2 size={16} />اتصال برقرار است</div>
              <div>{faDigits(test.products)} محصول و {faDigits(test.orders)} سفارش در سایت · واحد پول سایت: <b>{test.currency_label}</b></div>
              {!test.currency_ok && <div className="text-rose-600">واحد پول سایت «{test.currency}» پشتیبانی نمی‌شود (باید ریال یا تومان باشد).</div>}
            </div>
          ) : <div className="flex items-start gap-2 rounded-2xl bg-rose-500/10 p-3 text-sm text-rose-700 dark:text-rose-300"><AlertTriangle size={16} className="shrink-0" />{test.error}</div>)}
        </div>
      </Card>

      <Card title="۲. هماهنگی خودکار">
        <div className="space-y-4 text-sm">
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="ثبت سفارش‌های سایت از تاریخ" hint="سفارش‌های قبل از این تاریخ ثبت نمی‌شوند (در سوابق تیزپرداز هستند). معمولاً همان روز ورود موجودی از تیزپرداز.">
              <JalaliPicker withTime={false} pastOnly value={f.start ? `${f.start}T00:00` : ""} onChange={(v) => set("start", v ? v.slice(0, 10) : null)} />
            </Field>
            <Field label="هر چند دقیقه یک بار؟"><input type="number" min={1} max={1440} className="input num" value={f.interval} onChange={(e) => set("interval", Number(e.target.value))} /></Field>
            <Field label="پول سفارش‌های سایت روی کدام حساب بنشیند؟" hint="درگاه پرداخت سایت (یا حسابی که درگاه به آن واریز می‌کند)">
              <select className="input" value={f.account_id ?? 0} onChange={(e) => set("account_id", Number(e.target.value) || null)}>
                <option value={0}>— انتخاب کنید —</option>
                {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            </Field>
          </div>
          {methods.length > 0 && (
            <div className="rounded-2xl p-3" style={{ background: "var(--surface)" }}>
              <div className="mb-2 font-bold">روش‌های پرداخت سایت (اگر هر کدام به حساب دیگری می‌رود)</div>
              <div className="grid gap-2 sm:grid-cols-2">
                {methods.map(([m, title]: any) => (
                  <label key={m} className="flex items-center gap-2">
                    <span className="w-1/2 truncate" title={m}>{title}</span>
                    <select className="input w-1/2 py-1 text-xs" value={f.method_accounts?.[m] ?? 0}
                      onChange={(e) => set("method_accounts", { ...(f.method_accounts ?? {}), [m]: Number(e.target.value) || undefined })}>
                      <option value={0}>همان حساب اصلی</option>
                      {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                    </select>
                  </label>
                ))}
              </div>
            </div>
          )}
          <div className="space-y-2">
            <label className="flex items-center gap-2"><input type="checkbox" checked={!!f.push_stock} onChange={(e) => set("push_stock", e.target.checked)} />خرید، فروش حضوری و شمارش انبار، موجودی سایت را هم عوض کند</label>
            <label className="flex items-center gap-2"><input type="checkbox" checked={!!f.pull_prices} onChange={(e) => set("pull_prices", e.target.checked)} />قیمت محصولات سایت به‌عنوان «قیمت آنلاین» در سیستم بیاید</label>
            <label className="flex items-center gap-2 font-bold"><input type="checkbox" checked={!!f.enabled} onChange={(e) => set("enabled", e.target.checked)} />هماهنگی خودکار روشن باشد</label>
          </div>
          <button className="btn btn-primary w-full" disabled={busy} onClick={() => save()}>ذخیرهٔ تنظیمات هماهنگی</button>
        </div>
      </Card>

      <Card title="۳. وضعیت" actions={<button className="btn btn-sm" disabled={busy || !f.has_key} onClick={syncNow}><RefreshCw size={14} className={busy ? "animate-spin" : ""} />هماهنگ‌سازی همین حالا</button>}>
        <div className="grid gap-2 text-sm sm:grid-cols-4">
          <Box label="آخرین هماهنگی موفق" value={ago(st.last_ok)} tone={st.last_error ? "amber" : "emerald"} />
          <Box label="سفارش‌های ثبت‌شده" value={faDigits(st.orders?.booked ?? 0)} tone="sky" />
          <Box label="محصولات سایت (وصل به سیستم)" value={`${faDigits(st.site_products)} (${faDigits(st.linked)})`} tone="violet" />
          <Box label="تغییر موجودی در صف ارسال" value={faDigits(st.outbox_pending)} tone={st.outbox_pending ? "amber" : "zinc"} />
        </div>
        {!f.enabled && f.has_key && <div className="mt-3 rounded-xl bg-amber-500/10 p-2.5 text-xs text-amber-800 dark:text-amber-200">هماهنگی خودکار خاموش است؛ سفارش‌ها ثبت و موجودی ارسال نمی‌شود (فقط فهرست محصولات سایت خوانده می‌شود).</div>}
        {st.last_error && <div className="mt-3 flex items-start gap-2 rounded-xl bg-rose-500/10 p-2.5 text-xs text-rose-700 dark:text-rose-300"><AlertTriangle size={14} className="shrink-0" />آخرین تلاش ({ago(st.last_run)}): {st.last_error}</div>}
        {(st.orders?.error ?? 0) > 0 && <div className="mt-2 text-xs text-rose-600">{faDigits(st.orders.error)} سفارش ثبت نشده؛ در «سفارش‌های سایت» دلیلش نوشته شده است.</div>}
      </Card>

      <Card pad={false}>
        <div className="px-5 pt-4"><Tabs value={tab} onChange={setTab} items={[{ value: "products", label: "محصولات سایت و موجودی" }, { value: "orders", label: "سفارش‌های سایت" }, { value: "outbox", label: "ارسال موجودی به سایت" }, { value: "log", label: "گزارش" }]} /></div>
        <div className="p-5 pt-3">
          {tab === "products" && <SiteProducts key={tick} onChange={() => { reload(); setTick((t) => t + 1); }} />}
          {tab === "orders" && <SiteOrders key={tick} />}
          {tab === "outbox" && <Outbox key={tick} />}
          {tab === "log" && <SyncLog key={tick} />}
        </div>
      </Card>
    </div>
  );
}

function Box({ label, value, tone }: { label: string; value: any; tone: string }) {
  const cls: Record<string, string> = { emerald: "bg-emerald-500/10", amber: "bg-amber-500/10", sky: "bg-sky-500/10", violet: "bg-violet-500/10", zinc: "bg-zinc-500/10" };
  return <div className={`rounded-xl px-3 py-2 ${cls[tone]}`}><div className="muted text-xs">{label}</div><div className="text-base font-extrabold">{value}</div></div>;
}

function SiteProducts({ onChange }: { onChange: () => void }) {
  const toast = useToast();
  const { data, reload } = useApi<any>("/api/woo/products");
  const [only, setOnly] = useState<"all" | "differs" | "unlinked">("differs");
  const [linking, setLinking] = useState<number | null>(null);
  if (!data) return <div className="muted text-sm">در حال بارگذاری…</div>;
  const rows: any[] = data.rows;
  const differs = rows.filter((r) => r.stock_differs);
  const alignable = differs.filter((r) => r.product.stock_qty >= 0);
  const unlinked = rows.filter((r) => !r.product);
  const shown = only === "differs" ? differs : only === "unlinked" ? unlinked : rows;

  async function align(body: any, question: string) {
    if (!window.confirm(question)) return;
    try {
      const r = await api("/api/woo/stock/align", { body });
      toast(r.error ? r.error : `موجودی ${faDigits(r.queued)} کالا در سایت برابر سیستم شد`, r.error ? "error" : undefined);
      reload();
      onChange();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }
  async function link(woo_id: number, product_id: number | null) {
    try {
      await api("/api/woo/products/link", { body: { woo_id, product_id } });
      setLinking(null);
      reload();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }

  if (!rows.length) return <Empty text="هنوز محصولی از سایت خوانده نشده؛ «هماهنگ‌سازی همین حالا» را بزنید" />;
  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Tabs value={only} onChange={setOnly} items={[{ value: "differs", label: `موجودی متفاوت (${faDigits(differs.length)})` }, { value: "unlinked", label: `وصل‌نشده (${faDigits(unlinked.length)})` }, { value: "all", label: `همه (${faDigits(rows.length)})` }]} />
        {alignable.length > 0 && <button className="btn btn-sm btn-primary mr-auto" onClick={() => align({ all: true }, `موجودی ${faDigits(alignable.length)} کالا در سایت برابر موجودی سیستم شود؟\n(ملاک موجودی، این سیستم است. کالاهایی که موجودی سیستمشان منفی است عوض نمی‌شوند.)`)}>موجودی سایت = سیستم (همه)</button>}
      </div>
      <div className="overflow-x-auto rounded-2xl border" style={{ borderColor: "var(--border)" }}>
        <table className="table text-xs">
          <thead><tr><th>SKU</th><th>محصول در سایت</th><th>موجودی سایت</th><th>محصول در سیستم</th><th>موجودی سیستم</th><th>قیمت سایت</th><th /></tr></thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.woo_id} className={r.stock_differs ? "bg-amber-500/5" : ""}>
                <td className="num whitespace-nowrap" dir="ltr">{r.sku || "—"}</td>
                <td>{r.site_name}{r.site_status !== "publish" && <span className="muted"> ({r.site_status})</span>}</td>
                <td className="num">{r.manage_stock ? num(r.site_stock ?? 0) : <span className="text-amber-600">مدیریت موجودی خاموش</span>}</td>
                <td className="min-w-48">
                  {linking === r.woo_id ? <ProductPicker onChange={(p) => link(r.woo_id, p.id)} /> : r.product
                    ? <span><span className="num muted">{r.product.code}</span> {r.product.name}</span>
                    : <button className="text-violet-600 hover:underline dark:text-violet-300" onClick={() => setLinking(r.woo_id)}>وصل نیست - انتخاب محصول</button>}
                </td>
                <td className={`num ${r.stock_differs ? "font-bold text-amber-700 dark:text-amber-300" : ""}`}>{r.product ? num(r.product.stock_qty) : "—"}
                  {r.product && r.product.stock_qty < 0 && <div className="max-w-40 text-[10px] font-normal text-rose-600">منفی: اول خرید یا موجودی اول دورهٔ این کالا را ثبت کنید</div>}</td>
                <td className="num">{r.site_price ? money(r.site_price, false) : "—"}</td>
                <td className="whitespace-nowrap">
                  {r.product && r.stock_differs && <button className="btn btn-sm py-0.5 text-xs" onClick={() => align({ product_ids: [r.product.id] }, `موجودی «${r.site_name}» در سایت ${faDigits(Math.max(0, r.product.stock_qty))} شود؟`)}>برابر کن</button>}
                  {r.pending > 0 && <span className="badge mr-1 bg-amber-500/10 text-amber-700">در صف</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {shown.length === 0 && <div className="muted text-center text-xs">موردی نیست 👌</div>}
      {data.not_on_site.length > 0 && (
        <details className="rounded-2xl p-3 text-xs" style={{ background: "var(--surface)" }}>
          <summary className="cursor-pointer font-bold">محصولاتی که در سیستم SKU دارند ولی در سایت پیدا نشدند ({faDigits(data.not_on_site.length)})</summary>
          <div className="mt-2 grid gap-1 sm:grid-cols-2">{data.not_on_site.map((p: any) => <div key={p.id}><span className="num muted">{p.code}</span> {p.name} <span className="num muted" dir="ltr">{p.sku}</span></div>)}</div>
        </details>
      )}
    </div>
  );
}

function SiteOrders() {
  const { data } = useApi<any[]>("/api/woo/orders");
  if (!data) return <div className="muted text-sm">در حال بارگذاری…</div>;
  if (!data.length) return <Empty icon={<ShoppingCart size={28} />} text="هنوز سفارشی از سایت ثبت نشده" />;
  return (
    <div className="overflow-x-auto rounded-2xl border" style={{ borderColor: "var(--border)" }}>
      <table className="table text-xs">
        <thead><tr><th>سفارش</th><th>تاریخ</th><th>مشتری</th><th>مبلغ</th><th>وضعیت در سایت</th><th>در سیستم</th><th>فاکتور</th></tr></thead>
        <tbody>
          {data.map((o) => (
            <tr key={o.order_id}>
              <td className="num font-bold">#{o.number}</td>
              <td className="num whitespace-nowrap">{o.created ? formatJ(o.created) : "—"}</td>
              <td>{o.customer_id ? <Link className="hover:underline" to={`/customers?q=${encodeURIComponent(o.mobile ?? o.customer)}`}>{o.customer}</Link> : "—"}{o.mobile && <div className="num muted" dir="ltr">{o.mobile}</div>}</td>
              <td className="num">{money(o.total, false)}{o.refunded > 0 && <div className="text-[10px] text-rose-600">برگشتی {money(o.refunded, false)}</div>}</td>
              <td>{o.status_fa}{o.method && <div className="muted text-[10px]">{o.method}{o.paid ? " · پرداخت شده" : ""}</div>}</td>
              <td><span className={`badge ${STATE[o.state]?.cls ?? ""}`}>{STATE[o.state]?.l ?? o.state}</span>{o.note && <div className="max-w-48 text-[10px] text-rose-600">{o.note}</div>}</td>
              <td>{o.invoice_id ? <Link className="num text-violet-600 hover:underline dark:text-violet-300" to={`/invoices?open=${o.invoice_id}`}>{o.invoice}</Link> : "—"}{o.invoice_status === "void" && <div className="muted text-[10px]">باطل</div>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Outbox() {
  const toast = useToast();
  const { data, reload } = useApi<any[]>("/api/woo/outbox");
  if (!data) return <div className="muted text-sm">در حال بارگذاری…</div>;
  if (!data.length) return <Empty text="هنوز تغییری برای سایت فرستاده نشده" />;
  const errors = data.filter((e) => e.status === "error").length;
  return (
    <div className="space-y-2">
      {errors > 0 && <button className="btn btn-sm" onClick={async () => { await api("/api/woo/outbox/retry", { method: "POST" }); toast("دوباره در صف قرار گرفت"); reload(); }}>تلاش دوباره برای {faDigits(errors)} مورد خطادار</button>}
      <div className="overflow-x-auto rounded-2xl border" style={{ borderColor: "var(--border)" }}>
        <table className="table text-xs">
          <thead><tr><th>زمان</th><th>محصول</th><th>تغییر</th><th>علت</th><th>وضعیت</th><th>موجودی سایت</th></tr></thead>
          <tbody>
            {data.map((e) => (
              <tr key={e.id}>
                <td className="num whitespace-nowrap">{formatJ(e.created_at)}</td>
                <td>{e.product}</td>
                <td className="num">{e.kind === "set" ? "= موجودی سیستم" : <span className={e.qty < 0 ? "text-rose-600" : "text-emerald-600"}>{e.qty > 0 ? "+" : ""}{num(e.qty)}</span>}</td>
                <td>{e.reason}</td>
                <td><span className={`badge ${OUT_STATUS[e.status]?.cls}`}>{OUT_STATUS[e.status]?.l}</span>{e.error && <div className="max-w-56 text-[10px] text-rose-600">{e.error}{e.attempts ? ` (تلاش ${faDigits(e.attempts)})` : ""}</div>}</td>
                <td className="num whitespace-nowrap">{e.result ? `از ${faDigits(e.result.split(" → ")[0])} به ${faDigits(e.result.split(" → ")[1] ?? "")}` : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SyncLog() {
  const { data } = useApi<any[]>("/api/woo/log");
  if (!data) return <div className="muted text-sm">در حال بارگذاری…</div>;
  if (!data.length) return <Empty text="گزارشی نیست" />;
  return (
    <div className="max-h-96 space-y-1 overflow-y-auto text-xs">
      {data.map((x, i) => (
        <div key={i} className={`rounded-lg px-3 py-1.5 ${x.level === "error" ? "bg-rose-500/10" : x.level === "warn" ? "bg-amber-500/10" : ""}`} style={x.level === "info" ? { background: "var(--surface)" } : {}}>
          <span className="num muted ml-2">{formatJ(x.at)}</span>{x.message}
        </div>
      ))}
    </div>
  );
}
