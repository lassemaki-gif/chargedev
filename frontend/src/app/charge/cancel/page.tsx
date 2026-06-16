"use client";
import { useRouter } from "next/navigation";
import { Nav } from "@/components/Nav";

export default function CancelPage() {
  const router = useRouter();
  return (
    <div>
      <Nav />
      <div className="flex items-center justify-center min-h-[calc(100vh-64px)] px-6">
        <div className="card max-w-sm w-full text-center">
          <div className="text-4xl mb-4">↩️</div>
          <h2 className="text-xl font-bold text-white mb-2">Payment cancelled</h2>
          <p className="text-ash text-sm mb-6">No charge was made. You can go back and try again.</p>
          <button onClick={() => router.back()} className="btn-volt w-full text-center mb-3">Go back</button>
          <button onClick={() => router.push("/charge")} className="btn-outline w-full text-center text-sm">Browse chargers</button>
        </div>
      </div>
    </div>
  );
}
