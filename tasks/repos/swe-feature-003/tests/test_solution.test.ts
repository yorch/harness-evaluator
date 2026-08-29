import { describe, it, expect } from 'bun:test';
import { debounce } from '../src/solution';

describe('debounce', () => {
  it('should eventually call the function', async () => {
    let called = false;
    const debounced = debounce(() => {
      called = true;
    }, 50);
    debounced();
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(called).toBe(true);
  });

  it('should pass arguments to the function', async () => {
    let receivedArgs: number[] = [];
    const debounced = debounce((a: number, b: number) => {
      receivedArgs = [a, b];
    }, 50);
    debounced(3, 4);
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(receivedArgs).toEqual([3, 4]);
  });
});
