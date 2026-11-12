interface U { name: string }
export function get(m: Record<string, U>): string {
  const { alice } = m;
  return alice.name;
}
