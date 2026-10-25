"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";

export default function AuthCallbackPage() {
  const router = useRouter();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    async function processAuthCallback() {
      try {
        const urlParams = new URLSearchParams(window.location.search);
        const code = urlParams.get("code");
        const error = urlParams.get("error");
        const errorDesc = urlParams.get("error_description");

        if (error || errorDesc) {
          setErrorMessage(errorDesc || error || "Authentication error occurred.");
          return;
        }

        const supabase = createClient();

        if (code) {
          // Exchange authorization code for session (PKCE flow)
          const { data, error: exchangeError } = await supabase.auth.exchangeCodeForSession(code);
          if (exchangeError) {
            console.error("Code exchange error:", exchangeError);
            setErrorMessage(exchangeError.message);
            return;
          }
          if (data.session) {
            router.replace("/assistants");
            return;
          }
        }

        // Check if session is already active (e.g. hash fragments or persisted token)
        const { data: { session } } = await supabase.auth.getSession();
        if (session) {
          router.replace("/assistants");
          return;
        }

        // Listen for any delayed auth state change
        const { data: { subscription } } = supabase.auth.onAuthStateChange((event, s) => {
          if (s) {
            subscription.unsubscribe();
            router.replace("/assistants");
          }
        });
      } catch (err: any) {
        console.error("Callback processing exception:", err);
        setErrorMessage(err.message || "Failed to complete authentication.");
      }
    }

    processAuthCallback();
  }, [router]);

  if (errorMessage) {
    return (
      <div
        style={{
          minHeight: "100vh",
          display: "grid",
          placeItems: "center",
          background: "var(--bg)",
          padding: "20px",
        }}
      >
        <div className="modal" style={{ maxWidth: "420px", textAlign: "center", padding: "30px" }}>
          <div style={{ fontSize: "28px", color: "#f87171", marginBottom: "12px" }}>⚠</div>
          <h2>Authentication Failed</h2>
          <p style={{ color: "var(--muted)", fontSize: "13px", margin: "10px 0 24px" }}>
            {errorMessage}
          </p>
          <Link href="/login" className="primary" style={{ textDecoration: "none", width: "100%", justifyContent: "center" }}>
            Return to Sign In
          </Link>
        </div>
      </div>
    );
  }

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
