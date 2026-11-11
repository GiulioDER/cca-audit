function takesNumber(n: number): number {
  return n * 2;
}
export function caller(u: string): number {
  return takesNumber(u as unknown as number);
}
export const MODES = ["a", "b"] as const;
