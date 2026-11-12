interface U { name: string }
declare const items: U[];
const first = items[0];
export function nameOf(): string {
  return first.name;
}
