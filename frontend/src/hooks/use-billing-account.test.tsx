// @vitest-environment jsdom
// covers: console-logic
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

const billing = vi.hoisted(() => ({ account: vi.fn(), confirm: vi.fn() }));
vi.mock("@/client", () => ({
  default: {
    billing: {
      billingAccountRetrieve: billing.account,
      billingTopupConfirmCreate: billing.confirm,
    },
  },
}));

import { billingAccountQueryKey, useBillingAccount, useConfirmTopUp } from "./use-billing-account";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("keeps the confirmed balance when an older account read lands after it", async () => {
  let landStale: (value: unknown) => void = () => {};
  billing.account.mockReturnValueOnce(
    new Promise((resolve) => {
      landStale = resolve;
    })
  );
  const confirmed = { creditsUsd: "75.0000" };
  billing.confirm.mockResolvedValue({ account: confirmed, paid: true });
  const client = new QueryClient();
  const { result } = renderHook(
    () => ({ account: useBillingAccount(), confirm: useConfirmTopUp() }),
    {
      wrapper: ({ children }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    }
  );
  await waitFor(() => expect(billing.account).toHaveBeenCalledOnce());

  await act(() => result.current.confirm.mutateAsync("cs_test_paid"));
  await act(async () => landStale({ creditsUsd: "50.0000" }));

  expect(client.getQueryData(billingAccountQueryKey)).toEqual(confirmed);
});
