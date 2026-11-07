"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useApp } from "@/lib/context";
import { api, Assistant, Chat } from "@/lib/api";
import { modalSwal } from "@/lib/swal";
import { CreateAssistantModal } from "./CreateAssistantModal";
import {
  ChevronDown,
  ChevronUp,
  ChevronRight,
  Plus,
  LayoutGrid,
  BookOpen,
  MessageSquarePlus,
  LogOut,
  Check,
  Sun,
  Moon,
  X,
  MoreHorizontal,
  Pencil,
  Trash2,
} from "lucide-react";

interface SidebarProps {
  currentAssistant?: Assistant | null;
  activeView?: "assistants" | "knowledge" | "chat";
}

export function Sidebar({ currentAssistant, activeView = "assistants" }: SidebarProps) {
  const { t, theme, toggleTheme, language, setLanguage, user, signOut, sidebarOpen, setSidebarOpen } = useApp();
  const pathname = usePathname();
  const router = useRouter();

  const [assistants, setAssistants] = useState<Assistant[]>([]);
  const [chats, setChats] = useState<Chat[]>([]);
  const [isChatsOpen, setIsChatsOpen] = useState(true);
  const [isUserMenuOpen, setIsUserMenuOpen] = useState(false);
  const [isAssistantMenuOpen, setIsAssistantMenuOpen] = useState(false);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [openChatMenu, setOpenChatMenu] = useState<{ id: string; top: number; right: number } | null>(null);

  // Close chat dropdown on window click / scroll
  useEffect(() => {
    const handleGlobalClick = () => setOpenChatMenu(null);
    window.addEventListener("click", handleGlobalClick);
    window.addEventListener("scroll", handleGlobalClick, true);
    return () => {
      window.removeEventListener("click", handleGlobalClick);
      window.removeEventListener("scroll", handleGlobalClick, true);
    };
  }, []);

  // Load assistants for picker
  useEffect(() => {
    api.getAssistants()
      .then(setAssistants)
      .catch(() => {});
  }, []);

  // Auto-close sidebar on mobile/tablet after route navigation
  useEffect(() => {
    if (typeof window !== "undefined" && window.innerWidth <= 800) {
      setSidebarOpen(false);
    }
  }, [pathname, setSidebarOpen]);

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
      {/* Mobile/Tablet Backdrop Overlay */}
      <div
        className={`sidebar-overlay ${sidebarOpen ? "open" : ""}`}
        onClick={() => setSidebarOpen(false)}
        aria-hidden="true"
      />

      <aside className={`sidebar ${sidebarOpen ? "open" : "collapsed"}`}>
        {/* Brand */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <Link href="/" className="brand" style={{ padding: "2px 8px" }}>
            <div className="brand-mark">S</div>
            <div>
              <div className="brand-name">{t("brand.name")}</div>
              <div className="brand-sub">{t("brand.tagline")}</div>
            </div>
          </Link>

          <button
            type="button"
            className="sidebar-close-btn"
            onClick={() => setSidebarOpen(false)}
            aria-label="Close sidebar"
            title="Close sidebar"
          >
            <X size={18} strokeWidth={2} />
          </button>
        </div>

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
            <ChevronDown size={14} color="var(--muted)" strokeWidth={2} />
          </button>

          <button
            type="button"
            className="plus"
            id="openCreate"
            onClick={() => setIsCreateModalOpen(true)}
            aria-label="Create assistant"
            title="Create assistant"
          >
            <Plus size={16} strokeWidth={2.5} />
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
                <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                  <LayoutGrid size={13} strokeWidth={2} />
                  {t("nav.assistants")} (Overview)
                </span>
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
            <span className="nav-icon"><LayoutGrid size={15} strokeWidth={2} /></span>
            {t("nav.assistants")}
          </Link>

          {currentAssistant && (
            <>
              <Link
                href={`/assistants/${currentAssistant.slug}/knowledge`}
                className={`nav-item ${activeView === "knowledge" ? "active" : ""}`}
                id="navKnowledge"
              >
                <span className="nav-icon"><BookOpen size={15} strokeWidth={2} /></span>
                {t("nav.knowledgeBase")}
              </Link>
              <Link
                href={`/assistants/${currentAssistant.slug}/chat`}
                className={`nav-item ${activeView === "chat" && pathname === `/assistants/${currentAssistant.slug}/chat` ? "active" : ""}`}
                id="navNewChat"
              >
                <span className="nav-icon"><MessageSquarePlus size={15} strokeWidth={2} /></span>
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
                {isChatsOpen
                  ? <ChevronDown size={13} strokeWidth={2} />
                  : <ChevronRight size={13} strokeWidth={2} />
                }
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
                    const isMenuOpen = openChatMenu?.id === c.id;

                    const handleRename = async () => {
                      setOpenChatMenu(null);
                      const { value: newTitle } = await modalSwal.fire({
                        title: t("sidebarActions.renameTitle"),
                        input: "text",
                        inputValue: c.title,
                        inputPlaceholder: t("sidebarActions.renamePlaceholder"),
                        showCancelButton: true,
                        confirmButtonText: t("sidebarActions.renameSave"),
                        cancelButtonText: t("sidebarActions.renameCancel"),
                        inputValidator: (val) => {
                          if (!val || !val.trim()) {
                            return t("sidebarActions.renameValidation");
                          }
                          return null;
                        },
                      });

                      if (!newTitle?.trim() || newTitle.trim() === c.title) return;
                      try {
                        await api.updateChat(currentAssistant.slug, c.id, { title: newTitle.trim() });
                        setChats((prev) =>
                          prev.map((ch) => (ch.id === c.id ? { ...ch, title: newTitle.trim() } : ch))
                        );
                      } catch {
                        // silently fail — user stays in place
                      }
                    };

                    const handleDelete = async () => {
                      setOpenChatMenu(null);
                      const result = await modalSwal.fire({
                        title: t("sidebarActions.deleteTitle"),
                        text: t("sidebarActions.deleteText", { title: c.title }),
                        icon: "warning",
                        showCancelButton: true,
                        confirmButtonColor: "#dc2626",
                        confirmButtonText: t("sidebarActions.deleteConfirm"),
                        cancelButtonText: t("sidebarActions.deleteCancel"),
                      });

                      if (!result.isConfirmed) return;

                      try {
                        await api.deleteChat(currentAssistant.slug, c.id);
                        setChats((prev) => prev.filter((ch) => ch.id !== c.id));
                        if (isSelected) {
                          router.push(`/assistants/${currentAssistant.slug}/chat`);
                        }
                      } catch {
                        // silently fail
                      }
                    };

                    return (
                      <div key={c.id} className="chat-item-row">
                        <Link
                          href={`/assistants/${currentAssistant.slug}/chat/${c.id}`}
                          className={`chat-item ${isSelected ? "selected" : ""}`}
                          title={c.title}
                        >
                          {c.title}
                        </Link>

                        <button
                          type="button"
                          className="chat-menu-btn"
                          aria-label="Chat options"
                          onClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            if (isMenuOpen) {
                              setOpenChatMenu(null);
                            } else {
                              const rect = e.currentTarget.getBoundingClientRect();
                              setOpenChatMenu({
                                id: c.id,
                                top: rect.bottom + 4,
                                right: window.innerWidth - rect.right,
                              });
                            }
                          }}
                        >
                          <MoreHorizontal size={14} strokeWidth={2} />
                        </button>

                        {isMenuOpen && (
                          <div
                            className="chat-menu-floating"
                            style={{
                              position: "fixed",
                              top: `${openChatMenu.top}px`,
                              right: `${openChatMenu.right}px`,
                            }}
                            onClick={(e) => e.stopPropagation()}
                          >
                            <button type="button" className="chat-menu-item" onClick={handleRename}>
                              <Pencil size={12} strokeWidth={2} />
                              {t("sidebarActions.renameBtn")}
                            </button>
                            <button
                              type="button"
                              className="chat-menu-item chat-menu-item-danger"
                              onClick={handleDelete}
                            >
                              <Trash2 size={12} strokeWidth={2} />
                              {t("sidebarActions.deleteBtn")}
                            </button>
                          </div>
                        )}
                      </div>
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
                {language === "en" && <Check size={13} color="var(--accent)" strokeWidth={2.5} />}
              </button>
              <button
                type="button"
                className="menu-item"
                onClick={() => setLanguage("pt")}
              >
                <span>Português</span>
                {language === "pt" && <Check size={13} color="var(--accent)" strokeWidth={2.5} />}
              </button>
              <button
                type="button"
                className="menu-item"
                onClick={() => setLanguage("es")}
              >
                <span>Español</span>
                {language === "es" && <Check size={13} color="var(--accent)" strokeWidth={2.5} />}
              </button>

              <div style={{ height: "1px", background: "var(--border)", margin: "6px 0" }} />

              <button
                type="button"
                className="menu-item"
                id="themeButton"
                onClick={toggleTheme}
              >
                <span>{t("user.appearance")}</span>
                <span style={{ display: "flex", alignItems: "center", gap: "4px" }}>
                  {theme === "dark"
                    ? <><Moon size={12} strokeWidth={2} /> {t("user.darkMode")}</>
                    : <><Sun size={12} strokeWidth={2} /> {t("user.lightMode")}</>
                  }
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
                <LogOut size={13} strokeWidth={2} />
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
            {isUserMenuOpen
              ? <ChevronUp size={14} color="var(--muted)" strokeWidth={2} />
              : <ChevronDown size={14} color="var(--muted)" strokeWidth={2} />
            }
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
