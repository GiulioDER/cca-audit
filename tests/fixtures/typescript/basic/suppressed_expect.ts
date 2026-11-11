type User = { name: string };
export function nameOf(u: User | null): string {
  // @ts-expect-error
  return u.name;
}
