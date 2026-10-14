/*
 * Copyright 2024-2025 the original author or authors.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      https://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { normalizeNodeName } from '../../utils/nodeNames';

/**
 * @description 图搜索服务，处理与后端的流式 (SSE) 交互，实现搜索过程的实时反馈
 */

/**
 * @description 图搜索请求参数接口
 */
export interface GraphRequest {
  /** 智能体 ID */
  agentId: string;
  /** 聊天记忆会话 ID */
  conversationId: string;
  /** Graph 运行 ID，仅在人工反馈恢复时复用 */
  threadId?: string;
  /** 用户查询语句 */
  query: string;
  /** 是否需要人工反馈 */
  humanFeedback: boolean;
  /** 人工反馈内容 */
  humanFeedbackContent?: string;
  /** 是否拒绝了之前的计划 */
  rejectedPlan: boolean;
  /** 是否仅执行 NL2SQL */
  nl2sqlOnly: boolean;
  /** 可选的智能体 API Key；仅通过请求头发送，不写入 URL */
  apiKey?: string;
  attachmentIds?: string[];
}

/**
 * @description 图节点响应数据接口
 */
export interface GraphNodeResponse {
  /** 智能体 ID */
  agentId: string;
  /** 线程 ID */
  threadId: string;
  /** 事件语义；历史消息可能不存在 */
  eventType?: GraphEventType;
  /** 单次节点执行 ID；历史消息可能不存在 */
  stepId?: string;
  /** 同名节点在本次运行中的执行次数 */
  attempt?: number;
  /** 当前执行的节点名称 */
  nodeName: string;
  /** 文本内容类型 */
  textType: TextType;
  /** 文本内容 */
  text: string;
  /** 是否发生错误 */
  error: boolean;
  /** 是否已完成 */
  complete: boolean;
}

export enum GraphEventType {
  NODE_OUTPUT = "NODE_OUTPUT",
  FINAL_ANSWER = "FINAL_ANSWER",
  HUMAN_FEEDBACK_REQUIRED = "HUMAN_FEEDBACK_REQUIRED",
}

/**
 * @description 文本内容类型枚举
 */
export enum TextType {
  /** JSON 格式 */
  JSON = "JSON",
  /** Python 代码 */
  PYTHON = "PYTHON",
  /** SQL 语句 */
  SQL = "SQL",
  /** HTML 内容 */
  HTML = "HTML",
  /** Markdown 内容 */
  MARK_DOWN = "MARK_DOWN",
  /** 结果集数据 */
  RESULT_SET = "RESULT_SET",
  /** 普通文本 */
  TEXT = "TEXT",
}

const API_BASE_URL = "/api";

interface ParsedSseEvent {
  event: string;
  data: string;
}

/**
 * Parse one SSE frame according to the event-stream field rules. Exported so
 * framing behaviour (including multi-line data) can be covered by unit tests.
 */
export function parseSseFrame(frame: string): ParsedSseEvent | null {
  let event = "message";
  const data: string[] = [];

  for (const line of frame.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator < 0 ? line : line.slice(0, separator);
    let value = separator < 0 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value || "message";
    if (field === "data") data.push(value);
  }

  if (data.length === 0) return null;
  return { event, data: data.join("\n") };
}

function extractSseFrames(buffer: string): {
  frames: string[];
  remainder: string;
} {
  const frames: string[] = [];
  let remainder = buffer;
  while (true) {
    const match = /\r?\n\r?\n/.exec(remainder);
    if (!match || match.index === undefined) break;
    frames.push(remainder.slice(0, match.index));
    remainder = remainder.slice(match.index + match[0].length);
  }
  return { frames, remainder };
}

/**
 * @description 图搜索业务逻辑处理类
 */
