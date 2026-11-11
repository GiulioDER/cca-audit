import { find } from "@lib/util";
export function mainName(): string {
  const u = find();
  return u.name;
}
