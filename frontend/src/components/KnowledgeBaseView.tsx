"use client";

import React, { useState, useEffect, useRef } from "react";
import Link from "next/link";
import { useApp } from "@/lib/context";
import { api, Assistant, DocumentItem } from "@/lib/api";
import {
  ArrowLeft,
  Plus,
  Search,
  Upload,
  MoreHorizontal,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";

interface KnowledgeBaseViewProps {
  assistant: Assistant;
  initialDocuments: DocumentItem[];
}

export function KnowledgeBaseView({ assistant, initialDocuments }: KnowledgeBaseViewProps) {
  const { t, showToast } = useApp();
  const [documents, setDocuments] = useState<DocumentItem[]>(initialDocuments);
  const [searchTerm, setSearchTerm] = useState("");
  const [activeTab, setActiveTab] = useState<"documents" | "processing" | "settings">("documents");
  const [isUploading, setIsUploading] = useState(false);
  const [activeMenuDocId, setActiveMenuDocId] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const handleOutsideClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest(".doc-actions-wrapper")) {
        setActiveMenuDocId(null);
      }
    };
    if (activeMenuDocId) {
      document.addEventListener("mousedown", handleOutsideClick);
    }
    return () => {
      document.removeEventListener("mousedown", handleOutsideClick);
    };
  }, [activeMenuDocId]);

  const refreshDocuments = async () => {
    try {
      const refreshed = await api.getDocuments(assistant.slug);
      setDocuments(refreshed);
    } catch (err: any) {
      showToast(err.message || "Failed to refresh documents");
    }
  };

  // Automated polling when documents are being ingested
  useEffect(() => {
    const hasActiveProcessing = documents.some(
      (d) =>
        d.processing_status === "pending_upload" ||
        d.processing_status === "pending" ||
        d.processing_status === "processing"
    );
    if (!hasActiveProcessing) return;

    const intervalId = setInterval(async () => {
      try {
        const refreshed = await api.getDocuments(assistant.slug);
        setDocuments(refreshed);
      } catch {
        // Ignore polling errors and retry on next interval
      }
    }, 2500);

    return () => clearInterval(intervalId);
  }, [documents, assistant.slug]);

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length === 0) return;

    setIsUploading(true);
    for (const file of files) {
      const exists = documents.some((d) => d.name.toLowerCase() === file.name.toLowerCase());
      if (exists) {
        const proceed = window.confirm(
          `Um documento com o nome "${file.name}" já existe nesta base de conhecimento. Deseja enviar assim mesmo como um novo documento?`
        );
        if (!proceed) {
          continue;
        }
      }

      try {
        showToast(`Enviando "${file.name}"...`);
        const newDoc = await api.uploadDocument(assistant.slug, file);
        setDocuments((prev) => [newDoc, ...prev]);
        showToast(`"${file.name}" enviado com sucesso! Processamento assíncrono em andamento...`);
      } catch (err: any) {
        showToast(`Erro ao enviar "${file.name}": ${err.message}`);
      }
    }
    setIsUploading(false);
    if (fileInputRef.current) fileInputRef.current.value = "";
    refreshDocuments();
  };

  const handleReprocess = async (doc: DocumentItem) => {
    try {
      showToast(`Reenviando "${doc.name}" para a fila de processamento...`);
      const updated = await api.reprocessDocument(assistant.slug, doc.id);
      setDocuments((prev) => prev.map((d) => (d.id === updated.id ? updated : d)));
      showToast(`"${doc.name}" adicionado à fila de processamento assíncrono.`);
    } catch (err: any) {
      showToast(`Falha no reprocessamento: ${err.message}`);
    } finally {
      setActiveMenuDocId(null);
    }
  };

  const handleDelete = async (doc: DocumentItem) => {
    if (!confirm(t("knowledge.removeConfirm", { name: doc.name }))) return;
    try {
      await api.deleteDocument(assistant.slug, doc.id);
      setDocuments((prev) => prev.filter((d) => d.id !== doc.id));
      showToast(`Document "${doc.name}" deleted.`);
    } catch (err: any) {
      showToast(`Delete failed: ${err.message}`);
    } finally {
      setActiveMenuDocId(null);
    }
  };

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const formatDate = (isoStr: string) => {
    try {
      return new Date(isoStr).toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
      });
    } catch {
      return isoStr;
    }
  };

  const filteredDocs = documents.filter((d) =>
    d.name.toLowerCase().includes(searchTerm.toLowerCase().trim())
  );

  const indexedCount = documents.filter((d) => d.processing_status === "indexed").length;
  const kbStatusText =
    documents.length === 0
      ? "Empty"
      : indexedCount === documents.length
      ? t("knowledge.ready")
      : "Indexing...";

  return (
    <>
      <Link href="/assistants" className="back-link" id="backToAssistants">
        <ArrowLeft size={14} strokeWidth={2} />
        {t("nav.assistants")}
      </Link>

      <div className="page-heading-row">
        <div>
          <div className="eyebrow">
            <span className="eyebrow-dot" />
            {t("knowledge.eyebrow")}
          </div>
          <h1>{assistant.name}</h1>
          <p className="intro">{t("knowledge.intro")}</p>
        </div>

        <button
          type="button"
          className="primary"
          id="uploadTop"
          onClick={() => fileInputRef.current?.click()}
          disabled={isUploading}
          style={{ display: "flex", alignItems: "center", gap: "7px" }}
        >
          {isUploading ? (
            "Uploading..."
          ) : (
            <>
              <Plus size={15} strokeWidth={2.5} />
              {t("knowledge.addDocs")}
            </>
          )}
        </button>
      </div>

      {/* Tabs */}
      <div className="view-tabs">
        <button
          type="button"
          className={`view-tab ${activeTab === "documents" ? "active" : ""}`}
          onClick={() => setActiveTab("documents")}
        >
          {t("knowledge.tabDocuments")}
        </button>
        <button
          type="button"
          className={`view-tab ${activeTab === "processing" ? "active" : ""}`}
          onClick={() => setActiveTab("processing")}
        >
          {t("knowledge.tabProcessing")}
        </button>
        <button
          type="button"
          className={`view-tab ${activeTab === "settings" ? "active" : ""}`}
          onClick={() => setActiveTab("settings")}
        >
          {t("knowledge.tabSettings")}
        </button>
      </div>

      {activeTab === "documents" && (
        <>
          {/* Mini Stats */}
          <div className="stats-row">
            <div className="mini-stat">
              <span>{t("knowledge.totalDocs")}</span>
              <strong id="docCount">{documents.length}</strong>
            </div>
            <div className="mini-stat">
              <span>{t("knowledge.indexedDocs")}</span>
              <strong>{indexedCount}</strong>
            </div>
            <div className="mini-stat">
              <span>{t("knowledge.status")}</span>
              <strong style={{ fontSize: "16px", color: "var(--accent)", marginTop: "10px" }}>
                {kbStatusText}
              </strong>
            </div>
          </div>

          {/* Upload Zone */}
          <div className="upload-zone">
            <div className="upload-symbol">
              <Upload size={22} strokeWidth={1.5} />
            </div>
            <div className="upload-copy">
              <strong>{t("knowledge.uploadTitle")}</strong>
              <p>{t("knowledge.uploadSubtitle")}</p>
            </div>
            <button
              type="button"
              className="quiet-button"
              id="uploadInline"
              onClick={() => fileInputRef.current?.click()}
              disabled={isUploading}
            >
              {isUploading ? "Processing..." : t("knowledge.chooseFiles")}
            </button>
            <input
              type="file"
              ref={fileInputRef}
              onChange={handleFileUpload}
              accept=".pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain"
              multiple
              hidden
            />
          </div>

          {/* Documents Panel */}
          <div className="panel">
            <div className="panel-head">
              <div>
                <strong>{t("knowledge.tabDocuments")}</strong>
                <p>Files available to {assistant.name}</p>
              </div>
              <label className="kb-search">
                <Search size={13} strokeWidth={2} color="var(--muted)" />
                <input
                  id="docSearch"
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  placeholder={t("knowledge.searchDocs")}
                />
              </label>
            </div>

            <div id="documentRows">
              {filteredDocs.length === 0 ? (
                <div style={{ padding: "35px", textAlign: "center", color: "var(--muted)", fontSize: "13px" }}>
                  {t("knowledge.noDocs")}
                </div>
              ) : (
                filteredDocs.map((doc) => {
                  const isDocx = doc.name.toLowerCase().endsWith(".docx");
                  const isTxt = doc.name.toLowerCase().endsWith(".txt");
                  const iconClass = isDocx ? "docx" : isTxt ? "txt" : "";
                  const iconText = isDocx ? "DOCX" : isTxt ? "TXT" : "PDF";

                  let statusText = t("knowledge.statusIndexed");
                  let statusClass = "indexed";
                  if (doc.processing_status === "failed") {
                    statusText = t("knowledge.statusFailed");
                    statusClass = "failed";
                  } else if (doc.processing_status === "processing" || doc.processing_status === "pending") {
                    statusText = t("knowledge.statusProcessing");
                    statusClass = "processing";
                  }

                  const isMenuOpen = activeMenuDocId === doc.id;

                  return (
                    <div className="doc-row" key={doc.id}>
                      <div className="doc-main">
                        <div className={`file-icon ${iconClass}`}>{iconText}</div>
                        <div style={{ minWidth: 0 }}>
                          <div className="doc-name">{doc.name}</div>
                          <div className="doc-meta">
                            {formatFileSize(doc.file_size)} · Added {formatDate(doc.created_at)}
                            {doc.processing_error && (
                              <span style={{ color: "#f87171", marginLeft: "6px" }}>
                                (Error: {doc.processing_error.slice(0, 50)}...)
                              </span>
                            )}
                          </div>
                        </div>
                      </div>

                      <span className={`doc-status ${statusClass}`}>{statusText}</span>

                      <div className="doc-actions-wrapper" style={{ position: "relative" }}>
                        <button
                          type="button"
                          className="row-menu"
                          onClick={() => setActiveMenuDocId(isMenuOpen ? null : doc.id)}
                          aria-label="Document actions"
                        >
                          <MoreHorizontal size={15} strokeWidth={2} />
                        </button>

                        {isMenuOpen && (
                          <div
                            style={{
                              position: "absolute",
                              right: 0,
                              top: "30px",
                              background: "var(--panel)",
                              border: "1px solid var(--border)",
                              borderRadius: "9px",
                              padding: "5px",
                              zIndex: 100,
                              boxShadow: "0 10px 25px #0007",
                              minWidth: "180px",
                            }}
                          >
                            <button
                              type="button"
                              className="menu-item"
                              onClick={() => handleReprocess(doc)}
                            >
                              <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                                <RefreshCw size={13} strokeWidth={2} />
                                {t("knowledge.reprocess")}
                              </span>
                            </button>
                            <button
                              type="button"
                              className="menu-item"
                              style={{ color: "#f87171" }}
                              onClick={() => handleDelete(doc)}
                            >
                              <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                                <Trash2 size={13} strokeWidth={2} />
                                {t("knowledge.delete")}
                              </span>
                            </button>
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </>
      )}

      {activeTab === "processing" && (
        <div className="panel" style={{ padding: "24px" }}>
          <strong style={{ fontSize: "14px" }}>Active Ingestion Pipeline</strong>
          <p style={{ color: "var(--muted)", fontSize: "12px", marginTop: "6px" }}>
            Real-time status of document text parsing, chunk segmentation, and vector embedding indexing.
          </p>
          <div style={{ marginTop: "16px", display: "flex", flexDirection: "column", gap: "10px" }}>
            {documents.map((d) => (
              <div
                key={d.id}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  padding: "10px 14px",
                  background: "var(--panel-2)",
                  borderRadius: "8px",
                  fontSize: "12px",
                }}
              >
                <span>{d.name}</span>
                <span style={{ color: d.processing_status === "indexed" ? "var(--accent)" : "#fbbf24" }}>
                  {d.processing_status.toUpperCase()}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {activeTab === "settings" && (
        <div className="panel" style={{ padding: "24px" }}>
          <strong style={{ fontSize: "14px" }}>Knowledge Retrieval Settings</strong>
          <p style={{ color: "var(--muted)", fontSize: "12px", marginTop: "6px" }}>
            Configured chunk size: 500 words · Chunk overlap: 80 words · Hybrid RRF Fusion: pgvector Cosine + PostgreSQL Full-Text Search.
          </p>
        </div>
      )}
    </>
  );
}
