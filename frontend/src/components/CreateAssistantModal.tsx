"use client";

import React, { useState } from "react";
import { useApp } from "@/lib/context";
import { api, Assistant } from "@/lib/api";

interface CreateAssistantModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCreated: (assistant: Assistant) => void;
}

export function CreateAssistantModal({ isOpen, onClose, onCreated }: CreateAssistantModalProps) {
  const { t, showToast } = useApp();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [instructions, setInstructions] = useState("");
  const [isSlugCustom, setIsSlugCustom] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleNameChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setName(val);
    if (!isSlugCustom) {
      const generated = val
        .toLowerCase()
        .trim()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, "");
      setSlug(generated);
    }
  };

  const handleSlugChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setIsSlugCustom(true);
    setSlug(
      e.target.value
        .toLowerCase()
        .replace(/[^a-z0-9-]/g, "")
        .replace(/--+/g, "-")
    );
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !slug.trim() || !apiKey.trim()) {
      setError("Please fill out all required fields.");
      return;
    }

    try {
      setIsSubmitting(true);
      setError(null);
      const newAssistant = await api.createAssistant({
        name: name.trim(),
        slug: slug.trim(),
        openrouter_api_key: apiKey.trim(),
        instructions: { prompt: instructions.trim() },
      });

      showToast(`Assistant "${newAssistant.name}" created!`);
      onCreated(newAssistant);
      onClose();
      // Reset form
      setName("");
      setSlug("");
      setApiKey("");
      setInstructions("");
      setIsSlugCustom(false);
    } catch (err: any) {
      setError(err.message || "Failed to create assistant");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div
      className="overlay"
      role="dialog"
      aria-modal="true"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <form className="modal" onSubmit={handleSubmit}>
        <div className="modal-head">
          <div>
            <h2>{t("createModal.title")}</h2>
            <p className="modal-copy">{t("createModal.subtitle")}</p>
          </div>
          <button
            type="button"
            className="close"
            onClick={onClose}
            aria-label="Close"
          >
            ×
          </button>
        </div>

        {error && (
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
            {error}
          </div>
        )}

        <div className="field">
          <label htmlFor="modalAssistantName">{t("createModal.nameLabel")}</label>
          <input
            id="modalAssistantName"
            value={name}
            onChange={handleNameChange}
            placeholder={t("createModal.namePlaceholder")}
            required
            autoFocus
          />
        </div>

        <div className="field">
          <label htmlFor="modalAssistantSlug">{t("createModal.slugLabel")}</label>
          <input
            id="modalAssistantSlug"
            value={slug}
            onChange={handleSlugChange}
            placeholder={t("createModal.slugPlaceholder")}
            pattern="[a-z0-9]+(?:-[a-z0-9]+)*"
            required
          />
          <small>{t("createModal.slugHelp")}</small>
        </div>

        <div className="field">
          <label htmlFor="modalApiKey">{t("createModal.keyLabel")}</label>
          <div className="input-with-action">
            <input
              id="modalApiKey"
              type={showKey ? "text" : "password"}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={t("createModal.keyPlaceholder")}
              autoComplete="new-password"
              required
            />
            <button
              type="button"
              className="reveal"
              onClick={() => setShowKey(!showKey)}
            >
              {showKey ? t("createModal.hide") : t("createModal.show")}
            </button>
          </div>
          <small>{t("createModal.keyHelp")}</small>
        </div>

        <div className="field">
          <label htmlFor="modalInstructions">Custom Instructions (Optional)</label>
          <textarea
            id="modalInstructions"
            rows={2}
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            placeholder="e.g. Always respond in formal English, focus on HR policy details..."
          />
        </div>

        <div className="modal-actions">
          <button
            type="button"
            className="cancel"
            onClick={onClose}
            disabled={isSubmitting}
          >
            {t("createModal.cancel")}
          </button>
          <button
            type="submit"
            className="primary"
            disabled={isSubmitting}
          >
            {isSubmitting ? t("createModal.submitting") : t("createModal.submit")}
          </button>
        </div>
      </form>
    </div>
  );
}
