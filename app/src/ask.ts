// Viewer tabs ask the AI chat about a paragraph through a window event (the chat tab lives in App).
import type { ChatContext } from "./api";

export const ASK_EVENT = "spec-ask";

export function askAboutParagraph(ctx: ChatContext) {
  window.dispatchEvent(new CustomEvent(ASK_EVENT, { detail: ctx }));
}
