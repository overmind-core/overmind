// covers: console-logic

import { renderToStaticMarkup } from "react-dom/server";

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { Icon, type IconName } from "@/components/ui/icons";

describe("Icon registry", () => {
  it.each(Object.keys(Icon) as IconName[])("%s renders a square svg of paths", (name) => {
    const Glyph = Icon[name];
    const markup = renderToStaticMarkup(<Glyph className="size-4" />);
    const asset = markup.match(/<img[^>]*src="(\/brands\/[^" ]+\.svg)"/);
    const svg = asset ? readFileSync(`public${asset[1]}`, "utf8") : markup;
    const bounds = svg.match(/viewBox="0 0 (\d+) (\d+)"/);
    expect(bounds).not.toBeNull();
    expect(bounds?.[1]).toBe(bounds?.[2]);
    expect(markup).toContain('class="size-4"');
    expect(svg).toContain("<path ");
  });
});
