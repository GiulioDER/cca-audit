interface U { name: string }
export function first(items: U[]): string {
  let u: U;
  [u] = items;
  return u.name;
}
