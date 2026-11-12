function takesNumber(n: number): number {
  return n * 2;
}
export function caller(): number {
  return takesNumber(1);
}
