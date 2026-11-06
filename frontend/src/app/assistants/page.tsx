"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useApp } from "@/lib/context";
import { api, Assistant } from "@/lib/api";
import { Sidebar } from "@/components/Sidebar";
import { Topbar } from "@/components/Topbar";
import { CreateAssistantModal } from "@/components/CreateAssistantModal";
import { Sparkles, Search, Plus, FileText, ArrowUpRight, Eye, EyeOff, Loader2 } from "lucide-react";

export default function AssistantsPage() {
  const { t, showToast } = useApp();
  const router = useRouter();

  const [assistants, setAssistants] = useState<Assistant[]>([]);
  const [searchTerm, setSearchTerm] = useState("");
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  // Form state for first-time direct creation
  const [firstName, setFirstName] = useState("");
  const [firstSlug, setFirstSlug] = useState("");
  const [firstKey, setFirstKey] = useState("");
  const [firstShowKey, setFirstShowKey] = useState(false);
  const [firstInstructions, setFirstInstructions] = useState("");
  const [isFirstSubmitting, setIsFirstSubmitting] = useState(false);
  const [firstError, setFirstError] = useState<string | null>(null);

  const loadAssistants = async () => {
    try {
      setIsLoading(true);
      const data = await api.getAssistants();
      setAssistants(data);
    } catch (err: any) {
      console.warn("Could not load assistants from backend:", err);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadAssistants();
  }, []);

  const handleCreated = (created: Assistant) => {
    setAssistants((prev) => [created, ...prev]);
    router.push(`/assistants/${created.slug}/chat`);
  };

  const handleFirstAssistantSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!firstName.trim() || !firstSlug.trim() || !firstKey.trim()) {
      setFirstError("Please fill out all required fields.");
      return;
    }

    try {
      setIsFirstSubmitting(true);
      setFirstError(null);
      const created = await api.createAssistant({
        name: firstName.trim(),
        slug: firstSlug.trim(),
        openrouter_api_key: firstKey.trim(),
        instructions: { prompt: firstInstructions.trim() },
      });
      showToast(`Assistant "${created.name}" created!`);
      router.push(`/assistants/${created.slug}/chat`);
    } catch (err: any) {
      setFirstError(err.message || "Failed to create assistant");
    } finally {
      setIsFirstSubmitting(false);
    }
  };

  const filtered = assistants.filter((ast) =>
    ast.name.toLowerCase().includes(searchTerm.toLowerCase().trim())
  );

  return (
    <div className="app-container">
      <Sidebar activeView="assistants" />

      <main className="main">
        <Topbar breadcrumbs={[{ label: "Workspace" }, { label: t("nav.assistants") }]} />

        <section className="content">
          {isLoading ? (
            <div className="page-spinner">
              <Loader2 size={28} strokeWidth={2} className="animate-spin" style={{ color: "var(--accent)" }} />
              <span>Loading your workspace...</span>
            </div>
          ) : assistants.length === 0 ? (
            /* First-time onboarding: direct centered creation form */
            <div style={{ maxWidth: "480px", margin: "40px auto" }}>
              <div style={{ textAlign: "center", marginBottom: "28px" }}>
                <div className="welcome-mark"><Sparkles size={28} strokeWidth={1.5} /></div>
                <h2>{t("assistants.emptyState")}</h2>
                <p style={{ color: "var(--muted)", fontSize: "13px" }}>
                  {t("assistants.emptyStateAction")}
                </p>
              </div>

              <form
                className="panel"
                onSubmit={handleFirstAssistantSubmit}
                style={{ padding: "24px" }}
              >
                {firstError && (
                  <div
                    style={{
                      padding: "9px 12px",
                      background: "#3b1e1e",
                      border: "1px solid #6b2d2d",
                      borderRadius: "8px",
                      color: "#fca5a5",
                      fontSize: "12px",
                      marginBottom: "14px",
                    }}
                  >
                    {firstError}
                  </div>
                )}

                <div className="field">
                  <label htmlFor="firstName">{t("createModal.nameLabel")}</label>
                  <input
                    id="firstName"
                    value={firstName}
                    onChange={(e) => {
                      setFirstName(e.target.value);
                      setFirstSlug(
                        e.target.value
                          .toLowerCase()
                          .trim()
                          .replace(/[^a-z0-9]+/g, "-")
                          .replace(/^-|-$/g, "")
                      );
                    }}
                    placeholder={t("createModal.namePlaceholder")}
                    required
                    autoFocus
                  />
                </div>

                <div className="field">
                  <label htmlFor="firstSlug">{t("createModal.slugLabel")}</label>
                  <input
                    id="firstSlug"
                    value={firstSlug}
                    onChange={(e) => setFirstSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))}
                    placeholder={t("createModal.slugPlaceholder")}
                    pattern="[a-z0-9]+(?:-[a-z0-9]+)*"
                    required
                  />
                  <small>{t("createModal.slugHelp")}</small>
                </div>

                <div className="field">
                  <label htmlFor="firstKey">{t("createModal.keyLabel")}</label>
                  <div className="input-with-action">
                    <input
                      id="firstKey"
                      type={firstShowKey ? "text" : "password"}
                      value={firstKey}
                      onChange={(e) => setFirstKey(e.target.value)}
                      placeholder={t("createModal.keyPlaceholder")}
                      autoComplete="new-password"
                      required
                    />
                    <button
                      type="button"
                      className="reveal"
                      onClick={() => setFirstShowKey(!firstShowKey)}
                      aria-label={firstShowKey ? "Hide API key" : "Show API key"}
                    >
                      {firstShowKey ? <EyeOff size={14} strokeWidth={2} /> : <Eye size={14} strokeWidth={2} />}
                    </button>
                  </div>
                  <small>{t("createModal.keyHelp")}</small>
                </div>

                <div className="field">
                  <label htmlFor="firstInstructions">Custom Instructions (Optional)</label>
                  <textarea
                    id="firstInstructions"
                    rows={2}
                    value={firstInstructions}
                    onChange={(e) => setFirstInstructions(e.target.value)}
                    placeholder="e.g. You are an expert HR assistant. Answer questions strictly based on company policy..."
                  />
                </div>

                <div style={{ marginTop: "24px" }}>
                  <button
                    type="submit"
                    className="primary"
                    style={{ width: "100%" }}
                    disabled={isFirstSubmitting}
                  >
                    {isFirstSubmitting ? t("createModal.submitting") : t("createModal.submit")}
                  </button>
                </div>
              </form>
            </div>
          ) : (
            /* Standard Assistants Cards Grid */
            <>
              <div className="eyebrow">
                <span className="eyebrow-dot" />
                {t("assistants.eyebrow")}
              </div>
              <h1>{t("assistants.title")}</h1>
              <p className="intro">{t("assistants.intro")}</p>

              <div className="toolbar">
                <label className="search">
                  <Search size={13} strokeWidth={2} color="var(--muted)" />
                  <input
                    id="searchInput"
                    type="search"
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                    placeholder={t("assistants.searchPlaceholder")}
                  />
                </label>
                <button
                  type="button"
                  className="primary"
                  id="createFromPage"
                  onClick={() => setIsModalOpen(true)}
                  style={{ display: "flex", alignItems: "center", gap: "6px" }}
                >
                  <Plus size={15} strokeWidth={2.5} /> {t("assistants.createButton")}
                </button>
              </div>

              <div className="cards" id="assistantCards">
                {filtered.map((ast) => {
                  const initial = ast.name.charAt(0).toUpperCase();
                  const isReady = ast.document_count > 0;
                  const promptDesc =
                    ast.instructions?.prompt ||
                    "Answers questions using verified documents from its private knowledge base.";

                  return (
                    <Link
                      key={ast.id}
                      href={`/assistants/${ast.slug}/chat`}
                      className="assistant-card"
                    >
                      <div className="card-top">
                        <div className="card-symbol">{initial}</div>
                        <span className="status">
                          {isReady ? t("assistants.ready") : t("assistants.setupNeeded")}
                        </span>
                      </div>
                      <div className="card-title">{ast.name}</div>
                      <p className="card-desc">{promptDesc}</p>
                      <div className="card-footer">
                        <span className="file-count" style={{ display: "flex", alignItems: "center", gap: "4px" }}>
                          <FileText size={12} strokeWidth={2} />
                          {t("assistants.documentsCount", { count: ast.document_count })}
                        </span>
                        <span className="arrow"><ArrowUpRight size={14} strokeWidth={2} /></span>
                      </div>
                    </Link>
                  );
                })}
              </div>

              {filtered.length === 0 && (
                <p className="empty-filter" id="emptyFilter" style={{ display: "block", marginTop: "24px" }}>
                  {t("assistants.noMatch")}
                </p>
              )}
            </>
          )}
        </section>
      </main>

      <CreateAssistantModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onCreated={handleCreated}
      />
    </div>
  );
}
