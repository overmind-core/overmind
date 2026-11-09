import type { ReactNode } from "react";

import { z } from "zod";

export const ToolUIIdSchema = z.string().min(1);

export type ToolUIId = z.infer<typeof ToolUIIdSchema>;

export const ToolUIRoleSchema = z.enum([
  "information",
  "decision",
  "control",
  "state",
  "composite",
]);

export type ToolUIRole = z.infer<typeof ToolUIRoleSchema>;

export const ToolUIReceiptOutcomeSchema = z.enum(["success", "partial", "failed", "cancelled"]);

export type ToolUIReceiptOutcome = z.infer<typeof ToolUIReceiptOutcomeSchema>;

export const ToolUIReceiptSchema = z.object({
  at: z.string().datetime(),
  identifiers: z.record(z.string(), z.string()).optional(),
  outcome: ToolUIReceiptOutcomeSchema,
  summary: z.string().min(1),
});

export type ToolUIReceipt = z.infer<typeof ToolUIReceiptSchema>;

export const ToolUISurfaceSchema = z.object({
  id: ToolUIIdSchema,
  receipt: ToolUIReceiptSchema.optional(),
  role: ToolUIRoleSchema.optional(),
});

export type ToolUISurface = z.infer<typeof ToolUISurfaceSchema>;

export const ActionSchema = z.object({
  confirmLabel: z.string().optional(),
  disabled: z.boolean().optional(),
  icon: z.custom<ReactNode>().optional(),
  id: z.string().min(1),
  label: z.string().min(1),
  loading: z.boolean().optional(),

  sentence: z.string().optional(),
  shortcut: z.string().optional(),
  variant: z.enum(["default", "destructive", "secondary", "ghost", "outline"]).optional(),
});

export type Action = z.infer<typeof ActionSchema>;
export type LocalAction = Action;
export type DecisionAction = Action;

export const DecisionResultSchema = z.object({
  actionId: z.string().min(1),
  actionLabel: z.string().min(1),
  at: z.string().datetime(),
  decisionId: z.string().min(1),
  kind: z.literal("decision"),
  payload: z.record(z.string(), z.unknown()).optional(),
  version: z.literal(1),
});

export type DecisionResult<TPayload extends Record<string, unknown> = Record<string, unknown>> =
  Omit<z.infer<typeof DecisionResultSchema>, "payload"> & {
    payload?: TPayload;
  };

export function createDecisionResult<
  TPayload extends Record<string, unknown> = Record<string, unknown>,
>(args: {
  decisionId: string;
  action: { id: string; label: string };
  payload?: TPayload;
}): DecisionResult<TPayload> {
  return {
    actionId: args.action.id,
    actionLabel: args.action.label,
    at: new Date().toISOString(),
    decisionId: args.decisionId,
    kind: "decision",
    payload: args.payload,
    version: 1,
  };
}

export const ActionButtonsPropsSchema = z.object({
  actions: z.array(ActionSchema).min(1),
  align: z.enum(["left", "center", "right"]).optional(),
  className: z.string().optional(),
  confirmTimeout: z.number().positive().optional(),
});

export const SerializableActionSchema = ActionSchema.omit({ icon: true });
export const SerializableActionsSchema = ActionButtonsPropsSchema.extend({
  actions: z.array(SerializableActionSchema),
}).omit({ className: true });

export interface ActionsConfig {
  items: Action[];
  align?: "left" | "center" | "right";
  confirmTimeout?: number;
}

export const SerializableActionsConfigSchema = z.object({
  align: z.enum(["left", "center", "right"]).optional(),
  confirmTimeout: z.number().positive().optional(),
  items: z.array(SerializableActionSchema).min(1),
});

export type SerializableActionsConfig = z.infer<typeof SerializableActionsConfigSchema>;

export type SerializableAction = z.infer<typeof SerializableActionSchema>;
