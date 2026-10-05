import {
  BarChart3, Bell, BookOpenCheck, Bot, CalendarClock, Camera, HandCoins, LayoutDashboard, LogOut, Menu, Moon, Receipt,
  ScanLine, Settings, Sun, Users, UsersRound, Wallet, X,
} from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { api } from "../lib/api";
import ErrorBoundary from "./ErrorBoundary";
import { FreedSlotHost } from "./Waitlist";
import { can, useAuth } from "../lib/hooks";
import { jlong, ROLES } from "../lib/format";

type Item = { to: string; label: string; icon: ReactNode; perm?: string };
const NAV: { group: string; items: Item[] }[] = [
  { group: "", items: [{ to: "/", label: "داشبورد", icon: <LayoutDashboard size={19} /> }] },
  {
    group: "عملیات روزانه",
    items: [
      { to: "/invoices", label: "فروش و فاکتور", icon: <Receipt size={19} /> },
      { to: "/deposits", label: "بیعانه‌ها", icon: <HandCoins size={19} /> },
      { to: "/appointments", label: "نوبت‌ها", icon: <CalendarClock size={19} /> },
      { to: "/customers", label: "مشتریان", icon: <Users size={19} /> },
    ],
  },
  {
    group: "هوشمند",
    items: [
      { to: "/reconciliation", label: "تطبیق رسید و بانک", icon: <ScanLine size={19} />, perm: "finance" },
      { to: "/scan", label: "اسکن دفتر فروش", icon: <Camera size={19} />, perm: "finance" },
      { to: "/assistant", label: "دستیار هوش مصنوعی", icon: <Bot size={19} /> },
    ],
  },
  {
    group: "مالی و گزارش",
    items: [
      { to: "/reports", label: "گزارش و پیش‌بینی", icon: <BarChart3 size={19} />, perm: "reports" },
      { to: "/staff", label: "پرسنل و سهم‌ها", icon: <UsersRound size={19} />, perm: "reports" },
      { to: "/expenses", label: "هزینه‌ها", icon: <Wallet size={19} />, perm: "finance" },
      { to: "/ledger", label: "دفاتر حسابداری", icon: <BookOpenCheck size={19} />, perm: "finance" },
    ],
  },
  { group: "", items: [{ to: "/settings", label: "تنظیمات و افزونه‌ها", icon: <Settings size={19} /> }] },
];

function useTheme() {
  const [dark, setDark] = useState(() => {
    try {
      const s = localStorage.getItem("hesabdar.theme");
      return s ? s === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    } catch {
      return false;
    }
  });
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try {
      localStorage.setItem("hesabdar.theme", dark ? "dark" : "light");
    } catch {
      /* ignore */
    }
  }, [dark]);
  return [dark, setDark] as const;
}

export default function Layout() {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const [dark, setDark] = useTheme();
  const [alerts, setAlerts] = useState(0);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);
  useEffect(() => {
    const load = () => api("/api/alerts").then((a: any[]) => setAlerts(a.filter((x) => !x.is_read).length)).catch(() => {});
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, []);

  const sidebar = (
    <nav className="flex h-full flex-col gap-1 p-4">
      <div className="mb-5 flex items-center gap-3 px-2">
        <img src="/favicon.svg" className="h-10 w-10" alt="" />
        <div>
          <div className="text-lg font-extrabold gradient-text">حسابدار سالن</div>
          <div className="muted text-[11px]">حسابداری هوشمند زیبایی</div>
        </div>
      </div>
      <div className="flex-1 space-y-4 overflow-y-auto">
        {NAV.map((g, i) => {
          const items = g.items.filter((it) => !it.perm || can(user, it.perm));
          if (!items.length) return null;
          return (
            <div key={i}>
              {g.group && <div className="muted mb-1 px-3 text-[11px] font-bold">{g.group}</div>}
              {items.map((it) => (
                <NavLink key={it.to} to={it.to} end={it.to === "/"}
                  className={({ isActive }) => `flex items-center gap-3 rounded-2xl px-3 py-2.5 text-sm font-semibold transition ${isActive ? "bg-gradient-to-l from-pink-500 to-violet-600 text-white shadow-lg shadow-violet-500/25" : "hover:bg-violet-500/10"}`}>
                  {it.icon}
                  {it.label}
                </NavLink>
              ))}
            </div>
          );
        })}
      </div>
      <div className="mt-3 rounded-2xl p-3" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
        <div className="flex items-center gap-3">
          <div className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-pink-400 to-violet-500 font-bold text-white">{(user?.full_name || user?.username || "?").slice(0, 1)}</div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-bold">{user?.full_name || user?.username}</div>
            <div className="muted text-[11px]">{ROLES[user?.role ?? ""]}</div>
          </div>
          <button className="btn btn-ghost btn-sm" onClick={logout} title="خروج"><LogOut size={16} /></button>
        </div>
      </div>
    </nav>
  );

  return (
    <div className="flex min-h-full">
      <aside className="sticky top-0 hidden h-screen w-72 shrink-0 border-l lg:block" style={{ borderColor: "var(--border)", background: "var(--surface)" }}>{sidebar}</aside>
      {open && (
        <div className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm lg:hidden" onClick={() => setOpen(false)}>
          <aside className="fade-up h-full w-72 max-w-[85vw]" style={{ background: "var(--surface-solid)" }} onClick={(e) => e.stopPropagation()}>
            <button className="btn btn-ghost btn-sm absolute left-3 top-3" onClick={() => setOpen(false)}><X size={18} /></button>
            {sidebar}
          </aside>
        </div>
      )}
      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-30 flex items-center justify-between gap-3 border-b px-4 py-3 backdrop-blur-xl sm:px-6" style={{ borderColor: "var(--border)", background: "color-mix(in srgb, var(--bg) 75%, transparent)" }}>
          <div className="flex items-center gap-2">
            <button className="btn btn-ghost btn-sm lg:hidden" onClick={() => setOpen(true)} aria-label="منو"><Menu size={20} /></button>
            <span className="muted hidden text-sm sm:inline">{jlong()}</span>
          </div>
          <div className="flex items-center gap-1">
            <NavLink to="/assistant" className="btn btn-sm btn-primary"><Bot size={16} /><span className="hidden sm:inline">بپرس</span></NavLink>
            <NavLink to="/settings?tab=alerts" className="btn btn-ghost btn-sm relative" aria-label="هشدارها">
              <Bell size={18} />
              {alerts > 0 && <span className="absolute -top-0.5 -right-0.5 grid h-4 min-w-4 place-items-center rounded-full bg-rose-500 px-1 text-[10px] font-bold text-white">{alerts}</span>}
            </NavLink>
            <button className="btn btn-ghost btn-sm" onClick={() => setDark(!dark)} aria-label="تغییر پوسته">{dark ? <Sun size={18} /> : <Moon size={18} />}</button>
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
          <ErrorBoundary key={loc.pathname + loc.search}>
            <Outlet />
          </ErrorBoundary>
          <FreedSlotHost />
        </main>
      </div>
    </div>
  );
}
