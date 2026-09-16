import type { SVGProps } from "react";

type GlyphProps = SVGProps<SVGSVGElement>;

/** 24×24 grid with a symmetric 2px inset, `currentColor` fill: the frame every glyph
 *  below is drawn on. Call sites size with `className`. */
const Glyph = (props: GlyphProps) => (
  <svg
    fill="currentColor"
    height={24}
    viewBox="0 0 24 24"
    width={24}
    xmlns="http://www.w3.org/2000/svg"
    {...props}
  />
);

export const Algorithm = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M11 16h4v2h-4zm-8 0h4v2H3zm16 0h4v2h-4zM9 16h2v6H9zm-8 0h2v6H1zm16 0h2v6h-2z" />
    <path d="M9 20h6v2H9zm-8 0h6v2H1zm16 0h6v2h-6z" />
    <path d="M13 16h2v6h-2zm-8 0h2v6H5zm16 0h2v6h-2zM8 8h8v2H8z" />
    <path d="M8 2h2v8H8z" />
    <path d="M8 2h8v2H8z" />
    <path d="M14 2h2v8h-2zM3 14h2v3H3zm2-2h14v2H5zm14 2h2v3h-2z" />
    <path d="M11 9h2v9h-2zm5-4h2v2h-2zm-5-5h2v2h-2zM6 5h2v2H6z" />
  </Glyph>
);

export const Analytics = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M4 2h16v2H4zm0 18h16v2H4zM2 4h2v16H2zm18 0h2v16h-2zm-9 8h2v6h-2zm-4 2h2v4H7zm8-8h2v12h-2z" />
  </Glyph>
);

export const Anchor = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M10 2h4v2h-4zm0 6h4v2h-4zM8 4h2v4H8zm6 0h2v4h-2z" />
    <path d="M11 9h2v12h-2z" />
    <path d="M5 20h14v2H5zm-2-8h2v8H3zm16 0h2v8h-2zM5 12h2v2H5zm12 0h2v2h-2z" />
  </Glyph>
);

export const ArrowBarLeft = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M4 4v16H2V4zm18 7v2H6v-2zm-12 2v2H8v-2zm2 2v2h-2v-2zm2 2v2h-2v-2zm-4-8v2H8V9zm2-2v2h-2V7zm2-2v2h-2V5z" />
  </Glyph>
);

export const ArrowBarRight = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M20 4v16h2V4zM2 11v2h16v-2zm12 2v2h2v-2zm-2 2v2h2v-2zm-2 2v2h2v-2zm4-8v2h2V9zm-2-2v2h2V7zm-2-2v2h2V5z" />
  </Glyph>
);

export const ArrowDown = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M13 12h6v2h-2v2h-2v2h-2v2h-2v-2H9v-2H7v-2H5v-2h6V4h2v8Z" />
  </Glyph>
);

export const ArrowLeft = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M20 11v2H4v-2zM8 13v2H6v-2zm2 2v2H8v-2zm2 2v2h-2v-2zm-4-6V9H6v2z" />
    <path d="M10 15V7H8v8zm2 2V5h-2v12z" />
  </Glyph>
);

export const ArrowRight = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M4 11v2h16v-2zm12 2v2h2v-2zm-2 2v2h2v-2zm-2 2v2h2v-2zm4-6V9h2v2z" />
    <path d="M14 15V7h2v8zm-2 2V5h2v12z" />
  </Glyph>
);

export const ArrowUp = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M11 20h2V4h-2zm2-12h2V6h-2zm2 2h2V8h-2zm2 2h2v-2h-2zm-6-4H9V6h2z" />
    <path d="M15 10H7V8h8zm2 2H5v-2h12z" />
  </Glyph>
);

export const AspectRatio = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M4 4h16v2H4zM2 6h2v12H2zm2 12h16v2H4zM20 6h2v12h-2zM6 8h4v2H6zm8 6h4v2h-4zm-8-4h2v2H6zm10 2h2v2h-2z" />
  </Glyph>
);

export const AvatarCircleX = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M8 20h6v2H6v-2H4v-2h4v2Zm10 2h-2v-2h2v2Zm4 0h-2v-2h2v2Zm-2-2h-2v-2h2v2ZM4 18H2V6h2v12Zm10 0H8v-2h6v2Zm4 0h-2v-2h2v2Zm4 0h-2v-2h2v2Zm-8-4h-4v-2h4v2Zm8 0h-2V6h2v8Zm-12-2H8V8h2v4Zm6 0h-2V8h2v4Zm-2-4h-4V6h4v2ZM6 6H4V4h2v2Zm14 0h-2V4h2v2Zm-2-2H6V2h12v2Z" />
  </Glyph>
);

export const Bell = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M9 2h6v2H9zM7 4h2v2H7zm8 0h2v2h-2zM5 6h2v7H5zm12 0h2v7h-2zM3 13h2v4H3zm16 0h2v4h-2z" />
    <path d="M3 15h18v2H3zm5 3h2v2H8zm6 0h2v2h-2zm-4 2h4v2h-4z" />
  </Glyph>
);

export const Binary = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M7 3h2v2H7zm8 10h2v2h-2zM5 5h2v4H5zm8 10h2v4h-2zM9 5h2v4H9zm8 10h2v4h-2zM7 9h2v2H7zm8 10h2v2h-2zM13 3h4v2h-4zM5 13h4v2H5zm10-8h2v4h-2zM7 15h2v4H7zm6-6h6v2h-6zM5 19h6v2H5z" />
  </Glyph>
);

