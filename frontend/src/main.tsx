import { StrictMode, useCallback, useEffect, useState } from "react";
import ErrorBoundary from "./components/ErrorBoundary";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import "./index.css";
import { api, getToken, setToken } from "./lib/api";
import { AuthContext, type Toast, ToastContext, type User } from "./lib/hooks";
import Appointments from "./pages/Appointments";
import Assistant from "./pages/Assistant";
import Customers from "./pages/Customers";
import Dashboard from "./pages/Dashboard";
import Deposits from "./pages/Deposits";
import Expenses from "./pages/Expenses";
import Invoices from "./pages/Invoices";
import Ledger from "./pages/Ledger";
import Login from "./pages/Login";
import Reconciliation from "./pages/Reconciliation";
import Reports from "./pages/Reports";
import Scan from "./pages/Scan";
import PrintAppointments from "./pages/PrintAppointments";
import PrintInvoice from "./pages/PrintInvoice";
import SettingsPage from "./pages/Settings";
import StaffShares from "./pages/StaffShares";
import { Loading } from "./components/ui";

function VersionBanner() {
  const [server, setServer] = useState<string | null>(null);
  useEffect(() => {
    fetch("/api/health").then((r) => r.json()).then((h) => setServer(h.version)).catch(() => {});
  }, []);
  if (!server || server === __APP_VERSION__) return null;
  return (
    <div className="sticky top-0 z-[80] bg-rose-600 px-4 py-3 text-center text-sm font-bold text-white">
      نسخه سرور ({server}) با نسخه صفحه ({__APP_VERSION__}) یکی نیست: برنامه قدیمی هنوز در حال اجراست.
      همه پنجره‌های سیاه برنامه را ببندید و دوباره از میانبر Hesabdar اجرا کنید.
    </div>
  );
}

function App() {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
  }, []);

  useEffect(() => {
    if (!getToken()) return setReady(true);
    api<User>("/api/auth/me").then(setUser).catch(() => setToken(null)).finally(() => setReady(true));
  }, []);
  useEffect(() => {
    const h = () => setUser(null);
    window.addEventListener("hesabdar:logout", h);
    return () => window.removeEventListener("hesabdar:logout", h);
  }, []);

  const toast = useCallback((text: string, kind: Toast["kind"] = "ok") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.filter((x) => x.text !== text), { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 8000 : 4500);
  }, []);

  // never fail silently: show failed loads and unhandled action errors
  useEffect(() => {
    const onErr = (e: Event) => toast(`خطا: ${(e as CustomEvent).detail}`, "error");
    const onRej = (e: PromiseRejectionEvent) => toast(`خطا: ${e.reason?.message ?? e.reason}`, "error");
    window.addEventListener("hesabdar:error", onErr);
    window.addEventListener("unhandledrejection", onRej);
    return () => { window.removeEventListener("hesabdar:error", onErr); window.removeEventListener("unhandledrejection", onRej); };
  }, [toast]);

  if (!ready) return <Loading />;
  return (
    <AuthContext.Provider value={{ user, setUser, logout }}>
      <ToastContext.Provider value={toast}>
        <div className="no-print"><VersionBanner /></div>
        <ErrorBoundary>
        {!user ? (
          <Login />
        ) : (
          <BrowserRouter>
            <Routes>
              <Route path="print/appointments" element={<PrintAppointments />} />
              <Route path="print/invoice/:id" element={<PrintInvoice />} />
              <Route element={<Layout />}>
                <Route index element={<Dashboard />} />
                <Route path="invoices" element={<Invoices />} />
                <Route path="deposits" element={<Deposits />} />
                <Route path="appointments" element={<Appointments />} />
                <Route path="customers" element={<Customers />} />
                <Route path="reconciliation" element={<Reconciliation />} />
                <Route path="scan" element={<Scan />} />
                <Route path="assistant" element={<Assistant />} />
                <Route path="reports" element={<Reports />} />
                <Route path="staff" element={<StaffShares />} />
                <Route path="expenses" element={<Expenses />} />
                <Route path="ledger" element={<Ledger />} />
                <Route path="settings" element={<SettingsPage />} />
                <Route path="*" element={<Dashboard />} />
              </Route>
            </Routes>
          </BrowserRouter>
        )}
        </ErrorBoundary>
        <div className="pointer-events-none fixed bottom-4 left-4 z-[60] flex flex-col gap-2">
          {toasts.map((t) => (
            <div key={t.id} className={`fade-up pointer-events-auto rounded-2xl px-4 py-3 text-sm font-semibold text-white shadow-xl ${t.kind === "error" ? "bg-rose-500" : t.kind === "info" ? "bg-sky-500" : "bg-emerald-500"}`}>
              {t.text}
            </div>
          ))}
        </div>
      </ToastContext.Provider>
    </AuthContext.Provider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
