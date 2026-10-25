"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";

export default function AuthCallbackPage() {
  const router = useRouter();

  useEffect(() => {
    const supabase = createClient();
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (session) {
        router.push("/assistants");
      } else {
        // If code is in hash/search, handle auth state change
        const { data: { subscription } } = supabase.auth.onAuthStateChange((event, s) => {
          if (s) {
            router.push("/assistants");
          }
        });
        return () => subscription.unsubscribe();
      }
    });
  }, [router]);

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        background: "var(--bg)",
        color: "var(--muted)",
      }}
    >
      <div style={{ textAlign: "center" }}>
        <div style={{ fontSize: "24px", marginBottom: "12px" }}>⧗</div>
        <div>Completing sign in, redirecting to your workspace...</div>
      </div>
    </div>
  );
}
