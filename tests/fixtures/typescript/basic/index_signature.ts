type Dir = Record<string, { name: string }>;
export function f(m: Dir): string {
  return m.alice.name;
}
