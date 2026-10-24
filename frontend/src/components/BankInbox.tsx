import { ArrowDownLeft, ArrowUpRight, Check, CreditCard, HelpCircle, Inbox, RotateCcw, Sparkles } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { jdatetime, money } from "../lib/format";
import { can, useApi, useAuth, useToast } from "../lib/hooks";
import { faDigits, thisMonth, type YM } from "../lib/jalali";
import { MonthSelect } from "./MonthNav";
import { payoutText, SendWhatsApp } from "./StaffPay";
import { Card, Empty, Field, Loading, Modal } from "./ui";

const BASE = "/api/plugins/bank_sync";

/** a text with card numbers / IBANs kept left-to-right inside the Persian (else «6219-86**-****-5678» shows reversed) */
export function LtrNumbers({ text }: { text: string }) {
  const parts = (text || "").split(/((?:IR)?\d[\d*\-…]{6,}\d)/);
  return <>{parts.map((p, i) => (i % 2 ? <span key={i} dir="ltr" className="num inline-block">{p}</span> : p))}</>;
}

function Amount({ t }: { t: any }) {
  const tone = t.direction === "in" ? "text-emerald-600" : t.direction === "out" ? "text-rose-600" : "text-amber-600";
  return <b className={`num text-lg ${tone}`}>{t.direction === "in" ? "+" : t.direction === "out" ? "−" : "?"}{money(t.amount)}</b>;
}

function Party({ t }: { t: any }) {
  if (!t.counterparty && !t.counterparty_name) return null;
  return (
    <span className="inline-flex items-center gap-1 text-xs">
      <CreditCard size={13} className="muted" />
      {t.counterparty_name && <b>{t.counterparty_name}</b>}
      {t.counterparty && <span className="num muted" dir="ltr">{t.counterparty}</span>}
    </span>
  );
}

