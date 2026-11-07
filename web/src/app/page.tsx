import { ApiStatus } from "@/components/ApiStatus";

// Placeholder home page for the deploy skeleton (OP-102).
// The app shell and live map replace it in OP-113.
export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-6 px-4 py-16">
      <h1 className="text-3xl font-semibold">OpenPath</h1>
      <p className="text-lg">Every barrier between A and B, in one place.</p>
      <ApiStatus />
    </main>
  );
}
