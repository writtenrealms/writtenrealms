type EditorTab = Pick<
  Window,
  "closed" | "close" | "location" | "opener"
>;

type BrowserWindow = Pick<Window, "open">;

const pendingEditorTabs = new Map<string, EditorTab>();

export const isDirectEditRoomCommand = (command: unknown): boolean => (
  typeof command === "string"
  && /^\/edit(?:\s|$)/i.test(command.trim())
);

const detachOpener = (tab: EditorTab): void => {
  try {
    tab.opener = null;
  } catch {
    // Navigation is same-origin, but an unusual browser policy may make the
    // opener property immutable. The editor route itself remains safe.
  }
};

export const prepareEditRoomTab = (
  requestId: string,
  command: unknown,
  browserWindow: BrowserWindow = window,
): boolean => {
  if (!requestId || !isDirectEditRoomCommand(command)) return false;

  const existingTab = pendingEditorTabs.get(requestId);
  if (existingTab && !existingTab.closed) existingTab.close();
  pendingEditorTabs.delete(requestId);

  // Run while the submit event still carries user activation. Waiting for the
  // authoritative WebSocket response before opening a tab is commonly blocked
  // as a popup, so reserve a blank tab and navigate it after room resolution.
  const tab = browserWindow.open("about:blank", "_blank");
  if (!tab) return false;

  detachOpener(tab);
  pendingEditorTabs.set(requestId, tab);
  return true;
};

export const openResolvedEditRoomTab = (
  requestId: string | null,
  href: string,
  browserWindow: BrowserWindow = window,
): boolean => {
  const pendingTab = requestId
    ? pendingEditorTabs.get(requestId)
    : undefined;
  if (requestId) pendingEditorTabs.delete(requestId);

  if (pendingTab && !pendingTab.closed) {
    try {
      pendingTab.location.replace(href);
      return true;
    } catch {
      pendingTab.close();
    }
  }

  // This covers structured/aliased commands and a user-closing the reserved
  // tab. It may be blocked because resolution is asynchronous; callers should
  // surface a useful retry message when it returns null.
  const openedTab = browserWindow.open(href, "_blank");
  if (!openedTab) return false;
  detachOpener(openedTab);
  return true;
};

export const cancelEditRoomTab = (requestId: string | null): boolean => {
  if (!requestId) return false;
  const tab = pendingEditorTabs.get(requestId);
  pendingEditorTabs.delete(requestId);
  if (!tab || tab.closed) return false;
  tab.close();
  return true;
};

export const cancelAllEditRoomTabs = (): void => {
  for (const tab of pendingEditorTabs.values()) {
    if (!tab.closed) tab.close();
  }
  pendingEditorTabs.clear();
};
