import type { FixoraEvent } from './types';

export interface StreamHandle {
  close: () => void;
}

const KNOWN_EVENTS = [
  'start', 'step', 'tool_call', 'tool_result', 'llm_call', 'llm_error',
  'test_result', 'review_verdict', 'retry', 'awaiting_approval',
  'applied', 'done', 'stream_end',
];

export function streamRun(
  runId: string,
  onEvent: (event: FixoraEvent) => void,
  onError: (message: string) => void,
  onOpen?: () => void,
): StreamHandle {
  const url = `/runs/${encodeURIComponent(runId)}/events`;
  const source = new EventSource(url);
  let closed = false;

  const close = () => {
    if (closed) return;
    closed = true;
    source.close();
  };

  const parse = (message: MessageEvent) => {
    try {
      const event = JSON.parse(message.data) as FixoraEvent;
      onEvent(event);
      if (event.type === 'stream_end') close();
    } catch {
      onError('Received an unreadable event from the server.');
    }
  };

  source.onopen = () => onOpen?.();
  source.onerror = () => {
    onError('Live connection lost. The browser will retry and send Last-Event-ID; the timeline dedupes by event sequence.');
  };

  for (const type of KNOWN_EVENTS) {
    source.addEventListener(type, parse as EventListener);
  }

  return { close };
}
