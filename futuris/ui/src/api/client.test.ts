import { afterEach, describe, expect, it, vi } from 'vitest';
import { errorDetailFrom, fetchForecasts } from './client';

describe('errorDetailFrom', () => {
  it('shows the message from the API error envelope', () => {
    const body = { error: { code: 'not_found', message: 'Forecast not found', details: {} } };
    expect(errorDetailFrom(body)).toBe('Forecast not found');
  });

  it('never shows the raw envelope when a message is present', () => {
    expect(errorDetailFrom({ error: { code: 'x', message: 'm' } })).not.toContain('"code"');
  });

  it('falls back to the legacy top-level detail, then message', () => {
    expect(errorDetailFrom({ detail: 'Not Found' })).toBe('Not Found');
    expect(errorDetailFrom({ message: 'Gateway timeout' })).toBe('Gateway timeout');
  });

  it('prefers the envelope when both shapes are present', () => {
    expect(errorDetailFrom({ error: { message: 'envelope' }, detail: 'legacy' })).toBe('envelope');
  });

  it('shows unrecognised bodies as JSON, and a null body as "null"', () => {
    expect(errorDetailFrom({ unexpected: 1 })).toBe('{"unexpected":1}');
    expect(errorDetailFrom(null)).toBe('null');
  });
});

describe('error handling through a request', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('surfaces the envelope message with the HTTP status', async () => {
    const body = { error: { code: 'unauthorized', message: 'Invalid or revoked API Key', details: {} } };
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(body), {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
    await expect(fetchForecasts()).rejects.toThrow(
      'Failed to fetch forecasts (HTTP 401): Invalid or revoked API Key',
    );
  });
});
