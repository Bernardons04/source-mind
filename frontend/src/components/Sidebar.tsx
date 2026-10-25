"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useApp } from "@/lib/context";
import { api, Assistant, Chat } from "@/lib/api";
import { CreateAssistantModal } from "./CreateAssistantModal";

interface SidebarProps {
  currentAssistant?: Assistant | null;
  activeView?: "assistants" | "knowledge" | "chat";
}

export function Sidebar({ currentAssistant, activeView = "assistants" }: SidebarProps) {
  const { t, theme, toggleTheme, language, setLanguage, user, signOut } = useApp();
  const pathname = usePathname();
  const router = useRouter();

  const [assistants, setAssistants] = useState<Assistant[]>([]);
  const [chats, setChats] = useState<Chat[]>([]);
  const [isChatsOpen, setIsChatsOpen] = useState(true);
  const [isUserMenuOpen, setIsUserMenuOpen] = useState(false);
  const [isAssistantMenuOpen, setIsAssistantMenuOpen] = useState(false);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);

  // Load assistants for picker
  useEffect(() => {
    api.getAssistants()
      .then(setAssistants)
      .catch(() => {});
  }, []);

  // Load chats for selected assistant
  useEffect(() => {
    if (currentAssistant?.slug) {
      api.getChats(currentAssistant.slug)
        .then(setChats)
        .catch(() => {});
    } else {
      setChats([]);
    }
  }, [currentAssistant?.slug, pathname]);

  const handleAssistantCreated = (created: Assistant) => {
    setAssistants((prev) => [created, ...prev]);
    router.push(`/assistants/${created.slug}/chat`);
  };

  const assistantInitial = currentAssistant?.name
    ? currentAssistant.name.charAt(0).toUpperCase()
    : "A";

  return (
    <>
      <aside className="sidebar">
        {/* Brand */}
        <Link href="/" className="brand">
          <div className="brand-mark">M</div>
          <div>
            <div className="brand-name">{t("brand.name")}</div>
            <div className="brand-sub">{t("brand.tagline")}</div>
          </div>
        </Link>

        {/* Assistant Picker + Create Button */}
        <div className="assistant-picker" style={{ position: "relative" }}>
          <button
            type="button"
            className="picker"
            id="assistantPicker"
            onClick={() => setIsAssistantMenuOpen(!isAssistantMenuOpen)}
            aria-label="Choose assistant"
          >
            <span className="picker-left">
              <span className="assistant-icon">{assistantInitial}</span>
              <span className="picker-label">
                {currentAssistant ? currentAssistant.name : t("nav.assistants")}
              </span>
            </span>
            <span style={{ fontSize: "11px", color: "var(--muted)" }}>⌄</span>
          </button>

          <button
            type="button"
            className="plus"
            id="openCreate"
            onClick={() => setIsCreateModalOpen(true)}
            aria-label="Create assistant"
            title="Create assistant"
          >
            +
          </button>

          {/* Assistant dropdown */}
          {isAssistantMenuOpen && (
            <div
              style={{
                position: "absolute",
                top: "48px",
                left: 0,
                right: 0,
                background: "var(--panel)",
                border: "1px solid var(--border)",
                borderRadius: "10px",
                padding: "6px",
                zIndex: 150,
                boxShadow: "0 10px 30px #0008",
                maxHeight: "220px",
                overflowY: "auto",
              }}
            >
              <Link
                href="/assistants"
                className="menu-item"
                onClick={() => setIsAssistantMenuOpen(false)}
              >
                <span>▦ {t("nav.assistants")} (Overview)</span>
              </Link>
              <div style={{ height: "1px", background: "var(--border)", margin: "4px 0" }} />
              {assistants.length === 0 ? (
                <div style={{ padding: "8px 10px", fontSize: "12px", color: "var(--muted)" }}>
                  No assistants yet
                </div>
              ) : (
                assistants.map((ast) => (
                  <Link
                    key={ast.id}
                    href={`/assistants/${ast.slug}/chat`}
                    className="menu-item"
                    onClick={() => setIsAssistantMenuOpen(false)}
                  >
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {ast.name}
                    </span>
                    <span style={{ fontSize: "11px", color: "var(--muted)" }}>
                      {ast.document_count} docs
                    </span>
                  </Link>
                ))
              )}
            </div>
          )}
        </div>

        {/* Navigation */}
        <nav className="nav">
          <Link
            href="/assistants"
            className={`nav-item ${activeView === "assistants" ? "active" : ""}`}
            id="navAssistants"
          >
            <span className="nav-icon">▦</span>
            {t("nav.assistants")}
          </Link>

          {currentAssistant && (
            <>
              <Link
                href={`/assistants/${currentAssistant.slug}/knowledge`}
                className={`nav-item ${activeView === "knowledge" ? "active" : ""}`}
                id="navKnowledge"
              >
                <span className="nav-icon">▤</span>
                {t("nav.knowledgeBase")}
              </Link>
              <Link
                href={`/assistants/${currentAssistant.slug}/chat`}
                className={`nav-item ${activeView === "chat" ? "active" : ""}`}
                id="navNewChat"
              >
                <span className="nav-icon">✳</span>
                {t("nav.newChat")}
              </Link>
            </>
          )}
        </nav>

        {/* Expandable Chats List (Only when assistant is selected) */}
        {currentAssistant && (
          <>
            <div className="section-label">
              <span>{t("nav.chats")}</span>
              <button
                type="button"
                onClick={() => setIsChatsOpen(!isChatsOpen)}
                aria-label="Toggle chats"
              >
                {isChatsOpen ? "⌄" : "›"}
              </button>
            </div>

            {isChatsOpen && (
              <div className="chat-list" id="chatList">
                {chats.length === 0 ? (
                  <span style={{ fontSize: "11px", color: "var(--muted)", padding: "4px 10px" }}>
                    {t("nav.noChats")}
                  </span>
                ) : (
                  chats.map((c) => {
                    const isSelected = pathname === `/assistants/${currentAssistant.slug}/chat/${c.id}`;
                    return (
                      <Link
                        key={c.id}
                        href={`/assistants/${currentAssistant.slug}/chat/${c.id}`}
                        className={`chat-item ${isSelected ? "selected" : ""}`}
                        title={c.title}
                      >
                        {c.title}
                      </Link>
                    );
                  })
                )}
              </div>
            )}
          </>
        )}

        <div className="side-spacer" />

        {/* User Area with Profile Menu */}
        <div className="user-area">
          {isUserMenuOpen && (
            <div className="user-menu open" id="userMenu">
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "6px 10px 4px",
                  fontSize: "11px",
                  color: "var(--muted)",
                  textTransform: "uppercase",
                  letterSpacing: "0.08em",
                }}
              >
                <span>{t("user.language")}</span>
              </div>
              <button
                type="button"
                className="menu-item"
                onClick={() => setLanguage("en")}
              >
                <span>English</span>
                {language === "en" && <span style={{ color: "var(--accent)" }}>✓</span>}
              </button>
              <button
                type="button"
                className="menu-item"
                onClick={() => setLanguage("pt")}
              >
                <span>Português</span>
                {language === "pt" && <span style={{ color: "var(--accent)" }}>✓</span>}
              </button>
              <button
                type="button"
                className="menu-item"
                onClick={() => setLanguage("es")}
              >
                <span>Español</span>
                {language === "es" && <span style={{ color: "var(--accent)" }}>✓</span>}
              </button>

              <div style={{ height: "1px", background: "var(--border)", margin: "6px 0" }} />

              <button
                type="button"
                className="menu-item"
                id="themeButton"
                onClick={toggleTheme}
              >
                <span>{t("user.appearance")}</span>
                <span id="themeLabel">
                  {theme === "dark" ? `${t("user.darkMode")} ✓` : `${t("user.lightMode")} ✓`}
                </span>
              </button>

              <div style={{ height: "1px", background: "var(--border)", margin: "6px 0" }} />

              <button
                type="button"
                className="menu-item"
                id="signOutButton"
                onClick={signOut}
                style={{ color: "#f87171" }}
              >
                <span>{t("user.signOut")}</span>
                <span>↪</span>
              </button>
            </div>
          )}

          <button
            type="button"
            className="user-button"
            id="userButton"
            onClick={() => setIsUserMenuOpen(!isUserMenuOpen)}
          >
            <span className="avatar">{user.initials || "YA"}</span>
            <span className="user-info">
              <span className="user-name">{user.name || t("user.account")}</span>
              <span className="user-email">{user.email || "you@example.com"}</span>
            </span>
            <span style={{ color: "var(--muted)" }}>{isUserMenuOpen ? "⌄" : "⌃"}</span>
          </button>
        </div>
      </aside>

      {/* Assistant Creation Modal */}
      <CreateAssistantModal
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
        onCreated={handleAssistantCreated}
      />
    </>
  );
}
