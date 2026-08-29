import { describe, it, expect } from 'bun:test';
import { sumPositive } from '../src/solution';

describe('sumPositive', () => {
  it('should sum positive numbers', () => {
    expect(sumPositive([1, 2, 3])).toBe(6);
  });

  it('should ignore negative numbers when summing', () => {
    expect(sumPositive([1, -1, 2, -2])).toBe(3);
  });
});