export const Blocks = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M15 1h6v2h-6zm-2 2h2v6h-2zm2 6h6v2h-6zm6-6h2v6h-2zM3 5h6v2H3zM1 7h2v14H1zm2 14h14v2H3zm14-6h2v6h-2zM3 13h14v2H3z" />
    <path d="M9 7h2v14H9z" />
  </Glyph>
);

export const BookOpen = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M2 3h9v2H2zM0 19h11v2H0zM13 3h9v2h-9zm0 16h11v2H13zM11 5h2v18h-2zM0 5h2v14H0zm22 0h2v14h-2zm-7 2h5v2h-5zm0 4h5v2h-5zm0 4h2v2h-2z" />
  </Glyph>
);

export const Box = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M14 4h4v2h-4zm-4-2h4v2h-4zM6 8h4v2H6zm0 10h4v2H6zm4-8h4v2h-4zm0 10h4v2h-4zm4-12h4v2h-4zm0 10h4v2h-4zM6 4h4v2H6zM2 6h4v2H2zm0 10h4v2H2zM18 6h4v2h-4zm0 10h4v2h-4z" />
    <path d="M2 6h2v12H2zm18 0h2v12h-2zm-8 6h2v8h-2z" />
  </Glyph>
);

export const BoxPlus = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M14 4h4v2h-4zm-4-2h4v2h-4zM6 8h4v2H6zm0 10h4v2H6zm4-8h4v2h-4zm0 10h4v2h-4zm4-12h4v2h-4zM6 4h4v2H6zM2 6h4v2H2zm0 10h4v2H2zM18 6h4v2h-4z" />
    <path d="M2 6h2v12H2zm18 0h2v8h-2zm-8 6h2v8h-2zm6 4h2v6h-2z" />
    <path d="M16 18h6v2h-6z" />
  </Glyph>
);

export const Braces = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M6 4h4v2H6zm12 0h-4v2h4zM6 20h4v-2H6zm12 0h-4v-2h4zM4 6h2v5H4zm16 0h-2v5h2zM4 18h2v-5H4zm16 0h-2v-5h2zM2 11h2v2H2zm20 0h-2v2h2z" />
  </Glyph>
);

export const Bulletlist = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M10 5h12v2H10zm0 4h8v2h-8zm0 4h12v2H10zm0 4h8v2h-8zm-4-6H4V9h2v2ZM4 9H2V7h2v2Zm4 0H6V7h2v2ZM6 7H4V5h2v2Zm-2 6h2v2H4zm0 4h2v2H4zm-2 0v-2h2v2zm4 0v-2h2v2z" />
  </Glyph>
);

export const Cable = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M11 20H6v-2h5v2Zm6-4h4v-2h2v4h-1v2h-2v-2h-2v2h-2v-2h-1v-4h2v2ZM7 12H6v6H4v-6H3v-2h4v2Zm6 6h-2V5h2v13Zm8-4h-4v-2h1V5h2v7h1v2ZM4 6h2V4h2v2h1v4H7V8H3v2H1V6h1V4h2v2Zm14-1h-5V3h5v2Z" />
  </Glyph>
);

export const Cancel = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M18 22H6v-2h12v2ZM6 20H4v-2h2v2Zm14 0h-2v-2h2v2ZM4 18H2V6h2v12Zm4 0H6v-2h2v2Zm14 0h-2V6h2v12Zm-12-2H8v-2h2v2Zm2-2h-2v-2h2v2Zm2-2h-2v-2h2v2Zm2-2h-2V8h2v2Zm2-2h-2V6h2v2ZM6 6H4V4h2v2Zm14 0h-2V4h2v2Zm-2-2H6V2h12v2Z" />
  </Glyph>
);

export const Chart = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M20 22H4v-2h16v2ZM4 20H2V4h2v16Zm18 0h-2V4h2v16ZM9 17H7v-6h2v6Zm4 0h-2V7h2v10Zm4 0h-2v-4h2v4Zm3-13H4V2h16v2Z" />
  </Glyph>
);

export const ChartBarBig = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M22 22H4v-2h18v2ZM4 20H2V2h2v18Zm12-2H8v-2h8v2Zm-8-2H6v-3h2v3Zm10 0h-2v-3h2v3Zm-2-3H8v-2h8v2Zm2-4H8V7h10v2ZM8 7H6V4h2v3Zm12 0h-2V4h2v3Zm-2-3H8V2h10v2Z" />
  </Glyph>
);

export const ChartColumnStacked = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M22 22H4v-2h18v2ZM4 20H2V2h2v18Zm7-2H8v-2h3v2Zm9 0h-3v-2h3v2ZM8 12h3V8h2v8h-2v-2H8v2H6V8h2v4Zm9-4h3V6h2v10h-2v-6h-3v6h-2V6h2v2Zm-6 0H8V6h3v2Zm9-2h-3V4h3v2Z" />
  </Glyph>
);

export const Check = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M10 18H8v-2h2v2Zm-2-2H6v-2h2v2Zm4-2v2h-2v-2h2Zm-6 0H4v-2h2v2Zm8 0h-2v-2h2v2Zm2-2h-2v-2h2v2Zm2-2h-2V8h2v2Zm2-2h-2V6h2v2Z" />
  </Glyph>
);

