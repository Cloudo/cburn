// A mouse wheel scrolls the webview in whole notches: the page jumps a hundred pixels at a
// time, and reading it is like turning a ratchet. WebKit - the desktop window and Safari -
// does not animate that jump the way Chromium does, so the notch is eased here instead.
//
// Two things are deliberately left alone. A trackpad already glides: its events arrive as a
// fine stream, while a mouse sends multiples of 120 in `wheelDeltaY`, and that is the whole
// test. And a wheel over a list with a scrollbar of its own (the palettes, a widget body,
// the turn feed) belongs to that list, not to the page.

//: How much of the remaining distance is covered per frame. Higher is snappier; at 0.22 a
//: notch lands in some ten frames, short enough not to feel like the page lags behind.
const EASE = 0.22;

//: Below this the animation is over: half a pixel is not a movement.
const DONE = 0.5;

function pageScroller(): HTMLElement {
  return document.scrollingElement as HTMLElement;
}

/** Does something between the cursor and the page scroll on its own in this direction? */
function ownScroller(node: EventTarget | null, delta: number): boolean {
  let element = node instanceof Element ? node : null;
  const page = pageScroller();
  while (element && element !== page) {
    const style = getComputedStyle(element);
    const scrolls = /auto|scroll/.test(style.overflowY);
    if (scrolls && element.scrollHeight > element.clientHeight) {
      const below = element.scrollHeight - element.clientHeight - element.scrollTop;
      const room = delta < 0 ? element.scrollTop : below;
      if (room > 1) return true;
    }
    element = element.parentElement;
  }
  return false;
}

/** A mouse notch, not a trackpad glide: WebKit and Blink report whole multiples of 120. */
function isNotch(event: WheelEvent): boolean {
  const raw = (event as WheelEvent & { wheelDeltaY?: number }).wheelDeltaY;
  return typeof raw === "number" && raw !== 0 && raw % 120 === 0;
}

/** Ease the page under a mouse wheel. Returns the undo, so the caller can stop it. */
export function smoothWheel(): () => void {
  let target: number | null = null;
  let frame = 0;

  const step = () => {
    const page = pageScroller();
    if (target === null) return;
    const distance = target - page.scrollTop;
    if (Math.abs(distance) < DONE) {
      page.scrollTop = target;
      target = null;
      frame = 0;
      return;
    }
    page.scrollTop += distance * EASE;
    frame = requestAnimationFrame(step);
  };

  const onWheel = (event: WheelEvent) => {
    if (event.defaultPrevented || event.ctrlKey || event.deltaY === 0) return;
    if (!isNotch(event) || ownScroller(event.target, event.deltaY)) return;

    const page = pageScroller();
    const limit = page.scrollHeight - page.clientHeight;
    if (limit <= 0) return;

    // A fresh burst starts from where the page actually stands: it may have been dragged
    // by the scrollbar or by a keystroke while we were idle.
    const from = target === null ? page.scrollTop : target;
    const next = Math.min(Math.max(from + event.deltaY, 0), limit);
    if (next === page.scrollTop && target === null) return;

    event.preventDefault();
    target = next;
    if (!frame) frame = requestAnimationFrame(step);
  };

  window.addEventListener("wheel", onWheel, { passive: false });
  return () => {
    window.removeEventListener("wheel", onWheel);
    if (frame) cancelAnimationFrame(frame);
  };
}
