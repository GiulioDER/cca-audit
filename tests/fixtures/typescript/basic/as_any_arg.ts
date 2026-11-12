function takesNumber(n: number): number {
  return n * 2;
}
export function caller(u: string): number {
  return takesNumber(u as any);
}
