import {
  AlertTriangle, Bell, BookOpen, Bot, Brain, CreditCard, Database, DatabaseBackup, KeyRound, Pencil, Plug, Plus, Scissors, Settings as Cog, ShieldCheck,
  Trash2, UserCog, Users, Wrench,
} from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Badge, Card, Empty, Field, Loading, Modal, MoneyInput, PageHeader } from "../components/ui";
import { api, download } from "../lib/api";
import { ACCOUNT_KINDS, jdatetime, money, num, ROLES } from "../lib/format";
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

function Catalog() {
  const toast = useToast();
  const lines = useApi<any[]>("/api/lines");
  const services = useApi<any[]>("/api/services");
  const [edit, setEdit] = useState<any>(null);
  const [line, setLine] = useState<any>(null);
  const reload = () => { lines.reload(); services.reload(); };

  async function saveService() {
    try {
      const body = { ...edit, aliases: typeof edit.aliases === "string" ? edit.aliases.split(/[،,]/).map((s: string) => s.trim()).filter(Boolean) : edit.aliases };
      await api(edit.id ? `/api/services/${edit.id}` : "/api/services", { method: edit.id ? "PUT" : "POST", body });
      toast("ذخیره شد");
      setEdit(null);
      reload();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }
  async function deleteService(s: any) {
    if (!confirm(`خدمت «${s.name}» حذف شود؟`)) return;
    try {
      const r = await api(`/api/services/${s.id}`, { method: "DELETE" });
      toast(r.archived ? `«${s.name}» حذف شد (در سوابق قبلی نگه داشته می‌شود)` : `«${s.name}» حذف شد`);
      if (r.line_removed) toast("لاین بدون خدمت ماند و حذف شد", "info");
      setEdit(null);
      reload();
    } catch (e: any) {
      toast(`حذف انجام نشد: ${e.message}`, "error");
    }
  }
  async function saveDuration(s: any, minutes: number) {
    if (!minutes || minutes === s.duration_minutes) return;
    try {
      await api(`/api/services/${s.id}`, { method: "PUT", body: { ...s, duration_minutes: minutes } });
      toast(`مدت «${s.name}» ذخیره شد`);
      services.reload();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }
  async function saveLine() {
    try {
      await api(line.id ? `/api/lines/${line.id}` : "/api/lines", { method: line.id ? "PUT" : "POST", body: { name: line.name, color: line.color, icon: line.icon ?? "sparkles", is_active: true } });
      toast("ذخیره شد");
      setLine(null);
      reload();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }
  async function deleteLine() {
    const count = (services.data ?? []).filter((s) => s.line_id === line.id).length;
    if (!confirm(`لاین «${line.name}»${count ? ` و ${count} خدمت آن` : ""} حذف شود؟`)) return;
    try {
      await api(`/api/lines/${line.id}`, { method: "DELETE" });
      toast("لاین حذف شد");
      setLine(null);
      reload();
    } catch (e: any) {
      toast(`حذف انجام نشد: ${e.message}`, "error");
    }
  }

  return (
    <div className="space-y-4">
      <Card title="لاین‌های خدماتی" actions={<button className="btn btn-sm" onClick={() => setLine({ name: "", color: LINE_COLORS[(lines.data?.length ?? 0) % 8] })}><Plus size={14} />لاین جدید</button>}>
        <div className="flex flex-wrap gap-2">
          {(lines.data ?? []).map((l) => (
            <button key={l.id} onClick={() => setLine({ ...l })} className="badge cursor-pointer gap-2 py-2 text-sm transition hover:scale-105" style={{ background: l.color + "22", color: l.color }} title="ویرایش یا حذف">
              {l.name}<Pencil size={12} />
            </button>
          ))}
        </div>
        <p className="muted mt-2 text-xs">برای ویرایش یا حذف، روی لاین کلیک کنید. با حذف آخرین خدمت یک لاین، خود لاین هم حذف می‌شود.</p>
      </Card>
      <Card title="خدمات و قیمت‌ها" actions={<button className="btn btn-sm btn-primary" onClick={() => setEdit({ line_id: lines.data?.[0]?.id, name: "", base_price: 0, default_deposit: 0, duration_minutes: 60, aliases: "", is_active: true })}><Plus size={14} />خدمت جدید</button>} pad={false}>
        <div className="overflow-x-auto">
          <table className="table">
            <thead><tr><th>لاین</th><th>خدمت</th><th>مدت انجام</th><th>قیمت پایه</th><th>قیمت آموخته‌شده</th><th>بیعانه</th><th></th></tr></thead>
            <tbody>{(services.data ?? []).map((s) => (
              <tr key={s.id}>
                <td className="muted">{s.line}</td><td className="font-semibold">{s.name}</td><td>
                  <span className="flex items-center gap-1">
                    <input type="number" min={5} step={5} defaultValue={s.duration_minutes} key={s.duration_minutes} title="مدت انجام خدمت (دقیقه) - فاصله پیش‌فرض نوبت‌ها"
                      className="input num w-20 py-1 text-sm" onBlur={(e) => saveDuration(s, Number(e.target.value))}
                      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />
                    <span className="muted text-xs">دقیقه</span>
                  </span>
                </td><td className="num">{money(s.base_price)}</td>
                <td className="num">{s.learned_avg_price ? <>{money(s.learned_avg_price)} <span className="muted text-xs">({num(s.learned_count)})</span></> : "—"}</td>
                <td className="num">{s.default_deposit ? money(s.default_deposit) : "—"}</td>
                <td className="whitespace-nowrap">
                  <button className="btn btn-ghost btn-sm" onClick={() => setEdit({ ...s, aliases: s.aliases.join("، ") })} title="ویرایش"><Pencil size={15} /></button>
                  <button className="btn btn-ghost btn-sm text-rose-500" onClick={() => deleteService(s)} title="حذف"><Trash2 size={15} /></button>
                </td>
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
              <Field label="مدت زمان انجام (دقیقه)" hint="فاصله پیش‌فرض نوبت‌های این خدمت؛ هنگام نوبت‌دهی قابل تغییر است">
                <input type="number" min={5} step={5} className="input" value={edit.duration_minutes} onChange={(e) => setEdit({ ...edit, duration_minutes: Number(e.target.value) })} />
              </Field>
              <Field label="بیعانه پیش‌فرض"><MoneyInput value={edit.default_deposit} onChange={(v) => setEdit({ ...edit, default_deposit: v })} /></Field>
              <div />
              <Field label="حداقل قیمت مجاز"><MoneyInput value={edit.min_price ?? 0} onChange={(v) => setEdit({ ...edit, min_price: v || null })} /></Field>
              <Field label="حداکثر قیمت مجاز"><MoneyInput value={edit.max_price ?? 0} onChange={(v) => setEdit({ ...edit, max_price: v || null })} /></Field>
            </div>
            <Field label="نام‌های دیگری که مشتری‌ها استفاده می‌کنند" hint="با ویرگول جدا کنید؛ سیستم از این‌ها و گفتگوها یاد می‌گیرد"><input className="input" value={edit.aliases} onChange={(e) => setEdit({ ...edit, aliases: e.target.value })} /></Field>
            <div className="flex gap-2">
              <button className="btn btn-primary flex-1" onClick={saveService}>ذخیره</button>
              {edit.id && <button className="btn btn-danger" onClick={() => deleteService(edit)}><Trash2 size={15} />حذف</button>}
            </div>
          </div>
        )}
      </Modal>
      <Modal open={!!line} onClose={() => setLine(null)} title={line?.id ? "ویرایش لاین" : "لاین جدید"}>
        {line && (
          <div className="space-y-3">
            <Field label="نام لاین"><input className="input" value={line.name} onChange={(e) => setLine({ ...line, name: e.target.value })} /></Field>
            <div>
              <div className="label">رنگ</div>
              <div className="flex gap-2">{LINE_COLORS.map((c) => <button key={c} type="button" onClick={() => setLine({ ...line, color: c })} className={`h-8 w-8 rounded-full ${line.color === c ? "ring-4 ring-violet-300" : ""}`} style={{ background: c }} />)}</div>
            </div>
            <div className="flex gap-2">
              <button className="btn btn-primary flex-1" disabled={!line.name} onClick={saveLine}>ذخیره</button>
              {line.id && <button className="btn btn-danger" onClick={deleteLine}><Trash2 size={15} />حذف لاین و خدماتش</button>}
            </div>
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
  );
}

function StaffAndUsers() {
  const toast = useToast();
  const { user } = useAuth();
  const staff = useApi<any[]>("/api/staff");
  const users = useApi<any[]>(can(user, "users") ? "/api/auth/users" : null);
  const [s, setS] = useState({ full_name: "", commission_percent: 30, line_id: 0 });
  const lines = useApi<any[]>("/api/lines");
  const [u, setU] = useState({ username: "", full_name: "", password: "", role: "receptionist" });
  return (
    <div className="space-y-4">
      <Card title="پرسنل">
        <div className="space-y-2">
          {(staff.data ?? []).map((p) => (
            <div key={p.id} className={`grid grid-cols-12 items-center gap-2 text-sm ${p.is_active ? "" : "opacity-50"}`}>
              <span className="col-span-4 font-semibold">{p.full_name}</span>
              <select className="input col-span-4 py-1.5" value={p.line_id ?? ""} onChange={async (e) => { await api(`/api/staff/${p.id}`, { method: "PUT", body: { ...p, line_id: Number(e.target.value) || null } }); staff.reload(); }}>
                <option value="">بدون لاین</option>
                {(lines.data ?? []).map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
              </select>
              <span className="muted col-span-2 text-xs">پورسانت {num(p.commission_percent)}٪</span>
              <button className="btn btn-sm col-span-2" onClick={async () => { await api(`/api/staff/${p.id}`, { method: "PUT", body: { ...p, is_active: !p.is_active } }); staff.reload(); }}>{p.is_active ? "غیرفعال" : "فعال"}</button>
            </div>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <input className="input max-w-xs" placeholder="نام پرسنل" value={s.full_name} onChange={(e) => setS({ ...s, full_name: e.target.value })} />
          <select className="input w-40" value={s.line_id} onChange={(e) => setS({ ...s, line_id: Number(e.target.value) })}>
            <option value={0}>لاین…</option>
            {(lines.data ?? []).map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
          <input className="input w-24" type="number" title="درصد پورسانت" value={s.commission_percent} onChange={(e) => setS({ ...s, commission_percent: Number(e.target.value) })} />
          <button className="btn" disabled={!s.full_name} onClick={async () => { await api("/api/staff", { body: { ...s, line_id: s.line_id || null } }); setS({ full_name: "", commission_percent: 30, line_id: 0 }); staff.reload(); }}>افزودن</button>
        </div>
        <p className="muted mt-2 text-xs">لاین هر پرسنل برای محاسبه ظرفیت نوبت‌دهی استفاده می‌شود.</p>
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
  { key: "people", label: "پرسنل و کاربران", icon: <Users size={16} />, el: () => <StaffAndUsers /> },
  { key: "plugins", label: "افزونه‌ها", icon: <Plug size={16} />, el: () => <Plugins /> },
  { key: "ai", label: "هوش مصنوعی", icon: <Bot size={16} />, perm: "settings", el: () => <AIIntegration /> },
  { key: "backup", label: "پشتیبان‌گیری", icon: <DatabaseBackup size={16} />, perm: "backup", el: () => <Backups /> },
  { key: "alerts", label: "هشدارها", icon: <Bell size={16} />, el: () => <Alerts /> },
  { key: "security", label: "امنیت و ممیزی", icon: <ShieldCheck size={16} />, perm: "settings", el: () => <Security /> },
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
