type User = { name: string };
export function a(u: User | null): string {
  /// @ts-ignore
  return u.name;
}
export function b(u: User | null): string {
  /* @ts-ignore */
  return u.name;
}
export function c(u: User | null): string {
  // @ts-ignored
  return u.name;
}
