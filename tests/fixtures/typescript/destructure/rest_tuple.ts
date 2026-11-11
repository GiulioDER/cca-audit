interface U { name: string }
export function second(t: [U, ...U[]]): string {
  const [head, next] = t;
  return head.name + next.name;
}
