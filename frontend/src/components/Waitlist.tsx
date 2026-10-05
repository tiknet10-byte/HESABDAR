import { Crown, Plus, Trash2, UserCheck } from "lucide-react";
import { useEffect, useState } from "react";
import BookingFields, { type BookingState } from "./Booking";
import CustomerPicker, { type CustomerChoice } from "./CustomerPicker";
import StaffSelect from "./StaffSelect";
import { Card, Empty, Field, Modal } from "./ui";
import { api } from "../lib/api";
import { useApi, useToast } from "../lib/hooks";
import { faDigits, formatJ, formatJShort } from "../lib/jalali";

/** A time an appointment no longer uses (cancelled, or the service was done earlier than booked). */
export type FreedSlot = { appointment_id: number; start_at: string; service_id?: number | null; service?: string | null;
  staff_id?: number | null; duration_minutes?: number; waiting: number };

const EVENT = "hesabdar:slot-freed";
export const WAITLIST_CHANGED = "hesabdar:waitlist-changed";

/** Tell the app some times became free; it offers them to the waiting (VIP) customers. */
export function announceFreed(slots: (FreedSlot | null | undefined)[] | undefined) {
  const list = (slots ?? []).filter(Boolean) as FreedSlot[];
  if (list.length) window.dispatchEvent(new CustomEvent(EVENT, { detail: list }));
}

const VipBadge = () => (
  <span className="badge bg-amber-500/15 text-amber-700 dark:text-amber-300"><Crown size={12} />VIP</span>
);

function Offers({ slot, onDone }: { slot: FreedSlot; onDone: () => void }) {
  const toast = useToast();
  const [offers, setOffers] = useState<any[] | null>(null);
  const [busy, setBusy] = useState(0);

  useEffect(() => {
    const q = new URLSearchParams({ start_at: slot.start_at, ...(slot.service_id ? { service_id: String(slot.service_id) } : {}),
      ...(slot.staff_id ? { staff_id: String(slot.staff_id) } : {}), ...(slot.duration_minutes ? { duration: String(slot.duration_minutes) } : {}) });
    api<any[]>(`/api/waitlist/offers?${q}`).then(setOffers).catch(() => setOffers([]));
  }, [slot]);

  async function give(o: any) {
    if (!o.fits && !o.overridable) return;
    if (!o.fits && !window.confirm(`${o.reason}\nبا این حال نوبت داده شود؟`)) return;
    setBusy(o.id);
    try {
      await api(`/api/waitlist/${o.id}/book`, { body: { start_at: slot.start_at, service_id: o.offer_service_id, staff_id: o.offer_staff_id,
        duration_minutes: o.offer_duration, allow_outside_hours: !o.fits } });
      toast(`نوبت ${formatJShort(slot.start_at)} به ${o.customer} داده شد؛ با مشتری تماس بگیرید و اطلاع دهید`);
      window.dispatchEvent(new Event(WAITLIST_CHANGED));
      onDone();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(0);
    }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-2xl bg-emerald-500/10 p-3 text-sm">
        <div className="font-bold text-emerald-700 dark:text-emerald-300">این زمان آزاد شد:</div>
        <div className="mt-1 font-extrabold">{formatJ(slot.start_at)}</div>
        <div className="muted text-xs">{slot.service ?? ""}{slot.duration_minutes ? ` · ${faDigits(slot.duration_minutes)} دقیقه` : ""}</div>
      </div>
      <div className="text-sm font-bold">کدام مشتری لیست انتظار (VIP) این نوبت را بگیرد؟</div>
      {offers === null ? <div className="muted text-sm">در حال بررسی…</div> : offers.length === 0 ? (
        <Empty text="کسی در لیست انتظار نیست" />
      ) : (
        <div className="space-y-2">
          {offers.map((o) => (
            <div key={o.id} className={`flex flex-wrap items-center justify-between gap-2 rounded-2xl border px-3 py-2 ${o.fits ? "" : "opacity-70"}`}
              style={{ borderColor: "var(--border)", background: "var(--surface)" }}>
              <div className="min-w-0">
                <div className="flex items-center gap-2 font-semibold">{o.customer}{o.vip && <VipBadge />}</div>
                <div className="muted text-xs">{[o.customer_mobile, o.offer_service ?? "هر خدمتی", o.staff].filter(Boolean).join(" · ")}
                  {o.same_service ? " · همان خدمت" : o.same_line ? " · همان لاین" : ""}</div>
                {o.preference && <div className="text-xs text-violet-600 dark:text-violet-300">ترجیح: {o.preference}</div>}
                <div className="muted text-[11px]">در انتظار از {formatJ(o.created_at, false)}</div>
                {!o.fits && <div className="text-xs text-rose-600">✕ {o.reason}</div>}
              </div>
              <button className="btn btn-sm btn-primary" disabled={busy > 0 || (!o.fits && !o.overridable)} onClick={() => give(o)}>
                <UserCheck size={14} />{busy === o.id ? "…" : "این نوبت به او داده شود"}
              </button>
            </div>
          ))}
        </div>
      )}
      <button className="btn w-full" onClick={onDone}>هیچ‌کدام - زمان خالی بماند</button>
    </div>
  );
}

