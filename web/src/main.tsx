import { Component, StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { detect, translate } from "./dict";
import { LangProvider } from "./i18n";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import "./styles.css";

/** A render error must not leave a black screen: React unmounts the whole tree,
 *  and without this the dashboard keeps not a line about what happened. */
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
          {translate(detect(), "boundary.title", { message: error.message })}
          <br />
          {translate(detect(), "boundary.hint")}
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