export const Checkbox = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M20 22H4v-2h16v2ZM4 20H2V4h2v16Zm18 0h-2V4h2v16ZM20 4H4V2h16v2Z" />
  </Glyph>
);

export const CheckDouble = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M7 18H5v-2h2v2Zm6 0h-2v-2h2v2Zm-8-2H3v-2h2v2Zm4 0H7v-2h2v2Zm6-2v2h-2v-2h2ZM3 14H1v-2h2v2Zm8 0H9v-2h2v2Zm6 0h-2v-2h2v2Zm-4-2h-2v-2h2v2Zm6 0h-2v-2h2v2Zm-4-2h-2V8h2v2Zm6 0h-2V8h2v2Zm-4-2h-2V6h2v2Zm6 0h-2V6h2v2Z" />
  </Glyph>
);

export const ChevronDown = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M13 16h-2v-2h2v2Zm-2-2H9v-2h2v2Zm4 0h-2v-2h2v2Zm-6-2H7v-2h2v2Zm8 0h-2v-2h2v2ZM7 10H5V8h2v2Zm12 0h-2V8h2v2Z" />
  </Glyph>
);

export const ChevronLeft = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M8 13v-2h2v2H8Zm2-2V9h2v2h-2Zm0 4v-2h2v2h-2Zm2-6V7h2v2h-2Zm0 8v-2h2v2h-2Zm2-10V5h2v2h-2Zm0 12v-2h2v2h-2Z" />
  </Glyph>
);

export const ChevronRight = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M16 13v-2h-2v2h2Zm-2-2V9h-2v2h2Zm0 4v-2h-2v2h2Zm-2-6V7h-2v2h2Zm0 8v-2h-2v2h2ZM10 7V5H8v2h2Zm0 12v-2H8v2h2Z" />
  </Glyph>
);

export const ChevronsVertical = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M13 20h-2v-2h2v2Zm-2-2H9v-2h2v2Zm4 0h-2v-2h2v2Zm-6-2H7v-2h2v2Zm8-2v2h-2v-2h2Zm-8-4H7V8h2v2Zm8 0h-2V8h2v2Zm-6-2H9V6h2v2Zm4 0h-2V6h2v2Zm-2-2h-2V4h2v2Z" />
  </Glyph>
);

export const ChevronUp = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M13 8h-2v2h2V8Zm-2 2H9v2h2v-2Zm4 0h-2v2h2v-2Zm-6 2H7v2h2v-2Zm8 0h-2v2h2v-2ZM7 14H5v2h2v-2Zm12 0h-2v2h2v-2Z" />
  </Glyph>
);

export const Circle = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M18 22H6v-2h12v2ZM6 20H4v-2h2v2Zm14 0h-2v-2h2v2ZM4 18H2V6h2v12Zm18 0h-2V6h2v12ZM6 6H4V4h2v2Zm14 0h-2V4h2v2Zm-2-2H6V2h12v2Z" />
  </Glyph>
);

export const CircleQuestion = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M18 22H6V20H18V22ZM6 20H4V18H6V20ZM20 20H18V18H20V20ZM4 18H2V6H4V18ZM13 18H11V16H13V18ZM22 18H20V6H22V18ZM15 13H13V15H11V11H15V13ZM17 11H15V8H17V11ZM9 10H7V8H9V10ZM15 8H9V6H15V8ZM6 6H4V4H6V6ZM20 6H18V4H20V6ZM18 4H6V2H18V4Z" />
  </Glyph>
);

export const Clipboard = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M18 22H6v-2h12v2ZM6 20H4V6h2v14Zm14 0h-2V6h2v14ZM16 2v2h2v2h-2v2H8V6H6V4h2V2h8Zm-6 2v2h4V4h-4Z" />
  </Glyph>
);

export const Clock = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M18 22H6v-2h12v2ZM6 20H4v-2h2v2Zm14 0h-2v-2h2v2ZM4 18H2V6h2v12Zm18 0h-2V6h2v12Zm-5-1h-2v-2h2v2Zm-2-2h-2v-2h2v2Zm-2-2h-2V6h2v7ZM6 6H4V4h2v2Zm14 0h-2V4h2v2Zm-2-2H6V2h12v2Z" />
  </Glyph>
);

export const Coins = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M18 22h-6v-2h6v2Zm-6-2h-2v-2h2v2Zm8 0h-2v-2h2v2Zm-8-4h-2v2H8v-2H6v-2h6v2Zm5 2h-2v-4h-3v-2h2V6h2v2h2v2h-2v2h1v6Zm5 0h-2v-6h2v6ZM6 14H4v-2h2v2Zm-2-2H2V6h2v6Zm7 0H9V8H7V6h4v6Zm9 0h-2v-2h2v2ZM6 6H4V4h2v2Zm8 0h-2V4h2v2Zm-2-2H6V2h6v2Z" />
  </Glyph>
);

export const Comment = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M22 22h-2v-2h-2v-2h2V4h2v18Zm-4-4H4v-2h14v2ZM4 16H2V4h2v12ZM20 4H4V2h16v2Z" />
  </Glyph>
);

export const Copy = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M20 22H8v-2h12v2ZM8 20H6v-2H4v-2h2V8h2v12Zm14 0h-2V8h2v12ZM4 16H2V4h2v12ZM18 6h2v2H8V6h8V4h2v2Zm-2-2H4V2h12v2Z" />
  </Glyph>
);

