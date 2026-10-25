"use client";

import React from "react";
import Link from "next/link";
import { useApp } from "@/lib/context";

export default function LandingPage() {
  const { t, theme, toggleTheme } = useApp();

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column", background: "var(--bg)" }}>
      {/* Top Header */}
      <header
        style={{
          height: "68px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "0 36px",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <div className="brand-mark">M</div>
          <div>
            <div className="brand-name">{t("brand.name")}</div>
            <div className="brand-sub">{t("brand.tagline")}</div>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <button
            type="button"
            className="quiet-button"
            onClick={toggleTheme}
            title="Toggle appearance"
          >
            <span>◐</span>
            <span>{theme === "dark" ? "Dark" : "Light"}</span>
          </button>

          <Link href="/login" className="quiet-button" style={{ textDecoration: "none" }}>
            Sign In
          </Link>

          <Link href="/assistants" className="primary" style={{ textDecoration: "none" }}>
            Open Workspace →
          </Link>
        </div>
      </header>

      {/* Hero Section */}
      <main style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", padding: "64px 24px 80px" }}>
        <div style={{ maxWidth: "860px", textAlign: "center" }}>
          <div className="eyebrow" style={{ marginBottom: "14px" }}>
            <span className="eyebrow-dot" />
            Reliable Private Document Intelligence
          </div>

          <h1 style={{ fontSize: "clamp(34px, 5vw, 56px)", lineHeight: 1.1, margin: "0 auto 20px" }}>
            AI assistants grounded in your private knowledge.
          </h1>

          <p
            className="intro"
            style={{ fontSize: "17px", maxWidth: "660px", margin: "0 auto 36px", lineHeight: 1.6 }}
          >
            ManualMind gives every user their own dedicated AI assistants backed by hybrid RAG.
            Every response is anchored in real document snippets, with verifiable citations,
            automatic abstention on missing data, and full LangGraph execution traceability.
          </p>

          <div style={{ display: "flex", justifyContent: "center", gap: "14px", flexWrap: "wrap" }}>
            <Link
              href="/assistants"
              className="primary"
              style={{ fontSize: "15px", padding: "13px 24px", textDecoration: "none" }}
            >
              Get Started with Your Assistants →
            </Link>
            <Link
              href="/login"
              className="quiet-button"
              style={{ fontSize: "15px", padding: "13px 20px", textDecoration: "none" }}
            >
              Sign in with Google
            </Link>
          </div>
        </div>

        {/* Feature Highlights Grid */}
        <div
          style={{
            maxWidth: "1000px",
            width: "100%",
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
            gap: "18px",
            marginTop: "68px",
          }}
        >
          <div
            className="assistant-card"
            style={{ cursor: "default" }}
          >
            <div className="card-top">
              <div className="card-symbol">⚡</div>
              <span className="status">Hybrid Search</span>
            </div>
            <div className="card-title">pgvector + Full-Text Search</div>
            <p className="card-desc">
              Combines semantic vector embeddings with PostgreSQL lexical full-text search via Reciprocal Rank Fusion (RRF) for pinpoint accuracy.
            </p>
          </div>

          <div
            className="assistant-card"
            style={{ cursor: "default" }}
          >
            <div className="card-top">
              <div className="card-symbol">▤</div>
              <span className="status">Verifiable</span>
            </div>
            <div className="card-title">Real Source Citations</div>
            <p className="card-desc">
              Every factual assertion links directly to the specific page and document chunk stored in your assistant's private database.
            </p>
          </div>

          <div
            className="assistant-card"
            style={{ cursor: "default" }}
          >
            <div className="card-top">
              <div className="card-symbol">🛡</div>
              <span className="status">Strict Grounding</span>
            </div>
            <div className="card-title">Zero-Hallucination Policy</div>
            <p className="card-desc">
              If your documents do not contain verified evidence, the assistant explicitly abstains rather than making assumptions.
            </p>
          </div>

          <div
            className="assistant-card"
            style={{ cursor: "default" }}
          >
            <div className="card-top">
              <div className="card-symbol">⚙</div>
              <span className="status">LangGraph</span>
            </div>
            <div className="card-title">Observable Execution Trace</div>
            <p className="card-desc">
              Inspect step-by-step query analysis, fusion ranking, evidence sufficiency evaluation, and grounded validation for each response.
            </p>
          </div>

          <div
            className="assistant-card"
            style={{ cursor: "default" }}
          >
            <div className="card-top">
              <div className="card-symbol">🔒</div>
              <span className="status">Encrypted</span>
            </div>
            <div className="card-title">Per-Assistant API Keys</div>
            <p className="card-desc">
              Your OpenRouter keys are encrypted with Fernet AES-128-CBC at rest on the server and are never exposed to client browsers.
            </p>
          </div>

          <div
            className="assistant-card"
            style={{ cursor: "default" }}
          >
            <div className="card-top">
              <div className="card-symbol">📁</div>
              <span className="status">Storage</span>
            </div>
            <div className="card-title">Versioning & Reprocessing</div>
            <p className="card-desc">
              Ingest PDF, DOCX, and TXT files with full version history preservation, isolated storage, and safe re-indexing.
            </p>
          </div>
        </div>
      </main>

      {/* Footer */}
      <footer
        style={{
          padding: "24px 36px",
          borderTop: "1px solid var(--border)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          fontSize: "12px",
          color: "var(--muted)",
        }}
      >
        <div>ManualMind © 2026 · Private AI Assistant Platform</div>
        <div style={{ display: "flex", gap: "16px" }}>
          <span>English</span>
          <span>Português</span>
          <span>Español</span>
        </div>
      </footer>
    </div>
  );
}
