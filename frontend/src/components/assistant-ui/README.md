# assistant-ui tool components

Vendored from [assistant-ui](https://github.com/assistant-ui/assistant-ui/tree/f9dbafab250c8ac315bfc010a30dfff62320a423/packages/ui/src/components/react/assistant-ui/elements), revision `f9dbafab250c8ac315bfc010a30dfff62320a423` (MIT; see LICENSE.md).

`tool-group.tsx` is the upstream `tool-group.aui.tsx`. `tool-fallback.tsx` retains the upstream disclosure, arguments, result and error primitives from `tool-fallback.aui.tsx`; approval controls are not used by the Workshop.

Local adaptations use Overmind icons, Spinner, semantic colors and the existing Radix Collapsible instead of the upstream collapsible wrapper. Radix state selectors replace the upstream state selectors. The Workshop composes the primitives with its tool names, public summaries and execution errors, and feeds native assistant-ui tool-call message parts.
