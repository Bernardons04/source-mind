"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useApp } from "@/lib/context";
import { api, Assistant } from "@/lib/api";
import { modalSwal } from "@/lib/swal";
import { Sidebar } from "@/components/Sidebar";
import { Topbar } from "@/components/Topbar";
import { CreateAssistantModal } from "@/components/CreateAssistantModal";
import { Sparkles, Search, Plus, FileText, ArrowUpRight, Eye, EyeOff, Loader2, Settings, Trash2, X } from "lucide-react";

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

  // Edit assistant modal state
  const [editingAssistant, setEditingAssistant] = useState<Assistant | null>(null);
  const [editName, setEditName] = useState("");
  const [editSlug, setEditSlug] = useState("");
  const [editApiKey, setEditApiKey] = useState("");
  const [editShowKey, setEditShowKey] = useState(false);
  const [editInstructions, setEditInstructions] = useState("");
  const [isEditSubmitting, setIsEditSubmitting] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

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

  const handleOpenEdit = (e: React.MouseEvent, ast: Assistant) => {
    e.preventDefault();
    e.stopPropagation();
    setEditingAssistant(ast);
    setEditName(ast.name);
    setEditSlug(ast.slug);
    setEditApiKey("");
    setEditShowKey(false);
    setEditInstructions(ast.instructions?.prompt || "");
    setEditError(null);
  };

  const handleCloseEdit = () => {
    setEditingAssistant(null);
    setEditName("");
    setEditSlug("");
    setEditApiKey("");
    setEditShowKey(false);
    setEditInstructions("");
    setEditError(null);
  };

  const handleEditSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingAssistant) return;
    if (!editName.trim() || !editSlug.trim()) {
      setEditError(t("createModal.namePlaceholder"));
      return;
    }

    try {
      setIsEditSubmitting(true);
      setEditError(null);
      const payload: {
        name: string;
        slug: string;
        openrouter_api_key?: string;
        instructions: Record<string, any>;
      } = {
        name: editName.trim(),
        slug: editSlug.trim(),
        instructions: {
          ...(editingAssistant.instructions || {}),
          prompt: editInstructions.trim(),
        },
      };

      if (editApiKey.trim()) {
        payload.openrouter_api_key = editApiKey.trim();
      }

      const updated = await api.updateAssistant(editingAssistant.slug, payload);

      setAssistants((prev) => prev.map((a) => (a.id === editingAssistant.id ? updated : a)));
      showToast(t("editModal.success", { name: updated.name }));
      handleCloseEdit();
    } catch (err: any) {
      setEditError(err.message || "Failed to update assistant");
    } finally {
      setIsEditSubmitting(false);
    }
  };

  const handleDeleteAssistant = async (e: React.MouseEvent, ast: Assistant) => {
    e.preventDefault();
    e.stopPropagation();

    const result = await modalSwal.fire({
      title: t("editModal.deleteTitle"),
      text: t("editModal.deleteText", { name: ast.name }),
      icon: "warning",
      showCancelButton: true,
      confirmButtonColor: "#dc2626",
      confirmButtonText: t("editModal.deleteConfirm"),
      cancelButtonText: t("createModal.cancel"),
    });

    if (!result.isConfirmed) return;

    try {
      await api.deleteAssistant(ast.slug);
      setAssistants((prev) => prev.filter((a) => a.id !== ast.id));
      showToast(t("editModal.deleteSuccess", { name: ast.name }));
    } catch (err: any) {
      showToast(err.message || "Failed to delete assistant");
    }
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
                  const promptDesc =
                    ast.instructions?.prompt ||
                    "Answers questions using verified documents from its private knowledge base.";

                  return (
                    <div
                      key={ast.id}
                      className="assistant-card"
                      onClick={() => router.push(`/assistants/${ast.slug}/chat`)}
                      style={{ cursor: "pointer", position: "relative" }}
                    >
                      <div className="card-top">
                        <div className="card-symbol">{initial}</div>
                        <div className="card-actions" style={{ display: "flex", alignItems: "center", gap: "4px" }}>
                          <button
                            type="button"
                            className="card-action-btn"
                            title="Settings"
                            aria-label="Settings"
                            onClick={(e) => handleOpenEdit(e, ast)}
                          >
                            <Settings size={14} strokeWidth={2} />
                          </button>
                          <button
                            type="button"
                            className="card-action-btn danger"
                            title="Delete"
                            aria-label="Delete"
                            onClick={(e) => handleDeleteAssistant(e, ast)}
                          >
                            <Trash2 size={14} strokeWidth={2} />
                          </button>
                        </div>
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
                    </div>
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

      {editingAssistant && (
        <div
          className="overlay"
          role="dialog"
          aria-modal="true"
          onClick={(e) => {
            if (e.target === e.currentTarget) handleCloseEdit();
          }}
        >
          <form className="modal" onSubmit={handleEditSubmit} onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <div>
                <h2>{t("editModal.title")}</h2>
                <p className="modal-copy">{t("editModal.subtitle")}</p>
              </div>
              <button
                type="button"
                className="close"
                onClick={handleCloseEdit}
                aria-label={t("createModal.cancel")}
              >
                <X size={16} strokeWidth={2} />
              </button>
            </div>

            {editError && (
              <div
                style={{
                  padding: "9px 12px",
                  background: "#3b1e1e",
                  border: "1px solid #6b2d2d",
                  borderRadius: "8px",
                  color: "#fca5a5",
                  fontSize: "12px",
                  marginBottom: "12px",
                }}
              >
                {editError}
              </div>
            )}

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
              <div className="field">
                <label htmlFor="editAssistantName">{t("createModal.nameLabel")}</label>
                <input
                  id="editAssistantName"
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  placeholder={t("createModal.namePlaceholder")}
                  required
                />
              </div>

              <div className="field">
                <label htmlFor="editAssistantSlug">{t("createModal.slugLabel")}</label>
                <input
                  id="editAssistantSlug"
                  value={editSlug}
                  onChange={(e) =>
                    setEditSlug(
                      e.target.value
                        .toLowerCase()
                        .replace(/[^a-z0-9-]/g, "")
                        .replace(/--+/g, "-")
                    )
                  }
                  placeholder={t("createModal.slugPlaceholder")}
                  pattern="[a-z0-9]+(?:-[a-z0-9]+)*"
                  required
                />
              </div>
            </div>

            <div className="field">
              <label htmlFor="editApiKey">{t("createModal.keyLabel")}</label>
              <div className="input-with-action">
                <input
                  id="editApiKey"
                  type={editShowKey ? "text" : "password"}
                  value={editApiKey}
                  onChange={(e) => setEditApiKey(e.target.value)}
                  placeholder={t("createModal.keyPlaceholder")}
                  autoComplete="new-password"
                />
                <button
                  type="button"
                  className="reveal"
                  onClick={() => setEditShowKey(!editShowKey)}
                  aria-label={editShowKey ? t("createModal.hide") : t("createModal.show")}
                >
                  {editShowKey ? <EyeOff size={14} strokeWidth={2} /> : <Eye size={14} strokeWidth={2} />}
                </button>
              </div>
              <small>{t("editModal.keyHelp")}</small>
            </div>

            <div className="field">
              <label htmlFor="editAssistantInstructions">{t("editModal.customInstructionsLabel")}</label>
              <textarea
                id="editAssistantInstructions"
                rows={3}
                value={editInstructions}
                onChange={(e) => setEditInstructions(e.target.value)}
                placeholder={t("editModal.customInstructionsPlaceholder")}
              />
            </div>

            <div className="modal-actions">
              <button
                type="button"
                className="cancel"
                onClick={handleCloseEdit}
                disabled={isEditSubmitting}
              >
                {t("createModal.cancel")}
              </button>
              <button
                type="submit"
                className="primary"
                disabled={isEditSubmitting}
              >
                {isEditSubmitting ? t("editModal.submitting") : t("editModal.submit")}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
