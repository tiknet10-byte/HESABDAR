import { Bell, Bot, Brain, CreditCard, DatabaseBackup, Plug, Scissors, Settings as Cog, ShieldCheck, UserCog, Users } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Badge, Card, Empty, Field, Loading, Modal, MoneyInput, PageHeader } from "../components/ui";
import { api, download } from "../lib/api";
import { ACCOUNT_KINDS, jdatetime, money, ROLES } from "../lib/format";
import { can, useApi, useAuth, useToast } from "../lib/hooks";

function General() {
  const toast = useToast();
  const { data, setData } = useApi<Record<string, any>>("/api/settings");
  if (!data) return <Loading />;
  const set = (k: string, v: any) => setData({ ...data, [k]: v });
  const text = (k: string, label: string, area = false) => (
    <Field label={label}>{area ? <textarea className="input min-h-20" value={data[k] ?? ""} onChange={(e) => set(k, e.target.value)} /> : <input className="input" value={data[k] ?? ""} onChange={(e) => set(k, e.target.value)} />}</Field>
  );
  return (
    <div className="space-y-4">
      <Card title="مشخصات سالن"><div className="grid gap-3 sm:grid-cols-2">{text("salon.name", "نام سالن")}{text("salon.phone", "تلفن")}{text("salon.address", "آدرس")}</div></Card>
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
      <Card title="هوش مصنوعی و پشتیبان">
        <div className="space-y-3">
          {text("ai.assistant_instructions", "دستورالعمل اختصاصی برای دستیار (مثلاً سیاست تخفیف یا لحن)", true)}
          {text("backup.mirror_dir", "پوشه کپی دوم پشتیبان (فلش، Google Drive، Dropbox)")}
        </div>
      </Card>
      <button className="btn btn-primary" onClick={async () => { try { await api("/api/settings", { method: "PUT", body: data }); toast("ذخیره شد"); } catch (e: any) { toast(e.message, "error"); } }}>ذخیره تنظیمات</button>
    </div>
  );
}

