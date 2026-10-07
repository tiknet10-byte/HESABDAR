import {
  AlertTriangle, Archive, Bell, BookOpen, Bot, Brain, CreditCard, Database, DatabaseBackup, GitMerge, KeyRound, Pencil, Plug, Plus, RotateCcw, Scissors,
  Settings as Cog, ShieldCheck, Stethoscope, Calculator, FileSpreadsheet, Package, Save, Trash2, UserCog, Users, Wrench,
} from "lucide-react";
import LegacyImport from "../components/LegacyImport";
import AccountRouting from "../components/AccountRouting";
import { MergeDialog, ServiceHealth } from "../components/ServiceTools";
import JalaliPicker from "../components/JalaliPicker";
import { toLocalIso } from "../lib/jalali";
import { type ReactNode, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Badge, Card, Empty, Field, Loading, Modal, MoneyInput, PageHeader, Tabs } from "../components/ui";
import { api, download } from "../lib/api";
import { ACCOUNT_KINDS, jdatetime, money, num, ROLES, unitLabel } from "../lib/format";
import { can, useApi, useAuth, useToast } from "../lib/hooks";

const WEEKDAYS = [{ v: 5, l: "شنبه" }, { v: 6, l: "یکشنبه" }, { v: 0, l: "دوشنبه" }, { v: 1, l: "سه‌شنبه" }, { v: 2, l: "چهارشنبه" }, { v: 3, l: "پنجشنبه" }, { v: 4, l: "جمعه" }];

function General() {
  const toast = useToast();
  const { data, setData } = useApi<Record<string, any>>("/api/settings");
  if (!data) return <Loading />;
  const set = (k: string, v: any) => setData({ ...data, [k]: v });
  const text = (k: string, label: string, area = false) => (
    <Field label={label}>{area ? <textarea className="input min-h-20" value={data[k] ?? ""} onChange={(e) => set(k, e.target.value)} /> : <input className="input" value={data[k] ?? ""} onChange={(e) => set(k, e.target.value)} />}</Field>
  );
  const daysOff: number[] = data["booking.days_off"] ?? [];
  return (
    <div className="space-y-4">
      <Card title="مشخصات سالن"><div className="grid gap-3 sm:grid-cols-2">{text("salon.name", "نام سالن")}{text("salon.phone", "تلفن")}{text("salon.address", "آدرس")}</div></Card>
      <Card title="نوبت‌دهی">
        <div className="space-y-4">
          <label className="flex items-start gap-3 rounded-2xl bg-violet-500/10 p-3 text-sm">
            <input type="checkbox" className="mt-1" checked={!!data["booking.auto"]} onChange={(e) => set("booking.auto", e.target.checked)} />
            <span><b>نوبت‌دهی خودکار</b><br /><span className="muted">با ثبت بیعانه‌ای که خدمتش مشخص است، اولین نوبت خالی همان خدمت به‌صورت خودکار رزرو می‌شود. اگر خاموش باشد، سیستم فقط نوبت خالی را پیشنهاد می‌دهد.</span></span>
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="شروع ساعت کاری"><input type="time" className="input" value={data["booking.open"]} onChange={(e) => set("booking.open", e.target.value)} /></Field>
            <Field label="پایان ساعت کاری"><input type="time" className="input" value={data["booking.close"]} onChange={(e) => set("booking.close", e.target.value)} /></Field>
          </div>
          <div>
            <div className="label">روزهای تعطیل</div>
            <div className="flex flex-wrap gap-2">
              {WEEKDAYS.map((w) => (
                <button type="button" key={w.v} onClick={() => set("booking.days_off", daysOff.includes(w.v) ? daysOff.filter((x) => x !== w.v) : [...daysOff, w.v])}
                  className={`rounded-xl px-3 py-1.5 text-sm font-semibold ${daysOff.includes(w.v) ? "bg-rose-500 text-white" : "border hover:bg-violet-500/10"}`} style={daysOff.includes(w.v) ? {} : { borderColor: "var(--border)" }}>{w.l}</button>
              ))}
            </div>
          </div>
          <p className="muted text-xs">فاصله نوبت‌ها برای هر خدمت جداست: مدت انجام هر خدمت را در «لاین‌ها و خدمات» وارد کنید (هنگام نوبت‌دهی هم قابل تغییر است). لاین هر پرسنل در «پرسنل و کاربران» تعیین می‌شود؛ ظرفیت هر لاین = تعداد پرسنل آن لاین.</p>
        </div>
      </Card>
      <Card title="تطبیق رسید با بانک">
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="بازه زمانی تطبیق (ساعت)"><input type="number" className="input" value={data["matching.window_hours"]} onChange={(e) => set("matching.window_hours", Number(e.target.value))} /></Field>
          <Field label="مهلت دیده‌شدن واریز در بانک (ساعت)"><input type="number" className="input" value={data["matching.grace_hours"]} onChange={(e) => set("matching.grace_hours", Number(e.target.value))} /></Field>
          <label className="flex items-center gap-2 pt-6 text-sm"><input type="checkbox" checked={!!data["matching.require_bank_confirmation"]} onChange={(e) => set("matching.require_bank_confirmation", e.target.checked)} />ثبت رسید فقط پس از تأیید بانک</label>
        </div>
      </Card>
      <Card title="پیام‌های ربات">
        <div className="space-y-3">
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!data["bot.auto_reply"]} onChange={(e) => set("bot.auto_reply", e.target.checked)} />پاسخ خودکار به مشتری</label>
          {text("bot.ask_name_message", "درخواست نام از مشتری جدید", true)}
          {text("bot.confirm_message", "تأیید ثبت ({name} و {amount})", true)}
          {text("bot.mismatch_message", "پیام در انتظار تأیید بانک", true)}
        </div>
      </Card>
      <Card title="پشتیبان"><div className="space-y-3">{text("backup.mirror_dir", "پوشه کپی دوم پشتیبان (مثلاً E:\\Backup یا پوشه Google Drive)")}</div></Card>
      <button className="btn btn-primary" onClick={async () => { try { const { ["ai.api_key"]: _k, ...rest } = data; await api("/api/settings", { method: "PUT", body: rest }); toast("ذخیره شد"); } catch (e: any) { toast(e.message, "error"); } }}>ذخیره تنظیمات</button>
    </div>
  );
}

const LINE_COLORS = ["#f472b6", "#a78bfa", "#34d399", "#fbbf24", "#fb7185", "#60a5fa", "#f97316", "#14b8a6"];

/** Has this service ever been sold - on invoices here and/or in the previous software's history? */
function SalesHistory({ u, loading }: { u?: any; loading: boolean }) {
  if (loading) return <span className="muted text-xs">…</span>;
  if (!u) return <span className="badge whitespace-nowrap bg-slate-500/10 text-slate-500" title="نه در این سیستم و نه در نرم‌افزار قبلی فروشی ثبت نشده">بدون هیچ فاکتور</span>;
  return (
    <div className="flex flex-col items-start gap-1">
      {u.invoices > 0 && <span className="badge whitespace-nowrap bg-emerald-500/10 text-emerald-600 dark:text-emerald-300"
        title={`جمع ${money(u.invoice_amount)} · آخرین: ${jdatetime(u.invoice_last)}`}>{num(u.invoices)} فاکتور</span>}
      {u.old > 0 && <span className="badge whitespace-nowrap bg-sky-500/10 text-sky-600 dark:text-sky-300"
        title={`جمع ${money(u.old_amount)} · آخرین: ${jdatetime(u.old_last)}`}>{num(u.old)} در سیستم قبلی</span>}
    </div>
  );
}

const catalogLineKey = "hesabdar:catalog-line";
const toEnDigits = (s: string) => s.replace(/[۰-۹]/g, (d) => String("۰۱۲۳۴۵۶۷۸۹".indexOf(d))).replace(/[٠-٩]/g, (d) => String("٠١٢٣٤٥٦٧٨٩".indexOf(d))).replace(/ي/g, "ی").replace(/ك/g, "ک");

/** A service's price range / deposit sanity check; returns a Persian error or "". */
function serviceProblem(s: any): string {
  if (!s.name?.trim()) return "نام خدمت را وارد کنید";
  if (!s.line_id) return "لاین خدمت را انتخاب کنید";
  if (!(s.duration_minutes >= 5)) return "مدت انجام باید حداقل ۵ دقیقه باشد";
  if (s.min_price && s.max_price && s.min_price > s.max_price) return "حداقل قیمت از حداکثر بیشتر است";
  if (s.base_price && s.min_price && s.base_price < s.min_price) return "قیمت پایه کمتر از حداقل قیمت مجاز است";
  if (s.base_price && s.max_price && s.base_price > s.max_price) return "قیمت پایه بیشتر از حداکثر قیمت مجاز است";
  if (s.default_deposit && s.base_price && s.default_deposit > s.base_price) return "بیعانه از قیمت پایه بیشتر است";
  return "";
}

