import { CheckCircle2, Download, FileSpreadsheet, RotateCcw, Upload } from "lucide-react";
import { useState } from "react";
import CustomerCleanup from "./CustomerCleanup";
import { Badge, Card, Empty, Field } from "./ui";
import { api, download } from "../lib/api";
import { money } from "../lib/format";
import { useApi, useToast } from "../lib/hooks";
import { faDigits, formatJ } from "../lib/jalali";

const KINDS = [
  { key: "customers", title: "۱. مشتریان", text: "نام، موبایل، تاریخ تولد" },
  { key: "deposits", title: "۲. بیعانه‌های باز", text: "گزارش بیعانه چهره: فقط «صندوق ودیعه»ها منتقل می‌شوند" },
  { key: "history", title: "۳. فیش‌های صادرشده (سوابق خدمات)", text: "گزارش فیش‌ها: تاریخ، خدمت، پرسنل و مبلغ هر مشتری" },
] as const;
const FIELDS: Record<string, string[]> = {
  customers: ["customer_code", "name", "first_name", "last_name", "mobile", "birth_date", "notes"],
  history: ["customer_code", "name", "first_name", "last_name", "mobile", "receipt_no", "date", "time", "service", "line", "staff", "amount",
    "refund_date", "notes"],
  deposits: ["customer_code", "name", "first_name", "last_name", "mobile", "date", "amount", "appt_date", "time", "service", "line", "staff",
    "status", "settled_date", "notes"],
};
const STATUS: Record<string, string> = { review: "بررسی", committed: "ثبت شد", undone: "برگشت خورد", discarded: "کنار گذاشته شد" };

