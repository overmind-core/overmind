/**
 * Type roles, mapped to the classes defined in `styles.css`. Geist Pixel is set
 * on `body`, so only a title or prose needs a class at all. See DESIGN.md
 * § Typography for why each face has the job it has.
 */

/**
 * Every title picks exactly one step. Size is the only separator: Mondwest
 * ships a Regular master only, so a weight utility renders synthesised bold.
 */
export const TITLE = {
  /** 18px — card, dialog, sheet and alert titles. */
  card: "title-card",
  /** 36px — hero and empty-state headlines. One per screen, at most. */
  hero: "title-hero",
  /** ~29px — the single page header. Use `PageHeader`, don't hand-roll. */
  page: "page-title",
  /** 20px — a section heading within a page. */
  section: "title-section",
} as const;

/**
 * Sentences only — markdown, chat messages, score reasoning, long descriptions.
 * Not labels, values, table cells or chips. Prose sets wide, so never give a
 * prose container a fixed height.
 */
export const PROSE = "prose-body";

/** `sidebar` is the only role that switches face; use it on sidebar nav rows only. */
export const LABEL = {
  /** Chips and badges — tighter line-height for a boxed token. */
  chip: "chip-label",
  /** Eyebrows, table column headers, tab labels, stat captions. */
  pixel: "pixel-label",
  sidebar: "sidebar-label",
} as const;
