// A1 helpers shared by the adapter and the tests. Rows/columns are 0-based.

export function columnName(col: number): string {
  let n = col + 1;
  let name = '';
  while (n > 0) {
    const rem = (n - 1) % 26;
    name = String.fromCharCode(65 + rem) + name;
    n = Math.floor((n - 1) / 26);
  }
  return name;
}

export function toA1(row: number, col: number): string {
  return `${columnName(col)}${row + 1}`;
}

export function fromA1(address: string): { row: number; col: number } {
  const match = /^([A-Z]+)(\d+)$/.exec(address.toUpperCase().replace(/\$/g, ''));
  if (!match) throw new Error(`bad address ${address}`);
  let col = 0;
  for (const ch of match[1]) col = col * 26 + (ch.charCodeAt(0) - 64);
  return { row: Number(match[2]) - 1, col: col - 1 };
}
