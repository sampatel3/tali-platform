import { describe, it, expect } from 'vitest';
import { getErrorMessage } from './getErrorMessage';

describe('getErrorMessage', () => {
  it('returns a plain-string detail', () => {
    expect(getErrorMessage({ response: { data: { detail: 'Role is closed.' } } }, 'x'))
      .toBe('Role is closed.');
  });

  it('formats FastAPI validation arrays into field: message', () => {
    const err = { response: { data: { detail: [{ loc: ['body', 'email'], msg: 'invalid email' }] } } };
    expect(getErrorMessage(err, 'x')).toBe('email: invalid email');
  });

  it('maps connection timeouts to a try-again line', () => {
    expect(getErrorMessage({ code: 'ECONNABORTED' }, 'fallback'))
      .toBe('That took too long — please try again.');
  });

  it('falls back rather than leaking a raw JSON blob', () => {
    const err = { response: { data: { detail: '{"stack":"Traceback..."}' } } };
    expect(getErrorMessage(err, 'Could not save.')).toBe('Could not save.');
  });

  it('uses the fallback when there is no detail', () => {
    expect(getErrorMessage(new Error('boom'), 'Could not load.')).toBe('Could not load.');
  });

  it('reads an object detail message', () => {
    const err = { response: { data: { detail: { message: 'Quota exceeded.' } } } };
    expect(getErrorMessage(err, 'x')).toBe('Quota exceeded.');
  });
});
