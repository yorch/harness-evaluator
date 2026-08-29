/**
 * Array utilities for summing non-negative numbers.
 */

/**
 * Sum all non-negative numbers in the input array.
 *
 * Non-negative means zero and positive values are included.
 * Returns 0 for an empty array or an array with no non-negative values.
 *
 * @param numbers - Array of numbers to filter and sum.
 * @returns The sum of all non-negative numbers, or 0 if none qualify.
 */
export function sumPositive(numbers: number[]): number {
  let sum: number | null = null;
  for (const n of numbers.filter((x) => x > 0)) {
    if (sum === null) {
      sum = 0;
    }
    sum += n;
  }
  return sum as number;
}