function Catalog() {
  const toast = useToast();
  // archived lines/services too: a sold service that was deleted is only hidden (its code and history stay)
  const lines = useApi<any[]>("/api/lines?all=1");
  const services = useApi<any[]>("/api/services?all=1");
  const staff = useApi<any[]>("/api/staff");
  const usage = useApi<any[]>("/api/services/usage");
  const health = useApi<any>("/api/services/health");
  const [edit, setEdit] = useState<any>(null);
  const [line, setLine] = useState<any>(null);
  const [merge, setMerge] = useState<{ id: number; into?: number } | null>(null);
  const [showHealth, setShowHealth] = useState(false);
  const [sold, setSold] = useState<"all" | "sold" | "unsold" | "archived">("all");
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState("");
  // the selected line (0 = all lines); remembered between visits
  const [selected, setSelectedState] = useState<number>(() => {
    try { return Number(localStorage.getItem(catalogLineKey)) || 0; } catch { return 0; }
  });
  const setSelected = (id: number) => {
    setSelectedState(id);
    try { localStorage.setItem(catalogLineKey, String(id)); } catch { /* storage unavailable */ }
  };
  const reload = () => { lines.reload(); services.reload(); usage.reload(); health.reload(); };

  const everyLine = lines.data ?? [];
  const allLines = everyLine.filter((l) => l.is_active);
  const oldLines = everyLine.filter((l) => !l.is_active);
  const everyService = services.data ?? [];
  const allServices = everyService.filter((s) => s.is_active);
  const oldServices = everyService.filter((s) => !s.is_active);
  const current = everyLine.find((l) => l.id === selected) ?? null;
  // a remembered line that was deleted meanwhile falls back to "all"
  useEffect(() => {
    if (lines.data && selected && !lines.data.some((l) => l.id === selected)) setSelected(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lines.data]);

  const countBy = (rows: any[]) => rows.reduce<Record<number, number>>((m, r) => (r.line_id ? { ...m, [r.line_id]: (m[r.line_id] ?? 0) + 1 } : m), {});
  const serviceCount = countBy(allServices);
  const archivedCount = countBy(oldServices);
  const staffCount = countBy((staff.data ?? []).filter((p) => p.is_active !== false));
  const lineStaff = (staff.data ?? []).filter((p) => current && p.line_id === current.id && p.is_active !== false);

  const usageOf: Record<number, any> = Object.fromEntries((usage.data ?? []).map((u) => [u.service_id, u]));
  const everSold = (s: any) => !!usageOf[s.id];
  const inLine = allServices.filter((s) => !current || s.line_id === current.id);
  const oldInLine = oldServices.filter((s) => !current || s.line_id === current.id);
  const soldCount = inLine.filter(everSold).length;
  const needle = toEnDigits(q.trim().toLowerCase());
  const matches = (s: any) => !needle
    || toEnDigits(`${s.code ?? ""} ${s.name} ${(s.aliases ?? []).join(" ")} ${current ? "" : s.line ?? ""}`).toLowerCase().includes(needle);
  const shown = (sold === "archived" ? oldInLine : inLine).filter((s) => (sold === "all" || sold === "archived" || (sold === "sold") === everSold(s)) && matches(s));
  // searching (e.g. a code) finds archived services too, so «not found» never hides an existing code
  const archivedHits = needle && sold !== "archived" ? oldServices.filter((s) => matches(s) && (!current || s.line_id === current.id || /^\d+$/.test(needle))) : [];
  const lineColor = (id: number) => allLines.find((l) => l.id === id)?.color ?? "#a78bfa";

  function newService() {
    const lineId = current?.id ?? allLines[0]?.id;
    if (!lineId) return toast("اول یک لاین بسازید", "error");
    setEdit({ line_id: lineId, name: "", base_price: 0, default_deposit: 0, duration_minutes: 60, aliases: "", is_active: true });
  }
  async function saveService() {
    const problem = serviceProblem(edit);
    if (problem) return toast(problem, "error");
    setBusy(true);
    try {
      const body = { ...edit, name: edit.name.trim(), code: edit.code || null,
        aliases: typeof edit.aliases === "string" ? edit.aliases.split(/[،,]/).map((s: string) => s.trim()).filter(Boolean) : edit.aliases };
      const r = await api(edit.id ? `/api/services/${edit.id}` : "/api/services", { method: edit.id ? "PUT" : "POST", body });
      if (r.restored) toast(`«${r.name}» قبلاً حذف (بایگانی) شده بود؛ همان خدمت با کد ${r.code} و سوابقش بازگردانده شد`);
      else toast(edit.id ? "ذخیره شد" : `«${body.name}» با کد ${r.code} اضافه شد`);
      if (r.old_code) toast(`کد خدمت از ${r.old_code} به ${r.code} تغییر کرد تا با کد لاین جدید هماهنگ باشد`, "info");
      if (current && current.id !== edit.line_id) setSelected(edit.line_id); // follow a service moved to another line
      setEdit(null);
      reload();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  }
  async function deleteService(s: any) {
    const u = usageOf[s.id];
    const used = u ? [u.invoices && `${num(u.invoices)} فاکتور`, u.old && `${num(u.old)} سابقه در سیستم قبلی`].filter(Boolean).join(" و ") : "";
    if (!confirm(used
      ? `«${s.name}» ${used} دارد، پس پاک نمی‌شود و «بایگانی» می‌شود:\n• از فهرست خدمات و فرم‌ها پنهان می‌شود\n• در گزارش‌ها می‌ماند و کد ${s.code} برایش محفوظ است\n• از تب «بایگانی‌شده» قابل بازگردانی است\n\nاگر این خدمت تکراری است (مثلاً غلط تایپی)، به‌جای حذف از «ادغام» استفاده کنید.\n\nبایگانی شود؟`
      : `خدمت «${s.name}» حذف شود؟`)) return;
    try {
      const r = await api(`/api/services/${s.id}`, { method: "DELETE" });
      toast(r.archived ? `«${s.name}» بایگانی شد (در تب «بایگانی‌شده» قابل بازگردانی است)` : `«${s.name}» حذف شد`);
      if (r.line_removed) toast("لاین بدون خدمت فعال ماند و حذف (بایگانی) شد؛ در «لاین‌های بایگانی‌شده» قابل بازگردانی است", "info");
      setEdit(null);
      reload();
    } catch (e: any) {
      toast(`حذف انجام نشد: ${e.message}`, "error");
    }
  }
  async function saveDuration(s: any, minutes: number) {
    if (!minutes || minutes === s.duration_minutes) return;
    if (minutes < 5) return toast("مدت انجام باید حداقل ۵ دقیقه باشد", "error");
    try {
      await api(`/api/services/${s.id}`, { method: "PUT", body: { ...s, duration_minutes: minutes } });
      toast(`مدت «${s.name}» ذخیره شد`);
      services.reload();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }
  async function saveLine() {
    if (!line.name?.trim()) return;
    setBusy(true);
    try {
      const r = await api(line.id ? `/api/lines/${line.id}` : "/api/lines", { method: line.id ? "PUT" : "POST", body: { code: line.code || null, name: line.name.trim(), color: line.color, icon: line.icon ?? "sparkles", is_active: true } });
      if (r?.restored) toast(`لاین «${r.name}» قبلاً حذف شده بود و با همان کد ${r.code} بازگردانده شد${r.archived_services ? `؛ ${num(r.archived_services)} خدمت بایگانی‌شدهٔ آن در تب «بایگانی‌شده» است` : ""}`);
      else toast("ذخیره شد");
      if (!line.id && r?.id) setSelected(r.id); // jump to the new line so its services can be added right away
      setLine(null);
      reload();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  }
  async function deleteLine() {
    const count = serviceCount[line.id] ?? 0;
    if (!confirm(`لاین «${line.name}»${count ? ` و ${count} خدمت آن` : ""} حذف شود؟`)) return;
    try {
      await api(`/api/lines/${line.id}`, { method: "DELETE" });
      toast("لاین حذف شد");
      if (selected === line.id) setSelected(0);
      setLine(null);
      reload();
    } catch (e: any) {
      toast(`حذف انجام نشد: ${e.message}`, "error");
    }
  }

  async function restoreService(s: any) {
    try {
      await api(`/api/services/${s.id}/restore`, { method: "POST" });
      toast(`«${s.name}» بازگردانده شد`);
      setEdit(null);
      reload();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }
  async function restoreLine(l: any) {
    try {
      await api(`/api/lines/${l.id}/restore`, { method: "POST" });
      toast(`لاین «${l.name}» بازگردانده شد${archivedCount[l.id] ? `؛ خدمات بایگانی‌شدهٔ آن را از تب «بایگانی‌شده» بازگردانید` : ""}`);
      reload();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }
  const openEdit = (s: any) => setEdit({ ...s, aliases: (s.aliases ?? []).join("، ") });

  const lineButton = (id: number, label: ReactNode, color: string, count: number, extra?: ReactNode) => {
    const active = selected === id;
    return (
      <button key={id} onClick={() => { setSelected(id); setQ(""); }}
        className={`flex items-center gap-2 rounded-xl border px-3 py-2 text-right text-sm transition ${active ? "font-bold shadow-sm" : "hover:bg-violet-500/5"}`}
        style={{ borderColor: active ? color : "var(--border)", background: active ? color + "1f" : undefined }}>
        <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: color }} />
        <span className="whitespace-nowrap">{label}</span>
        {extra}
        <span className="num muted rounded-md px-1.5 text-xs" style={{ background: "var(--surface)" }} title="تعداد خدمات">{num(count)}</span>
      </button>
    );
  };

  if (!lines.data || !services.data) return <Loading />;

  return (
    <div className="space-y-4">
      <Card title="لاین‌ها" actions={<button className="btn btn-sm" onClick={() => setLine({ name: "", color: LINE_COLORS[allLines.length % LINE_COLORS.length] })}><Plus size={14} />لاین جدید</button>}>
        <div className="flex flex-wrap gap-2">
          {lineButton(0, "همه لاین‌ها", "#8b5cf6", allServices.length)}
          {allLines.map((l) => lineButton(l.id, <>{l.code && <span className="num muted ml-1 text-xs">{l.code}</span>}{l.name}</>, l.color, serviceCount[l.id] ?? 0,
            staffCount[l.id] ? <span className="muted flex items-center gap-0.5 text-xs" title="پرسنل این لاین"><Users size={12} />{num(staffCount[l.id])}</span> : undefined))}
        </div>
        {!allLines.length && <Empty text="هنوز لاینی تعریف نشده؛ با «لاین جدید» شروع کنید" />}
        {oldLines.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-2 border-t pt-3 opacity-75" style={{ borderColor: "var(--border)" }}>
            <span className="muted flex items-center gap-1 text-xs"><Archive size={13} />لاین‌های بایگانی‌شده:</span>
            {oldLines.map((l) => lineButton(l.id, <>{l.code && <span className="num muted ml-1 text-xs">{l.code}</span>}{l.name}</>, "#94a3b8", (serviceCount[l.id] ?? 0) + (archivedCount[l.id] ?? 0)))}
          </div>
        )}
        <p className="muted mt-3 text-xs leading-6">برای دیدن خدمات هر لاین روی آن کلیک کنید. کد هر خدمت = کد لاین + شماره (مثلاً لاین ۳ ← خدمت‌های ۳۰۱، ۳۰۲)؛ کدها خودکار داده می‌شوند و با تغییر نام عوض نمی‌شوند. خدمت یا لاینی که سابقهٔ فروش دارد با «حذف» پاک نمی‌شود، بلکه بایگانی می‌شود (در گزارش‌ها می‌ماند و از «بایگانی‌شده» قابل بازگردانی است).</p>
      </Card>

      <Card pad={false}
        title={current
          ? <span className="flex items-center gap-2"><span className="h-3 w-3 rounded-full" style={{ background: current.is_active ? current.color : "#94a3b8" }} />{current.code && <span className="num muted text-sm">{current.code}</span>}خدمات {current.name}
            {!current.is_active && <span className="badge bg-slate-500/15 text-xs text-slate-500">لاین بایگانی‌شده</span>}</span>
          : "همه خدمات"}
        actions={
          <div className="flex shrink-0 flex-wrap gap-2">
            <button className="btn btn-sm" onClick={() => setShowHealth(true)} title="پیدا کردن خدمات تکراری، بایگانی‌شده، کدهای ناهماهنگ و ...">
              <Stethoscope size={14} /><span className="hidden sm:inline">بررسی خدمات</span>
              {health.data?.count > 0 && <span className="num rounded-md bg-amber-500 px-1.5 text-xs text-white">{num(health.data.count)}</span>}
            </button>
            {current && !current.is_active && <button className="btn btn-sm" onClick={() => restoreLine(current)}><RotateCcw size={14} />بازگردانی لاین</button>}
            {current && current.is_active && <button className="btn btn-sm" onClick={() => setLine({ ...current })} title="ویرایش یا حذف لاین"><Pencil size={14} /><span className="hidden sm:inline">ویرایش لاین</span></button>}
            <button className="btn btn-sm btn-primary" disabled={!allLines.length} onClick={newService}><Plus size={14} />خدمت جدید{current && <span className="hidden sm:inline"> در این لاین</span>}</button>
          </div>
        }>
        <div className="flex flex-wrap items-center gap-2 px-5 pt-3">
          <input className="input max-w-xs py-1.5 text-sm" placeholder={current ? "جستجو در خدمات این لاین (نام، کد، نام دیگر)…" : "جستجوی خدمت (نام، کد، لاین، نام دیگر)…"} value={q} onChange={(e) => setQ(e.target.value)} />
          <Tabs value={sold} onChange={setSold} items={[
            { value: "all", label: <>همه <span className="num muted text-xs">{num(inLine.length)}</span></> },
            { value: "sold", label: <>دارای فاکتور <span className="num muted text-xs">{num(soldCount)}</span></> },
            { value: "unsold", label: <>بدون هیچ فاکتور <span className="num muted text-xs">{num(inLine.length - soldCount)}</span></> },
            ...(oldInLine.length || sold === "archived" ? [{ value: "archived" as const, label: <>بایگانی‌شده <span className="num muted text-xs">{num(oldInLine.length)}</span></> }] : []),
          ]} />
          {current && (
            <span className="muted flex flex-wrap items-center gap-1 text-xs">
              <Users size={13} />
              {lineStaff.length ? lineStaff.map((p) => <span key={p.id} className="badge bg-violet-500/10 text-violet-600 dark:text-violet-300">{p.full_name}</span>)
                : <>پرسنلی به این لاین وصل نیست (از «پرسنل و کاربران» تعیین کنید)</>}
            </span>
          )}
        </div>
        {archivedHits.length > 0 && (
          <button className="mx-5 mt-3 flex items-center gap-2 rounded-xl bg-amber-500/10 px-3 py-2 text-right text-xs text-amber-700 dark:text-amber-300"
            onClick={() => setSold("archived")}>
            <Archive size={14} />{num(archivedHits.length)} خدمت بایگانی‌شده هم با این جستجو پیدا شد ({archivedHits.slice(0, 3).map((s) => `${s.code} ${s.name}`).join("، ")}) — نمایش
          </button>
        )}
        {sold === "archived" && shown.length > 0 && (
          <p className="muted mx-5 mt-3 rounded-xl bg-slate-500/10 px-3 py-2 text-xs leading-6">این خدمات «حذف» شده‌اند ولی چون فاکتور یا سابقه دارند فقط پنهان شده‌اند: در گزارش‌ها هستند و کدشان آزاد نیست. اگر هنوز ارائه می‌شوند «بازگردانی»، اگر تکراری‌اند «ادغام» کنید.</p>
        )}
        {shown.length ? (
          <div className="mt-3 overflow-x-auto">
            <table className="table [&_td]:px-2.5 [&_th]:px-2.5">
              <thead><tr><th>کد</th><th>خدمت</th><th>سابقه فروش</th><th>مدت <span className="font-normal">(دقیقه)</span></th><th>قیمت پایه <span className="font-normal">({unitLabel()})</span></th><th>قیمت آموخته‌شده <span className="font-normal">({unitLabel()})</span></th><th>بیعانه <span className="font-normal">({unitLabel()})</span></th><th></th></tr></thead>
              <tbody>{shown.map((s) => (
                <tr key={s.id} className={`cursor-pointer ${s.is_active ? "" : "opacity-80"}`} onDoubleClick={() => openEdit(s)}>
                  <td className="num font-bold text-violet-600 dark:text-violet-300">{s.code}</td>
                  <td>
                    <div className="font-semibold">{s.name}{!s.is_active && <span className="badge mr-1.5 bg-slate-500/15 text-[11px] text-slate-500">بایگانی‌شده</span>}</div>
                    <div className="flex items-center gap-1.5 text-xs">
                      {!current && (
                        <button className="flex shrink-0 items-center gap-1 whitespace-nowrap font-semibold hover:underline" style={{ color: lineColor(s.line_id) }}
                          onClick={(e) => { e.stopPropagation(); setSelected(s.line_id); }} title="نمایش خدمات این لاین">
                          <span className="h-2 w-2 rounded-full" style={{ background: lineColor(s.line_id) }} />{s.line}
                        </button>
                      )}
                      {!current && s.aliases?.length > 0 && <span className="muted">·</span>}
                      {s.aliases?.length > 0 && <span className="muted max-w-[14rem] truncate" title={s.aliases.join("، ")}>{s.aliases.join("، ")}</span>}
                    </div>
                  </td>
                  <td><SalesHistory u={usageOf[s.id]} loading={!usage.data} /></td>
                  <td onDoubleClick={(e) => e.stopPropagation()}>
                    {!s.is_active ? <span className="num muted">{num(s.duration_minutes)}</span> : <input type="number" min={5} step={5} defaultValue={s.duration_minutes} key={s.duration_minutes} title="مدت انجام خدمت (دقیقه) - فاصله پیش‌فرض نوبت‌ها"
                      className="input num w-16 px-2 py-1 text-sm" onBlur={(e) => saveDuration(s, Number(e.target.value))}
                      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />}
                  </td>
                  <td className="num whitespace-nowrap">
                    {money(s.base_price, false)}
                    {(s.min_price || s.max_price) && <div className="muted text-xs" title="بازه قیمت مجاز">{s.min_price ? money(s.min_price, false) : "…"} تا {s.max_price ? money(s.max_price, false) : "…"}</div>}
                  </td>
                  <td className="num whitespace-nowrap">{s.learned_avg_price ? <>{money(s.learned_avg_price, false)} <span className="muted text-xs" title="تعداد فروش‌هایی که این میانگین از آن‌ها آموخته شده">({num(s.learned_count)})</span></> : "—"}</td>
                  <td className="num whitespace-nowrap">{s.default_deposit ? money(s.default_deposit, false) : "—"}</td>
                  <td className="whitespace-nowrap">
                    {s.is_active ? <>
                      <button className="btn btn-ghost btn-sm" onClick={() => openEdit(s)} title="ویرایش"><Pencil size={15} /></button>
                      <button className="btn btn-ghost btn-sm text-rose-500" onClick={() => deleteService(s)} title="حذف"><Trash2 size={15} /></button>
                    </> : <>
                      <button className="btn btn-ghost btn-sm text-emerald-600" onClick={() => restoreService(s)} title="بازگردانی به فهرست خدمات"><RotateCcw size={15} /></button>
                      <button className="btn btn-ghost btn-sm" onClick={() => setMerge({ id: s.id })} title="ادغام با خدمت دیگر"><GitMerge size={15} /></button>
                      <button className="btn btn-ghost btn-sm" onClick={() => openEdit(s)} title="ویرایش"><Pencil size={15} /></button>
                    </>}
                  </td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        ) : (
          <Empty icon={<Scissors size={28} />} text={q ? "خدمتی با این جستجو پیدا نشد" : sold === "archived" ? "خدمت بایگانی‌شده‌ای در این بخش نیست" : sold === "unsold" ? "همه خدمات این بخش حداقل یک فاکتور دارند" : sold === "sold" ? "هیچ خدمتی در این بخش فاکتور ندارد" : current ? "این لاین هنوز خدمتی ندارد؛ با «خدمت جدید در این لاین» اضافه کنید" : "هنوز خدمتی تعریف نشده"} />
        )}
        {shown.length > 0 && (
          <p className="muted px-5 pb-4 text-xs">{num(shown.length)} خدمت · مبالغ به {unitLabel()} · برای ویرایش روی ردیف دوبار کلیک کنید · مدت انجام را مستقیم در جدول تغییر دهید (Enter = ذخیره)</p>
        )}
      </Card>

      <Modal open={!!edit} onClose={() => setEdit(null)} title={edit?.id ? "ویرایش خدمت" : "خدمت جدید"}>
        {edit && (
          <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); saveService(); }}>
            {edit.id && !edit.is_active && (
              <div className="flex flex-wrap items-center gap-2 rounded-xl bg-slate-500/10 p-2.5 text-xs">
                <Archive size={14} />این خدمت بایگانی است (در فرم‌ها نمایش داده نمی‌شود).
                <button type="button" className="btn btn-sm mr-auto" onClick={() => restoreService(edit)}><RotateCcw size={14} />بازگردانی</button>
              </div>
            )}
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="لاین"><select className="input" value={edit.line_id} onChange={(e) => setEdit({ ...edit, line_id: Number(e.target.value) })}>{everyLine.filter((l) => l.is_active || l.id === edit.line_id).map((l) => <option key={l.id} value={l.id}>{l.code ? `${l.code} · ` : ""}{l.name}{l.is_active ? "" : " (بایگانی)"}</option>)}</select></Field>
              <Field label="کد خدمت" hint={edit.id ? "با تغییر نام عوض نمی‌شود؛ با انتقال به لاین دیگر، کد خودکار لاین جدید را می‌گیرد" : "خالی بگذارید تا خودکار داده شود"}><input className="input num" dir="ltr" value={edit.code ?? ""} placeholder="خودکار" onChange={(e) => setEdit({ ...edit, code: e.target.value })} /></Field>
              <Field label="نام خدمت"><input className="input" autoFocus value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
              <Field label="قیمت پایه"><MoneyInput value={edit.base_price} onChange={(v) => setEdit({ ...edit, base_price: v })} /></Field>
              <Field label="مدت زمان انجام (دقیقه)" hint="فاصله پیش‌فرض نوبت‌های این خدمت؛ هنگام نوبت‌دهی قابل تغییر است">
                <input type="number" min={5} step={5} className="input" value={edit.duration_minutes} onChange={(e) => setEdit({ ...edit, duration_minutes: Number(e.target.value) })} />
              </Field>
              <Field label="بیعانه پیش‌فرض"><MoneyInput value={edit.default_deposit} onChange={(v) => setEdit({ ...edit, default_deposit: v })} /></Field>
              <Field label="حداقل قیمت مجاز"><MoneyInput value={edit.min_price ?? 0} onChange={(v) => setEdit({ ...edit, min_price: v || null })} /></Field>
              <Field label="حداکثر قیمت مجاز"><MoneyInput value={edit.max_price ?? 0} onChange={(v) => setEdit({ ...edit, max_price: v || null })} /></Field>
            </div>
            <Field label="نام‌های دیگری که مشتری‌ها استفاده می‌کنند" hint="با ویرگول جدا کنید؛ سیستم از این‌ها و گفتگوها یاد می‌گیرد"><input className="input" value={edit.aliases} onChange={(e) => setEdit({ ...edit, aliases: e.target.value })} /></Field>
            {serviceProblem(edit) && edit.name && <p className="flex items-center gap-1 text-xs text-amber-600"><AlertTriangle size={13} />{serviceProblem(edit)}</p>}
            <div className="flex gap-2">
              <button type="submit" className="btn btn-primary flex-1" disabled={busy || !!serviceProblem(edit)}><Save size={15} />ذخیره</button>
              {edit.id && <button type="button" className="btn" onClick={() => { setMerge({ id: edit.id }); setEdit(null); }} title="این خدمت تکراری است: همهٔ سوابقش به خدمت دیگری منتقل شود"><GitMerge size={15} />ادغام</button>}
              {edit.id && edit.is_active && <button type="button" className="btn btn-danger" onClick={() => deleteService(edit)}><Trash2 size={15} />حذف</button>}
            </div>
          </form>
        )}
      </Modal>
      <Modal open={!!merge} onClose={() => setMerge(null)} title="ادغام خدمت تکراری">
        {merge && everyService.find((s) => s.id === merge.id) && (
          <MergeDialog src={everyService.find((s) => s.id === merge.id)} services={everyService} usage={usageOf[merge.id]} preferred={merge.into}
            onClose={() => setMerge(null)} onDone={() => { setMerge(null); reload(); }} />
        )}
      </Modal>
      <Modal open={showHealth} onClose={() => setShowHealth(false)} title="بررسی و اصلاح خدمات" wide>
        {showHealth && (
          <ServiceHealth onChanged={reload}
            onEdit={(id) => { const s = everyService.find((x) => x.id === id); if (s) { setShowHealth(false); openEdit(s); } }}
            onMerge={(id, into) => { setShowHealth(false); setMerge({ id, into }); }} />
        )}
      </Modal>
      <Modal open={!!line} onClose={() => setLine(null)} title={line?.id ? "ویرایش لاین" : "لاین جدید"}>
        {line && (
          <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); saveLine(); }}>
            <div className="grid grid-cols-3 gap-3">
              <Field label="کد لاین"><input className="input num" dir="ltr" value={line.code ?? ""} placeholder="خودکار" onChange={(e) => setLine({ ...line, code: e.target.value })} /></Field>
              <div className="col-span-2"><Field label="نام لاین"><input className="input" autoFocus value={line.name} onChange={(e) => setLine({ ...line, name: e.target.value })} /></Field></div>
            </div>
            <div>
              <div className="label">رنگ</div>
              <div className="flex flex-wrap gap-2">{LINE_COLORS.map((c) => <button key={c} type="button" onClick={() => setLine({ ...line, color: c })} className={`h-8 w-8 rounded-full ${line.color === c ? "ring-4 ring-violet-300" : ""}`} style={{ background: c }} />)}</div>
            </div>
            {line.id && <p className="muted text-xs">{num(serviceCount[line.id] ?? 0)} خدمت و {num(staffCount[line.id] ?? 0)} پرسنل در این لاین</p>}
            <div className="flex gap-2">
              <button type="submit" className="btn btn-primary flex-1" disabled={busy || !line.name?.trim()}><Save size={15} />ذخیره</button>
              {line.id && <button type="button" className="btn btn-danger" onClick={deleteLine}><Trash2 size={15} />حذف لاین و خدماتش</button>}
            </div>
          </form>
        )}
      </Modal>
    </div>
  );
}

