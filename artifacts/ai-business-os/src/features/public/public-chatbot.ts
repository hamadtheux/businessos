export const PUBLIC_CHATBOT_LOADER_URL =
  "https://9dbrain.com/widget-loader.js";
export const PUBLIC_CHATBOT_WIDGET_ID =
  "-m9auDA_XqXcj7943GayIrDyoVLpLq0lXO1gPsC-Tfk";

const loaderSelector =
  `script[src="${PUBLIC_CHATBOT_LOADER_URL}"][data-widget-id="${PUBLIC_CHATBOT_WIDGET_ID}"]`;
const widgetHostSelector =
  `[data-aibos-widget-host="${PUBLIC_CHATBOT_WIDGET_ID}"]`;
const activeMounts = new WeakMap<Document, number>();

function removePublicChatbotArtifacts(targetDocument: Document) {
  targetDocument
    .querySelectorAll(`${loaderSelector}, ${widgetHostSelector}`)
    .forEach((element) => element.remove());
}

export function mountPublicLandingChatbot(
  targetDocument: Document = document,
): () => void {
  activeMounts.set(
    targetDocument,
    (activeMounts.get(targetDocument) ?? 0) + 1,
  );

  let loader =
    targetDocument.querySelector<HTMLScriptElement>(loaderSelector);

  if (!loader) {
    loader = targetDocument.createElement("script");
    loader.src = PUBLIC_CHATBOT_LOADER_URL;
    loader.setAttribute("data-widget-id", PUBLIC_CHATBOT_WIDGET_ID);
    loader.async = true;
    targetDocument.body.appendChild(loader);
  }

  // If an async script finishes after its page has unmounted, remove any host
  // that it created. A newer active public mount keeps the same widget alive.
  loader.addEventListener(
    "load",
    () => {
      if (!activeMounts.has(targetDocument)) {
        removePublicChatbotArtifacts(targetDocument);
      }
    },
    { once: true },
  );

  let unmounted = false;
  return () => {
    if (unmounted) return;
    unmounted = true;

    const remainingMounts =
      (activeMounts.get(targetDocument) ?? 1) - 1;
    if (remainingMounts > 0) {
      activeMounts.set(targetDocument, remainingMounts);
      return;
    }

    activeMounts.delete(targetDocument);
    removePublicChatbotArtifacts(targetDocument);
  };
}