function Catalog() {
  const toast = useToast();
  const lines = useApi<any[]>("/api/lines");
  const services = useApi<any[]>("/api/services");
  const [edit, setEdit] = useState<any>(null);
  const [lineName, setLineName] = useState("");
  async function saveService() {
    try {
      const body = { ...edit, aliases: typeof edit.aliases === "string" ? edit.aliases.split(/[،,]/).map((s: string) => s.trim()).filter(Boolean) : edit.aliases };
      await api(edit.id ? `/api/services/${edit.id}` : "/api/services", { method: edit.id ? "PUT" : "POST", body });
      toast("ذخیره شد");
      setEdit(null);
      services.reload();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }
  return (
    <div className="space-y-4">
      <Card title="لاین‌های خدماتی">
        <div className="flex flex-wrap gap-2">
          {(lines.data ?? []).map((l) => <span key={l.id} className="badge py-1.5 text-sm" style={{ background: l.color + "22", color: l.color }}>{l.name}</span>)}
        </div>
        <div className="mt-3 flex gap-2">
          <input className="input max-w-xs" placeholder="نام لاین جدید" value={lineName} onChange={(e) => setLineName(e.target.value)} />
          <button className="btn" disabled={!lineName} onClick={async () => { await api("/api/lines", { body: { name: lineName } }); setLineName(""); lines.reload(); }}>افزودن</button>
        </div>
      </Card>
      <Card title="خدمات و قیمت‌ها" actions={<button className="btn btn-sm btn-primary" onClick={() => setEdit({ line_id: lines.data?.[0]?.id, name: "", base_price: 0, default_deposit: 0, duration_minutes: 60, aliases: [], is_active: true })}>خدمت جدید</button>} pad={false}>
        <div className="overflow-x-auto">
          <table className="table">
            <thead><tr><th>لاین</th><th>خدمت</th><th>قیمت پایه</th><th>قیمت آموخته‌شده</th><th>بیعانه</th><th>نام‌های دیگر</th></tr></thead>
            <tbody>{(services.data ?? []).map((s) => (
              <tr key={s.id} className="cursor-pointer" onClick={() => setEdit({ ...s, aliases: s.aliases.join("، ") })}>
                <td className="muted">{s.line}</td><td className="font-semibold">{s.name}</td><td className="num">{money(s.base_price)}</td>
                <td className="num">{s.learned_avg_price ? <>{money(s.learned_avg_price)} <span className="muted text-xs">({s.learned_count})</span></> : "—"}</td>
                <td className="num">{s.default_deposit ? money(s.default_deposit) : "—"}</td><td className="muted max-w-52 truncate text-xs">{s.aliases.join("، ")}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </Card>
      <Modal open={!!edit} onClose={() => setEdit(null)} title={edit?.id ? "ویرایش خدمت" : "خدمت جدید"}>
        {edit && (
          <div className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="لاین"><select className="input" value={edit.line_id} onChange={(e) => setEdit({ ...edit, line_id: Number(e.target.value) })}>{(lines.data ?? []).map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select></Field>
              <Field label="نام خدمت"><input className="input" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
              <Field label="قیمت پایه"><MoneyInput value={edit.base_price} onChange={(v) => setEdit({ ...edit, base_price: v })} /></Field>
              <Field label="بیعانه پیش‌فرض"><MoneyInput value={edit.default_deposit} onChange={(v) => setEdit({ ...edit, default_deposit: v })} /></Field>
              <Field label="حداقل قیمت مجاز"><MoneyInput value={edit.min_price ?? 0} onChange={(v) => setEdit({ ...edit, min_price: v || null })} /></Field>
              <Field label="حداکثر قیمت مجاز"><MoneyInput value={edit.max_price ?? 0} onChange={(v) => setEdit({ ...edit, max_price: v || null })} /></Field>
            </div>
            <Field label="نام‌های دیگری که مشتری‌ها استفاده می‌کنند" hint="با ویرگول جدا کنید؛ سیستم از این‌ها و گفتگوها یاد می‌گیرد"><input className="input" value={edit.aliases} onChange={(e) => setEdit({ ...edit, aliases: e.target.value })} /></Field>
            <button className="btn btn-primary w-full" onClick={saveService}>ذخیره</button>
          </div>
        )}
      </Modal>
    </div>
  );
}

function Accounts() {
  const toast = useToast();
  const { data, reload } = useApi<any[]>("/api/accounts");
  const [edit, setEdit] = useState<any>(null);
  return (
    <Card title="کارتخوان‌ها، کارت‌ها و حساب‌ها" actions={<button className="btn btn-sm btn-primary" onClick={() => setEdit({ kind: "pos", name: "", bank_name: "", provider_config: {}, is_active: true })}>افزودن</button>} pad={false}>
      <table className="table">
        <thead><tr><th>نام</th><th>نوع</th><th>بانک</th><th>کارت / ترمینال</th><th>اتصال API</th><th>وضعیت</th></tr></thead>
        <tbody>{(data ?? []).map((a) => (
          <tr key={a.id} className="cursor-pointer" onClick={() => setEdit({ ...a })}>
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
            </div>
            {edit.provider === "generic_http" && (
              <Field label="پیکربندی API (JSON)" hint='{"url": ".../transactions?from={since}", "token": "...", "items_path": "data"}'>
                <textarea className="input num min-h-24" dir="ltr" defaultValue={JSON.stringify(edit.provider_config, null, 1)} onBlur={(e) => { try { setEdit({ ...edit, provider_config: JSON.parse(e.target.value || "{}") }); } catch { toast("JSON نامعتبر", "error"); } }} />
              </Field>
            )}
            <button className="btn btn-primary w-full" onClick={async () => {
              try { await api(edit.id ? `/api/accounts/${edit.id}` : "/api/accounts", { method: edit.id ? "PUT" : "POST", body: edit }); toast("ذخیره شد"); setEdit(null); reload(); } catch (e: any) { toast(e.message, "error"); }
            }}>ذخیره</button>
          </div>
        )}
      </Modal>
    </Card>
  );
}

function StaffAndUsers() {
  const toast = useToast();
  const { user } = useAuth();
  const staff = useApi<any[]>("/api/staff");
  const users = useApi<any[]>(can(user, "users") ? "/api/auth/users" : null);
  const [s, setS] = useState({ full_name: "", commission_percent: 30 });
  const [u, setU] = useState({ username: "", full_name: "", password: "", role: "receptionist" });
  return (
    <div className="space-y-4">
      <Card title="پرسنل">
        <div className="space-y-1.5">{(staff.data ?? []).map((p) => <div key={p.id} className="flex justify-between text-sm"><span className="font-semibold">{p.full_name}</span><span className="muted">پورسانت {p.commission_percent}٪</span></div>)}</div>
        <div className="mt-3 flex flex-wrap gap-2">
          <input className="input max-w-xs" placeholder="نام پرسنل" value={s.full_name} onChange={(e) => setS({ ...s, full_name: e.target.value })} />
          <input className="input w-28" type="number" value={s.commission_percent} onChange={(e) => setS({ ...s, commission_percent: Number(e.target.value) })} />
          <button className="btn" disabled={!s.full_name} onClick={async () => { await api("/api/staff", { body: s }); setS({ full_name: "", commission_percent: 30 }); staff.reload(); }}>افزودن</button>
        </div>
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
  const mcp = `{
  "mcpServers": {
    "hesabdar": {
      "command": "<مسیر پروژه>/backend/.venv/bin/python",
      "args": ["manage.py", "mcp", "--user", "ai"],
      "cwd": "<مسیر پروژه>/backend"
    }
  }
}`;
  return (
    <div className="space-y-4">
      <Card title="اتصال به Claude و سایر هوش‌های مصنوعی">
        <div className="space-y-3 text-sm leading-7">
          <p>سیستم از سه راه با هوش مصنوعی همگام می‌شود: <b>دستیار داخلی</b> (با کلید <code dir="ltr">ANTHROPIC_API_KEY</code>)، <b>سرور MCP</b> برای Claude Desktop / Claude Code، و <b>REST API ابزارها</b> برای هر عامل هوشمند دیگر.</p>
          <div className="label">پیکربندی MCP (یک کاربر با نقش «عامل هوش مصنوعی» بسازید):</div>
          <pre className="overflow-x-auto rounded-2xl p-3 text-xs" dir="ltr" style={{ background: "var(--surface)" }}>{mcp}</pre>
          <div className="flex flex-wrap gap-2">
            <button className="btn btn-sm" onClick={async () => { const r = await api("/api/learning/retrain", { method: "POST" }); toast(`یادگیری مجدد: ${r.messages} پیام، ${r.invoice_items} آیتم فاکتور`); }}><Brain size={14} />یادگیری مجدد از کل سوابق</button>
            <button className="btn btn-sm" onClick={() => download("/api/ai/knowledge-export", "hesabdar-knowledge.json")}>خروجی دانش آموخته‌شده</button>
          </div>
        </div>
      </Card>
      <Card title={`ابزارهای قابل استفاده برای هوش مصنوعی (${tools.data?.length ?? 0})`} pad={false}>
        <table className="table"><tbody>{(tools.data ?? []).map((t) => <tr key={t.name}><td dir="ltr" className="text-left font-mono text-xs">{t.name}</td><td className="muted text-xs" dir="ltr">{t.description}</td><td><Badge>{t.permission}</Badge></td></tr>)}</tbody></table>
      </Card>
    </div>
  );
}

const TABS: { key: string; label: string; icon: ReactNode; perm?: string; el: () => ReactNode }[] = [
  { key: "general", label: "عمومی", icon: <Cog size={16} />, perm: "settings", el: () => <General /> },
  { key: "catalog", label: "لاین‌ها و خدمات", icon: <Scissors size={16} />, el: () => <Catalog /> },
  { key: "accounts", label: "کارتخوان و کارت‌ها", icon: <CreditCard size={16} />, el: () => <Accounts /> },
  { key: "people", label: "پرسنل و کاربران", icon: <Users size={16} />, el: () => <StaffAndUsers /> },
  { key: "plugins", label: "افزونه‌ها", icon: <Plug size={16} />, el: () => <Plugins /> },
  { key: "ai", label: "هوش مصنوعی", icon: <Bot size={16} />, el: () => <AIIntegration /> },
  { key: "backup", label: "پشتیبان‌گیری", icon: <DatabaseBackup size={16} />, perm: "backup", el: () => <Backups /> },
  { key: "alerts", label: "هشدارها", icon: <Bell size={16} />, el: () => <Alerts /> },
  { key: "security", label: "امنیت و ممیزی", icon: <ShieldCheck size={16} />, perm: "settings", el: () => <Security /> },
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