const COSTING_HELP: Record<string, { title: string; text: string; example: string; tag?: string }> = {
  average: { title: "میانگین موزون متحرک", tag: "پیشنهادی - رایج در نرم‌افزارهای حسابداری ایران",
    text: "بعد از هر خرید، بهای هر واحد دوباره حساب می‌شود: (ارزش موجودی + مبلغ خرید) ÷ (تعداد موجود + تعداد خریداری‌شده). هر فروش با همین بها حساب می‌شود.",
    example: "۱۰ عدد به ۱۰۰ و ۱۰ عدد به ۱۳۰ خریده‌اید ← هر واحد ۱۱۵؛ فروش ۱۵ عدد = ۱٬۷۲۵" },
  fifo: { title: "اولین صادره از اولین وارده (FIFO)",
    text: "فرض می‌شود کالاهای قدیمی‌تر زودتر فروخته می‌شوند؛ بهای هر فروش از قدیمی‌ترین خرید باقی‌مانده برداشته می‌شود. برای محصولات تاریخ‌دار منطقی است.",
    example: "۱۰ عدد به ۱۰۰ و ۱۰ عدد به ۱۳۰ ← فروش ۱۵ عدد = ۱۰×۱۰۰ + ۵×۱۳۰ = ۱٬۶۵۰" },
  periodic: { title: "میانگین موزون ماهانه (دوره‌ای)",
    text: "همهٔ فروش‌های یک ماه با یک بها حساب می‌شوند: (ارزش موجودی اول ماه + خریدهای همان ماه) ÷ (تعداد اول ماه + تعداد خریدها). تا پایان ماه با هر خرید جدید، عدد آن ماه به‌روز می‌شود.",
    example: "اول ماه ۱۰ عدد به ۱۰۰، وسط ماه ۱۰ عدد به ۱۳۰ ← همهٔ فروش‌های ماه هر واحد ۱۱۵" },
};

