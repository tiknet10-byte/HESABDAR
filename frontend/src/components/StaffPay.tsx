import { Ban, Camera, Check, ClipboardCopy, FileImage, HandCoins, Printer, Send, Upload, Wallet } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, getToken } from "../lib/api";
import { jdatetime, money, num } from "../lib/format";
import { can, useApi, useAuth, useToast } from "../lib/hooks";
import { faDigits, monthName, monthOf, parseLocal, sameMonth, stepMonth, thisMonth, toJalali, toLocalIso, type YM } from "../lib/jalali";
import AccountSelect from "./AccountSelect";
import JalaliPicker from "./JalaliPicker";
import MonthNav, { MonthSelect } from "./MonthNav";
import { Empty, Field, Loading, Modal, MoneyInput } from "./ui";

export type Person = { staff_id: number; name: string; employee?: boolean; mobile?: string | null; balance?: number };

export const kindLabel = (employee: boolean | undefined, kind: string) =>
  kind === "advance" ? (employee ? "مساعده" : "برداشتی") : (employee ? "پرداخت حقوق" : "تسویه");

/** a picture behind the login (an <img> can't send the token): fetched once, shown from memory */
export function useBlob(path: string | null) {
  const [blob, setBlob] = useState<Blob | null>(null);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    const token = getToken();
    fetch(path, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then((r) => (r.ok ? r.blob() : null)).then((b) => { if (alive) setBlob(b); }).catch(() => {});
    return () => { alive = false; };
  }, [path]);
  return blob;
}

function useObjectUrl(blob: Blob | null) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!blob) { setUrl(null); return; }
    const u = URL.createObjectURL(blob);
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [blob]);
  return url;
}

/** the receipt of a payout: a small picture; a click shows it large */
function ReceiptThumb({ payoutId }: { payoutId: number }) {
  const blob = useBlob(`/api/staff/payouts/${payoutId}/image`);
  const url = useObjectUrl(blob);
  const [open, setOpen] = useState(false);
  if (!url) return <FileImage size={16} className="muted" />;
  const pdf = blob?.type === "application/pdf";
  return (
    <>
      <button className="overflow-hidden rounded-lg border" style={{ borderColor: "var(--border)" }} onClick={() => setOpen(true)} title="دیدن رسید">
        {pdf ? <span className="badge">PDF</span> : <img src={url} alt="رسید" className="h-10 w-10 object-cover" />}
      </button>
      <Modal open={open} onClose={() => setOpen(false)} title="رسید پرداخت" wide>
        {pdf ? <iframe src={url} className="h-[70vh] w-full" title="رسید" /> : <img src={url} alt="رسید" className="mx-auto max-h-[75vh]" />}
      </Modal>
    </>
  );
}

/** +98 form of an Iranian mobile for wa.me */
const waNumber = (mobile?: string | null) => {
  const d = (mobile || "").replace(/\D/g, "");
  return /^09\d{9}$/.test(d) ? `98${d.slice(1)}` : "";
};

/** Send the receipt and its words to the person on WhatsApp: with the picture through the system's share window (the
 * WhatsApp app on this computer / phone), else WhatsApp with the words ready (the picture is then pasted by hand). */
export function SendWhatsApp({ person, text, file }: { person: Person; text: string; file?: File | null }) {
  const toast = useToast();
  const canShare = !!file && typeof navigator !== "undefined" && !!navigator.canShare?.({ files: [file] });
  const copyImage = async () => {
    try {
      if (!file) return;
      const png = file.type === "image/png" ? file : await toPng(file);
      await navigator.clipboard.write([new ClipboardItem({ "image/png": png })]);
      toast("عکس رسید کپی شد؛ در گفتگوی واتساپ Ctrl+V بزنید");
    } catch { toast("کپی عکس در این مرورگر نشد؛ عکس را از پوشهٔ خودش در گفتگو بگذارید", "error"); }
  };
  return (
    <div className="space-y-2 rounded-2xl bg-emerald-500/10 p-3 text-sm">
      <div className="font-semibold">ارسال برای {person.name} در واتساپ</div>
      <pre className="whitespace-pre-wrap rounded-xl bg-white/60 p-2 text-xs leading-6 dark:bg-black/20" style={{ fontFamily: "inherit" }}>{text}</pre>
      <div className="flex flex-wrap gap-2">
        {canShare && <button className="btn btn-primary btn-sm" onClick={async () => {
          try { await navigator.share({ files: [file!], text }); } catch { /* closed */ }
        }}><Send size={14} />فرستادن رسید و متن (واتساپ)</button>}
        <a className={`btn btn-sm ${canShare ? "" : "btn-primary"}`} target="_blank" rel="noreferrer"
          href={`https://wa.me/${waNumber(person.mobile)}?text=${encodeURIComponent(text)}`}><Send size={14} />باز کردن گفتگو با متن</a>
        <button className="btn btn-sm" onClick={async () => { try { await navigator.clipboard.writeText(text); toast("متن کپی شد"); } catch { toast("کپی نشد", "error"); } }}>
          <ClipboardCopy size={14} />کپی متن</button>
        {file && file.type.startsWith("image/") && <button className="btn btn-sm" onClick={copyImage}><ClipboardCopy size={14} />کپی عکس رسید</button>}
      </div>
      {!waNumber(person.mobile) && <div className="muted text-xs">شمارهٔ موبایل این شخص ثبت نشده؛ در واتساپ خودتان او را انتخاب کنید
        {person.employee ? " (یا موبایلش را در صفحهٔ «کارمندان و حقوق» با دکمهٔ ویرایش بنویسید)." : " (یا موبایلش را در «تنظیمات ← پرسنل و کاربران» بنویسید)."}</div>}
    </div>
  );
}

async function toPng(file: File): Promise<Blob> {
  const img = await createImageBitmap(file);
  const c = document.createElement("canvas");
  c.width = img.width;
  c.height = img.height;
  c.getContext("2d")!.drawImage(img, 0, 0);
  return await new Promise((ok, no) => c.toBlob((b) => (b ? ok(b) : no(new Error("png"))), "image/png"));
}

export const payoutText = (person: Person, kind: string, ym: YM, amount: number, description: string, paidAt?: string) =>
  [`${kindLabel(person.employee, kind)} ${monthName(ym)}`, `مبلغ: ${money(amount)}`, paidAt ? `تاریخ: ${jdatetime(paidAt)}` : "", description]
    .filter(Boolean).join("\n");

