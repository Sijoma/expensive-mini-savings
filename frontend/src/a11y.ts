// Keyboard support for tab lists and radio groups (roving tabindex).

/** Index after an arrow/Home/End key, or null if the key does not move. */
export function nextIndex(key: string, index: number, count: number): number | null {
  switch (key) {
    case "ArrowRight":
    case "ArrowDown":
      return (index + 1) % count;
    case "ArrowLeft":
    case "ArrowUp":
      return (index - 1 + count) % count;
    case "Home":
      return 0;
    case "End":
      return count - 1;
    default:
      return null;
  }
}

/** Moves focus within `container` to the item at `index` (items selected by `selector`). */
export function focusItem(container: ParentNode | null, selector: string, index: number): void {
  const items = container?.querySelectorAll<HTMLElement>(selector);
  items?.[index]?.focus();
}
