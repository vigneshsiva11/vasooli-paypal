import { describe, it, expect } from 'vitest';
import { handleRunTriggerError } from '../features/live/utils/liveUtils';

describe('run-state handling', () => {
  it('handles 403 (live runs disabled)', () => {
    const error = { status: 403, message: 'Forbidden' };
    const result = handleRunTriggerError(error);
    expect(result.disabled).toBe(true);
    expect(result.message).toContain('Live runs disabled');
    expect(result.retryAfterSeconds).toBeNull();
  });

  it('handles 409 (a run is active)', () => {
    const error = { status: 409, message: 'Conflict' };
    const result = handleRunTriggerError(error);
    expect(result.disabled).toBe(false);
    expect(result.message).toContain('active');
    expect(result.retryAfterSeconds).toBeNull();
  });

  it('handles 429 with retry_after in payload', () => {
    const error = {
      status: 429,
      message: 'Too Many Requests',
      payload: { retry_after: 45, detail: 'Rate limit: wait 45 seconds' },
    };
    const result = handleRunTriggerError(error);
    expect(result.disabled).toBe(false);
    expect(result.retryAfterSeconds).toBe(45);
    expect(result.message).toContain('45s');
  });

  it('handles 429 default countdown when no detail given', () => {
    const error = { status: 429, message: 'Too Many Requests' };
    const result = handleRunTriggerError(error);
    expect(result.disabled).toBe(false);
    expect(result.retryAfterSeconds).toBe(60);
    expect(result.message).toContain('60s');
  });

  it('handles network errors with clear text', () => {
    const error = { status: null, message: 'Failed to fetch' };
    const result = handleRunTriggerError(error);
    expect(result.disabled).toBe(false);
    expect(result.message).toBeTruthy();
  });
});

