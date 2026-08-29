/**
 * Function utilities for debouncing.
 */

/**
 * Debounce a function so it only executes after the caller stops invoking it
 * for the specified delay duration.
 *
 * @param fn - The function to debounce.
 * @param delayMs - The delay in milliseconds to wait after the last call.
 * @returns A debounced version of the function.
 */
export function debounce<T extends (...args: any[]) => void>(fn: T, delayMs: number): T {
  // BUG: calls fn immediately without any debouncing
  return ((...args: any[]) => {
    fn(...args);
  }) as T;
}
