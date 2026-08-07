/** Picking a tag by typing towards it, rather than by spelling it.
 *
 * A listing server compares a tag exactly, so a near miss — `mech` for
 * `mechanical` — answers with nothing and reads as an empty result rather
 * than as a typo. This control removes that failure by never letting a tag be
 * entered that does not exist: what is typed narrows a list, and what is
 * applied is always a value taken off that list.
 *
 * Chips were the other candidate and the numbers refused them. A page holds
 * fifty listings and tags are specific — `86 mm bore`, `M4` — so the distinct
 * count runs to the hundreds, which is more clutter than the text field this
 * replaces, not less.
 *
 * The tags on offer are the ones the arrived listings carry. A server does not
 * publish the whole set, so this is the honest extent of what can be
 * suggested; it grows as more pages arrive.
 */
import { useId, useState } from "react";

const MAX_SUGGESTIONS = 8;

export function TagPicker({
  available,
  selected,
  onChange,
}: {
  /** Tags carried by the listings on screen. */
  available: string[];
  selected: string[];
  onChange: (tags: string[]) => void;
}) {
  const [typed, setTyped] = useState("");
  const id = useId();

  // Case-insensitively, because the typing is a way of reaching a value rather
  // than the value itself — the exactness that matters happens on the server,
  // against whatever is picked here.
  const needle = typed.trim().toLowerCase();
  const matches = needle
    ? available
        .filter((tag) => !selected.includes(tag) && tag.toLowerCase().includes(needle))
        .slice(0, MAX_SUGGESTIONS)
    : [];

  function add(tag: string) {
    setTyped("");
    onChange([...selected, tag]);
  }

  return (
    <div className="tag-picker">
      <label htmlFor={id}>Tag</label>
      <input
        id={id}
        className="field"
        value={typed}
        placeholder="Start typing a tag…"
        autoComplete="off"
        onChange={(e) => setTyped(e.target.value)}
        onKeyDown={(e) => {
          // Enter takes the first suggestion and nothing else. Accepting the
          // raw text would put back exactly the near miss this control exists
          // to prevent.
          if (e.key !== "Enter") return;
          e.preventDefault();
          if (matches.length > 0) add(matches[0]);
        }}
      />
      {needle !== "" &&
        (matches.length > 0 ? (
          <ul className="tag-suggestions" role="listbox" aria-label="Matching tags">
            {matches.map((tag) => (
              <li key={tag}>
                <button role="option" aria-selected={false} onClick={() => add(tag)}>
                  {tag}
                </button>
              </li>
            ))}
          </ul>
        ) : (
          // Said rather than left blank: silence here reads as "still loading",
          // and the tags on offer are only the ones that have arrived.
          <p className="hint">No tag here matches that. Show more to widen what is offered.</p>
        ))}
      {selected.length > 0 && (
        <div className="chips">
          {selected.map((tag) => (
            <button
              key={tag}
              className="chip on"
              aria-label={`Remove tag ${tag}`}
              onClick={() => onChange(selected.filter((t) => t !== tag))}
            >
              {tag} ✕
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
