import { load } from "./helper";
export function userName(): string {
  return load().user.name;
}