/** the receipt part of the form: a picture (or the receipt's text) read to fill the rest */
function ReceiptPicker({ person, caption, kind, disabled, onRead, onBusy }:
  { person?: Person; caption: string; kind?: string; disabled?: boolean; onRead: (r: any, file: File | null, sentKind?: string) => void;
    onBusy?: (b: boolean) => void }) {
  const toast = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy0] = useState(false);
  const setBusy = (b: boolean) => { setBusy0(b); onBusy?.(b); };
  const input = useRef<HTMLInputElement>(null);
  const camera = useRef<HTMLInputElement>(null);
  const preview = useObjectUrl(file);
  // the form as it is when the answer comes (not when the read started), and only the latest read counts
  const onReadRef = useRef(onRead);
  onReadRef.current = onRead;
  const seq = useRef(0);
  const locked = busy || !!disabled;
  async function read(f: File | null, t: string) {
    if (!f && !t.trim()) return;
    const id = ++seq.current;
    setBusy(true);
    try {
      const form = new FormData();
      if (f) form.append("file", f);
      form.append("text", t);
      form.append("caption", caption);
      if (person) form.append("staff_id", String(person.staff_id));
      if (kind) form.append("kind", kind);
      const r = await api("/api/staff/payout-receipt", { form });
      if (id === seq.current) onReadRef.current(r, f, kind);
    } catch (e: any) { if (id === seq.current) toast(e.message, "error"); } finally { if (id === seq.current) setBusy(false); }
  }
  const pick = (f: File | undefined) => { if (!f || locked) return; setFile(f); read(f, text); };
  return (
    <div className="space-y-2 rounded-2xl border border-dashed p-3" style={{ borderColor: "var(--border)" }}
      onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); pick(e.dataTransfer.files?.[0]); }}>
      <div className="flex flex-wrap items-center gap-2">
        <FileImage size={18} className="text-violet-500" />
        <span className="text-sm font-semibold">رسید پرداخت</span>
        <span className="muted text-xs">(اختیاری) عکس رسید را اینجا بکشید یا انتخاب کنید؛ مبلغ، تاریخ و شمارهٔ پیگیری خودکار پر می‌شود</span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="btn btn-sm" disabled={locked} onClick={() => input.current?.click()}><Upload size={14} />انتخاب عکس یا PDF</button>
        <button type="button" className="btn btn-sm sm:hidden" disabled={locked} onClick={() => camera.current?.click()}><Camera size={14} />عکس با دوربین</button>
        <input ref={input} type="file" accept="image/*,application/pdf" className="hidden" onChange={(e) => pick(e.target.files?.[0])} />
        <input ref={camera} type="file" accept="image/*" capture="environment" className="hidden" onChange={(e) => pick(e.target.files?.[0])} />
        {busy && <span className="muted text-xs">در حال خواندن رسید…</span>}
        {preview && file?.type.startsWith("image/") && <img src={preview} alt="رسید" className="h-12 w-12 rounded-lg object-cover" />}
        {file && <span className="muted text-xs">{file.name}</span>}
      </div>
      <details className="text-xs">
        <summary className="muted cursor-pointer">یا متن رسید / پیامک بانک را بچسبانید</summary>
        <textarea className="input mt-2 h-20 text-xs" value={text} onChange={(e) => setText(e.target.value)} placeholder="متن رسید را اینجا بچسبانید" />
        <button type="button" className="btn btn-sm mt-1" disabled={locked || !text.trim()} onClick={() => read(file, text)}>خواندن متن</button>
      </details>
    </div>
  );
}

/** Give money to a staff member / an employee: an advance during the month, or the month-end settlement / salary. */
/** the month a payment is for, by default: an advance is for this month; a settlement made in the first half of a month
 * is for the month before (Shahrivar's share paid on 2 Mehr) */
const defaultMonth = (kind: string, month?: YM): YM => {
  const cur = thisMonth();
  const m = month ?? cur;
  if (kind !== "settlement" || !sameMonth(m, cur)) return m;
  const d = new Date();
  return toJalali(d.getFullYear(), d.getMonth() + 1, d.getDate())[2] <= 15 ? stepMonth(cur, -1) : cur;
};

/** the month a payment made on `iso` is for: an advance its own month; a settlement in the first half of a month the
 * month before (as the server suggests) */
const monthFor = (kind: string, iso: string): YM => {
  const m = monthOf(iso);
  const d = parseLocal(iso);
  return kind === "settlement" && toJalali(d.getFullYear(), d.getMonth() + 1, d.getDate())[2] <= 15 ? stepMonth(m, -1) : m;
};

