type User = { name: string };
function show(a: string, b: string): string {
  return a + b;
}
export function render(u: User | null): string {
  return show(
    u.name,
    "x");
}
