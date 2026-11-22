import type { components } from "../../__generated__/schema.ts";

type Schemas = components["schemas"];

export type Action = Schemas["Action"];
export type ActionStatus = Schemas["ActionStatus"];
export type Alert = Schemas["Alert"];
export type AlertKind = Schemas["AlertKind"];
export type AlertStatus = Schemas["AlertStatus"];
export type ApiKey = Schemas["ApiKey"];
export type Artifact = Schemas["Artifact"];
export type Breadth = Schemas["Breadth"];
export type Chat = Schemas["Chat"];
export type ChatCreate = Schemas["CreateChatInput"];
export type ChatPatch = Schemas["UpdateChatInput"];
export type ChatStatus = Schemas["ChatStatus"];
export type ChatTiming = Schemas["ChatTiming"];
export type ComputerStatus = Schemas["ComputerStatus"];
export type Control = Schemas["Control"];
export type Credential =
  | Schemas["TokenCredential"]
  | Schemas["LoginCredential"];
export type CredentialCreate =
  | Schemas["CreateTokenCredentialInput"]
  | Schemas["CreateLoginCredentialInput"];
export type CredentialEvent = Schemas["CredentialEvent"];
export type CredentialUpdate =
  | Schemas["UpdateTokenCredentialInput"]
  | Schemas["UpdateLoginCredentialInput"];
export type Decision = Schemas["Decision"];
export type DecisionInput = Schemas["DecideActionInput"];
export type Diff = Schemas["Diff"];
export type Effect = Schemas["Effect"];
export type ChatEvent = Schemas["Event"];
export type Execution = Schemas["Execution"];
export type ExecutionCreate = Schemas["CreateExecutionInput"];
export type ExecutionStatus = Schemas["ExecutionStatus"];
export type Me = Schemas["Me"];
export type Media = Schemas["Media"];
export type Message = Schemas["Message"];
export type MessageInput = Schemas["CreateMessageInput"];
export type MetricsSummary = Schemas["MetricsSummary"];
export type Mode = Schemas["Mode"];
export type Model = Schemas["LanguageModel"];
export type PaginationInfo = Schemas["PaginationInfo"];
export type PolicyRule = Schemas["PolicyRule"];
export type PolicyRuleInput = Schemas["CreatePolicyRuleInput"];
export type Question = Schemas["Question"];
export type Risk = Schemas["Risk"];
export type Schedule = Schemas["Schedule-Output"];
export type ScheduleInput = Schemas["CreateScheduleInput"];
export type Scope = Schemas["Scope"];
export type SecretType = Schemas["SecretType"];
export type Session = Schemas["Session"];
export type Source = Schemas["Source"];
export type SourceInput = Schemas["CreateSourceInput"];
export type SourceSpec = Schemas["SourceSpec"];
export type SourceStatus = Schemas["SourceStatus"];
export type Subscription = Schemas["Subscription"];
export type SubscriptionInput = Schemas["CreateSubscriptionInput"];
export type Tool = Schemas["Tool"];
export type ToolGroup = Schemas["ToolGroup"];
export type Turn = Schemas["Turn"];
export type TurnStatus = Schemas["TurnStatus"];
export type UsageDay = Schemas["UsageDay"];
export type User = Schemas["User"];
export type Version = Schemas["Version"];
export type VersionValue = Schemas["VersionValue"];
export type Workspace = Schemas["Workspace"];
export type WorkspaceDirectory = Schemas["WorkspaceDirectory"];
export type WorkspaceFile = Schemas["WorkspaceFile"];
export type WorkspaceStatus = Schemas["WorkspaceStatus"];

export type { Page } from "@/api/query.ts";

/** A JSON value as the API returns it for untyped payloads. */
export type Json =
  | string
  | number
  | boolean
  | null
  | Json[]
  | { [key: string]: Json };

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