/** Bring customers, past services and future deposits over from the previous salon software (e.g. «چهره»). */
export default function LegacyImport() {
  const toast = useToast();
  const services = useApi<any[]>("/api/services").data ?? [];
  const lines = useApi<any[]>("/api/lines").data ?? [];
  const accounts = (useApi<any[]>("/api/accounts").data ?? []).filter((a) => a.is_active);
  const history = useApi<any[]>("/api/import/legacy");
  const [kind, setKind] = useState<string>("customers");
  const [unit, setUnit] = useState("toman");
  const [file, setFile] = useState<File | null>(null);
  const [pv, setPv] = useState<any>(null);
  const [mapping, setMapping] = useState<Record<string, number>>({});
  const [svcMap, setSvcMap] = useState<Record<string, string>>({});
  const [lineId, setLineId] = useState(0);
  const [accountId, setAccountId] = useState(0);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<any>(null);

  async function check(withMapping?: Record<string, number>) {
    if (!file) return;
    setBusy(true);
    setDone(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("kind", kind);
      form.append("unit", unit);
      if (withMapping) form.append("mapping", JSON.stringify(withMapping));
      const r = await api("/api/import/legacy/preview", { form });
      setPv(r);
      setMapping(r.mapping);
      setSvcMap(Object.fromEntries(r.unknown_services.map((u: any) => [u.name, "new"])));
    } catch (e: any) {
      toast(e.message, "error");
      setPv(null);
    } finally {
      setBusy(false);
    }
  }

  async function commit() {
    if (!pv) return;
    if (!window.confirm(`${faDigits(pv.summary.ok)} ردیف «${pv.kind_label}» وارد سیستم شود؟ (در صورت نیاز بعداً قابل برگشت است)`)) return;
    setBusy(true);
    try {
      const r = await api(`/api/import/legacy/${pv.id}/commit`, { body: {
        service_map: Object.fromEntries(Object.entries(svcMap).map(([k, v]) => [k, /^\d+$/.test(v) ? Number(v) : v])),
        new_line_id: lineId || null, payment_account_id: accountId || null } });
      setDone(r.result);
      setPv(null);
      setFile(null);
      toast("اطلاعات منتقل شد");
      history.reload();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function undo(b: any) {
    if (!window.confirm(`همه اطلاعاتی که با فایل «${b.file_name}» وارد شده حذف شود؟ (مواردی که بعداً استفاده شده‌اند، مثل بیعانهٔ کسرشده، می‌مانند)`)) return;
    try {
      const r = await api(`/api/import/legacy/${b.id}/undo`, { method: "POST" });
      toast(`برگشت انجام شد: ${faDigits(r.removed.customers)} مشتری، ${faDigits(r.removed.appointments)} سابقه/نوبت، ${faDigits(r.removed.deposits)} بیعانه حذف شد`);
      history.reload();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }

  const s = pv?.summary;
  return (
    <div className="space-y-5">
      <Card title={<span className="flex items-center gap-2"><FileSpreadsheet size={18} className="text-emerald-500" />انتقال اطلاعات از نرم‌افزار قبلی (مثل «چهره»)</span>}>
        <div className="space-y-2 text-sm leading-7">
          <p>اطلاعات را از نرم‌افزار قبلی به صورت <b>فایل Excel</b> خروجی بگیرید و به ترتیب زیر وارد کنید. ستون‌ها خودکار شناخته می‌شوند (نام، موبایل، تاریخ شمسی، خدمت، مبلغ…) و قبل از ثبت، پیش‌نمایش و خطاها نشان داده می‌شود.</p>
          <ol className="list-inside list-decimal space-y-1 rounded-2xl bg-violet-500/5 p-3">
            <li><b>لیست مشتریان</b> (کد مشتری، نام، موبایل) ← «مشتریان». <b>کد مشتری</b> کلید همه چیز است: هر مشتری با همان کدِ نرم‌افزار قبلی ثبت می‌شود و بیعانه‌ها و فیش‌ها با همین کد به او وصل می‌شوند. مشتریان جدید این سیستم هم خودکار کد بعدی را می‌گیرند.</li>
            <li>گزارش <b>بیعانه‌ها</b> ← «بیعانه‌های باز». مشتری‌ها با «کد مشتری» و موبایل ساخته می‌شوند؛ ردیف‌های «تسویه» رد می‌شوند و «صندوق ودیعه»ها بیعانهٔ باز می‌شوند و در فاکتور همان مشتری کسر می‌شوند. چون پولش قبلاً گرفته شده، به موجودی کارت/کارتخوان اضافه نمی‌شود (حساب «مانده افتتاحیه»). اگر «تاریخ مراجعه» در آینده باشد، نوبت آن روز با «ساعت نامشخص» ثبت می‌شود.</li>
            <li>گزارش <b>فیش‌های صادرشده</b> ← «فیش‌ها». با «کد مشتری» به همان مشتری وصل می‌شود؛ تاریخ، خدمت، پرسنل و مبلغ در پروندهٔ مشتری ثبت می‌شود و <b>هیچ مبلغی وارد حساب‌ها نمی‌شود</b>. فیش‌های مسترد شده رد می‌شوند.</li>
          </ol>
          <p className="muted text-xs">فایل xls و xlsx هر دو پذیرفته می‌شوند. موبایل <b>اشتباه، ناقص یا تکراری</b> جلوی انتقال را نمی‌گیرد و فقط هشدار داده و در پروندهٔ مشتری علامت می‌خورد. وارد کردن دوبارهٔ یک فایل، مشتری، سابقه یا بیعانهٔ تکراری نمی‌سازد. اگر خروجی آماده ندارید، قالب استاندارد را بگیرید و پر کنید:</p>
          <div className="flex flex-wrap gap-2">
            {KINDS.map((k) => (
              <button key={k.key} className="btn btn-sm" onClick={() => download(`/api/import/legacy/template?kind=${k.key}`, `قالب-${k.key}.xlsx`).catch((e) => toast(e.message, "error"))}>
                <Download size={14} />قالب {k.title.slice(3)}
              </button>
            ))}
          </div>
        </div>
      </Card>

      <Card title="وارد کردن فایل">
        <div className="space-y-4">
          <div className="grid gap-2 sm:grid-cols-3">
            {KINDS.map((k) => (
              <button key={k.key} onClick={() => { setKind(k.key); setPv(null); }}
                className={`rounded-2xl border p-3 text-right transition ${kind === k.key ? "border-transparent bg-gradient-to-l from-pink-500 to-violet-600 text-white shadow" : "hover:bg-violet-500/5"}`}
                style={kind === k.key ? {} : { borderColor: "var(--border)" }}>
                <div className="font-bold">{k.title}</div>
                <div className={`text-xs ${kind === k.key ? "opacity-90" : "muted"}`}>{k.text}</div>
              </button>
            ))}
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="فایل Excel یا CSV">
              <input type="file" accept=".xlsx,.xlsm,.csv,.txt,.xls" className="input" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setPv(null); }} />
            </Field>
            {kind !== "customers" && (
              <Field label="واحد مبالغ در فایل">
                <div className="flex gap-2">
                  {[["rial", "ریال"], ["toman", "تومان"]].map(([v, l]) => (
                    <button key={v} type="button" onClick={() => { setUnit(v); setPv(null); }}
                      className={`flex-1 rounded-xl py-2 text-sm font-semibold ${unit === v ? "bg-violet-600 text-white" : "border"}`} style={unit === v ? {} : { borderColor: "var(--border)" }}>{l}</button>
                  ))}
                </div>
              </Field>
            )}
          </div>
          <button className="btn btn-primary w-full" disabled={!file || busy} onClick={() => check()}><Upload size={16} />{busy ? "در حال بررسی…" : "بررسی فایل (هنوز چیزی ثبت نمی‌شود)"}</button>
          {done && (
            <div className="rounded-2xl bg-emerald-500/10 p-3 text-sm">
              <div className="flex items-center gap-2 font-bold text-emerald-700 dark:text-emerald-300"><CheckCircle2 size={16} />انتقال انجام شد</div>
              <div>مشتری جدید: {faDigits(done.customers_new)} · مشتری موجود: {faDigits(done.customers_updated)} · سابقه/نوبت: {faDigits(done.appointments)} · بیعانه: {faDigits(done.deposits)}</div>
              {done.mobile_issues > 0 && <div className="text-xs text-amber-700 dark:text-amber-300">⚠ {faDigits(done.mobile_issues)} مشتری با موبایل اشتباه/ناقص/تکراری ثبت شد (در پرونده علامت خورده؛ پایین همین صفحه قابل پاک‌سازی است)</div>}
              {done.codes_moved > 0 && <div className="text-xs">{faDigits(done.codes_moved)} مشتری که قبلاً در این سیستم همان کد را گرفته بود، کد جدید گرفت تا کد نرم‌افزار قبلی حفظ شود.</div>}
              {(done.skipped_duplicates > 0 || done.skipped_errors > 0) && <div className="muted text-xs">ردیف تکراری ردشده: {faDigits(done.skipped_duplicates)} · ردیف دارای خطا: {faDigits(done.skipped_errors)}</div>}
            </div>
          )}
        </div>
      </Card>

      {pv && (
        <Card title={`پیش‌نمایش «${pv.kind_label}» - ${pv.file_name}`}>
          <div className="space-y-5">
            <div className="grid gap-2 text-sm sm:grid-cols-4">
              <Stat label="ردیف قابل ثبت" value={s.ok} tone="emerald" />
              <Stat label="ردیف دارای خطا (ثبت نمی‌شود)" value={s.errors} tone={s.errors ? "rose" : "zinc"} />
              <Stat label="مشتری (جدید / موجود)" value={`${faDigits(s.new_customers)} / ${faDigits(s.existing_customers)}`} tone="violet" />
              {s.mobile_issues > 0 && <Stat label="موبایل اشتباه/ناقص/تکراری (فقط هشدار)" value={s.mobile_issues} tone="amber" />}
              {(s.skipped_settled > 0 || s.skipped_refunded > 0) && (
                <Stat label={pv.kind === "deposits" ? "بیعانهٔ تسویه‌شده (منتقل نمی‌شود)" : "فیش مسترد شده (منتقل نمی‌شود)"} value={s.skipped_settled + s.skipped_refunded} tone="zinc" />
              )}
              {pv.kind === "deposits" ? <Stat label="جمع بیعانه‌ها" value={money(s.amount)} tone="amber" />
                : pv.kind === "history" ? <Stat label="بازهٔ تاریخ" value={s.first_date ? `${formatJ(s.first_date, false)} تا ${formatJ(s.last_date, false)}` : "—"} tone="amber" />
                : <Stat label="هشدار" value={s.warnings} tone="amber" />}
            </div>

            <details className="rounded-2xl border p-3 text-sm" style={{ borderColor: "var(--border)" }} open={s.errors > s.ok}>
              <summary className="cursor-pointer font-bold">ستون‌های شناخته‌شده (اگر اشتباه است اصلاح کنید)</summary>
              <div className="mt-3 grid gap-2 sm:grid-cols-3">
                {FIELDS[pv.kind].map((f) => (
                  <Field key={f} label={pv.fields[f]}>
                    <select className="input py-1.5 text-sm" value={mapping[f] ?? -1} onChange={(e) => setMapping({ ...mapping, [f]: Number(e.target.value) })}>
                      <option value={-1}>— ندارد —</option>
                      {pv.headers.map((h: string, i: number) => <option key={i} value={i}>{h || `ستون ${faDigits(i + 1)}`}</option>)}
                    </select>
                  </Field>
                ))}
              </div>
              <button className="btn btn-sm mt-3" disabled={busy} onClick={() => check(Object.fromEntries(Object.entries(mapping).filter(([, v]) => v >= 0)))}>بررسی دوباره با این ستون‌ها</button>
            </details>

            {pv.unknown_services.length > 0 && (
              <div className="space-y-2 rounded-2xl bg-amber-500/10 p-3 text-sm">
                <div className="font-bold">این خدمات در سیستم نیستند - هر کدام معادل کدام خدمت است؟</div>
                <div className="muted text-xs">«خدمت جدید» با همین نام ساخته می‌شود (بعداً در تنظیمات قابل ویرایش/ادغام است).</div>
                <div className="grid gap-2 sm:grid-cols-2">
                  {pv.unknown_services.map((u: any) => (
                    <div key={u.name} className="flex items-center gap-2">
                      <span className="w-1/2 truncate font-semibold" title={u.name}>{u.name} <span className="muted text-xs">({faDigits(u.count)})</span></span>
                      <select className="input w-1/2 py-1 text-sm" value={svcMap[u.name] ?? "new"} onChange={(e) => setSvcMap({ ...svcMap, [u.name]: e.target.value })}>
                        <option value="new">➕ خدمت جدید</option>
                        <option value="skip">فقط در توضیحات بماند</option>
                        {services.map((x) => <option key={x.id} value={x.id}>{x.line} / {x.name}</option>)}
                      </select>
                    </div>
                  ))}
                </div>
                {Object.values(svcMap).includes("new") && (
                  <Field label="خدمات جدید در کدام لاین ساخته شوند؟">
                    <select className="input py-1.5 text-sm" value={lineId} onChange={(e) => setLineId(Number(e.target.value))}>
                      <option value={0}>لاین جدید «خدمات انتقالی»</option>
                      {lines.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                    </select>
                  </Field>
                )}
              </div>
            )}
            {pv.unknown_staff.length > 0 && (
              <div className="rounded-2xl bg-sky-500/10 p-3 text-xs">پرسنلی که در سیستم تعریف نشده‌اند (نامشان در توضیحات سابقه می‌ماند): {pv.unknown_staff.map((u: any) => u.name).join("، ")}</div>
            )}
            {pv.kind === "deposits" && (
              <Field label="بیعانه‌ها روی کدام حساب دریافت ثبت شوند؟" hint="فقط برای مشخص بودن؛ مبلغ به موجودی این حساب اضافه نمی‌شود چون پول قبلاً دریافت شده است.">
                <select className="input" value={accountId || accounts[0]?.id || 0} onChange={(e) => setAccountId(Number(e.target.value))}>
                  {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
              </Field>
            )}

            <div>
              <div className="label">نمونهٔ ردیف‌ها (۳۰ ردیف اول)</div>
              <div className="overflow-x-auto rounded-2xl border" style={{ borderColor: "var(--border)" }}>
                <table className="table text-xs">
                  <thead><tr><th>ردیف</th><th>کد</th><th>مشتری</th><th>موبایل</th>{pv.kind !== "customers" && <><th>تاریخ</th><th>خدمت</th><th>پرسنل</th><th>مبلغ</th></>}{pv.kind === "deposits" && <th>نوبت</th>}<th>وضعیت</th></tr></thead>
                  <tbody>
                    {pv.sample.map((r: any) => (
                      <tr key={r.row} className={r.errors.length ? "bg-rose-500/10" : r.warnings.length ? "bg-amber-500/5" : ""}>
                        <td className="num">{faDigits(r.row)}</td>
                        <td className="num">{r.code}</td>
                        <td className="font-semibold">{r.name}{r.receipt ? <div className="muted num text-[10px]">فیش {r.receipt}</div> : null}</td>
                        <td className="num" dir="ltr">{r.mobile ?? ""}</td>
                        {pv.kind !== "customers" && <>
                          <td>{r.date ? formatJ(r.date, pv.kind === "history" && !r.date.endsWith("12:00")) : ""}</td>
                          <td>{r.service_name}{r.service_name && (r.service_id ? <span className="text-emerald-600"> ✓</span> : <span className="text-amber-600"> (جدید)</span>)}{!r.service_name && r.line_name ? <span className="muted">{r.line_name}</span> : null}</td>
                          <td>{r.staff_name}</td>
                          <td className="num">{r.amount ? money(r.amount) : ""}</td>
                        </>}
                        {pv.kind === "deposits" && <td>{r.appt_date ? formatJ(r.appt_date, r.appt_time_known) : "—"}{r.appt_date && !r.appt_time_known ? <div className="text-[10px] text-amber-600">ساعت نامشخص</div> : null}</td>}
                        <td className="max-w-56">{r.errors.length ? <span className="text-rose-600">✕ {r.errors.join("، ")}</span> : r.warnings.length ? <span className="text-amber-600">⚠ {r.warnings.join("، ")}</span> : <span className="text-emerald-600">✓</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            {pv.error_rows.length > 0 && (
              <details className="rounded-2xl bg-rose-500/10 p-3 text-xs">
                <summary className="cursor-pointer font-bold text-rose-700 dark:text-rose-300">ردیف‌های دارای خطا ({faDigits(s.errors)}) - این‌ها ثبت نمی‌شوند</summary>
                <div className="mt-2 space-y-1">{pv.error_rows.map((r: any) => <div key={r.row}>ردیف {faDigits(r.row)}: {r.name || r.mobile || "—"} - {r.errors.join("، ")}</div>)}</div>
              </details>
            )}
            <div className="flex flex-wrap gap-2">
              <button className="btn btn-primary flex-1 py-3" disabled={busy || !s.ok} onClick={commit}>ثبت {faDigits(s.ok)} ردیف در سیستم</button>
              <button className="btn" onClick={() => { api(`/api/import/legacy/${pv.id}`, { method: "DELETE" }).catch(() => {}); setPv(null); }}>انصراف</button>
            </div>
          </div>
        </Card>
      )}

      <Card title="پاک‌سازی مشتریان بدون سابقه با موبایل مشکل‌دار">
        <div className="muted mb-2 text-xs">بعد از ورود فیش‌ها، مشتریانی که هیچ خدمتی ندارند و موبایلشان اشتباه/ناقص/تکراری است اینجا فهرست می‌شوند تا یک‌جا حذف کنید.</div>
        <CustomerCleanup key={history.data?.length ?? 0} />
      </Card>

      <Card title="سابقهٔ انتقال‌ها">
        {!history.data?.length ? <Empty text="هنوز فایلی وارد نشده" /> : (
          <div className="space-y-2">
            {history.data.filter((b) => b.status !== "discarded").map((b) => (
              <div key={b.id} className="flex flex-wrap items-center justify-between gap-2 rounded-2xl px-3 py-2 text-sm" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
                <div>
                  <div className="font-semibold">{b.kind_label} · {b.file_name}</div>
                  <div className="muted text-xs">{formatJ(b.created_at)}{b.result ? ` · مشتری جدید ${faDigits(b.result.customers_new)} · سابقه/نوبت ${faDigits(b.result.appointments)} · بیعانه ${faDigits(b.result.deposits)}` : ""}</div>
                </div>
                <div className="flex items-center gap-2">
                  <Badge>{STATUS[b.status] ?? b.status}</Badge>
                  {b.status === "committed" && <button className="btn btn-sm" onClick={() => undo(b)}><RotateCcw size={14} />برگشت</button>}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: any; tone: string }) {
  const cls: Record<string, string> = { emerald: "bg-emerald-500/10", rose: "bg-rose-500/10", violet: "bg-violet-500/10", amber: "bg-amber-500/10", zinc: "bg-zinc-500/10" };
  return (
    <div className={`rounded-xl px-3 py-2 ${cls[tone]}`}>
      <div className="muted text-xs">{label}</div>
      <div className="text-base font-extrabold">{typeof value === "number" ? faDigits(value) : value}</div>
    </div>
  );
}
