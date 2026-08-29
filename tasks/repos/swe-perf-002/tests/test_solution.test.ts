import { describe, it, expect } from 'bun:test';
import { buildCsv } from '../src/solution';

describe('buildCsv', () => {
  it('should build a CSV string from multiple rows', () => {
    expect(buildCsv([['a', 'b'], ['c', 'd']])).toBe('a,b\nc,d\n');
  });

  it('should build a CSV string from a single row', () => {
    expect(buildCsv([['x', 'y']])).toBe('x,y\n');
  });
});
