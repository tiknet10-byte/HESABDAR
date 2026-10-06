import { AlertTriangle, GitMerge, Info, Pencil, RotateCcw, Wrench } from "lucide-react";
import { type ReactNode, useMemo, useState } from "react";
import { Empty, Loading } from "./ui";
import { api } from "../lib/api";
import { jdate, money, num } from "../lib/format";
import { useApi, useToast } from "../lib/hooks";

const norm = (s: string) => (s ?? "").replace(/[۰-۹]/g, (d) => String("۰۱۲۳۴۵۶۷۸۹".indexOf(d))).replace(/ي|ى/g, "ی").replace(/ك/g, "ک").replace(/[\s‌]+/g, "").toLowerCase();
const label = (s: any) => `${s.code ? `${s.code} · ` : ""}${s.name}`;

/** Move every record of one service (invoices, previous-software history, deposits, appointments) to another and remove it. */
export function MergeDialog({ src, services, usage, preferred, onClose, onDone }: {
  src: any; services: any[]; usage?: any; preferred?: number; onClose: () => void; onDone: () => void;
}) {
  const toast = useToast();
  const [q, setQ] = useState("");
  const [into, setInto] = useState<number>(preferred ?? 0);
  const [busy, setBusy] = useState(false);
  const options = useMemo(() => {
    const n = norm(q);
    return services.filter((s) => s.id !== src.id && (!n || norm(`${s.code ?? ""}${s.name}${(s.aliases ?? []).join("")}`).includes(n)))
      .sort((a, b) => Number(b.line_id === src.line_id) - Number(a.line_id === src.line_id) || Number(b.is_active) - Number(a.is_active));
  }, [q, services, src]);
  const target = services.find((s) => s.id === into);

  async function merge() {
    if (!target) return;
    if (!window.confirm(`همهٔ سوابق «${src.name}» به «${target.name}» منتقل و «${src.name}» حذف شود؟\nاین کار برگشت ندارد.`)) return;
    setBusy(true);
    try {
      const r = await api(`/api/services/${src.id}/merge`, { body: { into_id: target.id } });
      const m = r.moved ?? {};
      toast(`ادغام شد: ${num(m.invoice_items ?? 0)} ردیف فاکتور، ${num(m.appointments ?? 0)} نوبت/سابقه و ${num(m.deposits ?? 0)} بیعانه به «${target.name}» منتقل شد`);
      onDone();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 text-sm">
      <div className="rounded-2xl bg-violet-500/10 p-3">
        <div className="font-bold">{label(src)} <span className="muted font-normal">· {src.line}</span>{!src.is_active && <span className="badge mr-2 bg-slate-500/15 text-slate-500">بایگانی‌شده</span>}</div>
        {usage ? (
          <div className="muted mt-1 text-xs">
            {usage.invoices > 0 && <>{num(usage.invoices)} فاکتور · </>}{usage.old > 0 && <>{num(usage.old)} سابقه در سیستم قبلی · </>}
            جمع {money((usage.invoice_amount ?? 0) + (usage.old_amount ?? 0))}
          </div>
        ) : <div className="muted mt-1 text-xs">بدون سابقه فروش</div>}
      </div>
      <div className="muted text-xs leading-6">
        وقتی یک خدمت دو بار ثبت شده (مثلاً با غلط تایپی در نرم‌افزار قبلی)، همهٔ فاکتورها، سوابق، بیعانه‌ها و نوبت‌های آن به خدمت انتخابی
        منتقل می‌شود، نامش به عنوان «نام دیگر» آن خدمت ذخیره می‌شود و خودش حذف می‌شود. مبالغ و حساب‌ها تغییری نمی‌کنند.
      </div>
      <input className="input py-1.5" placeholder="جستجوی خدمت مقصد (نام یا کد)…" value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
      <div className="max-h-64 overflow-y-auto rounded-2xl border" style={{ borderColor: "var(--border)" }}>
        {options.length ? options.slice(0, 200).map((s) => (
          <label key={s.id} className={`flex cursor-pointer items-center gap-2 px-3 py-2 hover:bg-violet-500/5 ${into === s.id ? "bg-violet-500/10" : ""}`}>
            <input type="radio" name="merge-into" checked={into === s.id} onChange={() => setInto(s.id)} />
            <span className="num font-bold text-violet-600 dark:text-violet-300">{s.code}</span>
            <span className="font-semibold">{s.name}</span>
            <span className="muted text-xs">{s.line}</span>
            {!s.is_active && <span className="badge bg-slate-500/15 text-xs text-slate-500">بایگانی</span>}
          </label>
        )) : <Empty text="خدمتی پیدا نشد" />}
      </div>
      <div className="flex gap-2">
        <button className="btn btn-primary flex-1" disabled={busy || !target} onClick={merge}>
          <GitMerge size={15} />{target ? `ادغام در «${target.name}»` : "خدمت مقصد را انتخاب کنید"}
        </button>
        <button className="btn" onClick={onClose}>انصراف</button>
      </div>
    </div>
  );
}

const LEVEL = {
  warning: { icon: <AlertTriangle size={16} />, cls: "text-amber-600 dark:text-amber-300", bg: "bg-amber-500/10" },
  info: { icon: <Info size={16} />, cls: "text-sky-600 dark:text-sky-300", bg: "bg-sky-500/10" },
} as const;

/** Problems in the list of services, with a fix for each one. */
export function ServiceHealth({ onChanged, onEdit, onMerge }: {
  onChanged: () => void; onEdit: (id: number) => void; onMerge: (id: number, into?: number) => void;
}) {
  const toast = useToast();
  const { data, reload } = useApi<any>("/api/services/health");
  const [primary, setPrimary] = useState<Record<number, number>>({});
  const [busy, setBusy] = useState(false);

  async function run(fn: () => Promise<string | void>) {
    setBusy(true);
    try {
      const msg = await fn();
      if (msg) toast(msg);
      reload();
      onChanged();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  }
  const restore = (s: any) => run(async () => { await api(`/api/services/${s.id}/restore`, { method: "POST" }); return `«${s.name}» بازگردانده شد`; });
  const restoreLine = (s: any) => run(async () => { await api(`/api/lines/${s.line_id}/restore`, { method: "POST" }); return `لاین «${s.line}» بازگردانده شد`; });
  const recode = (ids: number[]) => run(async () => {
    const r = await api("/api/services/recode", { body: { ids } });
    return r.changed.length ? `کد ${num(r.changed.length)} خدمت اصلاح شد (${r.changed.slice(0, 3).map((c: any) => `${c.old}←${c.new}`).join("، ")}${r.changed.length > 3 ? "…" : ""})` : "کدی برای اصلاح نبود";
  });
  const mergeGroup = (group: any[], gi: number) => {
    const into = group.find((s) => s.id === (primary[gi] ?? group[0].id)) ?? group[0];
    const rest = group.filter((s) => s.id !== into.id);
    if (!window.confirm(`${rest.map((s) => `«${s.name}»`).join("، ")} در «${into.name}» ادغام شود؟\nهمهٔ فاکتورها و سوابق منتقل می‌شوند و این کار برگشت ندارد.`)) return;
    run(async () => {
      for (const s of rest) await api(`/api/services/${s.id}/merge`, { body: { into_id: into.id } });
      return `${num(rest.length)} خدمت در «${into.name}» ادغام شد`;
    });
  };

  if (!data) return <Loading />;
  if (!data.issues.length) return <Empty text="مشکلی در فهرست خدمات پیدا نشد ✓" />;

  const row = (s: any, actions: ReactNode) => (
    <tr key={s.id}>
      <td className="num font-bold text-violet-600 dark:text-violet-300">{s.code}</td>
      <td>
        <div className="font-semibold">{s.name}{!s.is_active && <span className="badge mr-1 bg-slate-500/15 text-[11px] text-slate-500">بایگانی</span>}</div>
        <div className="muted text-xs">{s.line}{s.line_code ? ` (کد ${s.line_code})` : ""}{!s.line_active && " · لاین بایگانی‌شده"}</div>
      </td>
      <td className="num whitespace-nowrap text-xs">{s.sales ? <>{num(s.sales)} فروش · {money(s.amount, false)}{s.last && <div className="muted">آخرین: {jdate(s.last)}</div>}</> : <span className="muted">بدون فروش</span>}</td>
      <td className="whitespace-nowrap">{actions}</td>
    </tr>
  );
  const edit = (s: any) => <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => onEdit(s.id)} title="ویرایش"><Pencil size={14} />ویرایش</button>;

  return (
    <div className="space-y-4 text-sm">
      {data.issues.map((issue: any) => {
        const lv = LEVEL[issue.level as keyof typeof LEVEL] ?? LEVEL.info;
        const items: any[] = issue.items ?? [];
        return (
          <section key={issue.kind} className="rounded-2xl border p-3" style={{ borderColor: "var(--border)" }}>
            <div className={`flex items-center gap-2 font-bold ${lv.cls}`}>
              {lv.icon}{issue.title}
              <span className={`badge ${lv.bg}`}>{num(items.length || issue.groups?.length || 0)}</span>
              {issue.kind === "code_mismatch" && (
                <button className="btn btn-sm mr-auto" disabled={busy} onClick={() => recode(items.map((s) => s.id))}><Wrench size={14} />اصلاح کد همه</button>
              )}
            </div>
            <p className="muted mt-1 text-xs leading-6">{issue.help}</p>
            {issue.kind === "duplicates" ? (
              <div className="mt-2 space-y-2">
                {issue.groups.map((g: any[], gi: number) => (
                  <div key={gi} className={`rounded-xl p-2 ${lv.bg}`}>
                    {g.map((s) => (
                      <label key={s.id} className="flex cursor-pointer flex-wrap items-center gap-2 py-1" title="خدمت اصلی (بقیه در این ادغام می‌شوند)">
                        <input type="radio" name={`dup-${gi}`} checked={(primary[gi] ?? g[0].id) === s.id} onChange={() => setPrimary({ ...primary, [gi]: s.id })} />
                        <span className="num font-bold text-violet-600 dark:text-violet-300">{s.code}</span>
                        <span className="font-semibold">{s.name}</span>
                        <span className="muted text-xs">{s.line}</span>
                        {!s.is_active && <span className="badge bg-slate-500/15 text-[11px] text-slate-500">بایگانی</span>}
                        <span className="muted num text-xs">{s.sales ? `${num(s.sales)} فروش` : "بدون فروش"}</span>
                      </label>
                    ))}
                    <button className="btn btn-sm mt-1" disabled={busy} onClick={() => mergeGroup(g, gi)}>
                      <GitMerge size={14} />ادغام در «{(g.find((s) => s.id === (primary[gi] ?? g[0].id)) ?? g[0]).name}»
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <div className="mt-2 max-h-72 overflow-y-auto">
                <table className="table text-sm [&_td]:px-2 [&_th]:px-2">
                  <tbody>
                    {items.slice(0, 100).map((s) => row(s, (
                      <div className="flex flex-wrap gap-1">
                        {issue.kind === "archived_used" && <>
                          <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => restore(s)}><RotateCcw size={14} />بازگردانی</button>
                          <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => onMerge(s.id)}><GitMerge size={14} />ادغام…</button>
                        </>}
                        {issue.kind === "line_archived" && <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => restoreLine(s)}><RotateCcw size={14} />بازگردانی لاین</button>}
                        {issue.kind === "code_mismatch" && <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => recode([s.id])}><Wrench size={14} />اصلاح کد</button>}
                        {issue.kind === "import_line" && <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => onMerge(s.id)}><GitMerge size={14} />ادغام…</button>}
                        {edit(s)}
                      </div>
                    )))}
                  </tbody>
                </table>
                {items.length > 100 && <div className="muted px-2 pt-1 text-xs">و {num(items.length - 100)} مورد دیگر…</div>}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
