declare function find(id: string): { text: string } | null;
const app = (find("app") as { text: string });
export function mount(): string {
  return app.text;
}
