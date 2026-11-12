interface U { name: string }
export function first(items: U[]): string {
  const [u] = items;
  return u.name;
}