/** Book a line as something chosen by hand: a staff payout, an expense, the owner's own money, or set it aside. */
function BookDialog({ t, preset, onDone }: { t: any; preset?: any; onDone: (r: any) => void }) {
  const toast = useToast();
  const { user } = useAuth();
  const staff = (useApi<any[]>("/api/staff?kind=all").data ?? []).filter((p) => p.is_active);
  const lines = (useApi<any[]>("/api/lines").data ?? []).filter((l) => l.is_active);
  const cats = useApi<any>("/api/expenses/categories").data;
  const capitalOk = can(user, "capital");
  const [target, setTarget] = useState<string>(preset?.target ?? (t.direction === "out" ? "staff" : capitalOk ? "owner" : "ignore"));
  const [staffId, setStaffId] = useState<number>(preset?.staff_id ?? 0);
  const [kind, setKind] = useState<string>(preset?.kind ?? "");
  const [ym, setYm] = useState<YM | null>(preset?.jy ? [preset.jy, preset.jm] : null);
  const [category, setCategory] = useState<string>(preset?.category ?? "");
  const [lineId, setLineId] = useState<number>(preset?.line_id ?? 0);
  const [fromTreasury, setFromTreasury] = useState<boolean>(!!preset?.from_treasury);
  const [remember, setRemember] = useState(!!(t.has_card || t.counterparty_name));
  const [auto, setAuto] = useState(!!t.has_card && can(user, "settings"));
  const [busy, setBusy] = useState(false);
  const [dup, setDup] = useState<string | null>(null);
  const options = (t.direction === "out"
    ? [["staff", "پرداخت به پرسنل / کارمند"], ["expense", "هزینهٔ کلینیک"], ["owner", "پول خود مدیر (برداشت شخصی)"], ["ignore", "کنار گذاشتن (به حساب خودمان، تکراری…)"]]
    : [["owner", "آوردن سرمایه (پول خود مدیر)"], ["ignore", "کنار گذاشتن"]]).filter(([k]) => k !== "owner" || capitalOk);
  // setting aside is never remembered (a card is never set aside by itself)
  const keep = remember && target !== "ignore";
  async function save(force = false) {
    setBusy(true);
    try {
      const r = await api(`${BASE}/transactions/${t.id}/book`, { body: {
        target, staff_id: target === "staff" ? staffId : null, kind: kind || null, jy: ym?.[0] ?? null, jm: ym?.[1] ?? null,
        category: target === "expense" ? category : null, line_id: target === "expense" && lineId ? lineId : null,
        from_treasury: target === "expense" && fromTreasury, remember: keep, auto: keep && auto, force,
      } });
      toast(target === "ignore" ? "کنار گذاشته شد" : "ثبت شد" + (r.remembered ? (r.rule_auto ? "؛ دفعهٔ بعد خودکار ثبت می‌شود" : "؛ دفعهٔ بعد پیشنهاد می‌شود") : ""));
      if (r.remember_note) toast(`به خاطر سپرده نشد: ${r.remember_note}`, "error");
      onDone(r);
    } catch (e: any) {
      if (e.status === 409) setDup(e.message); else toast(e.message, "error");
    } finally { setBusy(false); }
  }
  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap items-center gap-3 rounded-2xl p-3" style={{ background: "var(--surface)" }}>
        <Amount t={t} /><span className="muted text-xs">{jdatetime(t.occurred_at)} · {t.account ?? "—"}</span><Party t={t} />
        {t.description && <div className="muted w-full text-xs"><LtrNumbers text={t.description} /></div>}
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {options.map(([k, label]) => (
          <button key={k} type="button" className={`rounded-2xl border p-3 text-right ${target === k ? "border-violet-500 bg-violet-500/10 font-bold" : ""}`}
            style={target === k ? undefined : { borderColor: "var(--border)" }} onClick={() => setTarget(k)}>{label}</button>
        ))}
      </div>
      {target === "staff" && (
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="چه کسی؟">
            <select className="input" value={staffId} onChange={(e) => setStaffId(Number(e.target.value))}>
              <option value={0}>— انتخاب —</option>
              {staff.map((p) => <option key={p.id} value={p.id}>{p.full_name}{p.kind === "employee" ? " (کارمند)" : ""}</option>)}
            </select>
          </Field>
          <Field label="نوع">
            <select className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="">خودکار (از تاریخ و مانده)</option><option value="advance">برداشتی / مساعده</option><option value="settlement">تسویه / حقوق</option>
            </select>
          </Field>
          <Field label="بابت ماه">{ym ? <MonthSelect value={ym} onChange={setYm} />
            : <button type="button" className="input muted text-right" title="برای انتخاب ماه بزنید" onClick={() => setYm(thisMonth())}>خودکار (از تاریخ)</button>}</Field>
        </div>
      )}
      {target === "expense" && (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="دستهٔ هزینه">
            <input className="input" list="bank-cats" value={category} onChange={(e) => setCategory(e.target.value)} placeholder="مثلاً مواد مصرفی" />
            <datalist id="bank-cats">{[...(cats?.quick ?? []), ...(cats?.used ?? [])].map((c: string) => <option key={c} value={c} />)}</datalist>
          </Field>
          <Field label="برای لاین (اختیاری)">
            <select className="input" value={lineId} onChange={(e) => { setLineId(Number(e.target.value)); if (!Number(e.target.value)) setFromTreasury(false); }}>
              <option value={0}>— همهٔ کلینیک —</option>{lines.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select>
            {lineId > 0 && <label className="mt-1 flex items-center gap-2 text-xs"><input type="checkbox" checked={fromTreasury} onChange={(e) => setFromTreasury(e.target.checked)} />از خزانهٔ همین لاین خرج شد</label>}
          </Field>
        </div>
      )}
      {target !== "ignore" && (t.has_card || t.counterparty_name) && (
        <div className="space-y-1 rounded-2xl bg-violet-500/10 p-3 text-xs leading-6">
          <label className="flex items-center gap-2"><input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
            این {t.has_card ? "کارت/شبا" : "نام"} را به خاطر بسپار</label>
          {remember && t.has_card && can(user, "settings") && <label className="flex items-center gap-2"><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
            دفعهٔ بعد خودکار ثبت شود (بدون پرسیدن)</label>}
          {remember && !t.has_card && <div className="muted">با نام فقط پیشنهاد می‌شود (نام برای ثبت خودکار کافی نیست).</div>}
        </div>
      )}
      {dup && (
        <div className="space-y-2 rounded-2xl bg-rose-500/10 p-3 text-sm">
          <div>{dup}</div>
          <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => save(true)}>بله، پرداخت جداگانه‌ای است؛ ثبت شود</button>
        </div>
      )}
      <button className="btn btn-primary w-full py-3" disabled={busy || (target === "staff" && !staffId) || (target === "expense" && !category.trim())} onClick={() => save(false)}>
        <Check size={16} />{target === "ignore" ? "کنار گذاشتن" : "ثبت"}</button>
    </div>
  );
}

