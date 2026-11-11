type User = { name: string };
export function nameOf(u: User | null): string {
  const r = /`/;
  // @ts-ignore
  return u.name + r.source;
}
