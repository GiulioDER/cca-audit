type User = { name: string };
export function Card(u: User | null) {
  return <div>{u.name}</div>;
}