export function PayoutForm({ person, kind: kind0 = "advance", month, amount: amount0 = 0, booksStart, onDone }:
  { person: Person; kind?: string; month?: YM; amount?: number; booksStart?: string | null; onDone: () => void }) {
  const toast = useToast();
  const { user } = useAuth();
  const [kind, setKindState] = useState(kind0);
  const kindRef = useRef(kind0);
  const setKind0 = (k: string) => { kindRef.current = k; setKindState(k); };
  // a month given to the form (a past month's statement) or picked by hand is kept; a receipt only suggests one
  const monthTouched = useRef(!!month && !sameMonth(month, thisMonth()));
  const readDate = useRef<string | null>(null); // the receipt's date, to work the month out again if the kind changes
  const doneRef = useRef(false);
  const [monthNote, setMonthNote] = useState("");
  const [reading, setReading] = useState(false);
  const [amount, setAmount] = useState(amount0);
  const [acc, setAcc] = useState(0);
  const [fromOwner, setFromOwner] = useState(false);
  const [beforeStart, setBeforeStart] = useState(false);
  const [paidAt, setPaidAt] = useState(toLocalIso(new Date()));
  // paid before the program started (in the previous software's time): not from an account here
  const mayBeBefore = !!booksStart && paidAt.slice(0, 10) < booksStart;
  const before = beforeStart && mayBeBefore;
  const [ym, setYm0] = useState<YM>(defaultMonth(kind0, month));
  const setYm = (m: YM) => { setYm0(m); monthTouched.current = true; setMonthNote(""); };
  const setKind = (k: string) => {
    setKind0(k);
    if (monthTouched.current) return;
    if (readDate.current) setYm0(monthFor(k, readDate.current)); // still the receipt's date, for the new kind
    else { setYm0(defaultMonth(k, month)); setMonthNote(""); }
  };
  const [reference, setReference] = useState("");
  const [description, setDescription] = useState("");
  const [receipt, setReceipt] = useState<any>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dup, setDup] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<any>(null);
  const balance = person.balance;
  function applyRead(r: any, f: File | null) {
    if (doneRef.current) return; // read after the payment was booked: nothing changes (nor the WhatsApp text)
    setReceipt(r);
    setFile(f);
    const s = r.suggest;
    if (s.amount) setAmount(s.amount);
    if (s.paid_at) setPaidAt(s.paid_at);
    if (s.kind_from === "words" && s.kind !== kindRef.current) setKind0(s.kind); // the words said so («برداشتی» / «تسویه»)
    if (s.month_from === "words" && s.jy && s.jm) {
      setYm0([s.jy, s.jm]); // a month written in the message wins, and stays when the kind is changed
      monthTouched.current = true;
      readDate.current = null;
      setMonthNote("ماه از روی پیام خوانده شد");
    } else if (!monthTouched.current && s.paid_at) {
      readDate.current = s.paid_at;
      setYm0(monthFor(kindRef.current, s.paid_at)); // worked out here for the kind chosen now
      setMonthNote("ماه از روی تاریخ رسید پیشنهاد شد");
    }
    if (s.reference) setReference(s.reference);
    if (s.payment_account_id) { setAcc(s.payment_account_id); setFromOwner(false); }
    if (s.description) setDescription((d) => d || s.description);
  }
  async function save(force = false) {
    if (receipt?.failed && !force && !window.confirm("این رسید «ناموفق» یا نامعلوم به نظر می‌رسد. مطمئنید پول واقعاً پرداخت شده و ثبت شود؟")) return;
    if (receipt?.needs_check && !force && !window.confirm(`مبلغ ${money(amount)} درست است؟ (مبلغ رسید را یک بار با عکس مقایسه کنید)`)) return;
    setBusy(true);
    try {
      const r = await api(`/api/staff/${person.staff_id}/payout`, {
        body: {
          amount, kind, payment_account_id: fromOwner || before ? null : acc, from_owner: fromOwner && !before, paid_at: paidAt, jy: ym[0], jm: ym[1],
          reference, description, receipt_token: receipt?.token ?? null, receipt: receipt?.receipt ?? null, force, before_start: before,
        },
      });
      setDup(null);
      doneRef.current = true;
      setDone(r);
      toast(`${kindLabel(person.employee, kind)} ${person.name} ثبت شد`);
    } catch (e: any) {
      if (e.status === 409) setDup(e.message); else toast(e.message, "error");
    } finally { setBusy(false); }
  }
  if (done) return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 rounded-2xl bg-emerald-500/15 p-3 text-sm font-semibold text-emerald-700 dark:text-emerald-300">
        <Check size={18} />{done.kind_label} {done.month} به مبلغ {money(done.payout.amount)} ثبت شد. ماندهٔ {person.name} الان: {money(done.balance)}
        {done.balance < 0 && " (بدهکار است؛ از سهم/حقوق بعدی کم می‌شود)"}
      </div>
      <SendWhatsApp person={person} file={file} text={payoutText(person, kind, ym, amount, description, paidAt)} />
      <button className="btn w-full" onClick={onDone}>بستن</button>
    </div>
  );
  const s = receipt?.suggest;
  const other = s?.staff_id && s.staff_id !== person.staff_id ? receipt.people.find((p: any) => p.staff_id === s.staff_id) : null;
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2">
        {(["advance", "settlement"] as const).map((k) => (
          <button key={k} type="button" className={`rounded-2xl border p-3 text-right text-sm ${kind === k ? "border-violet-500 bg-violet-500/10 font-bold" : ""}`}
            style={kind === k ? undefined : { borderColor: "var(--border)" }} onClick={() => setKind(k)}>
            <div>{kindLabel(person.employee, k)}</div>
            <div className="muted text-[11px] font-normal">{k === "advance" ? "در طول ماه، پیش از حساب آخر ماه" : person.employee ? "حقوق ماه (یا باقی آن)" : "باقیماندهٔ سهم ماه، آخر ماه"}</div>
          </button>
        ))}
      </div>
      {typeof balance === "number" && (
        <div className="rounded-2xl bg-violet-500/10 p-3 text-sm leading-7">
          ماندهٔ حساب <b>{person.name}</b> تا امروز: <b className={`num ${balance < 0 ? "text-rose-600" : ""}`}>{money(balance)}</b>
          {balance < 0 && " (بیشتر از کارکردش گرفته)"}
          {amount > 0 && <> · بعد از این پرداخت: <b className={`num ${balance - amount < 0 ? "text-rose-600" : ""}`}>{money(balance - amount)}</b>
            {balance - amount < 0 && <span className="text-xs"> (بدهکار می‌شود و از ماه بعد کم می‌شود)</span>}</>}
        </div>
      )}
      <ReceiptPicker person={person} caption={description} kind={kind} disabled={busy} onRead={applyRead} onBusy={setReading} />
      {receipt && (
        <div className="space-y-1 text-xs">
          {receipt.via === "ai" && <div className="text-emerald-600">رسید با دستیار هوشمند خوانده شد؛ ارقام را یک بار نگاه کنید.</div>}
          {receipt.via === "text" && <div className="text-emerald-600">متن رسید خوانده شد؛ ارقام را یک بار نگاه کنید.</div>}
          {receipt.warnings?.map((w: string) => <div key={w} className="rounded-xl bg-amber-500/10 p-2 text-amber-800 dark:text-amber-200">{w}</div>)}
          {other && <div className="rounded-xl bg-rose-500/10 p-2 text-rose-700 dark:text-rose-300">به نظر می‌رسد این رسید مال «{other.name}» است ({other.why}). اگر اشتباه انتخاب کرده‌اید، این پنجره را ببندید.</div>}
        </div>
      )}
      <Field label="مبلغ" hint="مبلغ را بنویسید و Enter بزنید"><MoneyInput value={amount} onChange={setAmount} autoFocus
        onEnter={() => { if (amount && !busy && !reading && (before || fromOwner || acc)) save(false); }} /></Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="تاریخ پرداخت"><JalaliPicker pastOnly value={paidAt} onChange={(v) => {
          setPaidAt(v);
          if (!monthTouched.current) { readDate.current = v; setYm0(monthFor(kindRef.current, v)); setMonthNote(""); } // follows the date
        }} /></Field>
        <Field label="بابت ماه" hint={monthNote || "تسویهٔ مهر که اول آبان داده می‌شود، «مهر» است"}><MonthSelect value={ym} onChange={setYm} /></Field>
      </div>
      {mayBeBefore && (
        <label className="flex items-start gap-2 rounded-2xl bg-sky-500/10 p-3 text-xs leading-6">
          <input type="checkbox" className="mt-1" checked={beforeStart} onChange={(e) => setBeforeStart(e.target.checked)} />
          <span><b>پیش از شروع کار با این برنامه پرداخت شده</b> (در زمان نرم‌افزار قبلی): از هیچ صندوق و حسابی کم نمی‌شود، چون موجودی‌ای که
            اول کار وارد کردید این پرداخت را از قبل حساب کرده؛ فقط از طلب این شخص کم می‌شود.</span>
        </label>
      )}
      {!before && <Field label="پرداخت از">
        {fromOwner ? <div className="input muted">از جیب خود مدیر (به سرمایهٔ مدیر اضافه می‌شود)</div> : <AccountSelect value={acc} onChange={setAcc} remember="payout" />}
        {can(user, "capital") && <label className="mt-1 flex items-center gap-2 text-xs"><input type="checkbox" checked={fromOwner} onChange={(e) => setFromOwner(e.target.checked)} />از جیب خودم (نه از صندوق یا حساب سالن) پرداخت کردم</label>}
      </Field>}
      <div className="grid gap-3 sm:grid-cols-[1fr_2fr]">
        <Field label="شمارهٔ پیگیری (اختیاری)"><input className="input num" value={reference} onChange={(e) => setReference(e.target.value)} /></Field>
        <Field label="توضیح (همان که در واتساپ می‌نویسید)"><input className="input" value={description} onChange={(e) => setDescription(e.target.value)} placeholder={kind === "advance" ? "مثلاً برداشتی برای خرید" : "مثلاً تسویهٔ مهر"} /></Field>
      </div>
      {dup && (
        <div className="space-y-2 rounded-2xl bg-rose-500/10 p-3 text-sm">
          <div>{dup}</div>
          <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => save(true)}>بله، این پرداخت جداگانه‌ای است؛ ثبت شود</button>
        </div>
      )}
      {reading && <div className="muted text-center text-xs">صبر کنید تا رسید خوانده شود…</div>}
      <button className="btn btn-primary w-full py-3" disabled={!amount || busy || reading || (!fromOwner && !before && !acc)} onClick={() => save(false)}>
        <HandCoins size={16} />ثبت {kindLabel(person.employee, kind)} {amount ? money(amount) : ""}
      </button>
    </div>
  );
}

