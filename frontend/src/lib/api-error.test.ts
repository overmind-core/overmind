// covers: console-logic
import { expect, it } from "vitest";

import { ApiError, isPermanentApiRefusal } from "./api-error";
import { PaymentRequiredError } from "./credits";
import { GuestUpgradeError } from "./guest";
import { PlanLimitError } from "./plan-limits";

it.each([0, 408, 429, 500, 503])("keeps retryable HTTP %s failures recoverable", (status) => {
  expect(isPermanentApiRefusal(new ApiError(status, "failure", true))).toBe(false);
});
it.each([400, 401, 403, 404, 409, 413, 422])("recognizes permanent HTTP %s refusals", (status) => {
  expect(isPermanentApiRefusal(new ApiError(status, "refused", true))).toBe(true);
});
it.each([
  new PaymentRequiredError(),
  new GuestUpgradeError(),
  new PlanLimitError("Seat limit"),
])("recognizes the client's specialised refusal %s", (error) => {
  expect(isPermanentApiRefusal(error)).toBe(true);
});