/** «صندوق بانک»: the bank lines that are not a customer's money, each with one-tap suggestions. */
export function BankInboxList({ onChanged }: { onChanged?: () => void }) {
  const toast = useToast();
  const { data, reload } = useApi<any>(`${BASE}/inbox`, []);
  const [booking, setBooking] = useState<{ t: any; preset?: any } | null>(null);
  const [sent, setSent] = useState<any>(null);
  const changed = () => { reload(); onChanged?.(); };
  if (!data) return <Loading />;
  const done = (r: any) => {
    setBooking(null);
    changed();
    if (r?.target === "staff" && r.staff_id) setSent(r);
  };
  async function quick(t: any, s: any, force = false) {
    try {
      if (s.action === "link") await api(`${BASE}/transactions/${t.id}/link`, { body: { type: s.type, id: s.id } });
      else {
        const r = await api(`${BASE}/transactions/${t.id}/book`, { body: { target: s.target, staff_id: s.staff_id ?? null, kind: s.kind ?? null,
          jy: s.jy ?? null, jm: s.jm ?? null, category: s.category ?? null, line_id: s.line_id ?? null, from_treasury: !!s.from_treasury, force } });
        if (r.target === "staff") setSent(r);
      }
      toast("ثبت شد");
      changed();
    } catch (e: any) {
      // maybe the same money entered by hand: say so, and book it anyway only when the person confirms
      if (e.status === 409 && window.confirm(`${e.message}\n\nپرداخت جداگانه‌ای است و ثبت شود؟`)) return quick(t, s, true);
      if (e.status !== 409) toast(e.message, "error");
    }
  }
  async function direction(t: any, d: string) {
    try { await api(`${BASE}/transactions/${t.id}/direction`, { body: { direction: d } }); changed(); } catch (e: any) { toast(e.message, "error"); }
  }
  return (
    <div className="space-y-3">
      <div className="rounded-2xl bg-violet-500/10 p-3 text-sm leading-7">
        <div className="mb-1 flex items-center gap-2 font-bold"><Inbox size={17} />صندوق بانک</div>
        تراکنش‌هایی از بانک که برنامه خودش نفهمید چیست (پرداخت به پرسنل، خرید، اجاره…). روی پیشنهاد درست بزنید تا با همان مبلغ، تاریخ و حساب ثبت شود؛
        با «به خاطر بسپار»، دفعهٔ بعد پول به همان کارت خودش ثبت می‌شود. پرداخت به پرسنلی که کارتش را در صورت‌حسابش وارد کرده‌اید، خودکار ثبت می‌شود و اینجا نمی‌آید.
      </div>
      {data.rows.length === 0 ? <Empty text="چیزی منتظر نیست 🎉" /> : data.rows.map((t: any) => (
        <div key={t.id} className="space-y-2 rounded-2xl border p-3" style={{ borderColor: "var(--border)" }}>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            {t.direction === "in" ? <ArrowDownLeft size={18} className="text-emerald-600" /> : t.direction === "out" ? <ArrowUpRight size={18} className="text-rose-600" /> : <HelpCircle size={18} className="text-amber-600" />}
            <Amount t={t} />
            <span className="muted text-xs">{jdatetime(t.occurred_at)}</span>
            <span className="text-xs">{t.account ?? "حساب نامعلوم"}</span>
            <Party t={t} />
            {t.reference && <span className="muted num text-xs">پیگیری {t.reference}</span>}
          </div>
          {t.description && <div className="muted text-xs leading-6"><LtrNumbers text={t.description} /></div>}
          {t.direction === "unknown" ? (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span>این پول آمد یا رفت؟</span>
              <button className="btn btn-sm" onClick={() => direction(t, "in")}><ArrowDownLeft size={14} />واریز بود</button>
              <button className="btn btn-sm" onClick={() => direction(t, "out")}><ArrowUpRight size={14} />برداشت بود</button>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {t.suggestions.map((s: any, i: number) => (
                <button key={i} className={`btn btn-sm ${i === 0 ? "btn-primary" : ""}`} title={s.why} onClick={() => quick(t, s)}>
                  <Sparkles size={13} />{s.label}</button>
              ))}
              {t.direction === "out" && !t.suggestions.some((s: any) => s.target === "staff") && <button className="btn btn-sm" onClick={() => setBooking({ t, preset: { target: "staff" } })}>پرداخت به پرسنل…</button>}
              {t.direction === "out" && !t.suggestions.some((s: any) => s.target === "expense") && <button className="btn btn-sm" onClick={() => setBooking({ t, preset: { target: "expense" } })}>هزینه…</button>}
              <button className="btn btn-sm" onClick={() => setBooking({ t })}>{t.suggestions.length ? "چیز دیگری است…" : "چیز دیگر…"}</button>
              {t.suggestions.length === 0 && t.direction === "in" && <span className="muted self-center text-xs">اگر پول مشتری است، از «بیعانه‌ها» یا «فروش و فاکتور» ثبت کنید؛ خودش وصل می‌شود.</span>}
            </div>
          )}
        </div>
      ))}
      <Modal open={!!booking} onClose={() => setBooking(null)} title="این تراکنش چیست؟" wide>
        {booking && <BookDialog t={booking.t} preset={booking.preset} onDone={done} />}
      </Modal>
      <Modal open={!!sent} onClose={() => setSent(null)} title="ارسال در واتساپ">
        {sent && <SendWhatsApp person={{ staff_id: sent.staff_id, name: sent.name, mobile: sent.mobile }}
          text={payoutText({ staff_id: sent.staff_id, name: sent.name }, sent.kind, [sent.jy, sent.jm], sent.transaction?.amount ?? 0, "", sent.transaction?.occurred_at)} />}
      </Modal>
    </div>
  );
}

