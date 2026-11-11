interface U { name: string }
export function get(m: Record<string, U>): string {
  let alice: U;
  ({ alice } = m);
  return alice.name;
}
