import { Component, type ReactNode } from "react";

/** Shows a friendly message instead of a blank page when a screen crashes. */
export default class ErrorBoundary extends Component<{ children: ReactNode }, { error: string | null }> {
  state = { error: null as string | null };
  static getDerivedStateFromError(e: Error) {
    return { error: e.message };
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="card m-6 space-y-3 p-6">
        <div className="text-lg font-bold text-rose-600">این صفحه با خطا روبه‌رو شد</div>
        <div className="muted text-sm" dir="ltr">{this.state.error}</div>
        <div className="text-sm">اگر برنامه را تازه به‌روزرسانی کرده‌اید، پنجره سیاه برنامه را ببندید و دوباره اجرا کنید، سپس Ctrl+F5 بزنید.</div>
        <button className="btn" onClick={() => { this.setState({ error: null }); location.reload(); }}>بارگذاری دوباره</button>
      </div>
    );
  }
}