class GraphService {
  /**
   * @description 发起流式搜索请求 (SSE)
   * @param {GraphRequest} request - 请求参数
   * @param {(response: GraphNodeResponse) => Promise<void>} onMessage - 收到消息时的回调
   * @param {(error: Error) => Promise<void>} [onError] - 发生错误时的回调
   * @param {() => Promise<void>} [onComplete] - 搜索完成时的回调
   * @returns {Promise<(cancelRun?: boolean) => Promise<void>>} 返回一个用于关闭流连接并按需取消后端任务的函数
   */
  async streamSearch(
    request: GraphRequest,
    onMessage: (response: GraphNodeResponse) => Promise<void>,
    onError?: (error: Error) => Promise<void>,
    onComplete?: () => Promise<void>,
  ): Promise<(cancelRun?: boolean) => Promise<void>> {
    const params = new URLSearchParams();
    params.append("agentId", request.agentId);
    params.append("conversationId", request.conversationId);
    if (request.threadId) {
      params.append("threadId", request.threadId);
    }
    params.append("query", request.query);
    params.append("humanFeedback", request.humanFeedback.toString());
    params.append("rejectedPlan", request.rejectedPlan.toString());
    params.append("nl2sqlOnly", request.nl2sqlOnly.toString());

    if (request.humanFeedbackContent) {
      params.append("humanFeedbackContent", request.humanFeedbackContent);
    }

    const withAttachments = Boolean(request.attachmentIds?.length);
    const url = withAttachments ? `${API_BASE_URL}/stream/search` : `${API_BASE_URL}/stream/search?${params.toString()}`;

    const controller = new AbortController();
    let latestThreadId = request.threadId;
    let isCompleted = false;
    let errorReported = false;

    const reportError = async (error: Error) => {
      if (isCompleted || errorReported) return;
      errorReported = true;
      if (onError) await onError(error);
    };

    const headers: Record<string, string> = { Accept: "text/event-stream" };
    if (request.apiKey?.trim()) headers["X-API-Key"] = request.apiKey.trim();
    const { apiKey: _apiKey, ...body } = request;
    if (withAttachments) headers['Content-Type'] = 'application/json';

    let response: Response;
    try {
      response = await fetch(url, { headers, signal: controller.signal, ...(withAttachments ? { method: 'POST', body: JSON.stringify(body) } : {}) });
    } catch (error) {
      if (!controller.signal.aborted) {
        await reportError(
          error instanceof Error ? error : new Error("Stream connection failed"),
        );
      }
      return async () => undefined;
    }

    if (!response.ok) {
      let message = `Stream request failed: HTTP ${response.status}`;
      try {
        const payload = (await response.json()) as { detail?: string; message?: string };
        message = payload.detail || payload.message || message;
      } catch {
        // Keep the status-based message when the error body is not JSON.
      }
      await reportError(new Error(message));
      return async () => undefined;
    }

    const consume = async () => {
      if (!response.body) {
        await reportError(new Error("Streaming response has no body"));
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      const dispatch = async (frameText: string) => {
        const frame = parseSseFrame(frameText);
        if (!frame) return;
        if (frame.event === "complete") {
          if (isCompleted) return;
          isCompleted = true;
          if (onComplete) await onComplete();
          return;
        }

        let nodeResponse: GraphNodeResponse;
        try {
          nodeResponse = JSON.parse(frame.data) as GraphNodeResponse;
          nodeResponse.nodeName = normalizeNodeName(nodeResponse.nodeName || '');
        } catch {
          await reportError(new Error("Failed to parse server response"));
          return;
        }
        latestThreadId = nodeResponse.threadId || latestThreadId;
        // Failed node attempts may be retried; only terminal errors abort.
        if (frame.event === "error" || (nodeResponse.error && !nodeResponse.nodeName)) {
          await reportError(
            new Error(nodeResponse.text || "Stream processing failed"),
          );
          isCompleted = true;
          controller.abort();
          return;
        }
        await onMessage(nodeResponse);
      };

      try {
        while (!isCompleted) {
          const { done, value } = await reader.read();
          buffer += decoder.decode(value, { stream: !done });
          const parsed = extractSseFrames(buffer);
          buffer = parsed.remainder;
          for (const frame of parsed.frames) await dispatch(frame);
          if (done) break;
        }
        if (!isCompleted && buffer.trim()) await dispatch(buffer);
        if (!isCompleted && !errorReported && !controller.signal.aborted) {
          await reportError(new Error("Stream connection closed before completion"));
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          await reportError(
            error instanceof Error ? error : new Error("Stream connection failed"),
          );
        }
      } finally {
        reader.releaseLock();
      }
    };

    // Let the caller store the cancellation callback before a very short stream
    // can deliver its terminal event.
    setTimeout(() => void consume(), 0);

    return async (cancelRun = false) => {
      isCompleted = true;
      controller.abort();
      if (!cancelRun) return;

      const stopParams = new URLSearchParams({
        conversationId: request.conversationId,
      });
      if (latestThreadId) stopParams.append("threadId", latestThreadId);
      const response = await fetch(
        `${API_BASE_URL}/stream/stop?${stopParams.toString()}`,
        { method: "POST", keepalive: true, headers },
      );
      if (!response.ok) {
        throw new Error(`Failed to stop graph run: HTTP ${response.status}`);
      }
    };
  }
}

export default new GraphService();
