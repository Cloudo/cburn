// What cburn costs itself, in the corner of the masthead. A speedometer that eats a core
// while showing the burn rate is a joke about itself, so the figure is kept in sight while
// the thing is being written - and only then: `import.meta.env.DEV` is false in the built
// bundle, so a person who merely uses cburn never sees it.

import { useEffect, useState } from "react";

import { loadSelfCost, type SelfCost as Cost } from "../lib/api";
import { useLang } from "../lib/i18n";

//: Often enough to catch a burst, rarely enough to stay out of the measurement itself.
const EVERY_MS = 2000;

export function SelfCost() {
  const { t } = useLang();
  const [cost, setCost] = useState<Cost | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const next = await loadSelfCost();
        if (alive) setCost(next);
      } catch {
        // The dev meter is the last thing that should shout: the server restarts under
        // one's hands all the time, and the next poll will find it.
      }
    };
    void tick();
    const timer = setInterval(tick, EVERY_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  if (!cost) return null;
  const { server, app } = cost;
  return (
    <span className="self-cost" title={t("self.hint")}>
      <span className="self-cost-part">
        <span className="self-cost-name">srv</span>
        {server.cpu_percent === null ? "-" : `${server.cpu_percent}%`}
        <span className="self-cost-ram">{server.rss_mb} MB</span>
      </span>
      {app && (
        <span className="self-cost-part">
          <span className="self-cost-name">app</span>
          {app.cpu_percent}%<span className="self-cost-ram">{app.rss_mb} MB</span>
        </span>
      )}
    </span>
  );
}