/** One person's month: owed at the start + earned - given = left at the end, and what was paid for it later. */
export function Statement({ staffId, month: month0, onChanged }: { staffId: number; month?: YM; onChanged?: () => void }) {
  const toast = useToast();
  const { user } = useAuth();
  const [month, setMonth] = useState<YM>(month0 ?? thisMonth());
  const { data: st, reload } = useApi<any>(`/api/staff/${staffId}/statement?jy=${month[0]}&jm=${month[1]}`, [staffId, month[0], month[1]]);
  const [paying, setPaying] = useState<{ kind: string; amount: number } | null>(null);
  const [sending, setSending] = useState<any>(null);
  const changed = () => { reload(); onChanged?.(); };
  if (!st) return <Loading />;
  const person: Person = { staff_id: st.staff_id, name: st.name, employee: st.employee, mobile: st.mobile, balance: st.balance_now };
  const earnedRows = [
    ["services", "سهم از خدمات"], ["forfeits", "سهم از بیعانهٔ سوخت‌شده"], ["history", "سهم از کارکرد نرم‌افزار قبلی"], ["salary", "حقوق ماه"],
    ["other", "سایر"],
  ].filter(([k]) => st.earned[k]);
  async function voidPayout(p: any) {
    const reason = window.prompt(`باطل کردن ${kindLabel(st.employee, p.kind)} ${money(p.amount)}؟ دلیل را بنویسید (مثلاً «اشتباه ثبت شد»):`, "");
    if (reason === null) return;
    try { await api(`/api/staff/payouts/${p.id}/void`, { body: { reason } }); toast("باطل شد؛ پول به همان حساب برگشت"); changed(); } catch (e: any) { toast(e.message, "error"); }
  }
  const row = (p: any) => (
    <div key={p.id} className={`flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl px-3 py-2 text-sm ${p.status === "void" ? "opacity-50 line-through" : ""}`} style={{ background: "var(--surface)" }}>
      <span className={`badge ${p.kind === "advance" ? "bg-amber-500/15 text-amber-700 dark:text-amber-300" : "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"}`}>{kindLabel(st.employee, p.kind)}</span>
      <b className="num">{money(p.amount)}</b>
      <span className="muted text-xs">{jdatetime(p.paid_at)}</span>
      <span className="muted text-xs">{p.account ?? "—"}</span>
      {(p.jy !== month[0] || p.jm !== month[1]) && <span className="badge text-[10px]">بابت {monthName([p.jy, p.jm])}</span>}
      {p.reference && <span className="muted num text-xs">پیگیری {p.reference}</span>}
      {p.description && <span className="text-xs">{p.description}</span>}
      {p.status === "void" && <span className="text-xs text-rose-600 no-underline">باطل{p.void_reason ? `: ${p.void_reason}` : ""}</span>}
      <span className="mr-auto flex items-center gap-1">
        {p.has_image && <ReceiptThumb payoutId={p.id} />}
        {p.status !== "void" && <button className="btn btn-sm px-2" title="ارسال در واتساپ" onClick={() => setSending(p)}><Send size={13} /></button>}
        {p.status !== "void" && can(user, "void") && <button className="btn btn-sm px-2 text-rose-600" title="باطل کردن (اشتباه ثبت شده)" onClick={() => voidPayout(p)}><Ban size={13} /></button>}
      </span>
    </div>
  );
  const box = (label: string, value: number, note?: string, tone = "") => (
    <div className="rounded-2xl p-3" style={{ background: "var(--surface)" }}>
      <div className="muted text-xs">{label}</div>
      <div className={`num text-lg font-bold ${value < 0 ? "text-rose-600" : tone}`}>{money(value)}</div>
      {note && <div className="muted text-[11px] leading-5">{note}</div>}
    </div>
  );
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-lg font-bold">{st.name}</div>
          <div className="muted text-xs">{st.employee ? `${st.job_title || "کارمند"}${st.salary ? ` · حقوق ماهانه ${money(st.salary)}` : ""}` : `${st.line ?? "بدون لاین"} · سهم ${faDigits(String(+Number(st.percent || 0).toFixed(2)))}٪ از باقیماندهٔ بعد از خزانه`}</div>
        </div>
        <button className="btn btn-sm" onClick={() => window.open(`/print/staff/${staffId}?jy=${month[0]}&jm=${month[1]}`, "_blank")}><Printer size={14} />چاپ صورت‌حساب</button>
      </div>
      <MonthNav month={month} onChange={setMonth} />
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        {box("ماندهٔ اول ماه", st.opening, st.opening < 0 ? "بدهکار بود (از ماه قبل)" : st.opening > 0 ? "طلب از ماه‌های قبل" : undefined)}
        {box(st.employee ? "حقوق این ماه" : "سهم این ماه", st.earned_total, undefined, "text-emerald-600")}
        {box("داده‌شده در این ماه", st.given, st.advances ? `شامل ${money(st.advances)} ${kindLabel(st.employee, "advance")}` : undefined)}
        {box("ماندهٔ پایان ماه", st.closing, st.closing < 0 ? "بدهکار است؛ از ماه بعد کم می‌شود" : st.current ? "تا امروز" : "به ماه بعد منتقل شد")}
      </div>
      <div className="rounded-2xl bg-violet-500/10 p-3 text-xs leading-6">
        ماندهٔ اول ماه {money(st.opening)} + {st.employee ? "حقوق" : "سهم"} این ماه {money(st.earned_total)} − داده‌شده {money(st.given)} = <b>{money(st.closing)}</b>
        {(st.paid_later > 0 || st.paid_older > 0) && <> · بعد از ماه{st.paid_later > 0 ? ` بابت همین ماه ${money(st.paid_later)}` : ""}
          {st.paid_older > 0 ? `${st.paid_later > 0 ? " و" : ""} بابت ماه‌های قبل‌تر ${money(st.paid_older)}` : ""} پرداخت شد؛ باقی: <b className={st.remaining < 0 ? "text-rose-600" : ""}>{money(st.remaining)}</b></>}
      </div>
      {earnedRows.length > 0 && (
        <div className="grid gap-2 sm:grid-cols-2">
          {earnedRows.map(([k, label]) => <div key={k} className="flex justify-between rounded-xl px-3 py-2 text-sm" style={{ background: "var(--surface)" }}><span>{label}</span><b className="num">{money(st.earned[k])}</b></div>)}
        </div>
      )}
      {st.work && (st.work.revenue || st.work.forfeits) ? (
        <div className="muted text-xs leading-6">
          کارکرد: {num(st.work.services)} خدمت به مبلغ {money(st.work.revenue)}{st.work.forfeits ? ` + ${money(st.work.forfeits)} بیعانهٔ سوخت‌شده` : ""} · خزانه {money(st.work.treasury)} · سهم {money(st.work.staff_share)} · سهم سالن {money(st.work.salon_share)}
        </div>
      ) : null}
      <div className="space-y-2">
        <div className="text-sm font-bold">پرداخت‌های این ماه</div>
        {st.payouts.length === 0 ? <Empty text="در این ماه پرداختی ثبت نشده" /> : st.payouts.map(row)}
        {st.later.length > 0 && <><div className="pt-2 text-sm font-bold">بابت این ماه، بعد از آن پرداخت شده</div>{st.later.map(row)}</>}
      </div>
      {can(user, "finance") && (
        <div className="grid grid-cols-2 gap-2">
          <button className="btn" onClick={() => setPaying({ kind: "advance", amount: 0 })}><Wallet size={15} />{kindLabel(st.employee, "advance")} جدید</button>
          <button className="btn btn-primary" onClick={() => setPaying({ kind: "settlement", amount: Math.max(0, Math.min(st.remaining, st.balance_now)) })}><HandCoins size={15} />{kindLabel(st.employee, "settlement")}{Math.min(st.remaining, st.balance_now) > 0 ? ` (${money(Math.min(st.remaining, st.balance_now))})` : ""}</button>
        </div>
      )}
      <Modal open={!!paying} onClose={() => { setPaying(null); changed(); }} title={`${paying ? kindLabel(st.employee, paying.kind) : ""} - ${st.name}`}>
        {paying && <PayoutForm person={person} kind={paying.kind} amount={paying.amount} month={month} booksStart={st.books_start}
          onDone={() => { setPaying(null); changed(); }} />}
      </Modal>
      <PayCards staffId={staffId} name={st.name} onChanged={changed} />
      <Modal open={!!sending} onClose={() => setSending(null)} title="ارسال در واتساپ">
        {sending && <SendPayout person={person} payout={sending} />}
      </Modal>
    </div>
  );
}