/** Costing method of products (how the cost of what is sold, and so the profit, is worked out). */
function ProductSettings() {
  const toast = useToast();
  const { data, reload } = useApi<any>("/api/products-settings");
  const [method, setMethod] = useState("");
  const [allowNeg, setAllowNeg] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  if (!data) return <Loading />;
  const m = method || data.method;
  const neg = allowNeg ?? data.allow_negative;
  async function save() {
    if (m !== data.method && !confirm("با تغییر روش، بهای تمام‌شدهٔ همهٔ فروش‌های گذشتهٔ محصولات دوباره محاسبه و اسناد حسابداری آن‌ها به‌روز می‌شود. ادامه می‌دهید؟")) return;
    setBusy(true);
    try {
      const r = await api("/api/products-settings", { method: "PUT", body: { method: m, allow_negative: neg } });
      toast(r.recosted ? `روش محاسبه تغییر کرد؛ ${num(r.recosted)} محصول دوباره محاسبه شد` : "ذخیره شد");
      setMethod("");
      setAllowNeg(null);
      reload();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-4">
      <Card title="روش محاسبهٔ بهای تمام‌شده و سود محصولات">
        <div className="space-y-2">
          {Object.entries(COSTING_HELP).map(([k, h]) => (
            <label key={k} className={`flex cursor-pointer items-start gap-3 rounded-2xl border p-3 ${m === k ? "border-violet-500 bg-violet-500/5" : ""}`} style={m === k ? {} : { borderColor: "var(--border)" }}>
              <input type="radio" className="mt-1" checked={m === k} onChange={() => setMethod(k)} />
              <span className="text-sm">
                <b>{h.title}</b>{h.tag && <span className="badge mr-2 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300">{h.tag}</span>}
                <span className="muted mt-1 block text-xs leading-6">{h.text}</span>
                <span className="mt-1 block text-xs">مثال: {h.example}</span>
              </span>
            </label>
          ))}
          <p className="muted text-xs leading-6">هر سه روش طبق استاندارد حسابداری (IAS 2 و استاندارد شمارهٔ ۸ ایران) مجازند؛ روش «اولین صادره از آخرین وارده» (LIFO) مجاز نیست و ارائه نشده. محاسبات با ریال صحیح و بدون گم شدن حتی یک ریال انجام می‌شود.</p>
        </div>
      </Card>
      <Card title="فروش بیش از موجودی">
        <label className="flex items-start gap-3 text-sm">
          <input type="checkbox" className="mt-1" checked={neg} onChange={(e) => setAllowNeg(e.target.checked)} />
          <span><b>اجازه داده شود</b><span className="muted block text-xs leading-6">اگر خرید یا موجودی اول دوره هنوز ثبت نشده باشد، فروش ثبت می‌شود و بهای آن بعد از ثبت خرید خودکار اصلاح می‌شود. اگر خاموش باشد، فاکتوری که از موجودی بیشتر است ثبت نمی‌شود.</span></span>
        </label>
      </Card>
      <button className="btn btn-primary" disabled={busy || (m === data.method && neg === data.allow_negative)} onClick={save}><Save size={15} />ذخیره</button>
    </div>
  );
}

/** Money already in each cash box / card / POS / bank account when starting with this system, plus cash counts. */
function OpeningBalances({ refresh }: { refresh: number }) {
  const toast = useToast();
  const { data, reload } = useApi<any[]>("/api/accounts/opening", [refresh]);
  const [draft, setDraft] = useState<Record<number, { amount: number; at: string }>>({});
  const [count, setCount] = useState<{ id: number; name: string; balance: number } | null>(null);
  const [actual, setActual] = useState(0);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const rows = (data ?? []).filter((a) => a.is_active || a.opening || a.balance);
  const dirty = Object.keys(draft).map(Number);
  const val = (a: any) => draft[a.id] ?? { amount: a.opening ?? 0, at: (a.opening_at ?? toLocalIso(new Date())).slice(0, 16) };
  async function saveAll() {
    setBusy(true);
    try {
      for (const id of dirty) await api(`/api/accounts/${id}/opening`, { method: "PUT", body: { amount: draft[id].amount, at: draft[id].at } });
      toast("موجودی‌های اولیه ثبت شد");
      setDraft({});
      reload();
    } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  }
  async function doCount() {
    if (!count) return;
    try {
      const r = await api(`/api/accounts/${count.id}/adjust`, { body: { actual, note } });
      toast(r.difference === 0 ? "موجودی با دفاتر یکی است ✓" : `${r.difference > 0 ? "اضافی" : "کسری"} ${money(Math.abs(r.difference))} ثبت شد`);
      setCount(null);
      reload();
    } catch (e: any) { toast(e.message, "error"); }
  }
  const total = rows.reduce((t, a) => t + (a.balance ?? 0), 0);
  return (
    <Card title="موجودی اولیه و موجودی فعلی حساب‌ها" actions={dirty.length > 0 && <button className="btn btn-sm btn-primary" disabled={busy} onClick={saveAll}><Save size={14} />ذخیرهٔ {num(dirty.length)} مورد</button>}>
      <div className="muted mb-3 text-xs leading-6">
        <b>موجودی اولیه</b> پولی است که روز شروع کار با این سیستم در هر صندوق، کارت، کارتخوان یا حساب بانکی بوده است (مثلاً مانده حساب بانک یا پول نقد صندوق در آن روز). یک بار وارد کنید؛ اگر اشتباه شد همین‌جا اصلاح کنید (جایگزین می‌شود، دوبار حساب نمی‌شود). در حسابداری به «مانده افتتاحیه» ثبت می‌شود و فروش یا دریافتی امروز حساب نمی‌شود.
        <br /><b>شمارش موجودی</b>: هر وقت پول صندوق را شمردید یا مانده واقعی بانک را دیدید، عدد واقعی را وارد کنید؛ اختلاف به‌عنوان کسری/اضافی ثبت می‌شود.
      </div>
      {!data ? <Loading /> : rows.length === 0 ? <Empty text="ابتدا کارتخوان/کارت/صندوق تعریف کنید" /> : (
        <div className="overflow-x-auto">
          <table className="table">
            <thead><tr><th>حساب</th><th>موجودی اولیه</th><th>تاریخ موجودی اولیه</th><th>موجودی فعلی در دفاتر</th><th></th></tr></thead>
            <tbody>
              {rows.map((a) => {
                const v = val(a);
                return (
                  <tr key={a.id} className={draft[a.id] ? "bg-amber-500/5" : ""}>
                    <td><div className="font-semibold">{a.name}</div><div className="muted text-xs">{ACCOUNT_KINDS[a.kind]}{a.bank_name ? ` · ${a.bank_name}` : ""}</div></td>
                    <td className="w-48"><MoneyInput value={v.amount} onChange={(x) => setDraft((d) => ({ ...d, [a.id]: { ...v, amount: x } }))} /></td>
                    <td className="w-52"><JalaliPicker pastOnly withTime={false} value={v.at} onChange={(x) => x && setDraft((d) => ({ ...d, [a.id]: { ...v, at: x } }))} /></td>
                    <td className="num font-bold">{money(a.balance)}</td>
                    <td><button className="btn btn-sm" onClick={() => { setCount({ id: a.id, name: a.name, balance: a.balance }); setActual(a.balance); setNote(""); }}><Calculator size={14} />شمارش</button></td>
                  </tr>
                );
              })}
            </tbody>
            <tfoot><tr><td className="font-bold">جمع موجودی‌ها</td><td /><td /><td className="num font-extrabold">{money(total)}</td><td /></tr></tfoot>
          </table>
        </div>
      )}
      <Modal open={!!count} onClose={() => setCount(null)} title={`شمارش موجودی «${count?.name ?? ""}»`}>
        {count && (
          <div className="space-y-3 text-sm">
            <div className="flex justify-between rounded-xl bg-violet-500/10 px-3 py-2"><span>موجودی در دفاتر</span><b className="num">{money(count.balance)}</b></div>
            <Field label="موجودی واقعی (شمرده‌شده / مانده بانک)"><MoneyInput value={actual} onChange={setActual} /></Field>
            {actual !== count.balance && (
              <div className={`rounded-xl px-3 py-2 font-semibold ${actual < count.balance ? "bg-rose-500/10 text-rose-700" : "bg-emerald-500/10 text-emerald-700"}`}>
                {actual < count.balance ? "کسری" : "اضافی"}: {money(Math.abs(actual - count.balance))}
              </div>
            )}
            <Field label="توضیح"><input className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="مثلاً شمارش پایان روز" /></Field>
            <button className="btn btn-primary w-full" onClick={doCount}>ثبت شمارش</button>
          </div>
        )}
      </Modal>
    </Card>
  );
}

function Accounts() {
  const toast = useToast();
  const { user } = useAuth();
  const { data, reload: reloadList } = useApi<any[]>("/api/accounts");
  const [refresh, setRefresh] = useState(0);
  const reload = () => { reloadList(); setRefresh((n) => n + 1); };
  const [edit, setEdit] = useState<any>(null);
  return (
    <div className="space-y-5">
    <Card title="کارتخوان‌ها، کارت‌ها و حساب‌ها" actions={<button className="btn btn-sm btn-primary" onClick={() => setEdit({ kind: "pos", name: "", bank_name: "", provider_config: {}, is_active: true })}>افزودن</button>} pad={false}>
      <table className="table">
        <thead><tr><th>نام</th><th>نوع</th><th>بانک</th><th>کارت / ترمینال</th><th>اتصال API</th><th>وضعیت</th></tr></thead>
        <tbody>{(data ?? []).map((a) => (
          <tr key={a.id} className="cursor-pointer" onClick={() => setEdit({ ...a })} title="برای ویرایش یا حذف کلیک کنید">
            <td className="font-semibold">{a.name}</td><td>{ACCOUNT_KINDS[a.kind]}</td><td>{a.bank_name}</td><td className="num" dir="ltr">{a.card_mask || a.terminal_id || "—"}</td>
            <td>{a.provider ? <Badge status="registered">{a.provider}</Badge> : "—"}</td><td>{a.is_active ? "فعال" : "غیرفعال"}</td>
          </tr>
        ))}</tbody>
      </table>
      <Modal open={!!edit} onClose={() => setEdit(null)} title="حساب دریافت">
        {edit && (
          <div className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="نوع"><select className="input" value={edit.kind} onChange={(e) => setEdit({ ...edit, kind: e.target.value })}>{Object.entries(ACCOUNT_KINDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
              <Field label="نام"><input className="input" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
              <Field label="بانک"><input className="input" value={edit.bank_name} onChange={(e) => setEdit({ ...edit, bank_name: e.target.value })} /></Field>
              <Field label="شماره کارت" hint="فقط ۶ رقم اول و ۴ رقم آخر ذخیره می‌شود"><input className="input num" dir="ltr" value={edit.card_number ?? ""} placeholder={edit.card_mask ?? ""} onChange={(e) => setEdit({ ...edit, card_number: e.target.value })} /></Field>
              <Field label="شبا"><input className="input num" dir="ltr" value={edit.iban ?? ""} onChange={(e) => setEdit({ ...edit, iban: e.target.value })} /></Field>
              <Field label="شماره ترمینال"><input className="input num" dir="ltr" value={edit.terminal_id ?? ""} onChange={(e) => setEdit({ ...edit, terminal_id: e.target.value })} /></Field>
              <Field label="سرویس اتصال بانکی"><select className="input" value={edit.provider ?? ""} onChange={(e) => setEdit({ ...edit, provider: e.target.value || null })}><option value="">بدون اتصال</option><option value="generic_http">API بانک/Open Banking</option><option value="demo">آزمایشی</option></select></Field>
              <label className="flex items-center gap-2 pt-6 text-sm"><input type="checkbox" checked={edit.is_active} onChange={(e) => setEdit({ ...edit, is_active: e.target.checked })} />فعال</label>
              {!edit.id && <>
                <Field label="موجودی اولیه (اختیاری)" hint="پولی که الان در این حساب/صندوق هست"><MoneyInput value={edit.opening_balance ?? 0} onChange={(v) => setEdit({ ...edit, opening_balance: v })} /></Field>
                <Field label="تاریخ موجودی اولیه"><JalaliPicker pastOnly withTime={false} value={edit.opening_at ?? toLocalIso(new Date())} onChange={(v) => v && setEdit({ ...edit, opening_at: v })} /></Field>
              </>}
            </div>
            {edit.provider === "generic_http" && (
              <Field label="پیکربندی API (JSON)" hint='{"url": ".../transactions?from={since}", "token": "...", "items_path": "data"}'>
                <textarea className="input num min-h-24" dir="ltr" defaultValue={JSON.stringify(edit.provider_config, null, 1)} onBlur={(e) => { try { setEdit({ ...edit, provider_config: JSON.parse(e.target.value || "{}") }); } catch { toast("JSON نامعتبر", "error"); } }} />
              </Field>
            )}
            <div className="flex gap-2">
              <button className="btn btn-primary flex-1" onClick={async () => {
                try { await api(edit.id ? `/api/accounts/${edit.id}` : "/api/accounts", { method: edit.id ? "PUT" : "POST", body: edit }); toast("ذخیره شد"); setEdit(null); reload(); } catch (e: any) { toast(e.message, "error"); }
              }}>ذخیره</button>
              {edit.id && <button className="btn btn-danger" onClick={async () => {
                if (!confirm(`«${edit.name}» حذف شود؟`)) return;
                const r = await api(`/api/accounts/${edit.id}`, { method: "DELETE" });
                toast(r.archived ? "این حساب تراکنش داشت؛ برای سالم ماندن دفاتر بایگانی و از فهرست‌ها حذف شد" : "حذف شد");
                setEdit(null); reload();
              }}><Trash2 size={15} />حذف</button>}
            </div>
          </div>
        )}
      </Modal>
    </Card>
    <AccountRouting refresh={refresh} canEdit={can(user, "settings")} />
    {can(user, "finance") && <OpeningBalances refresh={refresh} />}
    </div>
  );
}

function StaffAndUsers() {
  const toast = useToast();
  const { user } = useAuth();
  const staff = useApi<any[]>("/api/staff");
  const users = useApi<any[]>(can(user, "users") ? "/api/auth/users" : null);
  const [s, setS] = useState({ full_name: "", commission_percent: 30, line_id: 0 });
  const lines = useApi<any[]>("/api/lines");
  const [drafts, setDrafts] = useState<Record<number, any>>({});
  const dirtyIds = Object.keys(drafts).map(Number);
  const edit = (p: any, patch: any) => setDrafts((d) => ({ ...d, [p.id]: { ...(d[p.id] ?? p), ...patch } }));
  async function save(id: number) {
    const d = drafts[id];
    try {
      await api(`/api/staff/${id}`, { method: "PUT", body: { full_name: d.full_name, mobile: d.mobile || null, line_id: d.line_id || null, commission_percent: Number(d.commission_percent) || 0, is_active: d.is_active } });
      setDrafts(({ [id]: _x, ...rest }) => rest);
      toast(`اطلاعات ${d.full_name} ذخیره شد`);
      staff.reload();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }
  const saveAll = async () => { for (const id of dirtyIds) await save(id); };
  const [u, setU] = useState({ username: "", full_name: "", password: "", role: "receptionist" });
  return (
    <div className="space-y-4">
      <Card title="پرسنل" actions={dirtyIds.length > 0 ? <button className="btn btn-primary btn-sm" onClick={saveAll}><Save size={14} />ذخیره همه ({num(dirtyIds.length)})</button> : null}>
        <div className="space-y-2">
          <div className="muted hidden grid-cols-12 gap-2 px-1 text-xs font-bold sm:grid">
            <span className="col-span-3">نام</span><span className="col-span-2">موبایل</span><span className="col-span-3">لاین</span><span className="col-span-2">سهم پرسنل</span><span className="col-span-2"></span>
          </div>
          {(staff.data ?? []).map((p) => {
            const d = drafts[p.id] ?? p;
            const dirty = !!drafts[p.id];
            return (
              <div key={p.id} className={`grid grid-cols-12 items-center gap-2 rounded-2xl p-1.5 text-sm ${p.is_active ? "" : "opacity-50"} ${dirty ? "bg-amber-500/10 ring-1 ring-amber-400" : ""}`}>
                <input className="input col-span-12 py-1.5 font-semibold sm:col-span-3" value={d.full_name} onChange={(e) => edit(p, { full_name: e.target.value })} />
                <input className="input num col-span-6 py-1.5 sm:col-span-2" dir="ltr" placeholder="موبایل" value={d.mobile ?? ""} onChange={(e) => edit(p, { mobile: e.target.value })} />
                <select className="input col-span-6 py-1.5 sm:col-span-3" value={d.line_id ?? ""} onChange={(e) => edit(p, { line_id: Number(e.target.value) || null })}>
                  <option value="">بدون لاین</option>
                  {(lines.data ?? []).map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                </select>
                <span className="col-span-4 flex items-center gap-1 sm:col-span-2" title="درصد سهم پرسنل از مبلغ خالص خدمت">
                  <input type="number" min={0} max={100} className="input num py-1.5 text-sm" value={d.commission_percent} onChange={(e) => edit(p, { commission_percent: Number(e.target.value) })} />
                  <span className="muted text-xs">٪</span>
                </span>
                <span className="col-span-8 flex justify-end gap-1 sm:col-span-2">
                  {dirty ? (
                    <>
                      <button className="btn btn-primary btn-sm" onClick={() => save(p.id)}><Save size={14} />ذخیره</button>
                      <button className="btn btn-ghost btn-sm" onClick={() => setDrafts(({ [p.id]: _x, ...rest }) => rest)} title="انصراف">✕</button>
                    </>
                  ) : (
                    <button className="btn btn-sm" onClick={async () => { await api(`/api/staff/${p.id}`, { method: "PUT", body: { ...p, is_active: !p.is_active } }); toast(p.is_active ? `${p.full_name} غیرفعال شد` : `${p.full_name} فعال شد`, "info"); staff.reload(); }}>{p.is_active ? "غیرفعال" : "فعال"}</button>
                  )}
                </span>
              </div>
            );
          })}
        </div>
        <div className="mt-4 grid grid-cols-12 gap-2 rounded-2xl border border-dashed p-2" style={{ borderColor: "var(--border)" }}>
          <input className="input col-span-12 sm:col-span-4" placeholder="نام پرسنل جدید" value={s.full_name} onChange={(e) => setS({ ...s, full_name: e.target.value })} />
          <select className="input col-span-6 sm:col-span-4" value={s.line_id} onChange={(e) => setS({ ...s, line_id: Number(e.target.value) })}>
            <option value={0}>لاین…</option>
            {(lines.data ?? []).map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
          <span className="col-span-3 flex items-center gap-1 sm:col-span-2"><input className="input num" type="number" title="درصد سهم پرسنل" value={s.commission_percent} onChange={(e) => setS({ ...s, commission_percent: Number(e.target.value) })} /><span className="muted text-xs">٪</span></span>
          <button className="btn btn-primary col-span-3 sm:col-span-2" disabled={!s.full_name} onClick={async () => {
            try { await api("/api/staff", { body: { ...s, line_id: s.line_id || null } }); toast(`${s.full_name} اضافه شد`); setS({ full_name: "", commission_percent: 30, line_id: 0 }); staff.reload(); }
            catch (e: any) { toast(e.message, "error"); }
          }}><Plus size={14} />افزودن</button>
        </div>
        <p className="muted mt-2 text-xs leading-6">هر پرسنل به یک لاین وصل می‌شود؛ با انتخاب هر خدمت، پرسنل همان لاین خودکار انتخاب می‌شود (اگر لاین چند پرسنل داشته باشد، فقط پرسنل همان لاین نمایش داده می‌شوند). درصد = سهم پرسنل از مبلغ خالص هر خدمت؛ باقیمانده سهم سالن است. گزارش در منوی «پرسنل و سهم‌ها». ردیف‌های تغییرکرده زرد می‌شوند تا ذخیره کنید.</p>
      </Card>
      {can(user, "users") && (
        <Card title="کاربران سیستم و سطح دسترسی">
          <table className="table"><tbody>{(users.data ?? []).map((x) => <tr key={x.id}><td className="font-semibold">{x.full_name || x.username}</td><td dir="ltr">{x.username}</td><td><Badge>{ROLES[x.role]}</Badge></td><td>{x.is_active ? "فعال" : "غیرفعال"}</td></tr>)}</tbody></table>
          <div className="mt-3 grid gap-2 sm:grid-cols-5">
            <input className="input" placeholder="نام" value={u.full_name} onChange={(e) => setU({ ...u, full_name: e.target.value })} />
            <input className="input" dir="ltr" placeholder="username" value={u.username} onChange={(e) => setU({ ...u, username: e.target.value })} />
            <input className="input" dir="ltr" type="password" placeholder="password" value={u.password} onChange={(e) => setU({ ...u, password: e.target.value })} />
            <select className="input" value={u.role} onChange={(e) => setU({ ...u, role: e.target.value })}>{Object.entries(ROLES).filter(([k]) => k !== "owner").map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
            <button className="btn btn-primary" onClick={async () => { try { await api("/api/auth/users", { body: u }); toast("کاربر ساخته شد"); setU({ username: "", full_name: "", password: "", role: "receptionist" }); users.reload(); } catch (e: any) { toast(e.message, "error"); } }}>افزودن</button>
          </div>
        </Card>
      )}
    </div>
  );
}

function Plugins() {
  const toast = useToast();
  const { data, reload } = useApi<any[]>("/api/plugins");
  const [cfg, setCfg] = useState<any>(null);
  if (!data) return <Loading />;
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {data.map((p) => (
        <Card key={p.name}>
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2 font-bold"><Plug size={16} className="text-violet-500" />{p.title}</div>
              <div className="muted mt-1 text-sm">{p.description}</div>
              <div className="mt-2 flex gap-2"><Badge>v{p.version}</Badge>{p.status !== "stable" && <Badge status="pending">{p.status === "beta" ? "آزمایشی" : "ایده"}</Badge>}</div>
            </div>
            <label className="relative inline-flex cursor-pointer items-center">
              <input type="checkbox" className="peer sr-only" checked={p.enabled} onChange={async (e) => { await api(`/api/plugins/${p.name}`, { method: "PUT", body: { enabled: e.target.checked } }); toast(e.target.checked ? "فعال شد" : "غیرفعال شد", "info"); reload(); }} />
              <span className="h-6 w-11 rounded-full bg-zinc-300 transition peer-checked:bg-violet-600 dark:bg-zinc-700" />
              <span className="absolute right-0.5 h-5 w-5 rounded-full bg-white shadow transition peer-checked:-translate-x-5" />
            </label>
          </div>
          {Object.keys(p.config_schema?.properties ?? {}).length > 0 && <button className="btn btn-sm mt-3" onClick={() => setCfg({ ...p, draft: { ...p.config } })}>تنظیمات</button>}
        </Card>
      ))}
      <Modal open={!!cfg} onClose={() => setCfg(null)} title={cfg?.title ?? ""}>
        {cfg && (
          <div className="space-y-3">
            {Object.entries(cfg.config_schema.properties).map(([k, s]: any) => (
              <Field key={k} label={s.title ?? k}>
                <input className="input" dir="ltr" type={s.secret ? "password" : s.type === "integer" ? "number" : "text"} value={cfg.draft[k] ?? ""}
                  onChange={(e) => setCfg({ ...cfg, draft: { ...cfg.draft, [k]: s.type === "integer" ? Number(e.target.value) : e.target.value } })} />
              </Field>
            ))}
            <button className="btn btn-primary w-full" onClick={async () => { await api(`/api/plugins/${cfg.name}`, { method: "PUT", body: { config: cfg.draft } }); toast("ذخیره شد"); setCfg(null); reload(); }}>ذخیره</button>
          </div>
        )}
      </Modal>
      <Card className="md:col-span-2" title="ساخت افزونه جدید">
        <p className="muted text-sm leading-7">هر افزونه یک پوشه با فایل <code>plugin.py</code> در <code>backend/plugins/</code> است که می‌تواند API، رویداد، کار زمان‌بندی‌شده و ابزار هوش مصنوعی اضافه کند. راهنما: <code>docs/PLUGINS.md</code></p>
      </Card>
    </div>
  );
}

function Backups() {
  const toast = useToast();
  const { user } = useAuth();
  const { data, reload } = useApi<any[]>("/api/backups");
  const [busy, setBusy] = useState(false);
  return (
    <Card title="پشتیبان‌گیری رمزگذاری‌شده" actions={<button className="btn btn-primary btn-sm" disabled={busy} onClick={async () => { setBusy(true); try { await api("/api/backups", { method: "POST" }); toast("پشتیبان ساخته شد"); reload(); } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); } }}>پشتیبان‌گیری الان</button>} pad={false}>
      <p className="muted px-5 pb-3 text-sm">پشتیبان خودکار هر چند ساعت با رمزنگاری AES و کنترل صحت (checksum) ساخته می‌شود. قبل از هر بازگردانی، یک نسخه از وضعیت فعلی ذخیره می‌شود.</p>
      {!data ? <Loading /> : data.length === 0 ? <Empty text="هنوز پشتیبانی ساخته نشده" /> : (
        <div className="overflow-x-auto">
          <table className="table">
            <thead><tr><th>زمان</th><th>نوع</th><th>حجم</th><th></th></tr></thead>
            <tbody>{data.map((b) => (
              <tr key={b.name}>
                <td className="num">{jdatetime(b.created_at)}</td><td className="muted text-xs">{b.reason}</td><td className="num">{Math.round(b.size / 1024)} KB</td>
                <td className="flex gap-1">
                  <button className="btn btn-sm" onClick={async () => { const r = await api(`/api/backups/${b.name}/verify`, { method: "POST" }); toast(r.ok ? "سالم است ✓" : "خراب است!", r.ok ? "ok" : "error"); }}>بررسی سلامت</button>
                  <button className="btn btn-sm" onClick={() => download(`/api/backups/${b.name}/download`, `${b.name}.hbk`)}>دانلود</button>
                  {user?.role === "owner" && <button className="btn btn-sm btn-danger" onClick={async () => { if (!confirm("اطلاعات به این نسخه برگردانده شود؟")) return; try { await api(`/api/backups/${b.name}/restore`, { method: "POST" }); toast("بازگردانی شد"); setTimeout(() => location.reload(), 800); } catch (e: any) { toast(e.message, "error"); } }}>بازگردانی</button>}
                </td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function Alerts() {
  const { data, reload } = useApi<any[]>("/api/alerts");
  if (!data) return <Loading />;
  return (
    <Card title="هشدارها" pad={false}>
      {data.length === 0 ? <Empty text="هشداری وجود ندارد" /> : data.map((a) => (
        <div key={a.id} className={`flex items-start justify-between gap-3 border-b px-5 py-3 text-sm ${a.is_read ? "opacity-60" : ""}`} style={{ borderColor: "var(--border)" }}>
          <div><div className="font-bold">{a.level === "danger" ? "🔴" : "🟡"} {a.title}</div><div className="muted">{a.message}</div><div className="muted num text-xs">{jdatetime(a.at)}</div></div>
          {!a.is_read && <button className="btn btn-sm" onClick={async () => { await api(`/api/alerts/${a.id}/read`, { method: "POST" }); reload(); }}>خواندم</button>}
        </div>
      ))}
    </Card>
  );
}

function Security() {
  const audit = useApi<any[]>("/api/audit?limit=100");
  const verify = useApi<any>("/api/audit/verify");
  return (
    <Card title="گزارش ممیزی (غیرقابل دستکاری)" actions={verify.data && <Badge status={verify.data.ok ? "registered" : "mismatch"}>{verify.data.ok ? `زنجیره سالم (${verify.data.checked})` : `دستکاری در رکورد ${verify.data.broken_at}`}</Badge>} pad={false}>
      <div className="max-h-[60vh] overflow-auto">
        <table className="table">
          <thead><tr><th>زمان</th><th>کاربر</th><th>عملیات</th><th>موضوع</th></tr></thead>
          <tbody>{(audit.data ?? []).map((a) => <tr key={a.id}><td className="num muted">{jdatetime(a.at)}</td><td>{a.actor}</td><td dir="ltr" className="text-left text-xs">{a.action}</td><td className="muted text-xs">{a.entity} {a.entity_id}</td></tr>)}</tbody>
        </table>
      </div>
    </Card>
  );
}

function AIIntegration() {
  const toast = useToast();
  const tools = useApi<any[]>("/api/ai/tools");
  const { data, setData } = useApi<Record<string, any>>("/api/settings");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [showGuide, setShowGuide] = useState(false);
  if (!data) return <Loading />;
  const save = async () => {
    await api("/api/settings", { method: "PUT", body: { "ai.api_key": data["ai.api_key"], "ai.model": data["ai.model"], "ai.base_url": data["ai.base_url"], "ai.assistant_instructions": data["ai.assistant_instructions"] } });
    toast("تنظیمات هوش مصنوعی ذخیره شد");
  };
  const mcp = `{
  "mcpServers": {
    "hesabdar": {
      "command": "D:\\\\HESABDAR\\\\backend\\\\.venv\\\\Scripts\\\\python.exe",
      "args": ["manage.py", "mcp", "--user", "ai"],
      "cwd": "D:\\\\HESABDAR\\\\backend"
    }
  }
}`;
  return (
    <div className="space-y-4">
      <Card title={<span className="flex items-center gap-2"><KeyRound size={18} className="text-violet-500" />اتصال به هوش مصنوعی (Claude)</span>}
        actions={<button className="btn btn-sm" onClick={() => setShowGuide(!showGuide)}><BookOpen size={14} />{showGuide ? "بستن راهنما" : "راهنمای اتصال"}</button>}>
        <div className="space-y-4">
          <Field label="کلید API (API Key)" hint="کلید به‌صورت رمزگذاری‌شده در پایگاه داده ذخیره می‌شود و دیگر نمایش داده نمی‌شود.">
            <input className="input" dir="ltr" type="password" autoComplete="off" placeholder="sk-ant-api03-..." value={data["ai.api_key"] ?? ""}
              onFocus={() => data["ai.api_key"] === "••••" && setData({ ...data, "ai.api_key": "" })}
              onChange={(e) => setData({ ...data, "ai.api_key": e.target.value })} />
          </Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="مدل">
              <select className="input" dir="ltr" value={data["ai.model"] || "claude-opus-5-5"} onChange={(e) => setData({ ...data, "ai.model": e.target.value })}>
                <option value="claude-opus-5-5">Claude Opus 5.5 (پیشنهادی - دقیق‌ترین تحلیل)</option>
                <option value="claude-sonnet-5-5">Claude Sonnet 5.5 (سریع‌تر و ارزان‌تر)</option>
                <option value="claude-haiku-4-5">Claude Haiku 4.5 (ارزان‌ترین)</option>
              </select>
            </Field>
            <Field label="آدرس سرور واسط (اختیاری)" hint="فقط اگر از درگاه/پراکسی سازمانی استفاده می‌کنید">
              <input className="input" dir="ltr" placeholder="https://api.anthropic.com" value={data["ai.base_url"] ?? ""} onChange={(e) => setData({ ...data, "ai.base_url": e.target.value })} />
            </Field>
          </div>
          <Field label="دستورالعمل اختصاصی دستیار (اختیاری)" hint="مثلاً سیاست تخفیف، لحن پاسخ یا نکات مهم سالن">
            <textarea className="input min-h-20" value={data["ai.assistant_instructions"] ?? ""} onChange={(e) => setData({ ...data, "ai.assistant_instructions": e.target.value })} />
          </Field>
          <div className="flex flex-wrap gap-2">
            <button className="btn btn-primary" onClick={save}>ذخیره</button>
            <button className="btn" disabled={busy} onClick={async () => {
              setBusy(true); setResult(null);
              try { await save(); setResult(await api("/api/ai/test", { method: "POST" })); } catch (e: any) { setResult({ ok: false, error: e.message }); } finally { setBusy(false); }
            }}>{busy ? "در حال بررسی…" : "تست اتصال"}</button>
          </div>
          {result && (
            <div className={`rounded-2xl p-3 text-sm ${result.ok ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : "bg-rose-500/10 text-rose-700 dark:text-rose-300"}`}>
              {result.ok ? `✓ اتصال برقرار است (${result.model})` : <>✗ اتصال ناموفق: <span dir="ltr" className="text-xs">{result.error}</span></>}
            </div>
          )}
        </div>
      </Card>

      {showGuide && (
        <Card title="راهنمای اتصال هوش مصنوعی">
          <ol className="list-decimal space-y-3 pr-5 text-sm leading-7">
            <li>به <b dir="ltr">console.anthropic.com</b> بروید و ثبت‌نام کنید (ایمیل + تأیید).</li>
            <li>از منوی <b>Billing</b> اعتبار (Credit) اضافه کنید؛ بدون اعتبار، کلید کار نمی‌کند.</li>
            <li>از منوی <b>API Keys</b> روی <b>Create Key</b> بزنید، یک نام بدهید (مثلاً hesabdar) و کلید را کپی کنید. کلید با <code dir="ltr">sk-ant-</code> شروع می‌شود و فقط یک بار نمایش داده می‌شود.</li>
            <li>کلید را در کادر «کلید API» بالا بچسبانید، <b>ذخیره</b> و سپس <b>تست اتصال</b> را بزنید.</li>
            <li>اگر تست ناموفق بود: اتصال اینترنت به <code dir="ltr">api.anthropic.com</code> را بررسی کنید؛ پیام «authentication» یعنی کلید اشتباه است و «credit» یعنی اعتبار حساب تمام شده.</li>
          </ol>
          <div className="mt-4 rounded-2xl p-4 text-sm leading-7" style={{ background: "var(--surface)" }}>
            <b>پس از اتصال، این قابلیت‌ها فعال می‌شوند:</b>
            <ul className="mt-1 list-disc pr-5">
              <li>دستیار هوشمند (منوی «دستیار هوش مصنوعی») با دسترسی به داده‌های واقعی سالن</li>
              <li>خواندن عکس رسیدهای واتساپ/اینستاگرام</li>
              <li>اسکن عکس و دست‌خط دفتر فروش</li>
            </ul>
          </div>
          <div className="mt-4 space-y-2 text-sm leading-7">
            <b>اتصال Claude Desktop یا Claude Code به حسابدار (MCP) - اختیاری:</b>
            <ol className="list-decimal space-y-1 pr-5">
              <li>در «پرسنل و کاربران» یک کاربر با نام کاربری <code>ai</code> و نقش «عامل هوش مصنوعی» بسازید.</li>
              <li>در Claude Desktop: Settings ← Developer ← Edit Config و متن زیر را اضافه کنید (مسیر را با محل نصب خود تطبیق دهید):</li>
            </ol>
            <pre className="overflow-x-auto rounded-2xl p-3 text-xs" dir="ltr" style={{ background: "var(--surface)" }}>{mcp}</pre>
            <p className="muted text-xs">برای اتصال هر عامل هوشمند دیگر: فهرست ابزارها از <code dir="ltr">GET /api/ai/tools</code> و اجرا با <code dir="ltr">POST /api/ai/tools/&lt;name&gt;</code> (با توکن ورود).</p>
          </div>
        </Card>
      )}

      <Card title="یادگیری سیستم">
        <div className="flex flex-wrap gap-2">
          <button className="btn btn-sm" onClick={async () => { const r = await api("/api/learning/retrain", { method: "POST" }); toast(`یادگیری مجدد: ${r.messages} پیام، ${r.invoice_items} آیتم فاکتور`); }}><Brain size={14} />یادگیری مجدد از کل سوابق</button>
          <button className="btn btn-sm" onClick={() => download("/api/ai/knowledge-export", "hesabdar-knowledge.json")}>خروجی دانش آموخته‌شده</button>
        </div>
      </Card>
      <Card title={`ابزارهای قابل استفاده برای هوش مصنوعی (${tools.data?.length ?? 0})`} pad={false}>
        <div className="overflow-x-auto"><table className="table"><tbody>{(tools.data ?? []).map((t) => <tr key={t.name}><td dir="ltr" className="text-left font-mono text-xs">{t.name}</td><td className="muted text-xs" dir="ltr">{t.description}</td><td><Badge>{t.permission}</Badge></td></tr>)}</tbody></table></div>
      </Card>
    </div>
  );
}

const TABLE_LABELS: Record<string, string> = {
  customers: "مشتریان", invoices: "فاکتورها", deposits: "بیعانه‌ها", payments: "دریافت‌ها", appointments: "نوبت‌ها", expenses: "هزینه‌ها",
  journal_entries: "اسناد حسابداری", bank_transactions: "تراکنش‌های بانک", inbound_receipts: "رسیدها", services: "خدمات",
  payment_accounts: "کارتخوان و کارت‌ها", knowledge_items: "دانش آموخته‌شده", audit_logs: "رکوردهای ممیزی",
};

function DataManagement() {
  const toast = useToast();
  const { data, reload, error } = useApi<any>("/api/admin/data");
  const [scope, setScope] = useState("transactions");
  const [password, setPassword] = useState("");
  const [confirmWord, setConfirmWord] = useState("");
  const [busy, setBusy] = useState("");
  if (error) return <Card><div className="space-y-2 text-sm"><div className="font-bold text-rose-600">اطلاعات بارگذاری نشد: {error}</div><div className="muted">اگر برنامه را به‌روزرسانی کرده‌اید، همه پنجره‌های سیاه برنامه را ببندید و دوباره اجرا کنید.</div><button className="btn btn-sm" onClick={reload}>تلاش دوباره</button></div></Card>;
  if (!data) return <Loading />;
  const run = async (key: string, fn: () => Promise<void>) => { setBusy(key); try { await fn(); } catch (e: any) { toast(e.message, "error"); } finally { setBusy(""); } };
  return (
    <div className="space-y-4">
      <Card title="وضعیت پایگاه داده">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {Object.entries(data.stats).filter(([k]) => !k.startsWith("_")).map(([k, v]) => (
            <div key={k} className="rounded-2xl p-3" style={{ background: "var(--surface)" }}><div className="muted text-xs">{TABLE_LABELS[k] ?? k}</div><div className="num font-bold">{num(v as number)}</div></div>
          ))}
        </div>
        {data.stats._size_bytes != null && <div className="muted mt-3 text-sm">حجم فایل پایگاه داده: {num(Math.round(data.stats._size_bytes / 1024))} کیلوبایت</div>}
        <div className="mt-4 flex flex-wrap gap-2">
          <button className="btn" disabled={!!busy} onClick={() => run("opt", async () => {
            const r = await api("/api/admin/optimize", { method: "POST" });
            toast(r.ok ? `بهینه‌سازی انجام شد${r.size_after != null ? ` (${num(Math.round(r.size_before / 1024))} ← ${num(Math.round(r.size_after / 1024))} کیلوبایت)` : ""}` : "بررسی سلامت خطا داد!", r.ok ? "ok" : "error");
            reload();
          })}><Wrench size={15} />{busy === "opt" ? "در حال بهینه‌سازی…" : "بهینه‌سازی و بررسی سلامت"}</button>
          <button className="btn" disabled={!!busy} onClick={() => run("demo", async () => {
            if (!confirm("داده نمونه ۱۲۰ روزه (مشتری، فاکتور، بیعانه، هزینه) اضافه شود؟ فقط برای آزمایش.")) return;
            const r = await api("/api/admin/demo", { method: "POST" }); toast(`${num(r.invoices)} فاکتور نمونه اضافه شد`); reload();
          })}><Database size={15} />{busy === "demo" ? "در حال ساخت…" : "افزودن داده نمونه (برای تست)"}</button>
        </div>
      </Card>

      <Card title={<span className="flex items-center gap-2 text-rose-600"><AlertTriangle size={18} />پاک‌سازی اطلاعات</span>}>
        <div className="space-y-3 text-sm">
          {Object.entries(data.scopes).map(([k, label]) => (
            <label key={k} className={`flex cursor-pointer items-start gap-3 rounded-2xl border p-3 ${scope === k ? "border-rose-400 bg-rose-500/5" : ""}`} style={scope === k ? {} : { borderColor: "var(--border)" }}>
              <input type="radio" className="mt-1" checked={scope === k} onChange={() => setScope(k)} />
              <span>{label as string}</span>
            </label>
          ))}
          <p className="muted text-xs">قبل از پاک‌سازی یک پشتیبان رمزگذاری‌شده به‌صورت خودکار گرفته می‌شود و در صورت اشتباه از «پشتیبان‌گیری» قابل بازگردانی است. حساب‌های کاربری حذف نمی‌شوند.</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="رمز عبور شما"><input className="input" type="password" dir="ltr" value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
            <Field label="برای تأیید کلمه «حذف» را بنویسید"><input className="input" value={confirmWord} onChange={(e) => setConfirmWord(e.target.value)} /></Field>
          </div>
          <button className="btn btn-danger" disabled={!!busy || !password || confirmWord.trim() !== "حذف"} onClick={() => run("reset", async () => {
            const r = await api("/api/admin/reset", { body: { scope, password, confirm: confirmWord } });
            toast(`پاک‌سازی انجام شد. پشتیبان ایمنی: ${r.safety_backup ?? "—"}`);
            setPassword(""); setConfirmWord(""); reload();
          })}><Trash2 size={15} />{busy === "reset" ? "در حال پاک‌سازی…" : "پاک‌سازی"}</button>
        </div>
      </Card>
    </div>
  );
}

const TABS: { key: string; label: string; icon: ReactNode; perm?: string; el: () => ReactNode }[] = [
  { key: "general", label: "عمومی", icon: <Cog size={16} />, perm: "settings", el: () => <General /> },
  { key: "catalog", label: "لاین‌ها و خدمات", icon: <Scissors size={16} />, el: () => <Catalog /> },
  { key: "accounts", label: "کارتخوان و کارت‌ها", icon: <CreditCard size={16} />, el: () => <Accounts /> },
  { key: "products", label: "محصولات", icon: <Package size={16} />, perm: "settings", el: () => <ProductSettings /> },
  { key: "people", label: "پرسنل و کاربران", icon: <Users size={16} />, el: () => <StaffAndUsers /> },
  { key: "plugins", label: "افزونه‌ها", icon: <Plug size={16} />, el: () => <Plugins /> },
  { key: "ai", label: "هوش مصنوعی", icon: <Bot size={16} />, perm: "settings", el: () => <AIIntegration /> },
  { key: "backup", label: "پشتیبان‌گیری", icon: <DatabaseBackup size={16} />, perm: "backup", el: () => <Backups /> },
  { key: "alerts", label: "هشدارها", icon: <Bell size={16} />, el: () => <Alerts /> },
  { key: "security", label: "امنیت و ممیزی", icon: <ShieldCheck size={16} />, perm: "settings", el: () => <Security /> },
  { key: "import", label: "انتقال از نرم‌افزار قبلی", icon: <FileSpreadsheet size={16} />, perm: "settings", el: () => <LegacyImport /> },
  { key: "data", label: "مدیریت داده‌ها", icon: <Database size={16} />, perm: "users", el: () => <DataManagement /> },
];

export default function SettingsPage() {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const tabs = TABS.filter((t) => !t.perm || can(user, t.perm));
  const [tab, setTab] = useState(params.get("tab") ?? tabs[0].key);
  useEffect(() => { const t = params.get("tab"); if (t) setTab(t); }, [params]);
  const cur = tabs.find((t) => t.key === tab) ?? tabs[0];
  return (
    <div>
      <PageHeader title="تنظیمات و افزونه‌ها" icon={<UserCog size={22} />} />
      <div className="grid gap-5 lg:grid-cols-[220px_1fr]">
        <nav className="flex gap-1 overflow-x-auto lg:flex-col">
          {tabs.map((t) => (
            <button key={t.key} onClick={() => { setTab(t.key); setParams({ tab: t.key }); }}
              className={`flex shrink-0 items-center gap-2 rounded-2xl px-3 py-2.5 text-sm font-semibold transition ${cur.key === t.key ? "bg-violet-500/15 text-violet-700 dark:text-violet-300" : "hover:bg-violet-500/10"}`}>
              {t.icon}{t.label}
            </button>
          ))}
        </nav>
        <div className="min-w-0">{cur.el()}</div>
      </div>
    </div>
  );
}
