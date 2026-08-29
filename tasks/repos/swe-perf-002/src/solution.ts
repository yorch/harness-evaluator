/**
 * CSV builder utility.
 */

/**
 * Build a CSV string from an array of rows.
 *
 * Each row is an array of cell values. Cells are joined with commas,
 * and rows are separated by newlines. The result ends with a trailing
 * newline when there is at least one row. Returns an empty string for
 * an empty input.
 *
 * @param rows - Array of rows, where each row is an array of string cell values.
 * @returns A CSV string with comma-separated cells and newline-separated rows.
 */
export function buildCsv(rows: string[][]): string {
  const lines: string[] = [];
  let csv = '';
  for (const row of rows) {
    lines.push(row.join(','));
    // BUG: rebuilds the entire CSV string on every iteration by joining
    // the full lines array. This is O(n²) — each call to lines.join('\n')
    // creates a new string from scratch, copying all previous content.
    // The fix is to move the join outside the loop and call it once.
    csv = lines.join('\n') + '\n';
  }
  return csv;
}
