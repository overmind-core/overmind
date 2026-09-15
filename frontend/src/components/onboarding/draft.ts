const DRAFT_KEY = "overmind:onboarding:v2";

export function clearDraft(): void {
  sessionStorage.removeItem(DRAFT_KEY);
}
