/** Normalize the Python wire names and legacy Java names at the UI boundary. */
export function normalizeNodeName(name: string): string {
  if (!/^[A-Z][A-Z_]+_NODE$/.test(name)) return name;
  return name.toLowerCase().split('_').map(part => part[0]!.toUpperCase() + part.slice(1)).join('');
}
