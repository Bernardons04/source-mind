import { createClient } from "./supabase/client";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

async function getAuthHeader(): Promise<HeadersInit> {
  const supabase = createClient();
  const { data: { session } } = await supabase.auth.getSession();
  const headers: Record<string, string> = {};

  if (session?.access_token) {
    headers["Authorization"] = `Bearer ${session.access_token}`;
  } else {
    // Development fallback token if Supabase Auth isn't fully configured yet
    const localDevToken = typeof window !== "undefined" ? localStorage.getItem("dev_auth_token") : null;
    if (localDevToken) {
      headers["Authorization"] = `Bearer ${localDevToken}`;
    }
  }
  return headers;
}

export interface Assistant {
  id: string;
  name: string;
  slug: string;
  instructions: Record<string, any>;
  has_api_key: boolean;
  document_count: number;
  created_at: string;
  updated_at: string;
}

export interface DocumentItem {
  id: string;
  assistant_id: string;
  name: string;
  current_version_number: number;
  file_size: number;
  mime_type: string;
  processing_status: "pending" | "processing" | "indexed" | "failed";
  processing_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface Chat {
  id: string;
  assistant_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface Citation {
  id: string;
  quote: string;
  document_chunk_id: string;
  document_name?: string;
  metadata: Record<string, any>;
  created_at: string;
}

export interface MessageEvent {
  id: string;
  sequence_number: number;
  event_type: string;
  payload: Record<string, any>;
  created_at: string;
}

export interface Message {
  id: string;
  chat_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  citations: Citation[];
  events: MessageEvent[];
}

export const api = {
  // Assistants
  async getAssistants(): Promise<Assistant[]> {
    const authHeaders = await getAuthHeader();
    const res = await fetch(`${API_BASE}/assistants`, { headers: authHeaders });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async createAssistant(data: {
    name: string;
    slug: string;
    openrouter_api_key: string;
    instructions?: Record<string, any>;
  }): Promise<Assistant> {
    const authHeaders = await getAuthHeader();
    const res = await fetch(`${API_BASE}/assistants`, {
      method: "POST",
      headers: { ...authHeaders, "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Failed to create assistant" }));
      throw new Error(err.detail || "Failed to create assistant");
    }
    return res.json();
  },

  async getAssistant(slug: string): Promise<Assistant> {
    const authHeaders = await getAuthHeader();
    const res = await fetch(`${API_BASE}/assistants/${slug}`, { headers: authHeaders });
    if (!res.ok) throw new Error("Assistant not found");
    return res.json();
  },

  async updateAssistant(
    slug: string,
    data: { name?: string; instructions?: Record<string, any>; openrouter_api_key?: string }
  ): Promise<Assistant> {
    const authHeaders = await getAuthHeader();
    const res = await fetch(`${API_BASE}/assistants/${slug}`, {
      method: "PUT",
      headers: { ...authHeaders, "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async deleteAssistant(slug: string): Promise<void> {
    const authHeaders = await getAuthHeader();
    const res = await fetch(`${API_BASE}/assistants/${slug}`, {
      method: "DELETE",
      headers: authHeaders,
    });
    if (!res.ok) throw new Error(await res.text());
  },

  // Documents
  async getDocuments(slug: string): Promise<DocumentItem[]> {
    const authHeaders = await getAuthHeader();
    const res = await fetch(`${API_BASE}/assistants/${slug}/documents`, { headers: authHeaders });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async uploadDocument(slug: string, file: File): Promise<DocumentItem> {
    const authHeaders = await getAuthHeader();
    const formData = new FormData();
    formData.append("file", file);

    const res = await fetch(`${API_BASE}/assistants/${slug}/documents`, {
      method: "POST",
      headers: authHeaders,
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Upload failed" }));
      throw new Error(err.detail || "Upload failed");
    }
    return res.json();
  },

  async replaceDocument(slug: string, docId: string, file: File): Promise<DocumentItem> {
    const authHeaders = await getAuthHeader();
    const formData = new FormData();
    formData.append("file", file);

    const res = await fetch(`${API_BASE}/assistants/${slug}/documents/${docId}/replace`, {
      method: "POST",
      headers: authHeaders,
      body: formData,
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async reprocessDocument(slug: string, docId: string): Promise<DocumentItem> {
    const authHeaders = await getAuthHeader();
    const res = await fetch(`${API_BASE}/assistants/${slug}/documents/${docId}/reprocess`, {
      method: "POST",
      headers: authHeaders,
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async deleteDocument(slug: string, docId: string): Promise<void> {
    const authHeaders = await getAuthHeader();
    const res = await fetch(`${API_BASE}/assistants/${slug}/documents/${docId}`, {
      method: "DELETE",
      headers: authHeaders,
    });
    if (!res.ok) throw new Error(await res.text());
  },

  // Chats
  async getChats(slug: string): Promise<Chat[]> {
    const authHeaders = await getAuthHeader();
    const res = await fetch(`${API_BASE}/assistants/${slug}/chats`, { headers: authHeaders });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async createChat(slug: string, title?: string): Promise<Chat> {
    const authHeaders = await getAuthHeader();
    const res = await fetch(`${API_BASE}/assistants/${slug}/chats`, {
      method: "POST",
      headers: { ...authHeaders, "Content-Type": "application/json" },
      body: JSON.stringify({ title: title || "New Chat" }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async getChatMessages(slug: string, chatId: string): Promise<Message[]> {
    const authHeaders = await getAuthHeader();
    const res = await fetch(`${API_BASE}/assistants/${slug}/chats/${chatId}/messages`, {
      headers: authHeaders,
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async sendMessage(slug: string, chatId: string, content: string): Promise<Message> {
    const authHeaders = await getAuthHeader();
    const res = await fetch(`${API_BASE}/assistants/${slug}/chats/${chatId}/messages`, {
      method: "POST",
      headers: { ...authHeaders, "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Failed to send message" }));
      throw new Error(err.detail || "Failed to send message");
    }
    return res.json();
  },

  async deleteChat(slug: string, chatId: string): Promise<void> {
    const authHeaders = await getAuthHeader();
    const res = await fetch(`${API_BASE}/assistants/${slug}/chats/${chatId}`, {
      method: "DELETE",
      headers: authHeaders,
    });
    if (!res.ok) throw new Error(await res.text());
  },

  // Preferences
  async getPreferences(): Promise<{ language: string; theme: string }> {
    const authHeaders = await getAuthHeader();
    const res = await fetch(`${API_BASE}/preferences`, { headers: authHeaders });
    if (!res.ok) return { language: "en", theme: "dark" };
    return res.json();
  },

  async updatePreferences(data: { language?: string; theme?: string }): Promise<void> {
    const authHeaders = await getAuthHeader();
    await fetch(`${API_BASE}/preferences`, {
      method: "PUT",
      headers: { ...authHeaders, "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
  },
};
