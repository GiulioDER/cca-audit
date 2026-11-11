type User = { name: string };
export function nameOf(u: User | null): string {
  // @ts-ignore
  return u.name;
}
