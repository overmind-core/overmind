# TanStack Router: explicit page recovery

`@tanstack/react-router@1.170.17` reloads the document automatically when a route
module fails to download. A network interruption can therefore discard unsaved
input before the application error boundary can offer recovery.

The patch removes that reload from the source and both distributed module formats.
Import failures reach `RouteErrorFallback`, where the user can refresh explicitly.
Preloading and lazy loading otherwise retain their upstream behavior.

Keep the dependency pinned while this patch is required. Remove the patch when
upstream provides equivalent configurable behavior, and verify with
`test_unavailable_page_offers_recovery_without_reloading` in the Console journeys.