/** Mounted once in the layout: shows the freed times one by one and lets the user pick a waiting customer. */
export function FreedSlotHost() {
  const toast = useToast();
  const [queue, setQueue] = useState<FreedSlot[]>([]);
  useEffect(() => {
    const h = (e: Event) => {
      const list = (e as CustomEvent<FreedSlot[]>).detail;
      const withWaiting = list.filter((s) => s.waiting > 0);
      list.filter((s) => s.waiting === 0).forEach((s) => toast(`زمان ${formatJShort(s.start_at)} آزاد شد (لیست انتظار خالی است)`, "info"));
      if (withWaiting.length) setQueue((q) => [...q, ...withWaiting]);
    };
    window.addEventListener(EVENT, h);
    return () => window.removeEventListener(EVENT, h);
  }, [toast]);
  const slot = queue[0];
  const next = () => setQueue((q) => q.slice(1));
  return (
    <Modal open={!!slot} onClose={next} title="نوبت آزاد شد - لیست انتظار">
      {slot && <Offers key={`${slot.appointment_id}-${slot.start_at}`} slot={slot} onDone={next} />}
    </Modal>
  );
}

function AddForm({ onDone }: { onDone: () => void }) {
  const toast = useToast();
  const services = useApi<any[]>("/api/services").data ?? [];
  const [cust, setCust] = useState<CustomerChoice>({});
  const [f, setF] = useState({ service_id: 0, staff_id: 0, vip: true, preference: "" });
  async function save() {
    try {
      await api("/api/waitlist", { body: { ...cust, ...f, service_id: f.service_id || null, staff_id: f.staff_id || null } });
      toast("به لیست انتظار اضافه شد");
      onDone();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }
  return (
    <div className="space-y-4">
      <Field label="مشتری"><CustomerPicker value={cust} onChange={setCust} /></Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="خدمت">
          <select className="input" value={f.service_id} onChange={(e) => setF({ ...f, service_id: Number(e.target.value), staff_id: 0 })}>
            <option value={0}>هر خدمتی</option>
            {services.map((s) => <option key={s.id} value={s.id}>{s.line} / {s.name}</option>)}
          </select>
        </Field>
        <Field label="پرسنل">
          <StaffSelect serviceId={f.service_id || undefined} value={f.staff_id || undefined} emptyLabel="فرقی ندارد"
            onChange={(id) => setF((x) => ({ ...x, staff_id: id ?? 0 }))} />
        </Field>
      </div>
      <Field label="ترجیح زمانی / توضیح" hint="مثلاً: فقط صبح‌ها، هر روز به جز جمعه">
        <input className="input" value={f.preference} onChange={(e) => setF({ ...f, preference: e.target.value })} />
      </Field>
      <label className="flex items-center gap-2 text-sm font-semibold">
        <input type="checkbox" checked={f.vip} onChange={(e) => setF({ ...f, vip: e.target.checked })} />
        مشتری VIP (در صورت کنسلی، اول به او پیشنهاد شود)
      </label>
      <button className="btn btn-primary w-full" disabled={!(cust.customer_id || cust.customer_name || cust.customer_mobile)} onClick={save}>افزودن به لیست انتظار</button>
    </div>
  );
}

function BookForm({ entry, onDone }: { entry: any; onDone: () => void }) {
  const toast = useToast();
  const [f, setF] = useState({ service_id: entry.service_id ?? 0, staff_id: entry.staff_id ?? 0, start_at: "", duration_minutes: 0 });
  const [bs, setBs] = useState<BookingState>({ ready: false, allowOutside: false });
  const services = useApi<any[]>("/api/services").data ?? [];
  async function save() {
    try {
      await api(`/api/waitlist/${entry.id}/book`, { body: { ...f, service_id: f.service_id || null, staff_id: f.staff_id || null,
        duration_minutes: f.duration_minutes || null, allow_outside_hours: bs.allowOutside } });
      toast(`نوبت ${entry.customer} ثبت شد`);
      onDone();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }
  return (
    <div className="space-y-4">
      <div className="font-semibold">{entry.customer} {entry.vip && <VipBadge />}</div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="خدمت">
          <select className="input" value={f.service_id} onChange={(e) => setF({ ...f, service_id: Number(e.target.value) })}>
            <option value={0}>—</option>
            {services.map((s) => <option key={s.id} value={s.id}>{s.line} / {s.name}</option>)}
          </select>
        </Field>
        <Field label="پرسنل">
          <StaffSelect serviceId={f.service_id || undefined} value={f.staff_id || undefined} emptyLabel="هر پرسنل آزاد"
            onChange={(id) => setF((x) => ({ ...x, staff_id: id ?? 0 }))} />
        </Field>
      </div>
      <Field label="زمان نوبت">
        <BookingFields serviceId={f.service_id || undefined} staffId={f.staff_id || undefined} value={f.start_at}
          onChange={(v) => setF((x) => ({ ...x, start_at: v }))} onStaff={(id) => setF((x) => ({ ...x, staff_id: id ?? 0 }))}
          duration={f.duration_minutes || undefined} onDuration={(m) => setF((x) => ({ ...x, duration_minutes: m }))}
          customerId={entry.customer_id} onState={setBs} />
      </Field>
      <button className="btn btn-primary w-full" disabled={!bs.ready} onClick={save}>ثبت نوبت</button>
    </div>
  );
}

/** Waiting list (VIP) card for the appointments page. */
export function WaitlistPanel({ onBooked }: { onBooked?: () => void }) {
  const toast = useToast();
  const { data, reload } = useApi<any[]>("/api/waitlist");
  const [add, setAdd] = useState(false);
  const [book, setBook] = useState<any>(null);
  useEffect(() => {
    window.addEventListener(WAITLIST_CHANGED, reload);
    return () => window.removeEventListener(WAITLIST_CHANGED, reload);
  }, [reload]);
  async function remove(w: any) {
    if (!window.confirm(`${w.customer} از لیست انتظار حذف شود؟`)) return;
    try {
      await api(`/api/waitlist/${w.id}`, { method: "DELETE" });
      reload();
    } catch (e: any) {
      toast(e.message, "error");
    }
  }
  return (
    <Card title={<span className="flex items-center gap-2"><Crown size={18} className="text-amber-500" />لیست انتظار / VIP {data?.length ? `(${faDigits(data.length)})` : ""}</span>}
      actions={<button className="btn btn-sm" onClick={() => setAdd(true)}><Plus size={14} />افزودن</button>}>
      <div className="muted mb-3 text-xs">هر وقت نوبتی لغو شود یا زودتر انجام شود، سیستم همین افراد را (اول VIPها) برای آن زمان پیشنهاد می‌دهد.</div>
      {!data?.length ? <Empty text="کسی در انتظار نیست" /> : (
        <div className="space-y-2">
          {data.map((w) => (
            <div key={w.id} className="flex flex-wrap items-center justify-between gap-2 rounded-2xl px-3 py-2" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
              <div>
                <div className="flex items-center gap-2 font-semibold">{w.customer}{w.vip && <VipBadge />}</div>
                <div className="muted text-xs">{[w.customer_mobile, w.line && w.service ? `${w.line} / ${w.service}` : "هر خدمتی", w.staff].filter(Boolean).join(" · ")}</div>
                {w.preference && <div className="text-xs text-violet-600 dark:text-violet-300">ترجیح: {w.preference}</div>}
              </div>
              <div className="flex gap-1">
                <button className="btn btn-sm btn-primary" onClick={() => setBook(w)}>نوبت بده</button>
                <button className="btn btn-ghost btn-sm" onClick={() => remove(w)} aria-label="حذف"><Trash2 size={15} /></button>
              </div>
            </div>
          ))}
        </div>
      )}
      <Modal open={add} onClose={() => setAdd(false)} title="افزودن به لیست انتظار">
        {add && <AddForm onDone={() => { setAdd(false); reload(); }} />}
      </Modal>
      <Modal open={!!book} onClose={() => setBook(null)} title="نوبت برای مشتری لیست انتظار">
        {book && <BookForm entry={book} onDone={() => { setBook(null); reload(); onBooked?.(); }} />}
      </Modal>
    </Card>
  );
}
