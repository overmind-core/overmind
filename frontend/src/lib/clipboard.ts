export function writeClipboardText(text: string | Promise<string>): Promise<void> {
  if (typeof navigator === "undefined" || !navigator.clipboard) {
    return Promise.reject(new Error("Clipboard unavailable"));
  }

  if (typeof ClipboardItem !== "undefined" && navigator.clipboard.write) {
    const content = Promise.resolve(text).then(
      (value) => new Blob([value], { type: "text/plain" })
    );
    return navigator.clipboard.write([new ClipboardItem({ "text/plain": content })]);
  }

  return Promise.resolve(text).then((value) => navigator.clipboard.writeText(value));
}
