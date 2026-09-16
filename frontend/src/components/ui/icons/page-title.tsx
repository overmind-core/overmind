import type { SVGProps } from "react";

import { ICON_INK } from "@/lib/colors";

type SvgIconProps = SVGProps<SVGSVGElement>;

/** Two-tone heading marks (ink + white holes) on a 16×16 grid. Call sites add
 *  `dark:invert`. */
const INK = ICON_INK;

/** Paths from `@/assets/desktop-tower.svg`. */
export const DesktopTower = (props: SvgIconProps) => (
  <svg
    fill="none"
    height={16}
    viewBox="0 0 16 16"
    width={16}
    xmlns="http://www.w3.org/2000/svg"
    {...props}
  >
    <path clipRule="evenodd" d="M14 15H12V16H14V15Z" fill="white" fillRule="evenodd" />
    <path clipRule="evenodd" d="M8 15H6V16H8V15Z" fill="white" fillRule="evenodd" />
    <path clipRule="evenodd" d="M4 15H2V16H4V15Z" fill="white" fillRule="evenodd" />
    <path d="M7 14V1H1V14H7ZM2 7H3V6H2V5H3V4H4V5H5V6H4V7H5V8H4V9H3V8H2V7Z" fill="white" />
    <path clipRule="evenodd" d="M4 7H3V8H4V7Z" fill="white" fillRule="evenodd" />
    <path clipRule="evenodd" d="M4 5H3V6H4V5Z" fill="white" fillRule="evenodd" />
    <path d="M14 3H9V1H8V14H15V1H14V3ZM9 8H11V10H9V8ZM14 6H9V4H14V6Z" fill="white" />
    <path clipRule="evenodd" d="M13 1H10V2H13V1Z" fill="white" fillRule="evenodd" />
    <path clipRule="evenodd" d="M11 8H9V10H11V8Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M4 8H3V9H4V8Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M5 7H4V8H5V7Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M3 7H2V8H3V7Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M4 6H3V7H4V6Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M5 5H4V6H5V5Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M3 5H2V6H3V5Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M14 4H9V6H14V4Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M4 4H3V5H4V4Z" fill={INK} fillRule="evenodd" />
    <path
      d="M0 0V15H1V16H2V15H4V16H6V15H8V16H9V15H11V16H12V15H14V16H15V15H16V0H0ZM13 1V2H10V1H13ZM1 14V1H7V14H1ZM15 14H8V1H9V3H14V1H15V14Z"
      fill={INK}
    />
  </svg>
);

export const BarbellVertical = (props: SvgIconProps) => (
  <svg
    fill="none"
    height={16}
    viewBox="0 0 16 16"
    width={16}
    xmlns="http://www.w3.org/2000/svg"
    {...props}
  >
    <path clipRule="evenodd" d="M2 6H1V9H2V6Z" fill="white" fillRule="evenodd" />
    <path clipRule="evenodd" d="M15 6H14V9H15V6Z" fill="white" fillRule="evenodd" />
    <path clipRule="evenodd" d="M10 6H6V9H10V6Z" fill="white" fillRule="evenodd" />
    <path clipRule="evenodd" d="M13 2H11V13H13V2Z" fill="white" fillRule="evenodd" />
    <path clipRule="evenodd" d="M5 2H3V13H5V2Z" fill="white" fillRule="evenodd" />
    <path clipRule="evenodd" d="M13 13H11V14H13V13Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M5 13H3V14H5V13Z" fill={INK} fillRule="evenodd" />
    <path d="M11 2H10V5H6V2H5V13H6V10H10V13H11V2ZM6 9V6H10V9H6Z" fill={INK} />
    <path d="M3 2H2V5H1V6H2V9H1V10H2V13H3V2Z" fill={INK} />
    <path d="M14 6H15V5H14V2H13V13H14V10H15V9H14V6Z" fill={INK} />
    <path clipRule="evenodd" d="M1 6H0V9H1V6Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M16 6H15V9H16V6Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M13 1H11V2H13V1Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M5 1H3V2H5V1Z" fill={INK} fillRule="evenodd" />
  </svg>
);

