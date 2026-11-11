type User = { name: string };
export function nameOf(u: User | null): string {
  const k = ""; // @ts-ignore
  return u.name + k;
}
export function other(u: User | null): string {
  const t = `${1}`; /* @ts-ignore */
  return u.name + t;
}