/** an earlier payout sent again (its receipt from the program) */
function SendPayout({ person, payout }: { person: Person; payout: any }) {
  const blob = useBlob(payout.has_image ? `/api/staff/payouts/${payout.id}/image` : null);
  const file = blob ? new File([blob], blob.type === "application/pdf" ? "receipt.pdf" : "receipt.png", { type: blob.type }) : null;
  return <SendWhatsApp person={person} file={file} text={payoutText(person, payout.kind, [payout.jy, payout.jm], payout.amount, payout.description, payout.paid_at)} />;
}

/** Month end: several receipts at once (one per person). Each one is read, the person and amount suggested; a person
 * checks each row and books it. */
export function ReceiptBatch({ onDone }: { onDone: () => void }) {
  const toast = useToast();
  const { user } = useAuth();
  const ownerOk = can(user, "capital");
  const people = (useApi<any[]>("/api/staff?kind=all").data ?? []).filter((p) => p.is_active);
  const [rows, setRows] = useState<any[]>([]);
  const [reading, setReading] = useState(0);
  const [bookingAll, setBookingAll] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const latest = useRef<any[]>([]); // the rows as they are now (a loop started earlier must not use old ones)
  latest.current = rows;
  const inflight = useRef(new Set<string>()); // rows being booked right now: never sent twice
  async function add(files: FileList | null) {
    const list = Array.from(files ?? []);
    setReading((n) => n + list.length);
    for (const f of list) {
      try {
        const form = new FormData();
        form.append("file", f);
        form.append("caption", "");
        const r: any = await api("/api/staff/payout-receipt", { form });
        const s = r.suggest;
        const state = r.duplicate ? "dup" : r.failed ? "failed" : r.needs_check ? "check" : "ready";
        setRows((x) => [...x, { key: `${f.name}-${f.size}-${x.length}`, file: f, read: r, staff_id: s.staff_id ?? 0, kind: s.kind, ym: [s.jy, s.jm] as YM,
          amount: s.amount ?? 0, paid_at: s.paid_at ?? toLocalIso(new Date()), acc: s.payment_account_id ?? 0, accFromReceipt: !!s.payment_account_id,
          fromOwner: false, reference: s.reference ?? "", description: s.description ?? "", state,
          error: r.duplicate ? "این رسید قبلاً ثبت شده" : r.failed ? "این رسید «ناموفق» یا نامعلوم به نظر می‌رسد"
            : r.needs_check ? "مبلغ را با عکس رسید مقایسه کنید، بعد «ثبت» بزنید" : "" }]);
      } catch (e: any) { toast(`${f.name}: ${e.message}`, "error"); }
      setReading((n) => n - 1);
    }
  }
  // a booked row stays booked: a late answer (e.g. «already booked» from a second click) never changes it back
  const set = (key: string, patch: any) => setRows((x) => x.map((r) => (r.key === key && r.state !== "done" ? { ...r, ...patch } : r)));
  async function book(key: string, force = false) {
    const r = latest.current.find((x) => x.key === key);
    if (!r || r.state === "done" || inflight.current.has(key)) return false;
    if (!r.staff_id || !r.amount || (!r.acc && !r.fromOwner)) { set(key, { error: "شخص، مبلغ و حساب را انتخاب کنید" }); return false; }
    if (r.state === "failed" && !window.confirm("این رسید «ناموفق» یا نامعلوم به نظر می‌رسد. مطمئنید پول پرداخت شده و ثبت شود؟")) return false;
    if (r.state === "check" && !window.confirm(`مبلغ ${money(r.amount)} با عکس رسید یکی است؟`)) return false;
    inflight.current.add(key);
    set(key, { state: "saving", error: "" });
    try {
      await api(`/api/staff/${r.staff_id}/payout`, { body: { amount: r.amount, kind: r.kind, payment_account_id: r.fromOwner ? null : r.acc,
        from_owner: r.fromOwner, paid_at: r.paid_at, jy: r.ym[0], jm: r.ym[1], reference: r.reference, description: r.description,
        receipt_token: r.read.token, receipt: r.read.receipt, force } });
      set(key, { state: "done" });
      return true;
    } catch (e: any) {
      set(key, { state: e.status === 409 ? "dup" : r.state === "failed" || r.state === "check" ? r.state : "ready", error: e.message });
      return false;
    } finally { inflight.current.delete(key); }
  }
  const ready = rows.filter((r) => r.state === "ready");
  return (
    <div className="space-y-3">
      <div className="rounded-2xl bg-violet-500/10 p-3 text-sm leading-7">
        عکس رسیدهای پرداخت این ماه را با هم انتخاب کنید (مثلاً تسویهٔ آخر ماه همهٔ پرسنل). هر رسید خوانده می‌شود و شخص، مبلغ و تاریخ پیشنهاد می‌شود؛
        هر ردیف را نگاه کنید و «ثبت» بزنید. {people.length === 0 && "اول پرسنل را در تنظیمات اضافه کنید."}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn btn-primary" onClick={() => input.current?.click()}><Upload size={15} />انتخاب رسیدها</button>
        <input ref={input} type="file" multiple accept="image/*,application/pdf" className="hidden" onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
        {reading > 0 && <span className="muted text-sm">در حال خواندن {faDigits(reading)} رسید…</span>}
        {ready.length > 1 && <button className="btn" disabled={bookingAll} onClick={async () => {
          setBookingAll(true);
          try { for (const k of ready.map((r) => r.key)) await book(k); } finally { setBookingAll(false); }
        }}><Check size={15} />{bookingAll ? "در حال ثبت…" : "ثبت همهٔ ردیف‌های آماده"}</button>}
      </div>
      {rows.map((r) => (
        <div key={r.key} className={`space-y-2 rounded-2xl border p-3 ${r.state === "done" ? "border-emerald-500/50 bg-emerald-500/5" : r.state === "failed" ? "border-rose-500/50" : r.state === "check" ? "border-amber-500/60" : ""}`}
          style={["done", "failed", "check"].includes(r.state) ? undefined : { borderColor: "var(--border)" }}>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <BatchThumb file={r.file} />
            <span className="muted">{r.file.name}</span>
            {r.read.warnings?.map((w: string) => <span key={w} className="rounded-lg bg-amber-500/10 px-2 py-1 text-amber-800 dark:text-amber-200">{w}</span>)}
            {r.state === "done" && <span className="font-bold text-emerald-600">ثبت شد ✓</span>}
          </div>
          {r.state !== "done" && (
            <div className="grid gap-2 sm:grid-cols-3">
              <select className="input" value={r.staff_id} onChange={(e) => set(r.key, { staff_id: Number(e.target.value) })}>
                <option value={0}>— چه کسی؟ —</option>
                {people.map((p) => <option key={p.id} value={p.id}>{p.full_name}{p.kind === "employee" ? " (کارمند)" : ""}</option>)}
              </select>
              <select className="input" value={r.kind} onChange={(e) => set(r.key, { kind: e.target.value })}>
                <option value="advance">برداشتی / مساعده</option><option value="settlement">تسویه / پرداخت حقوق</option>
              </select>
              <MonthSelect value={r.ym} onChange={(m) => set(r.key, { ym: m })} />
              <MoneyInput value={r.amount} onChange={(v) => set(r.key, { amount: v })} />
              <JalaliPicker pastOnly value={r.paid_at} onChange={(v) => set(r.key, { paid_at: v })} />
              <div>
                {r.fromOwner ? <div className="input muted text-xs">از جیب خود مدیر</div> : <AccountSelect value={r.acc} onChange={(id) => set(r.key, { acc: id })} />}
                {!r.accFromReceipt && !r.fromOwner && <div className="muted mt-0.5 text-[11px]">حساب از روی رسید شناخته نشد؛ درستش را انتخاب کنید</div>}
                {ownerOk && <label className="mt-0.5 flex items-center gap-1 text-[11px]"><input type="checkbox" checked={r.fromOwner} onChange={(e) => set(r.key, { fromOwner: e.target.checked })} />از جیب خودم</label>}
              </div>
              <input className="input sm:col-span-2" value={r.description} onChange={(e) => set(r.key, { description: e.target.value })} placeholder="توضیح (همان که در واتساپ می‌نویسید)" />
              <div className="flex gap-2">
                <button className="btn btn-primary flex-1" disabled={r.state === "saving" || bookingAll} onClick={() => book(r.key, r.state === "dup")}>
                  {r.state === "dup" ? "باز هم ثبت شود" : r.state === "failed" ? "با این حال ثبت شود" : r.state === "saving" ? "…" : "ثبت"}</button>
                <button className="btn" disabled={r.state === "saving"} onClick={() => setRows((x) => x.filter((y) => y.key !== r.key))}>حذف</button>
              </div>
            </div>
          )}
          {r.error && <div className="text-xs text-rose-600">{r.error}</div>}
        </div>
      ))}
      {rows.length > 0 && <button className="btn w-full" onClick={onDone}>تمام</button>}
    </div>
  );
}

