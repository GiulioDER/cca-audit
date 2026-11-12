declare function find(id: string): { text: string } | null;
const app = find("app");
export function mount(): string {
  return app.text;
}
