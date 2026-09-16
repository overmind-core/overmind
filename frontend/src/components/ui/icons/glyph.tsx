import type { SVGProps } from "react";

import { type GlyphName, glyphs } from "./glyphs";

export type GlyphProps = SVGProps<SVGSVGElement>;

/** The frame `pixelarticons/react` rendered: 24×24 viewBox, 2px inset paths in
 *  `currentColor`, props override the defaults. Call sites size with `className`. */
export const glyph = (name: GlyphName) => {
  const Glyph = (props: GlyphProps) => (
    <svg
      fill="currentColor"
      height={24}
      viewBox="0 0 24 24"
      width={24}
      xmlns="http://www.w3.org/2000/svg"
      {...props}
    >
      {glyphs[name].map((d) => (
        <path d={d} key={d} />
      ))}
    </svg>
  );
  Glyph.displayName = name;
  return Glyph;
};
