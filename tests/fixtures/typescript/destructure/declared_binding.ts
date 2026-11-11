interface U { name: string }
declare function useState<S>(initial: S): [S, (next: S) => void];
export function names(m: Record<string, U>, user: U): string[] {
  const [count, setCount] = useState(0);
  setCount(count + 1);
  const { name } = user;
  const out = [name];
  for (const [key, value] of Object.entries(m)) out.push(key + value.name);
  return out;
}