export const LockersTwoFilled = (props: SvgIconProps) => (
  <svg
    fill="none"
    height={16}
    viewBox="0 0 16 16"
    width={16}
    xmlns="http://www.w3.org/2000/svg"
    {...props}
  >
    <path d="M11 8V15H15V8H11ZM14 14H12V13H13V12H12V11H13V10H12V9H14V14Z" fill="white" />
    <path d="M1 7V15H5V7H1ZM4 11H3V12H4V13H2V10H3V9H2V8H4V11Z" fill="white" />
    <path clipRule="evenodd" d="M15 5H11V7H15V5Z" fill="white" fillRule="evenodd" />
    <path d="M6 4V15H10V4H6ZM9 10H8V6H7V5H9V10Z" fill="white" />
    <path clipRule="evenodd" d="M5 4H1V6H5V4Z" fill="white" fillRule="evenodd" />
    <path clipRule="evenodd" d="M10 1H6V3H10V1Z" fill="white" fillRule="evenodd" />
    <path d="M14 9H12V10H13V11H12V12H13V13H12V14H14V9Z" fill={INK} />
    <path d="M4 12H3V11H4V8H2V9H3V10H2V13H4V12Z" fill={INK} />
    <path d="M8 10H9V5H7V6H8V10Z" fill={INK} />
    <path
      d="M5 0V3H0V16H16V4H11V0H5ZM5 15H1V7H5V15ZM5 6H1V4H5V6ZM10 15H6V4H10V15ZM10 3H6V1H10V3ZM15 15H11V8H15V15ZM15 5V7H11V5H15Z"
      fill={INK}
    />
  </svg>
);

export const Chart = (props: SvgIconProps) => (
  <svg
    fill="none"
    height={16}
    viewBox="0 0 16 16"
    width={16}
    xmlns="http://www.w3.org/2000/svg"
    {...props}
  >
    <path d="M15 12H14V13H15V15H13V13H12V15H7V14H6V15H3V16H16V9H15V12Z" fill="white" />
    <path
      d="M15 6H14V7H13V8H12V9H11V10H10V11H9V12H8V11H7V10H6V9H5V10H4V11H3V12H2V13H1V14H0V16H1V15H2V14H3V13H4V12H5V11H6V12H7V13H8V14H9V13H10V12H11V11H12V10H13V9H14V8H15V7H16V5H15V6Z"
      fill="white"
    />
    <path
      d="M0 0V12H1V10H2V9H1V7H3V9H4V7H6V8H7V7H9V9H10V7H12V6H10V4H12V6H13V4H14V3H13V1H15V3H16V0H0ZM9 1V3H7V1H9ZM6 1V3H4V1H6ZM1 1H3V3H1V1ZM1 6V4H3V6H1ZM4 6V4H6V6H4ZM9 6H7V4H9V6ZM12 3H10V1H12V3Z"
      fill="white"
    />
    <path
      d="M15 8H14V9H13V10H12V11H11V12H10V13H9V14H8V13H7V12H6V11H5V12H4V13H3V14H2V15H1V16H3V15H6V14H7V15H12V13H13V15H15V13H14V12H15V9H16V7H15V8Z"
      fill={INK}
    />
    <path clipRule="evenodd" d="M12 4H10V6H12V4Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M9 4H7V6H9V4Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M6 4H4V6H6V4Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M3 4H1V6H3V4Z" fill={INK} fillRule="evenodd" />
    <path
      d="M15 1H13V3H14V4H13V6H12V7H10V9H9V7H7V8H6V7H4V9H3V7H1V9H2V10H1V12H0V14H1V13H2V12H3V11H4V10H5V9H6V10H7V11H8V12H9V11H10V10H11V9H12V8H13V7H14V6H15V5H16V3H15V1Z"
      fill={INK}
    />
    <path clipRule="evenodd" d="M9 1H7V3H9V1Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M6 1H4V3H6V1Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M12 1H10V3H12V1Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M3 1H1V3H3V1Z" fill={INK} fillRule="evenodd" />
  </svg>
);

