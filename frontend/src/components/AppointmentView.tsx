import { AlertTriangle, ArrowRight, Ban, CalendarClock, CheckCircle2, Pencil, Receipt, RotateCcw, Trash2, UserRound, XCircle } from "lucide-react";
import { type ReactNode, useState } from "react";
import { Link } from "react-router-dom";
import AppointmentForm from "./AppointmentForm";
import { announceFreed } from "./Waitlist";
import { Badge, Field, Loading } from "./ui";
import { api } from "../lib/api";
import { money } from "../lib/format";
import { can, useApi, useAuth, useToast } from "../lib/hooks";
import { faDigits, formatJ } from "../lib/jalali";

const hm = (iso: string) => faDigits(iso.slice(11, 16));
const plus = (iso: string, m: number) => {
  const d = new Date(new Date(iso.length <= 16 ? iso + ":00" : iso).getTime() + m * 60000);
  return faDigits(`${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`);
};

/** One big, clearly explained button. */
function Action({ icon, title, hint, onClick, tone = "", to }: { icon: ReactNode; title: string; hint: string; onClick?: () => void; tone?: string; to?: string }) {
  const cls = `flex w-full items-start gap-3 rounded-2xl border p-3 text-right transition hover:shadow-md ${tone}`;
  const body = (
    <>
      <span className="mt-0.5 shrink-0">{icon}</span>
      <span><span className="block font-bold">{title}</span><span className="muted block text-xs leading-5">{hint}</span></span>
    </>
  );
  return to
    ? <Link to={to} className={cls} style={{ borderColor: "var(--border)" }}>{body}</Link>
    : <button type="button" onClick={onClick} className={cls} style={{ borderColor: "var(--border)" }}>{body}</button>;
}

export type Step = "view" | "edit" | "cancelled" | "no_show" | "delete";

