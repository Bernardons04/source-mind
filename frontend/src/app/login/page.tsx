"use client";

import React, { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useApp } from "@/lib/context";
import { createClient } from "@/lib/supabase/client";

export default function LoginPage() {
  const { t, showToast } = useApp();
  const router = useRouter();
  const [loading, setLoading] = useState(false);

  React.useEffect(() => {
    if (typeof window !== "undefined") {
      const errorParam = new URLSearchParams(window.location.search).get("error");
      if (errorParam) {
        showToast(`Login notice: ${errorParam}`);
      }
    }
  }, [showToast]);

  const handleGoogleLogin = async () => {
    try {
      setLoading(true);
      const supabase = createClient();
      const nextParam =
        typeof window !== "undefined"
          ? new URLSearchParams(window.location.search).get("next")
          : null;
      const callbackNext = nextParam ? `?next=${encodeURIComponent(nextParam)}` : "";
      const redirectUrl = `${window.location.origin}/auth/callback${callbackNext}`;

      const { error } = await supabase.auth.signInWithOAuth({
        provider: "google",
        options: {
          redirectTo: redirectUrl,
          queryParams: {
            access_type: "offline",
            prompt: "consent",
          },
        },
      });

      if (error) {
        showToast(`Google login error: ${error.message}`);
        setLoading(false);
      }
    } catch (err: any) {
      showToast(err.message || "Failed to initiate Google sign in.");
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--bg)",
        padding: "20px",
      }}
    >
      <div
        className="modal"
        style={{
          maxWidth: "420px",
          width: "100%",
          padding: "36px 32px",
          textAlign: "center",
        }}
      >
        <Link
          href="/"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "10px",
            textDecoration: "none",
            color: "inherit",
            marginBottom: "24px",
          }}
        >
          <div className="brand-mark" style={{ width: "36px", height: "36px", fontSize: "20px" }}>
            S
          </div>
          <div style={{ textAlign: "left" }}>
            <div className="brand-name" style={{ fontSize: "18px" }}>
              {t("brand.name")}
            </div>
            <div className="brand-sub">{t("brand.tagline")}</div>
          </div>
        </Link>

        <h2 style={{ fontSize: "22px", margin: "0 0 8px" }}>{t("auth.signInTitle")}</h2>
        <p style={{ color: "var(--muted)", fontSize: "13px", margin: "0 0 28px" }}>
          {t("auth.signInSubtitle")}
        </p>

        {/* Google OAuth Button */}
        <button
          type="button"
          onClick={handleGoogleLogin}
          disabled={loading}
          style={{
            width: "100%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: "12px",
            padding: "12px 16px",
            background: "#ffffff",
            color: "#1f2937",
            border: "1px solid #d1d5db",
            borderRadius: "10px",
            fontWeight: 650,
            fontSize: "14px",
            boxShadow: "0 1px 3px rgba(0,0,0,0.1)",
            transition: "background 0.15s",
          }}
        >
          <svg width="18" height="18" viewBox="0 0 24 24">
            <path
              fill="#4285F4"
              d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
            />
            <path
              fill="#34A853"
              d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
            />
            <path
              fill="#FBBC05"
              d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"
            />
            <path
              fill="#EA4335"
              d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"
            />
          </svg>
          <span>{loading ? t("auth.signingIn") : t("auth.googleButton")}</span>
        </button>

        <p style={{ color: "var(--muted)", fontSize: "11px", marginTop: "24px" }}>
          {t("auth.tagline")}
        </p>
      </div>
    </div>
  );
}
