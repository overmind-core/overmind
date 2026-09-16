import { renderToStaticMarkup } from "react-dom/server";

import { describe, expect, it } from "vitest";

import { Icon, type IconName } from "@/components/ui/icons";

describe("Icon registry", () => {
  it.each(Object.keys(Icon) as IconName[])("%s renders a square svg of paths", (name) => {
    const Glyph = Icon[name];
    const markup = renderToStaticMarkup(<Glyph className="size-4" />);
    expect(markup).toMatch(/^<svg [^>]*viewBox="0 0 (24 24|16 16)"/);
    expect(markup).toContain('class="size-4"');
    expect(markup).toContain("<path ");
  });
});