/** What was booked or linked from bank lines lately: by itself or from the inbox; each can be taken back. */
export function BankBooked({ onChanged }: { onChanged?: () => void }) {
  const toast = useToast();
  const { data, reload } = useApi<any[]>(`${BASE}/booked?days=60`, []);
  if (!data) return <Loading />;
  async function undo(t: any) {
    const what = t.status === "ignored" ? "به صندوق بانک برگردد؟" : t.auto_booked === null ? "اتصال برداشته شود؟ (ثبت خودش می‌ماند)" : `«${t.matched_label}» باطل شود و تراکنش به صندوق بانک برگردد؟`;
    if (!window.confirm(what)) return;
    try { await api(`${BASE}/transactions/${t.id}/undo`, { body: {} }); toast("برگشت"); reload(); onChanged?.(); } catch (e: any) { toast(e.message, "error"); }
  }
  return (
    <Card title="ثبت‌شده‌ها از روی بانک (۶۰ روز اخیر)" pad={false}>
      {data.length === 0 ? <Empty text="هنوز چیزی از روی بانک ثبت نشده" /> : (
        <div className="overflow-x-auto">
          <table className="table">
            <thead><tr><th>زمان</th><th>مبلغ</th><th>حساب</th><th>طرف حساب</th><th>ثبت شد به‌عنوان</th><th>چطور</th><th></th></tr></thead>
            <tbody>{data.map((t) => (
              <tr key={t.id}>
                <td className="num muted">{jdatetime(t.occurred_at)}</td><td><Amount t={t} /></td><td className="text-xs">{t.account ?? "—"}</td>
                <td><Party t={t} /></td>
                <td className="font-semibold">{t.status === "ignored" ? "کنار گذاشته شد" : t.matched_label ?? "—"}</td>
                <td>{t.auto_booked ? <span className="badge bg-emerald-500/15 text-emerald-700 dark:text-emerald-300">خودکار</span>
                  : t.auto_booked === false ? <span className="badge">از صندوق بانک</span> : t.status === "matched" ? <span className="badge bg-sky-500/15 text-sky-700 dark:text-sky-300">وصل به ثبت دستی</span> : null}</td>
                <td>{t.can_undo && <button className="btn btn-sm" onClick={() => undo(t)}><RotateCcw size={13} />برگرداندن</button>}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      <p className="muted px-5 py-3 text-xs leading-6">اگر چیزی اشتباه خودکار ثبت شده، «برگرداندن» بزنید: پرداخت یا هزینه در همان تاریخ خودش باطل می‌شود، تراکنش به صندوق بانک برمی‌گردد و
        آن کارت دیگر خودکار ثبت نمی‌شود تا دوباره تأییدش کنید.</p>
    </Card>
  );
}

/** Who each card / IBAN / name is (learned from the inbox or entered on a staff member's statement). */
export function BankRules() {
  const toast = useToast();
  const { user } = useAuth();
  const { data, reload } = useApi<any[]>(`${BASE}/rules`, []);
  const mayEdit = can(user, "settings");
  if (!data) return <Loading />;
  const what = (r: any) => r.target === "staff" ? `پرداخت به ${r.staff ?? "—"}` : r.target === "expense" ? `هزینهٔ ${r.category}${r.from_treasury ? " (از خزانه)" : ""}`
    : r.target === "owner" ? "پول خود مدیر" : "کنار گذاشتن";
  async function update(r: any, body: any) {
    try { await api(`${BASE}/rules/${r.id}`, { method: "PUT", body }); reload(); } catch (e: any) { toast(e.message, "error"); }
  }
  return (
    <Card title="کارت‌ها و نام‌هایی که برنامه می‌شناسد" pad={false}>
      {data.length === 0 ? <Empty text="هنوز چیزی نیست؛ کارت پرسنل را در صورت‌حساب هر نفر وارد کنید یا در صندوق بانک «به خاطر بسپار» بزنید" /> : (
        <div className="overflow-x-auto">
          <table className="table">
            <thead><tr><th>کارت / شبا / نام</th><th>یعنی</th><th>ثبت خودکار</th><th>دفعه‌ها</th><th></th></tr></thead>
            <tbody>{data.map((r) => (
              <tr key={r.id}>
                <td className="num" dir={r.kind === "name" ? undefined : "ltr"}>{r.text}{r.kind === "name" && <span className="muted text-xs"> (نام)</span>}</td>
                <td className="font-semibold">{what(r)}</td>
                <td>{r.kind === "name" ? <span className="muted text-xs">فقط پیشنهاد</span> : (
                  <label className="flex items-center gap-1 text-xs"><input type="checkbox" disabled={!mayEdit} checked={r.auto} onChange={(e) => update(r, { auto: e.target.checked })} />
                    {r.auto ? "روشن" : "خاموش"}</label>)}</td>
                <td className="num text-xs">{faDigits(r.hits)}{r.undos ? ` (${faDigits(r.undos)} بار برگشت)` : ""}</td>
                <td>{mayEdit && <button className="btn btn-sm text-rose-600" onClick={() => { if (window.confirm("فراموش شود؟")) update(r, { active: false }); }}>فراموش کن</button>}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