export const Cpu = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M19 21h-2v2h-2v-2h-2v2h-2v-2H9v2H7v-2H5v-2h14v2ZM5 19H3v-2H1v-2h2v-2H1v-2h2V9H1V7h2V5h2v14ZM21 7h2v2h-2v2h2v2h-2v2h2v2h-2v2h-2V5h2v2Zm-6 10H9v-2h6v2Zm-6-2H7V9h2v6Zm8 0h-2V9h2v6Zm-2-6H9V7h6v2ZM9 3h2V1h2v2h2V1h2v2h2v2H5V3h2V1h2v2Z" />
  </Glyph>
);

export const Database = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M16 20v2H8v-2h8Zm-8 0H4v-2h4v2Zm12 0h-4v-2h4v2ZM4 10h4v2H4v2h4v2H4v2H2V6h2v4Zm18 8h-2v-2h-4v2H8v-2h8v-2h4v-2h-4v2H8v-2h8v-2h4V6h2v12ZM8 6H4V4h4v2Zm12 0h-4V4h4v2Zm-4-2H8V2h8v2Z" />
  </Glyph>
);

export const DatabaseZap = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M14 22H8v-2h6v2Zm4 0h-2v-2h2v2ZM8 20H4v-2h4v2Zm12 0h-2v-2h-4v-2h2v-2h2v2h4v2h-2v2ZM4 10h4v2H4v2h4v2H4v2H2V6h2v4Zm8 8H8v-2h4v2Zm2-4H8v-2h6v2Zm6 0h-2v-2h2v2Zm2-4h-2V6h2v4ZM8 6H4V4h4v2Zm12 0h-4V4h4v2Zm-4-2H8V2h8v2Z" />
  </Glyph>
);

export const Download = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M19 21H5v-2h14v2ZM5 19H3v-4h2v4Zm16 0h-2v-4h2v4Zm-8-8h4v2h-2v2h-2v2h-2v-2H9v-2H7v-2h4V3h2v8Z" />
  </Glyph>
);

export const Expand = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M15 19h-2v2h-2v-2H9v-2H7v-2h10v2h-2v2Zm5-6H4v-2h16v2Zm-7-8h2v2h2v2H7V7h2V5h2V3h2v2Z" />
  </Glyph>
);

export const ExternalLink = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M17 21H5v-2h12v2ZM5 19H3V7h2v12Zm14 0h-2v-6h2v6Zm-8-4H9v-2h2v2Zm2-2h-2v-2h2v2Zm2-2h-2V9h2v2Zm6 0h-2V7h-2V5h-4V3h8v8Zm-4-2h-2V7h2v2Zm-6-2H5V5h6v2Z" />
  </Glyph>
);

export const Eye = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M16 20H8v-2h8v2Zm-8-2H4v-2h4v2Zm12 0h-4v-2h4v2ZM4 16H2v-2h2v2Zm10-6h-2v2h2v-2h2v4h-2v2h-4v-2H8v-4h2V8h4v2Zm8 6h-2v-2h2v2ZM2 14H0v-4h2v4Zm22 0h-2v-4h2v4ZM4 10H2V8h2v2Zm18 0h-2V8h2v2ZM8 8H4V6h4v2Zm12 0h-4V6h4v2Zm-4-2H8V4h8v2Z" />
  </Glyph>
);

export const EyeOff = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M22 22h-2v-2h2v2Zm-6-2H8v-2h8v2Zm4 0h-2v-2h2v2ZM8 18H4v-2h4v2Zm10 0h-2v-2h2v2ZM4 16H2v-2h2v2Zm6-6h2v2h2v2h2v2h-6v-2H8V8h2v2Zm12 6h-2v-2h2v2ZM2 14H0v-4h2v4Zm22 0h-2v-4h2v4Zm-8-2h-2v-2h2v2ZM4 10H2V8h2v2Zm10 0h-2V8h2v2Zm8 0h-2V8h2v2ZM6 6h2v2H4V4h2v2Zm14 2h-4V6h4v2Zm-4-2h-6V4h6v2ZM4 4H2V2h2v2Z" />
  </Glyph>
);

export const Files = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M15 23H5v-2h10v2ZM5 21H3V7h2v14Zm12 0h-2v-2H9v-2h10v2h-2v2Zm-8-4H7V7H5V5h2V3h2v14Zm8-14h-2v4h4V5h2v12h-2V9h-6V3H9V1h8v2Zm2 2h-2V3h2v2Z" />
  </Glyph>
);

export const FileText = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M18 22H6v-2h12v2ZM6 20H4V4h2v16ZM16 4h-2v4h4V6h2v14h-2V10h-6V4H6V2h10v2Zm0 14H8v-2h8v2Zm0-4H8v-2h8v2Zm-6-4H8V8h2v2Zm8-4h-2V4h2v2Z" />
  </Glyph>
);

export const Filter = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M11 20h2v2H9V12h2v8Zm4 0h-2v-8h2v8Zm-6-8H7v-2h2v2Zm8 0h-2v-2h2v2ZM7 10H5V8h2v2Zm12 0h-2V8h2v2Zm2-2h-2V4H5v4H3V2h18v6Z" />
  </Glyph>
);

export const Folder = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M20 20H4v-2h16v2ZM4 18H2V6h2v12Zm18 0h-2V8h2v10ZM20 8H10V6H4V4h8v2h8v2Z" />
  </Glyph>
);