export const Eyeball = (props: SvgIconProps) => (
  <svg
    fill="none"
    height={16}
    viewBox="0 0 16 16"
    width={16}
    xmlns="http://www.w3.org/2000/svg"
    {...props}
  >
    <path clipRule="evenodd" d="M10 13H6V14H10V13Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M12 12H10V13H12V12Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M6 12H4V13H6V12Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M13 11H12V12H13V11Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M4 11H3V12H4V11Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M14 10H13V11H14V10Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M3 10H2V11H3V10Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M15 9H14V10H15V9Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M2 9H1V10H2V9Z" fill={INK} fillRule="evenodd" />
    <path d="M2 8V7H1V6H0V9H1V8H2Z" fill={INK} />
    <path d="M15 7H14V8H15V9H16V6H15V7Z" fill={INK} />
    <path clipRule="evenodd" d="M14 6H13V7H14V6Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M3 6H2V7H3V6Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M15 5H14V6H15V5Z" fill={INK} fillRule="evenodd" />
    <path
      d="M12 5V4H10V3H6V4H4V5H3V6H4V9H5V10H6V11H10V10H11V9H12V6H13V5H12ZM8 5V7H6V5H8ZM9 9V8H10V9H9Z"
      fill={INK}
    />
    <path clipRule="evenodd" d="M2 5H1V6H2V5Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M14 4H13V5H14V4Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M3 4H2V5H3V4Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M13 3H12V4H13V3Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M4 3H3V4H4V3Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M12 2H10V3H12V2Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M6 2H4V3H6V2Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M10 1H6V2H10V1Z" fill={INK} fillRule="evenodd" />
    <path clipRule="evenodd" d="M10 8H9V9H10V8Z" fill="white" fillRule="evenodd" />
    <path clipRule="evenodd" d="M15 6H14V7H15V6Z" fill="white" fillRule="evenodd" />
    <path
      d="M13 6H12V9H11V10H10V11H6V10H5V9H4V6H3V7H2V8H1V9H2V10H3V11H4V12H6V13H10V12H12V11H13V10H14V9H15V8H14V7H13V6Z"
      fill="white"
    />
    <path clipRule="evenodd" d="M8 5H6V7H8V5Z" fill="white" fillRule="evenodd" />
    <path clipRule="evenodd" d="M2 6H1V7H2V6Z" fill="white" fillRule="evenodd" />
    <path clipRule="evenodd" d="M14 5H13V6H14V5Z" fill="white" fillRule="evenodd" />
    <path clipRule="evenodd" d="M3 5H2V6H3V5Z" fill="white" fillRule="evenodd" />
    <path clipRule="evenodd" d="M13 4H12V5H13V4Z" fill="white" fillRule="evenodd" />
    <path clipRule="evenodd" d="M4 4H3V5H4V4Z" fill="white" fillRule="evenodd" />
    <path clipRule="evenodd" d="M12 3H10V4H12V3Z" fill="white" fillRule="evenodd" />
    <path clipRule="evenodd" d="M6 3H4V4H6V3Z" fill="white" fillRule="evenodd" />
    <path clipRule="evenodd" d="M10 2H6V3H10V2Z" fill="white" fillRule="evenodd" />
  </svg>
);

/** Paths from `@/assets/overmind-eye-mono.svg`, `currentColor` so nav inherits the
 *  sidebar foreground. `translate(2 2) scale(0.3125)` maps the 64×64 glyph into the same
 *  24×24 viewBox and symmetric 2px inset the pixelart glyphs use. */
export const OvermindEyeMono = (props: SvgIconProps) => (
  <svg
    fill="currentColor"
    height={24}
    viewBox="0 0 24 24"
    width={24}
    xmlns="http://www.w3.org/2000/svg"
    {...props}
  >
    <g transform="translate(2 2) scale(0.3125)">
      <path d="M32 28H28V32H24V20H28V16H32V28Z" />
      <path
        clipRule="evenodd"
        d="M44 4H52V8H56V12H60V20H64V44H60V52H56V56H52V60H44V64H20V60H12V56H8V52H4V44H0V20H4V12H8V8H12V4H20V0H44V4ZM28 8H24V12H20V24H16V40H20V52H24V56H28V60H36V56H40V52H44V40H48V24H44V12H40V8H36V4H28V8Z"
        fillRule="evenodd"
      />
    </g>
  </svg>
);
