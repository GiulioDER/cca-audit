type User = { name: string };
export function nameOf(u: User | null): string {
  return u.name;
}