export const FolderPlus = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M20 18h2v2h-2v2h-2v-2h-2v-2h2v-2h2v2Zm-6 2H4v-2h10v2ZM4 18H2V6h2v12Zm18-4h-2V8h2v6ZM12 6h8v2H10V6H4V4h8v2Z" />
  </Glyph>
);

export const GitBranch = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M8 22H4v-2h4v2Zm-4-2H2v-4h2v4Zm6 0H8v-4h2v4Zm7-1h-5v-2h5v2Zm2-2h-2v-5h2v5ZM8 16H4v-2h4v2Zm-1-4H5V2h2v10Zm13-2h-4V8h4v2Zm-4-2h-2V4h2v4Zm6 0h-2V4h2v4Zm-2-4h-4V2h4v2Z" />
  </Glyph>
);

export const GitPullRequest = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M7 12v10H5V12h2Zm13 10h-4v-2h4v2Zm-4-2h-2v-4h2v4Zm6 0h-2v-4h2v4Zm-2-4h-4v-2h4v2ZM8 10H4V8h4v2ZM4 8H2V4h2v4Zm6 0H8V4h2v4Zm7-1h-5V5h5v2ZM8 4H4V2h4v2Z" />
  </Glyph>
);

export const Grid3x2 = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M14 20v-7h-4v7h4ZM10 4v7h4V4h-4Zm6 7h4V4h2v16h-2v-7h-4v7h4v2H4v-2h4v-7H4v7H2V4h2v7h4V4H4V2h16v2h-4v7Z" />
  </Glyph>
);

export const Grid3x3 = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M14 20v-4h-4v4h4ZM4 14h4v-4H4v4Zm12 0h4v-4h-4v4Zm-6 0h4v-4h-4v4Zm0-10v4h4V4h-4Zm6 4h4V4h2v16h-2v-4h-4v4h4v2H4v-2h4v-4H4v4H2V4h2v4h4V4H4V2h16v2h-4v4Z" />
  </Glyph>
);

export const Hash = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M14 14v-4h-4v4h4Zm7 2h-6v5h-2v-5H9v5H7v-5H3v-2h5v-4H3V8h6V3h2v5h4V3h2v5h4v2h-5v4h5v2Z" />
  </Glyph>
);

export const Home = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M10 20h4v-6h2v6h4v2H4v-2h4v-6h2v6Zm-6 0H2V10h2v10Zm18 0h-2V10h2v10Zm-8-6h-4v-2h4v2Zm-8-4H4V8h2v2Zm14 0h-2V8h2v2ZM8 8H6V6h2v2Zm10 0h-2V6h2v2Zm-8-2H8V4h2v2Zm6 0h-2V4h2v2Zm-2-2h-4V2h4v2Z" />
  </Glyph>
);

export const InfoBox = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M20 22H4v-2h16v2ZM4 20H2V4h2v16Zm18 0h-2V4h2v16Zm-9-9v6h-2v-6h2Zm0-2h-2V7h2v2Zm7-5H4V2h16v2Z" />
  </Glyph>
);

export const Link = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M11 18H4v-2h7v2Zm9 0h-7v-2h7v2ZM4 16H2V8h2v8Zm18 0h-2V8h2v8Zm-5-3H7v-2h10v2Zm-6-5H4V6h7v2Zm9 0h-7V6h7v2Z" />
  </Glyph>
);

export const ListBox = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M20 22H4v-2h16v2ZM4 20H2V4h2v16Zm18 0h-2V4h2v16ZM8 17H6v-2h2v2Zm10 0h-8v-2h8v2ZM8 13H6v-2h2v2Zm10 0h-8v-2h8v2ZM8 9H6V7h2v2Zm10 0h-8V7h8v2Zm2-5H4V2h16v2Z" />
  </Glyph>
);

export const Loader = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M13 22h-2v-6h2v6Zm-6-3H5v-2h2v2Zm12 0h-2v-2h2v2ZM9 17H7v-2h2v2Zm8 0h-2v-2h2v2Zm-9-4H2v-2h6v2Zm14 0h-6v-2h6v2ZM9 9H7V7h2v2Zm8 0h-2V7h2v2Zm-4-1h-2V2h2v6ZM7 7H5V5h2v2Zm12 0h-2V5h2v2Z" />
  </Glyph>
);

export const Lock = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M19 22H5v-2h14v2ZM5 20H3V10h2v10Zm16 0h-2V10h2v10ZM9 8h6V4h2v4h2v2H5V8h2V4h2v4Zm6-4H9V2h6v2Z" />
  </Glyph>
);

export const Login = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M18 22H6v-2h12v2ZM6 20H4v-5h2v5Zm14 0h-2V4h2v16ZM12 9h2v2h2v2h-2v2h-2v2h-2v-4H2v-2h8V7h2v2ZM6 9H4V4h2v5Zm12-5H6V2h12v2Z" />
  </Glyph>
);

export const Logout = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M18 22H6v-2h12v2ZM6 20H4V4h2v16Zm14 0h-2v-3h2v3ZM16 9h2v2h2v2h-2v2h-2v2h-2v-4H8v-2h6V7h2v2Zm4-2h-2V4h2v3Zm-2-3H6V2h12v2Z" />
  </Glyph>
);

export const MapGlyph = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M4 20h2v2H2V6h2v14Zm12 0h2v2h-4v-2h-2v-2h2V8h-2V6h4v14Zm-8 0H6v-2h2v2Zm12 0h-2v-2h2v2ZM10 4h2v2h-2v10h2v2H8V4H6V2h4v2Zm12 14h-2V4h-2V2h4v16ZM6 6H4V4h2v2Zm12 0h-2V4h2v2Z" />
  </Glyph>
);

