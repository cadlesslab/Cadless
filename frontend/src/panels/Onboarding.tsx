/** First-run onboarding tip. Dismissible; remembered in localStorage. */
import { useState } from "react";

import { IconButton } from "../components";

const KEY = "cadless-onboarded";

function seen(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

export function Onboarding() {
  const [dismissed, setDismissed] = useState(seen());
  if (dismissed) return null;

  function dismiss() {
    try {
      localStorage.setItem(KEY, "1");
    } catch {
      /* ignore */
    }
    setDismissed(true);
  }

  return (
    <div className="onboarding" role="note">
      <span className="onboarding-text">
        👋 Describe a part in plain English and hit <kbd>Generate</kbd> — or pick a starter below.
        Then orbit the model, switch views, tweak parameters, and export.
      </span>
      <IconButton label="Dismiss onboarding" onClick={dismiss}>
        ✕
      </IconButton>
    </div>
  );
}
