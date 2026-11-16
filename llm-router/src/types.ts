// OpenAI Chat Completions wire types — the one format the router speaks to clients.
// Unknown fields are tolerated everywhere so client-specific extras pass through.

export type Role = 'system' | 'developer' | 'user' | 'assistant' | 'tool';

export interface ContentPart {
  type: string;
  text?: string;
  image_url?: { url: string; detail?: string };
  [k: string]: unknown;
}

export interface ToolCall {
  id: string;
  type: 'function';
  function: { name: string; arguments: string };
  /** Provider extras (e.g. Gemini thought signatures) that must round-trip. */
  extra_content?: unknown;
  [k: string]: unknown;
}

export interface ChatMessage {
  role: Role;
  content?: string | ContentPart[] | null;
  name?: string;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
  [k: string]: unknown;
}

export interface ToolDef {
  type: 'function';
  function: {
    name: string;
    description?: string;
    parameters?: Record<string, unknown>;
    strict?: boolean;
  };
}

export type ToolChoice =
  | 'auto'
  | 'none'
  | 'required'
  | { type: 'function'; function: { name: string } };

export type ResponseFormat =
  | { type: 'text' }
  | { type: 'json_object' }
  | {
      type: 'json_schema';
      json_schema: { name?: string; schema?: Record<string, unknown>; strict?: boolean };
    };

export interface ChatRequest {
  model?: string;
  messages: ChatMessage[];
  tools?: ToolDef[];
  tool_choice?: ToolChoice;
  parallel_tool_calls?: boolean;
  temperature?: number;
  top_p?: number;
  max_tokens?: number;
  max_completion_tokens?: number;
  stop?: string | string[];
  stream?: boolean;
  stream_options?: { include_usage?: boolean };
  response_format?: ResponseFormat;
  reasoning_effort?: string;
  n?: number;
  seed?: number;
  [k: string]: unknown;
}

export interface Usage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  prompt_tokens_details?: {
    /** Input tokens served from the provider's prompt cache (billed at a discount). */
    cached_tokens?: number;
    /** Input tokens written to the provider's prompt cache (Claude bills a surcharge). */
    cache_write_tokens?: number;
  };
}

export type FinishReason = 'stop' | 'length' | 'tool_calls' | 'content_filter';

export interface AssistantMessage {
  role: 'assistant';
  content: string | null;
  tool_calls?: ToolCall[];
  refusal?: string | null;
}

export interface ChatCompletion {
  id: string;
  object: 'chat.completion';
  created: number;
  model: string;
  choices: Array<{
    index: number;
    message: AssistantMessage;
    finish_reason: FinishReason | null;
  }>;
  usage?: Usage;
}

export interface ToolCallDelta {
  index: number;
  id?: string;
  type?: 'function';
  function?: { name?: string; arguments?: string };
  extra_content?: unknown;
}

export interface ChatChunk {
  id: string;
  object: 'chat.completion.chunk';
  created: number;
  model: string;
  choices: Array<{
    index: number;
    delta: {
      role?: 'assistant';
      content?: string | null;
      tool_calls?: ToolCallDelta[];
      refusal?: string | null;
    };
    finish_reason: FinishReason | null;
  }>;
  usage?: Usage | null;
}

export type ProviderName = 'gemini' | 'claude';
export type Tier = 'cheap' | 'mid' | 'smart';

export interface CallOptions {
  signal?: AbortSignal;
}

/** One upstream. `req.messages` is already compressed; `model` is the upstream model id. */
export interface Provider {
  readonly name: ProviderName;
  complete(req: ChatRequest, model: string, opts: CallOptions): Promise<ChatCompletion>;
  /** Must throw before yielding anything if the upstream rejects, so the router can fall over. */
  stream(req: ChatRequest, model: string, opts: CallOptions): AsyncGenerator<ChatChunk>;
}

export class UpstreamError extends Error {
  constructor(
    readonly provider: ProviderName,
    readonly status: number,
    message: string,
    readonly retryAfterMs?: number,
  ) {
    super(message);
    this.name = 'UpstreamError';
  }
}