function BatchThumb({ file }: { file: File }) {
  const url = useObjectUrl(file);
  return url && file.type.startsWith("image/") ? <img src={url} alt="" className="h-10 w-10 rounded-lg object-cover" /> : <FileImage size={18} />;
}

/** The cards / IBANs a person is paid on: with the bank connected, money sent to one of them is booked as their payout
 * by itself. Only a mask is kept (never the whole number). */
export function PayCards({ staffId, name, onChanged }: { staffId: number; name: string; onChanged?: () => void }) {
  const toast = useToast();
  const { user } = useAuth();
  const { data, reload } = useApi<any[]>(`/api/staff/${staffId}/pay-cards`, [staffId]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const mayEdit = can(user, "settings");
  if (!data || (!data.length && !mayEdit)) return null;
  async function add() {
    setBusy(true);
    try {
      const r = await api(`/api/staff/${staffId}/pay-cards`, { body: { number: text } });
      setText("");
      toast(r.booked_now ? `کارت ثبت شد؛ ${faDigits(r.booked_now)} پرداخت قبلی به این کارت از روی بانک ثبت شد (صورت‌حساب را ببینید)` : "کارت ثبت شد");
      reload();
      onChanged?.();
    }
    catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  }
  return (
    <details className="rounded-2xl p-3 text-sm" style={{ background: "var(--surface)" }} open={data.length > 0}>
      <summary className="cursor-pointer font-semibold">کارت / شبای دریافت پول {name} {data.length > 0 && <span className="badge mr-1">{faDigits(data.length)}</span>}</summary>
      <p className="muted mt-2 text-xs leading-6">
        وقتی برنامه به بانک وصل است (API بانک یا فایل صورت‌حساب)، پولی که از حساب‌های سالن به این کارت یا شبا برود خودکار به‌عنوان
        برداشتی یا تسویهٔ {name} ثبت می‌شود (در «تطبیق رسید و بانک ← ثبت‌شده‌ها از بانک» می‌توانید ببینید و اگر اشتباه بود برگردانید).
        شمارهٔ کامل در برنامه نگه داشته نمی‌شود.
      </p>
      <div className="mt-2 space-y-1">
        {data.map((c) => (
          <div key={c.id} className="flex flex-wrap items-center gap-2 rounded-xl px-2 py-1" style={{ background: "var(--surface-solid)" }}>
            <span className="num" dir="ltr">{c.mask}</span>
            <span className={`badge text-[10px] ${c.auto ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300" : "bg-amber-500/15 text-amber-700 dark:text-amber-300"}`}>
              {c.auto ? "ثبت خودکار" : "فقط پیشنهاد (یک بار برگردانده شد)"}</span>
            {c.hits > 0 && <span className="muted text-xs">{faDigits(c.hits)} بار ثبت شد</span>}
            {mayEdit && <button className="btn btn-sm mr-auto px-2 text-rose-600" title="برداشتن" onClick={async () => {
              if (!window.confirm("این کارت برداشته شود؟")) return;
              try { await api(`/api/staff/pay-cards/${c.id}`, { method: "DELETE" }); reload(); } catch (e: any) { toast(e.message, "error"); }
            }}><Ban size={13} /></button>}
          </div>
        ))}
      </div>
      {mayEdit && (
        <div className="mt-2 flex gap-2">
          <input className="input num flex-1" dir="ltr" value={text} onChange={(e) => setText(e.target.value)} placeholder="6037 9912 3456 7890 یا IR..."
            onKeyDown={(e) => { if (e.key === "Enter" && text.trim()) add(); }} />
          <button className="btn btn-sm" disabled={busy || !text.trim()} onClick={add}>افزودن</button>
        </div>
      )}
    </details>
  );
}

/** «تسویهٔ همه»: everyone with what is still to be paid for the month, ticked; one account and date; one click. */
export function SettleAll({ who, month: month0, onDone }: { who: "staff" | "employee" | "all"; month?: YM; onDone: () => void }) {
  const toast = useToast();
  const { user } = useAuth();
  const [month, setMonth] = useState<YM>(month0 ?? defaultMonth("settlement"));
  const { data, reload } = useApi<any>(`/api/staff/settle-plan?jy=${month[0]}&jm=${month[1]}&who=${who}`, [month[0], month[1], who]);
  const [picked, setPicked] = useState<Record<number, { on: boolean; amount: number }>>({});
  const [acc, setAcc] = useState(0);
  const [fromOwner, setFromOwner] = useState(false);
  const [paidAt, setPaidAt] = useState(toLocalIso(new Date()));
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [sending, setSending] = useState<any>(null);
  // ticked once per month (what is still to pay); after a run the person's own choices stay
  const pickedFor = useRef("");
  useEffect(() => {
    if (!data || pickedFor.current === `${data.jy}-${data.jm}`) return;
    pickedFor.current = `${data.jy}-${data.jm}`;
    setPicked(Object.fromEntries(data.rows.map((r: any) => [r.staff_id, { on: r.due > 0, amount: r.due }])));
  }, [data]);
  if (!data) return <Loading />;
  const rows = data.rows as any[];
  const paidNow = new Set((result?.done ?? []).map((x: any) => x.staff_id));
  const chosen = rows.filter((r) => !paidNow.has(r.staff_id) && picked[r.staff_id]?.on && picked[r.staff_id]?.amount > 0);
  const total = chosen.reduce((a, r) => a + picked[r.staff_id].amount, 0);
  async function run() {
    setBusy(true);
    try {
      const r = await api("/api/staff/settle", { body: { jy: month[0], jm: month[1], payment_account_id: fromOwner ? null : acc, from_owner: fromOwner,
        paid_at: paidAt, rows: chosen.map((x) => ({ staff_id: x.staff_id, amount: picked[x.staff_id].amount, expected: x.due })) } });
      setResult(r);
      if (r.done.length) toast(`${faDigits(r.done.length)} پرداخت ثبت شد (جمع ${money(r.total)})`);
      reload();
    } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  }
  /** a row refused as maybe the same payment again, confirmed by the person as another one */
  async function forceOne(row: any) {
    setBusy(true);
    try {
      const r = await api("/api/staff/settle", { body: { jy: month[0], jm: month[1], payment_account_id: fromOwner ? null : acc, from_owner: fromOwner,
        paid_at: paidAt, rows: [{ staff_id: row.staff_id, amount: picked[row.staff_id]?.amount ?? row.due, expected: row.due, force: true }] } });
      setResult((old: any) => ({ ...r, done: [...(old?.done ?? []), ...r.done], total: (old?.total ?? 0) + r.total,
        errors: [...(old?.errors ?? []).filter((x: any) => x.staff_id !== row.staff_id), ...r.errors] }));
      if (r.done.length) toast(`${row.name}: ثبت شد`);
      reload();
    } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  }
  const person = (r: any): Person => ({ staff_id: r.staff_id, name: r.name, mobile: r.mobile, employee: rows.find((x) => x.staff_id === r.staff_id)?.employee });
  return (
    <div className="space-y-3">
      <div className="rounded-2xl bg-violet-500/10 p-3 text-sm leading-7">
        برای هر نفر، ماندهٔ <b>{monthName(month)}</b> حساب شده (سهم یا حقوق ماه منهای برداشتی‌ها و پرداخت‌های قبلی). کسانی که تیک خورده‌اند با یک دکمه
        تسویه می‌شوند؛ مبلغ هر نفر را می‌توانید عوض کنید.
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="بابت ماه"><MonthSelect value={month} onChange={(m) => { setMonth(m); setResult(null); }} /></Field>
        <Field label="تاریخ پرداخت"><JalaliPicker pastOnly value={paidAt} onChange={setPaidAt} /></Field>
        <Field label="پرداخت از">
          {fromOwner ? <div className="input muted text-xs">از جیب خود مدیر</div> : <AccountSelect value={acc} onChange={setAcc} remember="payout" />}
          {can(user, "capital") && <label className="mt-1 flex items-center gap-1 text-xs"><input type="checkbox" checked={fromOwner} onChange={(e) => setFromOwner(e.target.checked)} />از جیب خودم</label>}
        </Field>
      </div>
      {rows.length === 0 ? <Empty text="کسی در این ماه نیست" /> : (
        <div className="space-y-1">
          {rows.map((r) => {
            const p = picked[r.staff_id] ?? { on: false, amount: 0 };
            const done = result?.done?.find((x: any) => x.staff_id === r.staff_id);
            const err = result?.errors?.find((x: any) => x.staff_id === r.staff_id);
            return (
              <div key={r.staff_id} className={`grid items-center gap-2 rounded-xl px-3 py-2 text-sm sm:grid-cols-[auto_1fr_10rem_auto] ${done ? "bg-emerald-500/10" : ""}`}
                style={done ? undefined : { background: "var(--surface)" }}>
                <input type="checkbox" checked={p.on} disabled={!!done} onChange={(e) => setPicked({ ...picked, [r.staff_id]: { ...p, on: e.target.checked } })} />
                <div className="min-w-0">
                  <div className="font-semibold">{r.name}{r.employee && <span className="muted text-xs"> (کارمند)</span>}</div>
                  <div className="muted text-[11px] leading-5">
                    {r.opening > 0 && <>طلب از ماه‌های قبل {money(r.opening)} · </>}{r.opening < 0 && <>بدهکار از قبل {money(-r.opening)} · </>}
                    {r.employee ? "حقوق" : "سهم"} ماه {money(r.earned)} · داده‌شده {money(r.given + r.paid_later)}
                    {r.why && r.due <= 0 && <> · <span className={r.balance_now < 0 ? "text-rose-600" : ""}>{r.why}</span></>}
                    {r.salary_missing && <> · <a href="/employees" className="underline">ثبت حقوق</a></>}
                  </div>
                </div>
                {done ? <div className="font-bold text-emerald-600">ثبت شد ✓</div>
                  : <MoneyInput value={p.amount} onChange={(v) => setPicked({ ...picked, [r.staff_id]: { on: v > 0, amount: v } })} />}
                <div className="flex items-center gap-1">
                  {done && <button className="btn btn-sm" title="ارسال در واتساپ" onClick={() => setSending(done)}><Send size={13} /></button>}
                </div>
                {err && <div className="flex flex-wrap items-center gap-2 text-xs text-rose-600 sm:col-span-4">{err.error}
                  {err.duplicate && <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => forceOne(r)}>بله، پرداخت جداگانه‌ای است؛ ثبت شود</button>}</div>}
              </div>
            );
          })}
        </div>
      )}
      <button className="btn btn-primary w-full py-3" disabled={busy || !chosen.length || (!fromOwner && !acc)} onClick={run}>
        <HandCoins size={16} />{busy ? "در حال ثبت…" : `تسویهٔ ${faDigits(chosen.length)} نفر - جمع ${money(total)}`}
      </button>
      {result && <button className="btn w-full" onClick={onDone}>تمام</button>}
      <Modal open={!!sending} onClose={() => setSending(null)} title="ارسال در واتساپ">
        {sending && <SendWhatsApp person={person(sending)} text={payoutText(person(sending), "settlement", month, sending.amount, "", paidAt)} />}
      </Modal>
    </div>
  );
}