export const Megaphone = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M12 22H8v-2h2v-4H8v4H6v-4H4v-2h4V8H4V6h12v2h-6v6h6v2h-4v6Zm10-2h-4v-2h2V4h-2V2h4v18Zm-4-2h-2v-2h2v2ZM4 14H2V8h2v6Zm14-8h-2V4h2v2Z" />
  </Glyph>
);

export const Menu = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M20 18H4v-2h16v2Zm0-5H4v-2h16v2Zm0-5H4V6h16v2Z" />
  </Glyph>
);

export const MoneyBagCoins = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M19 24h-6v-2h6v2ZM9 22H6v-2h3v2Zm4 0h-2v-2h2v2Zm2-4h6v4h-2v-2h-6v-4h2v2Zm-9 2H4v-2h2v2Zm-2-2H2v-6h2v6Zm19 0h-2v-2h2v2Zm-2-2h-6v-2h6v2ZM6 12H4v-2h2v2Zm5 0H9v-2h2v2Zm4 0h-2v-2h2v2Zm5 0h-2v-2h2v2ZM8 10H6V8h2v2Zm5 0h-2V8h2v2Zm5 0h-2V8h2v2Zm-7-2H8V6h3v2Zm5 0h-3V6h3v2ZM8 6H6V4h2v2Zm10 0h-2V4h2v2Zm-2-2H8V2h8v2Z" />
  </Glyph>
);

export const MoonStar = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M18 22H8v-2h10v2ZM8 20H6v-2h2v2Zm12 0h-2v-2h2v2ZM6 18H4v-2h2v2Zm16 0h-2v-4h-2v-2h2v-2h2v8ZM4 16H2V6h2v10Zm14 0h-6v-2h6v2Zm-6-2h-2v-2h2v2Zm-2-2H8V6h2v6Zm10-8h2v2h-2v2h-2V6h-2V4h2V2h2v2ZM6 6H4V4h2v2Zm8-2h-2v2h-2V4H6V2h8v2Z" />
  </Glyph>
);

export const NeuralNetwork2 = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M7 23H5v-2h2v2Zm14-1h-3v-2h3v2ZM5 21H3v-2h2v2Zm4 0H7v-2h2v2Zm5-1h-4v-2h4v2Zm4 0h-2v-3h2v3Zm5 0h-2v-3h2v3ZM7 19H5v-2h2v2Zm14-2h-3v-2h3v2ZM7 16H5v-2h2v2Zm9 0h-2v-2h2v2Zm-2-2h-2v-2h2v2Zm6 0h-2V9h2v5ZM7 13H3v-2h4v2Zm5-1h-2v-2h2v2Zm-9-1H1V7h2v4Zm6 0H7V7h2v4Zm3-3h-2V6h2v2Zm8 0h-3V6h3v2ZM7 7H3V5h4v2Zm7-1h-2V4h2v2Zm3 0h-2V3h2v3Zm5 0h-2V3h2v3Zm-2-3h-3V1h3v2Z" />
  </Glyph>
);

export const PenSquare = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M19 21H5v-2h14v2ZM5 19H3V5h2v14Zm16 0h-2v-6h2v6Zm-11-7h2v2h2v2H8v-6h2v2Zm6 2h-2v-2h2v2Zm2-2h-2v-2h2v2Zm-6-2h-2V8h2v2Zm8 0h-2V8h2v2Zm-6-2h-2V6h2v2Zm8 0h-2V6h2v2Zm-6-2h-2V4h2v2Zm4 0h-2V4h2v2Zm-9-1H5V3h6v2Zm7-1h-2V2h2v2Z" />
  </Glyph>
);

export const Play = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M9 5h2v2H9v10h2v2H9v2H7V3h2v2Zm4 12h-2v-2h2v2Zm2-2h-2v-2h2v2Zm2-2h-2v-2h2v2Zm-2-2h-2V9h2v2Zm-2-2h-2V7h2v2Z" />
  </Glyph>
);

export const Plus = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M13 11h7v2h-7v7h-2v-7H4v-2h7V4h2v7Z" />
  </Glyph>
);

export const PowerOff = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M16 22H6v-2h10v2Zm6 0h-2v-2h2v2ZM6 20H4v-2h2v2Zm14 0h-2v-2h2v2ZM4 18H2V8h2v10Zm14 0h-2v-2h2v2Zm-2-2h-2v-2h2v2Zm6 0h-2V8h2v8Zm-8-2h-2v-2h2v2Zm-2-2h-2v-2h2v2Zm-2-2H8V8h2v2ZM8 8H6V6h2v2Zm5 0h-2V2h2v6Zm7 0h-2V6h2v2ZM6 6H4V4h2v2Zm12 0h-2V4h2v2ZM4 4H2V2h2v2Z" />
  </Glyph>
);

export const Reload = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M10 16h10v2H10v4H8v-2H6v-2H4v-2h2v-2h2v-2h2v4Zm12 0h-2v-5h2v5ZM4 13H2V8h2v5Zm12-9h2v2h2v2h-2v2h-2v2h-2V8H4V6h10V2h2v2Z" />
  </Glyph>
);

