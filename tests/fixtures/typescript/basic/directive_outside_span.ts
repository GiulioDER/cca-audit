// @ts-ignore
type User = { name: string };
export function nameOf(u: User | null): string {
  if (u === null) return "";
  return u.name;
}