/** Everything about one appointment with simple actions: change, invoice, cancel, no-show, delete, bring back. */
export default function AppointmentView({ id, initialStep, onChanged, onClose }: {
  id: number; initialStep?: Step; onChanged: () => void; onClose: () => void;
}) {
  const toast = useToast();
  const { user } = useAuth();
  const { data: a, reload } = useApi<any>(`/api/appointments/${id}`);
  const accounts = (useApi<any[]>("/api/accounts").data ?? []).filter((x) => x.is_active);
  const [step, setStep] = useState<Step>(initialStep ?? "view");
  const [money_, setMoney] = useState<"keep" | "refund" | "forfeit">("keep");
  const [account, setAccount] = useState(0);
  const [busy, setBusy] = useState(false);
  if (!a) return <Loading />;

  const held = (a.deposits ?? []).filter((d: any) => d.status === "held");
  const heldSum = held.reduce((s: number, d: any) => s + d.amount, 0);
  const finance = can(user, "finance");
  const past = new Date(a.start_at.length <= 16 ? a.start_at + ":00" : a.start_at).getTime() < Date.now();
  const done = () => { onChanged(); reload(); };

  async function setStatus(status: string) {
    setBusy(true);
    try {
      const q = new URLSearchParams({ status, ...(held.length ? { deposits: money_ } : {}), ...(money_ === "refund" && account ? { refund_account_id: String(account) } : {}) });
      const r = await api(`/api/appointments/${a.id}?${q}`, { method: "PATCH" });
      const dep = r.deposits?.length ? { keep: " · بیعانه نزد سالن ماند", refund: " · بیعانه به مشتری برگردانده شد", forfeit: " · بیعانه سوخت شد" }[money_] : "";
      toast(({ cancelled: "نوبت لغو شد", no_show: "ثبت شد: مشتری نیامد", booked: "نوبت دوباره فعال شد", done: "ثبت شد" } as Record<string, string>)[status] + dep);
      announceFreed([r.freed]);
      setStep("view");
      done();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    setBusy(true);
    try {
      const r = await api(`/api/appointments/${a.id}`, { method: "DELETE" });
      toast(`نوبت حذف شد${r.deposits_kept ? " · بیعانه به‌صورت بیعانهٔ باز مشتری باقی ماند" : ""}`);
      announceFreed([r.freed]);
      onChanged();
      onClose();
    } catch (e: any) {
      toast(e.message, "error");
      setBusy(false);
    }
  }

  const back = <button className="btn btn-ghost btn-sm" onClick={() => setStep("view")}><ArrowRight size={15} />بازگشت</button>;

  const depositChoice = (forNoShow: boolean) => held.length > 0 && (
    <div className="space-y-2 rounded-2xl bg-emerald-500/10 p-3 text-sm">
      <div className="font-bold">این نوبت {money(heldSum)} بیعانه دارد. با این پول چه کنیم؟</div>
      <label className="flex items-start gap-2"><input type="radio" className="mt-1" checked={money_ === "keep"} onChange={() => setMoney("keep")} />
        <span><b>نزد سالن بماند</b><span className="muted block text-xs">بیعانهٔ باز مشتری می‌ماند و برای نوبت بعدی‌اش استفاده می‌شود.</span></span></label>
      {!forNoShow && (
        <label className={`flex items-start gap-2 ${finance ? "" : "opacity-50"}`}><input type="radio" className="mt-1" disabled={!finance} checked={money_ === "refund"} onChange={() => setMoney("refund")} />
          <span><b>به مشتری برگردانده شد (استرداد)</b><span className="muted block text-xs">پول از حساب سالن به مشتری پرداخت شده است.{!finance && " (فقط مدیر یا حسابدار)"}</span></span></label>
      )}
      {money_ === "refund" && (
        <Field label="از کدام حساب پرداخت شد؟">
          <select className="input" value={account || held[0]?.payment_account_id || accounts[0]?.id || 0} onChange={(e) => setAccount(Number(e.target.value))}>
            {accounts.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
          </select>
        </Field>
      )}
      <label className={`flex items-start gap-2 ${finance ? "" : "opacity-50"}`}><input type="radio" className="mt-1" disabled={!finance} checked={money_ === "forfeit"} onChange={() => setMoney("forfeit")} />
        <span><b>بیعانه سوخت شود</b><span className="muted block text-xs">طبق قانون سالن پول برگردانده نمی‌شود و درآمد سالن حساب می‌شود.{!finance && " (فقط مدیر یا حسابدار)"}</span></span></label>
    </div>
  );

  return (
    <div className="space-y-4 text-sm">
      {/* who and when */}
      <div className="rounded-2xl bg-gradient-to-l from-pink-500/10 to-violet-600/10 p-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <div className="text-lg font-extrabold">{a.customer}</div>
            {a.customer_mobile && <div className="num muted text-xs" dir="ltr">{a.customer_mobile}</div>}
          </div>
          <Badge status={a.status} />
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <CalendarClock size={18} className="text-violet-500" />
          <span className="font-bold">{formatJ(a.start_at, false)}</span>
          {a.time_unknown ? <span className="badge bg-amber-500/15 text-amber-700 dark:text-amber-300">ساعت نامشخص</span>
            : <span className="num font-bold">ساعت {hm(a.start_at)} تا {plus(a.start_at, a.duration_minutes ?? 60)}</span>}
        </div>
        <div className="muted mt-1 text-xs">{[a.line, a.service, a.staff && `پرسنل: ${a.staff}`, `${faDigits(a.duration_minutes ?? 60)} دقیقه`].filter(Boolean).join(" · ")}</div>
        {a.notes && <div className="mt-1 text-xs">یادداشت: {a.notes}</div>}
        {(a.deposits ?? []).length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
            {a.deposits.map((d: any) => <span key={d.id} className="flex items-center gap-1 rounded-lg px-2 py-0.5" style={{ background: "var(--surface)" }}>بیعانه <b className="num">{money(d.amount)}</b> <Badge status={d.status} /></span>)}
          </div>
        )}
      </div>
      {a.deposit_gone && a.status === "booked" && (
        <div className="flex items-start gap-2 rounded-2xl bg-rose-500/10 p-3 text-xs text-rose-700 dark:text-rose-300">
          <AlertTriangle size={16} className="shrink-0" />بیعانهٔ این نوبت به مشتری پس داده شده یا سوخت شده ولی نوبت هنوز فعال است. اگر مشتری نمی‌آید، «لغو نوبت» را بزنید.
        </div>
      )}

      {step === "view" && (
        <div className="grid gap-2 sm:grid-cols-2">
          {a.status === "booked" && <>
            <Action icon={<Pencil size={20} className="text-violet-500" />} title="تغییر نوبت" hint="عوض کردن روز، ساعت، خدمت، پرسنل یا مشتری" onClick={() => setStep("edit")} />
            <Action icon={<Receipt size={20} className="text-emerald-600" />} title="خدمت انجام شد · صدور فاکتور" hint="بعد از انجام کار؛ بیعانه خودکار از مبلغ کم می‌شود" to={`/invoices?new=1&appointment=${a.id}`} />
            <Action icon={<XCircle size={20} className="text-amber-600" />} title="لغو نوبت" hint="مشتری نمی‌آید یا نوبت را کنسل کرد؛ در سوابق می‌ماند" onClick={() => { setMoney("keep"); setStep("cancelled"); }} />
            {past && <Action icon={<Ban size={20} className="text-rose-600" />} title="مشتری نیامد" hint="زمان نوبت گذشته و مشتری نیامده است" onClick={() => { setMoney("keep"); setStep("no_show"); }} />}
          </>}
          {(a.status === "cancelled" || a.status === "no_show") && (
            <Action icon={<RotateCcw size={20} className="text-emerald-600" />} title="برگرداندن نوبت" hint="اگر اشتباهی لغو شده، نوبت دوباره فعال می‌شود (اگر آن زمان هنوز خالی باشد)" onClick={() => setStatus("booked")} />
          )}
          {a.status === "done" && (a.invoice_id
            ? <Action icon={<Receipt size={20} className="text-emerald-600" />} title="دیدن فاکتور" hint="این نوبت انجام شده و فاکتور دارد" to={`/invoices?open=${a.invoice_id}`} />
            : <div className="muted rounded-2xl border p-3 text-xs" style={{ borderColor: "var(--border)" }}><CheckCircle2 size={16} className="ml-1 inline text-emerald-600" />این نوبت انجام شده و جزو سوابق مشتری است.</div>)}
          {a.status !== "done" && !a.invoice_id && (
            <Action icon={<Trash2 size={20} className="text-rose-600" />} title="حذف نوبت" hint="فقط اگر اشتباهی ثبت شده؛ کاملاً پاک می‌شود" tone="hover:bg-rose-500/5" onClick={() => setStep("delete")} />
          )}
          <Action icon={<UserRound size={20} className="text-sky-600" />} title="پروندهٔ مشتری" hint="همهٔ نوبت‌ها، بیعانه‌ها و خدمات این مشتری" to={`/customers?q=${encodeURIComponent(a.customer_mobile || a.customer)}`} />
        </div>
      )}

      {step === "edit" && (
        <div className="space-y-3">
          {back}
          <AppointmentForm initial={a} onDone={() => { setStep("view"); done(); }} />
        </div>
      )}

      {(step === "cancelled" || step === "no_show") && (
        <div className="space-y-3">
          {back}
          <div className="rounded-2xl bg-amber-500/10 p-3">
            {step === "cancelled"
              ? <>نوبت <b>{a.customer}</b> در <b>{formatJ(a.start_at)}</b> لغو شود؟<div className="muted mt-1 text-xs">زمان آن آزاد می‌شود و اگر کسی در لیست انتظار باشد، به شما پیشنهاد داده می‌شود.</div></>
              : <>ثبت شود که <b>{a.customer}</b> در نوبت <b>{formatJ(a.start_at)}</b> نیامد؟</>}
          </div>
          {depositChoice(step === "no_show")}
          <button className="btn btn-primary w-full py-3 text-base" disabled={busy} onClick={() => setStatus(step)}>
            {step === "cancelled" ? "بله، نوبت لغو شود" : "بله، مشتری نیامد"}
          </button>
        </div>
      )}

      {step === "delete" && (
        <div className="space-y-3">
          {back}
          <div className="space-y-2 rounded-2xl bg-rose-500/10 p-3 text-sm">
            <div className="font-bold text-rose-700 dark:text-rose-300">این نوبت کاملاً پاک می‌شود.</div>
            <div className="text-xs leading-6">
              «حذف» فقط برای نوبتی است که <b>اشتباهی ثبت شده</b>. اگر مشتری نوبتش را کنسل کرده، به‌جای حذف «لغو نوبت» را بزنید تا در سوابق بماند.
              {held.length > 0 && <><br />بیعانهٔ {money(heldSum)} پاک نمی‌شود و به‌صورت بیعانهٔ باز مشتری باقی می‌ماند.</>}
            </div>
          </div>
          <button className="btn btn-danger w-full py-3 text-base" disabled={busy} onClick={remove}><Trash2 size={16} />بله، این نوبت حذف شود</button>
        </div>
      )}
    </div>
  );
}