export const RobotBody = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M7 11H17V5H21V9H19V13H17V17H19V13H21V19H17V23H7V19H3V13H5V17H7V13H5V9H3V5H7V11ZM9 21H11V19H9V21ZM13 21H15V19H13V21ZM9 17H15V13H9V17ZM11 9H9V7H11V9ZM15 9H13V7H15V9ZM13 3H17V5H7V3H11V1H13V3Z" />
  </Glyph>
);

export const Search = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M22 22h-2v-2h2v2Zm-2-2h-2v-2h2v2Zm-6-2H6v-2h8v2Zm4 0h-2v-2h2v2ZM6 16H4v-2h2v2Zm10 0h-2v-2h2v2ZM4 14H2V6h2v8Zm14 0h-2V6h2v8ZM6 6H4V4h2v2Zm10 0h-2V4h2v2Zm-2-2H6V2h8v2Z" />
  </Glyph>
);

export const Send = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M4 19h4v2H2v-8h2v6Zm8 0H8v-2h4v2Zm4-2h-4v-2h4v2Zm4-2h-4v-2h4v2Zm-10-2H4v-2h6v2Zm12 0h-2v-2h2v2ZM8 5H4v6H2V3h6v2Zm12 6h-4V9h4v2Zm-4-2h-4V7h4v2Zm-4-2H8V5h4v2Z" />
  </Glyph>
);

export const Server = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M20 21H4v-2h16v2ZM4 11h16V5h2v14h-2v-6H4v6H2V5h2v6Zm6 6H6v-2h4v2Zm0-8H6V7h4v2Zm10-4H4V3h16v2Z" />
  </Glyph>
);

export const Settings2 = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M10 22H6v-2h4v2Zm-4-2H4v-2H2v-2h2v-2h2v6Zm6-4h10v2H12v2h-2v-6h2v2Zm-2-2H6v-2h4v2Zm8-2h-4v-2h4v2Zm-6-4H2V6h10V4h2v6h-2V8Zm8-2h2v2h-2v2h-2V4h2v2Zm-2-2h-4V2h4v2Z" />
  </Glyph>
);

export const SettingsCog2 = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M6 22H4v-2h2v2Zm4-2h4v-2h4v2h-2v2H8v-2H6v-2h4v2Zm10 2h-2v-2h2v2ZM4 20H2v-2h2v2Zm18 0h-2v-2h2v2ZM6 10H4v4h2v4H4v-2H2V8h2V6h2v4Zm14-2h2v8h-2v2h-2v-4h2v-4h-2V6h2v2Zm-6 8h-4v-2h4v2Zm-4-2H8v-4h2v4Zm6 0h-2v-4h2v4Zm-2-4h-4V8h4v2ZM4 6H2V4h2v2Zm12-2h2v2h-4V4h-4v2H6V4h2V2h8v2Zm6 2h-2V4h2v2ZM6 4H4V2h2v2Zm14 0h-2V2h2v2Z" />
  </Glyph>
);

export const Skull = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M9 22H7v-2h2v2Zm4 0h-2v-2h2v2Zm4 0h-2v-2h2v2ZM7 20H5v-4H3v-2h4v6Zm4 0H9v-4h2v4Zm4 0h-2v-4h2v4Zm6-4h-2v4h-2v-6h4v2ZM3 14H1V4h2v10Zm20 0h-2V4h2v10Zm-13-3H8V7h2v4Zm6 0h-2V7h2v4Zm5-7H3V2h18v2Z" />
  </Glyph>
);

export const SlidersHorizontal = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M17 18h5v2h-5v2h-2v-6h2v2Zm-4 2H2v-2h11v2Zm-4-5H7v-2H2v-2h5V9h2v6Zm13-2H11v-2h11v2Zm-7-9h7v2h-7v2h-2V2h2v2Zm-4 2H2V4h9v2Z" />
  </Glyph>
);

export const SortVertical = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M8 6h2v2h2v2H8v10H6V10H2V8h2V6h2V4h2v2Zm10 8h4v2h-2v2h-2v2h-2v-2h-2v-2h-2v-2h4V4h2v10Z" />
  </Glyph>
);

export const Sparkle = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M13 23h-2v-4h2v4Zm-2-4H9v-4h2v4Zm4 0h-2v-4h2v4Zm-6-4H5v-2h4v2Zm10-2v2h-4v-2h4ZM5 13H1v-2h4v2Zm18 0h-4v-2h4v2ZM9 11H5V9h4v2Zm10 0h-4V9h4v2Zm-8-2H9V5h2v4Zm4 0h-2V5h2v4Zm-2-4h-2V1h2v4Z" />
  </Glyph>
);

export const SpeedFast = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M5 19H3v-2h2v2Zm16 0h-2v-2h2v2ZM3 17H1v-6h2v6Zm11 0h-4v-4h4v4Zm9 0h-2v-6h2v6Zm-7-4h-2v-2h2v2ZM5 11H3V9h2v2Zm13 0h-2V9h2v2ZM9 9H5V7h4v2Zm11 0h-2V7h2v2Zm-5-2H9V5h6v2Z" />
  </Glyph>
);

export const Sun = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M13 22h-2v-3h2v3Zm-6-3H5v-2h2v2Zm12 0h-2v-2h2v2Zm-4-2H9v-2h6v2Zm-6-2H7V9h2v6Zm8 0h-2V9h2v6ZM5 13H2v-2h3v2Zm17 0h-3v-2h3v2Zm-7-4H9V7h6v2ZM7 7H5V5h2v2Zm12 0h-2V5h2v2Zm-6-2h-2V2h2v3Z" />
  </Glyph>
);

