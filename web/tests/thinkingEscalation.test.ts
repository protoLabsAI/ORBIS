import { describe, expect, test } from 'bun:test';
import {
  THINKING_NO_RESPONSE_MS,
  THINKING_STILL_WORKING_MS,
  thinkingText,
} from '../src/voice/thinkingEscalation';

describe('thinkingText', () => {
  test('escalates thinking → still working → no response', () => {
    expect(thinkingText(0)).toBe('thinking…');
    expect(thinkingText(THINKING_STILL_WORKING_MS - 1)).toBe('thinking…');
    expect(thinkingText(THINKING_STILL_WORKING_MS)).toBe('still working…');
    expect(thinkingText(THINKING_NO_RESPONSE_MS - 1)).toBe('still working…');
    expect(thinkingText(THINKING_NO_RESPONSE_MS)).toBe('no response');
  });
});
