type User = { name: string };
export function nameOf(u: User | null): string {
  const s = "// @ts-ignore";
  if (u === null) return s;
  return u.name;
}