export const Target = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M19 23H5v-2h14v2ZM5 21H3v-2h2v2Zm16 0h-2v-2h2v2ZM3 19H1V5h2v14Zm12 0H9v-2h6v2Zm8 0h-2V5h2v14ZM9 17H7v-2h2v2Zm8 0h-2v-2h2v2ZM7 15H5V9h2v6Zm6 0h-2v-2h2v2Zm6 0h-2V9h2v6Zm-8-2H9v-2h2v2Zm4 0h-2v-2h2v2Zm-2-2h-2V9h2v2ZM9 9H7V7h2v2Zm8 0h-2V7h2v2Zm-2-2H9V5h6v2ZM5 5H3V3h2v2Zm16 0h-2V3h2v2Zm-2-2H5V1h14v2Z" />
  </Glyph>
);

export const Terminal = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M20 22H4v-2h16v2ZM4 20H2V4h2v16Zm18 0h-2V4h2v16ZM8 18H6v-2h2v2Zm8 0h-4v-2h4v2Zm-6-2H8v-2h2v2Zm-2-2H6v-2h2v2ZM20 4H4V2h16v2Z" />
  </Glyph>
);

export const TestTubes = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M8 22H4v-2h4v2Zm12 0h-4v-2h4v2ZM11 4h-1v16H8v-5H4v5H2V4H1V2h10v2Zm12 0h-1v16h-2v-5h-4v5h-2V4h-1V2h10v2Zm-7 9h4V4h-4v9ZM4 13h4V4H4v9Z" />
  </Glyph>
);

export const ToolCase = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M20 23H4v-2h16v2Zm0-12h2v10h-2v-8H4v8H2V11h2V8h2v3h6V8h2v3h4V4h2v7Zm-5 6H9v-2h6v2ZM10 6h2v2H6V6h2V4h2v2Zm8-2h-8V2h8v2Z" />
  </Glyph>
);

export const Trash = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M18 22H6V20H18V22ZM9 6H15V4H17V6H22V8H20V20H18V8H6V20H4V8H2V6H7V4H9V6ZM15 4H9V2H15V4Z" />
  </Glyph>
);

export const Trophy = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M16 17h-3v2h2v2H9v-2h2v-2H8v-2h8v2Zm2-12h4v6h-2V7h-2v4h2v2h-2v2h-2V5H8v10H6v-2H4v-2h2V7H4v4H2V5h4V3h12v2Z" />
  </Glyph>
);

export const Undo = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M18 20h-6v-2h6v2Zm2-2h-2v-8h2v8Zm-10-4H8v-2H6v-2H4V8h2V6h2V4h2v4h8v2h-8v4Z" />
  </Glyph>
);

export const Unlock = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M19 22H5v-2h14v2ZM5 20H3V10h2v10Zm16 0h-2V10h2v10ZM9 8h10v2H5V8h2V4h2v4Zm8-2h-2V4h2v2Zm-2-2H9V2h6v2Z" />
  </Glyph>
);

export const Upload = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M19 21H5v-2h14v2ZM5 19H3v-4h2v4Zm16 0h-2v-4h2v4ZM13 5h2v2h2v2h-4v8h-2V9H7V7h2V5h2V3h2v2Z" />
  </Glyph>
);

export const User = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M6 22H4v-4h2v4Zm14 0h-2v-4h2v4ZM8 18H6v-2h2v2Zm10 0h-2v-2h2v2Zm-2-2H8v-2h8v2Zm-1-4H9v-2h6v2Zm-6-2H7V4h2v6Zm8 0h-2V4h2v6Zm-2-6H9V2h6v2Z" />
  </Glyph>
);

export const UserPlus = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M6 22H4v-4h2v4Zm14-4h2v2h-2v2h-2v-2h-2v-2h2v-2h2v2ZM8 18H6v-2h2v2Zm8-2H8v-2h8v2Zm-1-4H9v-2h6v2Zm-6-2H7V4h2v6Zm8 0h-2V4h2v6Zm-2-6H9V2h6v2Z" />
  </Glyph>
);

export const WarningDiamond = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M14 22h-4v-2h4v2Zm-4-2H8v-2h2v2Zm6 0h-2v-2h2v2Zm-8-2H6v-2h2v2Zm10 0h-2v-2h2v2Zm-5-1h-2v-2h2v2Zm-7-1H4v-2h2v2Zm14 0h-2v-2h2v2ZM4 14H2v-4h2v4Zm18 0h-2v-4h2v4Zm-9-7v6h-2V7h2Zm-7 3H4V8h2v2Zm14 0h-2V8h2v2ZM8 8H6V6h2v2Zm10 0h-2V6h2v2Zm-8-2H8V4h2v2Zm6 0h-2V4h2v2Zm-2-2h-4V2h4v2Z" />
  </Glyph>
);

export const Zap = (props: GlyphProps) => (
  <Glyph {...props}>
    <path d="M4 13h8v6h2v2h-2v2h-2v-8H2v-4h2v2Zm12 6h-2v-2h2v2Zm2-2h-2v-2h2v2Zm2-2h-2v-2h2v2Zm-6-6h8v4h-2v-2h-8V5h-2V3h2V1h2v8Zm-8 2H4V9h2v2Zm2-2H6V7h2v2Zm2-2H8V5h2v2Z" />
  </Glyph>
);
