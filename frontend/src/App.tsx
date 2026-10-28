import { cn } from "./lib/utils.ts";

export default function App() {
  return (
    <main
      className={cn("app-shell")}
      style={{ background: "var(--background)", color: "var(--ink)" }}
    >
      <h1>ELSEWHERE</h1>
      <p>Feel at home, elsewhere.</p>
    </main>
  );
}
