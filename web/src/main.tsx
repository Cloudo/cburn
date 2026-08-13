import { Component, StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { LangProvider } from "./i18n";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import "./styles.css";

/** Ошибка отрисовки не должна оставлять чёрный экран: React снимает всё дерево,
 *  и без этого на дашборде не остаётся ни строчки о том, что случилось. */
class Boundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error): { error: Error } {
    return { error };
  }

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <main>
        <p className="empty-note">
          дашборд не отрисовался: {error.message}
          <br />
          если сервер запущен давно, а фронт пересобран — перезапустите cdash serve
        </p>
      </main>
    );
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Boundary>
      <LangProvider>
        <App />
      </LangProvider>
    </Boundary>
  </StrictMode>,
);
