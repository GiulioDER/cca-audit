export function userName(s: string): string {
  return JSON.parse(s).user.name;
}
