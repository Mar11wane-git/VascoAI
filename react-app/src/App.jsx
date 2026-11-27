import React, { Suspense, lazy, useState, useEffect, useRef } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  ArrowUp, ArrowUpRight, AudioLines, Brain, Calculator, Camera, ChevronDown, Code2, Copy, Download, FileText, Globe,
  Image as ImageIcon, ImagePlus, KeyRound, LogOut, MessageSquare, Mic, MonitorPlay, Moon, Newspaper, PanelLeft,
  Paperclip, Plus, Search, Sparkles, Sun, Trash2, UploadCloud, UserRound, Wand2, X,
} from 'lucide-react';
import AuthPage from './components/AuthPage.jsx';
import VoiceModeOverlay, { primeVoiceAudio } from './components/VoiceModeOverlay.jsx';
import AgentMessage from './components/AgentMessage.jsx';
import VascoLogo from './components/VascoLogo.jsx';
const ProfilePage = lazy(() => import('./components/profile/ProfilePage.jsx'));
import { apiFetch, streamAgent } from './lib/api.js';
import { deleteRemote, fetchRemote, loadSynced, mergeConversations, pushChanged, saveSynced } from './lib/convSync.js';
import { toolIcon } from './lib/icons.js';

function savePrefs(patch) {
  apiFetch('/api/profile', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  }).catch(() => {});
}

// Applies one streamed agent event to an assistant message (returns a new message object).
function applyAgentEvent(msg, ev) {
  const parts = [...(msg.parts || [])];
  const last = parts[parts.length - 1];
  switch (ev.type) {
    case 'text':
    case 'reasoning':
      if (last && last.type === ev.type) parts[parts.length - 1] = { ...last, text: last.text + ev.text };
      else parts.push({ type: ev.type, text: ev.text });
      return { ...msg, parts, statusText: '' };
    case 'tool_start':
      parts.push({ type: 'tool', id: ev.id, name: ev.name, label: ev.label, icon: ev.icon, args: ev.args, status: 'running' });
      return { ...msg, parts, statusText: '' };
    case 'tool_end':
      return {
        ...msg,
        parts: parts.map((p) =>
          p.type === 'tool' && p.id === ev.id
            ? { ...p, status: ev.ok ? 'ok' : 'error', summary: ev.summary, preview: ev.preview,
                duration_ms: ev.duration_ms, image_url: ev.image_url, video: ev.video }
            : p
        ),
        sources: [...(msg.sources || []), ...(ev.sources || [])],
      };
    case 'status':
      return { ...msg, statusText: ev.message };
    case 'notice':
      parts.push({ type: 'notice', text: ev.message });
      return { ...msg, parts };
    case 'done':
      return {
        ...msg,
        status: 'done',
        statusText: '',
        content: ev.content,
        sources: ev.sources?.length ? ev.sources : msg.sources,
        provider_used: ev.provider_used,
        model_used: ev.model_used,
        stats: { steps: ev.steps, tool_calls: ev.tool_calls, elapsed_ms: ev.elapsed_ms, usage: ev.usage },
      };
    case 'error':
      return { ...msg, status: 'error', statusText: '', error: ev.message };
    default:
      return msg;
  }
}

const textOfParts = (msg) =>
  (msg.parts || []).filter((p) => p.type === 'text').map((p) => p.text).join('\n\n').trim();
  
export default function App() {
  // Authentication State (Mandatory Gate)
  const [user, setUser] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem('vasco_user') || 'null');
    } catch {
      return null;
    }
  });
  const [authToken, setAuthToken] = useState(() => localStorage.getItem('vasco_auth_token') || '');
  const [authChecking, setAuthChecking] = useState(true);

  // UI & Theme State
  const [theme, setTheme] = useState(() => localStorage.getItem('vasco_theme') || 'dark');
  const mobileQuery = '(max-width: 768px)';
  const [isMobile, setIsMobile] = useState(() => window.matchMedia(mobileQuery).matches);
  // On phones the sidebar is an overlay: start closed so the chat is visible.
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => window.matchMedia(mobileQuery).matches);
  useEffect(() => {
    const mq = window.matchMedia(mobileQuery);
    const onChange = (e) => {
      setIsMobile(e.matches);
      setSidebarCollapsed(e.matches);
    };
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);
  const closeSidebarOnMobile = () => {
    if (window.matchMedia(mobileQuery).matches) setSidebarCollapsed(true);
  };
  const [modelDropdownOpen, setModelDropdownOpen] = useState(false);
  const [toasts, setToasts] = useState([]);
  const [view, setView] = useState('chat'); // 'chat' | 'profile'

  // Models & Providers
  const [providers, setProviders] = useState([]);
  const [keysStatus, setKeysStatus] = useState({});
  const [selectedProvider, setSelectedProvider] = useState(
    () => localStorage.getItem('vasco_provider') || 'vasco_free'
  );
  const [selectedModel, setSelectedModel] = useState(
    () => localStorage.getItem('vasco_model') || 'openai'
  );

  // Conversations (Scoped per authenticated user)
  const userId = user?.id;
  const storageKey = user ? `vasco_convs_${user.id}` : 'vasco_conversations';
  const [conversations, setConversations] = useState([]);
  const [currentConvId, setCurrentConvId] = useState(null);
  const [historySearch, setHistorySearch] = useState('');

  // Prompt Bar & Capabilities State
  const [promptText, setPromptText] = useState('');
  const [pendingAttachments, setPendingAttachments] = useState([]);
  const [deepThink, setDeepThink] = useState(false);
  const [webSearch, setWebSearch] = useState(true);
  const [imageGen, setImageGen] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isDragging, setIsDragging] = useState(false);

  // Modals & Drawers
  const [voiceModeOpen, setVoiceModeOpen] = useState(false);
  const [keysModalOpen, setKeysModalOpen] = useState(false);
  const [personaModalOpen, setPersonaModalOpen] = useState(false);
  const [cameraModalOpen, setCameraModalOpen] = useState(false);
  const [lightboxSrc, setLightboxSrc] = useState(null);
  const [canvasHtml, setCanvasHtml] = useState('');
  const [canvasOpen, setCanvasOpen] = useState(false);

  // Settings
  const [systemPrompt, setSystemPrompt] = useState(
    () => localStorage.getItem('vasco_system_prompt') || ''
  );
  const [autoTTS, setAutoTTS] = useState(() => localStorage.getItem('vasco_auto_tts') === 'true');
  const [keyInputs, setKeyInputs] = useState({
    POLLINATIONS_API_KEY: '',
    GEMINI_API_KEY: '',
    OPENAI_API_KEY: '',
    ANTHROPIC_API_KEY: '',
    DEEPSEEK_API_KEY: '',
    OPENROUTER_API_KEY: '',
    OLLAMA_BASE_URL: 'http://localhost:11434',
  });

  // Refs
  const chatScrollRef = useRef(null);
  const fileInputRef = useRef(null);
  const photoInputRef = useRef(null);
  const videoRef = useRef(null);
  const cameraStreamRef = useRef(null);
  const activeAudioRef = useRef(null);
  const abortRef = useRef(null);
  const lastAttachmentsRef = useRef({}); // convId -> attachments of the last user turn (for regenerate)
  const [agentTools, setAgentTools] = useState([]);
  const [customModel, setCustomModel] = useState('');

  const showToast = (msg) => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { id, msg }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 3000);
  };

  // Code block buttons (rendered as sanitized HTML) are handled by event delegation.
  const handleChatClick = (e) => {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    const code = decodeURIComponent(btn.getAttribute('data-code') || '');
    if (btn.dataset.action === 'copy') {
      navigator.clipboard.writeText(code);
      const orig = btn.textContent;
      btn.textContent = '✓ Copié !';
      setTimeout(() => (btn.textContent = orig), 1600);
    } else if (btn.dataset.action === 'preview') {
      setCanvasHtml(code);
      setCanvasOpen(true);
    }
  };

  // Theme application
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('vasco_theme', theme);
  }, [theme]);

  // Verify existing auth session or OAuth redirect token on startup
  useEffect(() => {
    async function verifySession() {
      const params = new URLSearchParams(window.location.search);
      const urlOauthToken = params.get('oauth_token');
      const tokenToVerify = urlOauthToken || authToken;
      if (urlOauthToken) {
        window.history.replaceState({}, document.title, '/');
      }
      if (!tokenToVerify) {
        setUser(null);
        setAuthChecking(false);
        return;
      }
      try {
        const res = await fetch('/api/auth/me', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token: tokenToVerify }),
        });
        if (res.ok) {
          const data = await res.json();
          setUser(data.user);
          setAuthToken(tokenToVerify);
          localStorage.setItem('vasco_auth_token', tokenToVerify);
          localStorage.setItem('vasco_user', JSON.stringify(data.user));
        } else {
          setUser(null);
          setAuthToken('');
          localStorage.removeItem('vasco_auth_token');
          localStorage.removeItem('vasco_user');
        }
      } catch (_) {
        // Keep cached user if offline
      } finally {
        setAuthChecking(false);
      }
    }
    verifySession();
    fetchModelsAndKeys();
  }, []);

  // Load conversations when user logs in: this device's copy first, then merged with the account's.
  const syncedRef = useRef({}); // convId -> fingerprint of the version the server has
  const syncReadyRef = useRef(false);
  const pushTimerRef = useRef(null);
  const pushingRef = useRef(false);
  useEffect(() => {
    if (!user) return undefined;
    syncReadyRef.current = false;
    syncedRef.current = loadSynced(userId);
    let local = [];
    try {
      local = JSON.parse(localStorage.getItem(storageKey) || '[]') || [];
    } catch {
      local = [];
    }
    if (!local.length) {
      local = [{ id: 'conv_' + Date.now(), title: 'Nouvelle discussion', createdAt: Date.now(), messages: [] }];
    }
    setConversations(local);
    setCurrentConvId(local[0].id);

    let cancelled = false;
    fetchRemote()
      .then((remote) => {
        if (cancelled) return;
        setConversations((current) => {
          const { merged, synced } = mergeConversations(current, remote, syncedRef.current);
          syncedRef.current = synced;
          saveSynced(userId, synced);
          setCurrentConvId((id) => (merged.some((c) => c.id === id) ? id : merged[0]?.id));
          return merged.length ? merged : current;
        });
      })
      .catch(() => {}) // offline or server issue: keep working from this device's copy
      .finally(() => {
        if (!cancelled) syncReadyRef.current = true;
      });
    return () => {
      cancelled = true;
    };
  }, [userId]);

  // Preferences saved in the MySQL profile follow the user across devices.
  const applyPrefs = (p, { initial = false } = {}) => {
    if (!p) return;
    if (p.pref_theme) setTheme(p.pref_theme);
    setSystemPrompt(p.system_prompt || '');
    localStorage.setItem('vasco_system_prompt', p.system_prompt || '');
    setAutoTTS(!!p.pref_auto_tts);
    localStorage.setItem('vasco_auto_tts', String(!!p.pref_auto_tts));
    if (p.pref_voice) localStorage.setItem('vasco_voice_gender', p.pref_voice);
    if (initial) setWebSearch(p.pref_web_search !== false);
  };

  useEffect(() => {
    if (!userId) return;
    apiFetch('/api/profile')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d) return;
        applyPrefs(d.profile, { initial: true });
        handleUserUpdate(d.user);
      })
      .catch(() => {});
  }, [userId]);

  const handleUserUpdate = (u) => {
    if (!u) return;
    setUser((prev) => {
      const next = { ...prev, id: u.id, name: u.name, email: u.email, avatar: u.avatar, username: u.username, provider: u.provider };
      try {
        localStorage.setItem('vasco_user', JSON.stringify(next));
      } catch (_) {}
      return next;
    });
  };

  // Persist conversations (skipped while streaming; saved once the run ends)
  useEffect(() => {
    if (user && conversations.length > 0 && !isGenerating) {
      try {
        localStorage.setItem(storageKey, JSON.stringify(conversations));
      } catch (_) {
        showToast('⚠️ Stockage local plein : supprimez d’anciennes discussions.');
      }
    }
    // Upload what changed to the account (debounced), once the sign-in merge is done.
    if (!user || isGenerating) return undefined;
    clearTimeout(pushTimerRef.current);
    pushTimerRef.current = setTimeout(async () => {
      if (!syncReadyRef.current || pushingRef.current) return;
      pushingRef.current = true;
      try {
        syncedRef.current = await pushChanged(conversations, syncedRef.current);
        saveSynced(userId, syncedRef.current);
      } catch (_) {
        /* offline: retried on the next change or the next sign-in */
      } finally {
        pushingRef.current = false;
      }
    }, 1200);
    return () => clearTimeout(pushTimerRef.current);
  }, [conversations, user, isGenerating]);

  // Auto-scroll chat
  const currentConversation = conversations.find((c) => c.id === currentConvId) || conversations[0];
  const lastMsg = currentConversation?.messages?.[currentConversation.messages.length - 1];
  const streamTick = lastMsg?.parts ? lastMsg.parts.length + (lastMsg.parts.at(-1)?.text?.length || 0) : 0;
  // Stick to the bottom while the answer grows, unless the user scrolled up to read.
  const stickToBottomRef = useRef(true);
  useEffect(() => {
    const el = chatScrollRef.current;
    if (!el || !currentConversation?.messages?.length) return undefined;
    let lastTop = el.scrollTop;
    const onScroll = () => {
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
      // Content growing under us must not unstick: only an upward scroll by the user does.
      if (el.scrollTop < lastTop - 2 && distance > 120) stickToBottomRef.current = false;
      else if (distance < 120) stickToBottomRef.current = true;
      lastTop = el.scrollTop;
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    let raf = 0;
    const follow = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        if (stickToBottomRef.current) el.scrollTop = el.scrollHeight;
      });
    };
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(follow) : null;
    if (ro && el.firstElementChild) ro.observe(el.firstElementChild);
    return () => {
      el.removeEventListener('scroll', onScroll);
      ro?.disconnect();
      cancelAnimationFrame(raf);
    };
  }, [currentConvId, Boolean(currentConversation?.messages?.length)]);

  useEffect(() => {
    const el = chatScrollRef.current;
    if (!el) return;
    // A new message (sent or received) always brings the view back to the bottom.
    stickToBottomRef.current = true;
    el.scrollTop = currentConversation?.messages?.length ? el.scrollHeight : 0;
  }, [currentConversation?.messages?.length, currentConvId]);

  useEffect(() => {
    const el = chatScrollRef.current;
    if (el && stickToBottomRef.current) el.scrollTop = el.scrollHeight;
  }, [streamTick]);

  const fetchModelsAndKeys = async () => {
    try {
      fetch('/api/agent/tools')
        .then((r) => r.json())
        .then((d) => setAgentTools(d.tools || []))
        .catch(() => {});
      const res = await fetch('/api/models');
      const data = await res.json();
      setProviders(data.providers || []);
      setKeysStatus(data.keys_status || {});

      // A saved provider may need a key that isn't configured (or no longer exist):
      // fall back to the free engine instead of failing on the first message.
      const savedProvider = localStorage.getItem('vasco_provider') || 'vasco_free';
      const prov = (data.providers || []).find((p) => p.id === savedProvider);
      const usable = prov && (!prov.requires_key || data.keys_status?.[prov.env_key]?.configured);
      if (!usable && savedProvider !== 'vasco_free') {
        setSelectedProvider('vasco_free');
        setSelectedModel('openai');
        localStorage.setItem('vasco_provider', 'vasco_free');
        localStorage.setItem('vasco_model', 'openai');
        showToast(`⚡ ${prov?.name?.replace(/^\S+\s*/, '') || savedProvider} n'a pas de clé API : passage au moteur gratuit Vasco Cloud.`);
      }
      if (data.keys_status?.OLLAMA_BASE_URL?.value) {
        setKeyInputs((prev) => ({
          ...prev,
          OLLAMA_BASE_URL: data.keys_status.OLLAMA_BASE_URL.value,
        }));
      }
    } catch (e) {
      console.error(e);
    }
  };

  // Auth Handlers (Login & Logout)
  const handleAuthSuccess = (loggedUser, token) => {
    setUser(loggedUser);
    setAuthToken(token);
    localStorage.setItem('vasco_auth_token', token);
    localStorage.setItem('vasco_user', JSON.stringify(loggedUser));
    showToast(`👋 Bienvenue sur Vasco AI, ${loggedUser.name} !`);
  };

  const handleLogout = async () => {
    setVoiceModeOpen(false);
    setView('chat');
    if (activeAudioRef.current) {
      activeAudioRef.current.pause();
      activeAudioRef.current = null;
    }
    try {
      if (authToken) {
        await fetch('/api/auth/logout', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token: authToken }),
        });
      }
    } catch (_) {}
    setUser(null);
    setAuthToken('');
    localStorage.removeItem('vasco_auth_token');
    localStorage.removeItem('vasco_user');
  };

  // Conversation Handlers
  const startNewChat = () => {
    const newConv = {
      id: 'conv_' + Date.now(),
      title: 'Nouvelle discussion',
      createdAt: Date.now(),
      messages: [],
    };
    setConversations((prev) => [newConv, ...prev]);
    setCurrentConvId(newConv.id);
    setView('chat');
    closeSidebarOnMobile();
  };

  const toggleTheme = () => {
    const next = theme === 'dark' ? 'light' : 'dark';
    setTheme(next);
    savePrefs({ pref_theme: next });
  };

  const openProfile = () => {
    setView('profile');
    closeSidebarOnMobile();
  };

  const deleteConv = (id, e) => {
    e.stopPropagation();
    deleteRemote(id);
    delete syncedRef.current[id];
    const remaining = conversations.filter((c) => c.id !== id);
    if (remaining.length === 0) {
      const fresh = {
        id: 'conv_' + Date.now(),
        title: 'Nouvelle discussion',
        createdAt: Date.now(),
        messages: [],
      };
      setConversations([fresh]);
      setCurrentConvId(fresh.id);
    } else {
      setConversations(remaining);
      if (currentConvId === id) setCurrentConvId(remaining[0].id);
    }
  };

  // File & Photo Upload
  const handleFilesUpload = async (fileList) => {
    if (!fileList || fileList.length === 0) return;
    const formData = new FormData();
    Array.from(fileList).forEach((f) => formData.append('files', f));
    showToast(`⏳ Analyse de ${fileList.length} fichier(s)...`);

    try {
      const res = await apiFetch('/api/upload', { method: 'POST', body: formData });
      const data = await res.json();
      if (data.files) {
        const valid = data.files.filter((f) => f.category !== 'error');
        setPendingAttachments((prev) => [...prev, ...valid]);
        showToast(`✅ ${valid.length} fichier(s) joint(s) !`);
      }
    } catch (_) {
      showToast('❌ Erreur lors du chargement du fichier.');
    }
  };

  // Send Chat Message
  const handleSendMessage = async (overrideText = null, overrideDeepThink = null) => {
    if (isGenerating) return;
    const text = (overrideText !== null ? overrideText : promptText).trim();
    if (!text && pendingAttachments.length === 0) return;

    const useDT = overrideDeepThink !== null ? overrideDeepThink : deepThink;
    const attachmentsToSend = [...pendingAttachments];

    const userMsg = {
      role: 'user',
      content: text || 'Analyse les fichiers/photos joints en détail.',
      attachments: attachmentsToSend.map((a) => ({
        filename: a.filename,
        category: a.category,
        size_human: a.size_human,
        summary: a.summary,
        data_url: a.data_url,
      })),
    };

    const targetConvId = currentConvId || conversations[0]?.id;
    const updatedConvs = conversations.map((c) => {
      if (c.id !== targetConvId) return c;
      const nextMsgs = [...c.messages, userMsg];
      const nextTitle =
        c.messages.length === 0
          ? (text || attachmentsToSend[0]?.filename || 'Discussion').slice(0, 42)
          : c.title;
      return { ...c, title: nextTitle, messages: nextMsgs };
    });

    setConversations(updatedConvs);
    setPromptText('');
    setPendingAttachments([]);
    lastAttachmentsRef.current[targetConvId] = attachmentsToSend;

    const activeConv = updatedConvs.find((c) => c.id === targetConvId);
    await runAgent(targetConvId, activeConv.messages, attachmentsToSend, useDT);
  };

  // Streams one agent run into a new assistant message of conversation `convId`.
  const runAgent = async (convId, history, attachments, useDT) => {
    const apiMessages = history.map((m) => ({ role: m.role, content: m.content || '' }));
    const updateLast = (fn) =>
      setConversations((prev) =>
        prev.map((c) => {
          if (c.id !== convId) return c;
          const msgs = [...c.messages];
          msgs[msgs.length - 1] = fn(msgs[msgs.length - 1]);
          return { ...c, messages: msgs };
        })
      );

    setConversations((prev) =>
      prev.map((c) =>
        c.id === convId
          ? { ...c, messages: [...c.messages, { role: 'assistant', content: '', parts: [], sources: [], status: 'streaming' }] }
          : c
      )
    );
    setIsGenerating(true);

    const controller = new AbortController();
    abortRef.current = controller;
    let pending = [];
    let timer = null;
    const flush = () => {
      timer = null;
      if (!pending.length) return;
      const batch = pending;
      pending = [];
      updateLast((m) => batch.reduce(applyAgentEvent, m));
    };
    let finalText = '';

    try {
      await streamAgent(
        {
          provider: selectedProvider,
          model: selectedModel,
          messages: apiMessages,
          attachments,
          deep_think: useDT,
          web_search: webSearch,
          image_gen: imageGen,
          system_prompt: systemPrompt,
        },
        {
          signal: controller.signal,
          onEvent: (ev) => {
            if (ev.type === 'done') finalText = ev.content;
            pending.push(ev);
            if (!timer) timer = setTimeout(flush, 40);
          },
        }
      );
      flush();
      updateLast((m) =>
        m.status === 'streaming' ? { ...m, status: 'error', error: 'Connexion interrompue avant la fin de la réponse.' } : m
      );
      if (autoTTS && finalText) handleSpeakText(finalText);
      if (imageGen) setImageGen(false);
    } catch (err) {
      clearTimeout(timer);
      flush();
      if (err.name === 'AbortError') {
        updateLast((m) => ({ ...m, status: 'stopped', content: textOfParts(m) }));
      } else {
        updateLast((m) => ({ ...m, status: 'error', error: err.message, content: textOfParts(m) }));
        if (err.status === 401) showToast('🔒 Session expirée : reconnectez-vous.');
      }
    } finally {
      abortRef.current = null;
      setIsGenerating(false);
    }
  };

  const handleStop = () => abortRef.current?.abort();

  const handleRegenerate = () => {
    if (isGenerating || !currentConversation) return;
    const msgs = currentConversation.messages;
    if (msgs.length < 2 || msgs[msgs.length - 1].role !== 'assistant') return;
    const history = msgs.slice(0, -1);
    const convId = currentConversation.id;
    setConversations((prev) => prev.map((c) => (c.id === convId ? { ...c, messages: history } : c)));
    runAgent(convId, history, lastAttachmentsRef.current[convId] || [], deepThink);
  };

  const handleSpeakText = async (text) => {
    if (activeAudioRef.current) {
      activeAudioRef.current.pause();
      activeAudioRef.current = null;
      return;
    }
    try {
      const res = await apiFetch('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, voice: localStorage.getItem('vasco_voice_gender') || 'male' }),
      });
      if (res.ok) {
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        activeAudioRef.current = audio;
        audio.onended = () => {
          activeAudioRef.current = null;
          URL.revokeObjectURL(url);
        };
        await audio.play();
      }
    } catch (_) {}
  };

  // Save turns from Live Voice Mode
  const handleNewVoiceTurnSaved = (spokenUserText, assistantMsgObj) => {
    const targetId = currentConvId || conversations[0]?.id;
    setConversations((prev) =>
      prev.map((c) => {
        if (c.id !== targetId) return c;
        const nextTitle = c.messages.length === 0 ? `🎙️ ${spokenUserText.slice(0, 35)}` : c.title;
        return {
          ...c,
          title: nextTitle,
          messages: [...c.messages, { role: 'user', content: spokenUserText }, assistantMsgObj],
        };
      })
    );
  };

  // Webcam Capture
  const openCamera = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true });
      cameraStreamRef.current = stream;
      setCameraModalOpen(true);
      setTimeout(() => {
        if (videoRef.current) videoRef.current.srcObject = stream;
      }, 100);
    } catch (_) {
      showToast('📷 Caméra non disponible, sélectionnez une image.');
      photoInputRef.current?.click();
    }
  };

  const closeCamera = () => {
    if (cameraStreamRef.current) {
      cameraStreamRef.current.getTracks().forEach((t) => t.stop());
      cameraStreamRef.current = null;
    }
    setCameraModalOpen(false);
  };

  const snapPhoto = () => {
    const video = videoRef.current;
    if (!video || !video.videoWidth) return;
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0);
    canvas.toBlob((blob) => {
      if (blob) {
        const file = new File([blob], `photo_vasco_${Date.now()}.png`, { type: 'image/png' });
        closeCamera();
        handleFilesUpload([file]);
      }
    }, 'image/png');
  };

  // Save API Keys
  const handleSaveKeys = async () => {
    const payload = {};
    Object.entries(keyInputs).forEach(([k, v]) => {
      if (v && v.trim()) payload[k] = v.trim();
    });
    try {
      const res = await apiFetch('/api/settings/keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) {
        showToast(`🔒 ${data.detail || 'Modification refusée.'}`);
        return;
      }
      if (data.keys_status) setKeysStatus(data.keys_status);
      setKeysModalOpen(false);
      showToast('✅ Clés API enregistrées !');
    } catch (_) {
      showToast('❌ Erreur lors de la sauvegarde.');
    }
  };

  // Export Markdown
  const handleExportChat = () => {
    if (!currentConversation || currentConversation.messages.length === 0) {
      showToast('Aucun message à exporter.');
      return;
    }
    let md = `# ${currentConversation.title}\n\n`;
    currentConversation.messages.forEach((m) => {
      md += `### ${m.role === 'user' ? '👤 Vous' : '✦ Vasco'}\n\n${m.content}\n\n---\n\n`;
    });
    const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${currentConversation.title.replace(/[^a-z0-9]/gi, '_')}.md`;
    a.click();
  };

  // Active Provider & Model info
  const activeProvObj = providers.find((p) => p.id === selectedProvider) || providers[0];
  const activeModObj =
    activeProvObj?.models?.find((m) => m.id === selectedModel) || activeProvObj?.models?.[0];

  // MANDATORY AUTHENTICATION GATE: Must Login to access Vasco!
  if (authChecking) {
    return <div className="auth-screen" />;
  }

  if (!user) {
    return <AuthPage onAuthSuccess={handleAuthSuccess} />;
  }

  return (
    <div
      className="app-layout"
      onDragOver={(e) => {
        e.preventDefault();
        setIsDragging(true);
      }}
      onDrop={(e) => {
        e.preventDefault();
        setIsDragging(false);
        if (e.dataTransfer?.files?.length) handleFilesUpload(e.dataTransfer.files);
      }}
    >
      {/* Drag & Drop Overlay */}
      {isDragging && (
        <div className="drop-overlay" onDragLeave={() => setIsDragging(false)}>
          <div className="drop-card">
            <div className="drop-icon"><UploadCloud size={40} /></div>
            <h3>Déposez vos fichiers et photos ici</h3>
            <p>Images, PDF, Word (.docx), Code (.py, .js, .html...), CSV, TXT</p>
          </div>
        </div>
      )}

      {/* ================= LEFT SIDEBAR ================= */}
      {!sidebarCollapsed && (
        <div className="sidebar-backdrop" onClick={() => setSidebarCollapsed(true)} aria-hidden="true" />
      )}
      <aside className={`sidebar ${sidebarCollapsed ? 'collapsed' : ''}`}>
        <div className="sidebar-top">
          <div className="brand">
            <div className="brand-logo">
              <VascoLogo size={40} />
            </div>
            <div className="brand-text">
              <span className="brand-name">Vasco</span>
              <span className="brand-tag">Agent IA autonome</span>
            </div>
          </div>
        </div>

        <div className="sidebar-actions">
          <button className="new-chat-btn" onClick={startNewChat}>
            <Plus size={17} strokeWidth={2.4} />
            <span>Nouvelle conversation</span>
          </button>
          <div className="search-box">
            <Search size={15} className="search-box-icon" />
            <input
              type="text"
              placeholder="Rechercher une discussion..."
              value={historySearch}
              onChange={(e) => setHistorySearch(e.target.value)}
            />
          </div>
        </div>

        <div className="sidebar-section-label">Historique</div>
        <div className="conversations-list">
          {conversations
            .filter((c) => c.title.toLowerCase().includes(historySearch.toLowerCase()))
            .map((conv) => (
              <div
                key={conv.id}
                className={`conv-item ${conv.id === currentConversation?.id && view === 'chat' ? 'active' : ''}`}
                onClick={() => {
                  setCurrentConvId(conv.id);
                  setView('chat');
                  closeSidebarOnMobile();
                }}
              >
                <MessageSquare size={15} className="conv-icon" />
                <span className="conv-title">{conv.title}</span>
                <button className="conv-delete-btn" onClick={(e) => deleteConv(conv.id, e)} title="Supprimer">
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
        </div>

        {/* Sidebar Footer with Authenticated User Profile & Logout */}
        <div className="sidebar-footer">
          <button className="footer-btn" onClick={() => setKeysModalOpen(true)}>
            <span className="footer-btn-icon"><KeyRound size={16} /></span>
            <div className="footer-btn-text">
              <strong>Clés API & Modèles</strong>
              <small>Vasco, Gemini, ChatGPT, Ollama</small>
            </div>
          </button>

          <button className="footer-btn" onClick={() => setPersonaModalOpen(true)}>
            <span className="footer-btn-icon"><Wand2 size={16} /></span>
            <div className="footer-btn-text">
              <strong>Personnaliser Vasco</strong>
              <small>Instructions & Voix</small>
            </div>
          </button>

          <div className="footer-bottom-row">
            <button className="footer-mini-btn" onClick={toggleTheme}>
              {theme === 'dark' ? <Moon size={14} /> : <Sun size={14} />}
              {theme === 'dark' ? 'Sombre' : 'Clair'}
            </button>
            <button
              className="footer-mini-btn danger-hover"
              onClick={() => {
                conversations.forEach((c) => {
                  deleteRemote(c.id);
                  delete syncedRef.current[c.id];
                });
                startNewChat();
                setConversations((prev) => [prev[0]]);
              }}
            >
              <Trash2 size={14} /> Effacer
            </button>
          </div>

          <div
            className={`sidebar-user-card ${view === 'profile' ? 'active' : ''}`}
            role="button"
            tabIndex={0}
            onClick={openProfile}
            onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && openProfile()}
            title="Mon profil"
          >
            <img
              src={
                user.avatar ||
                `https://ui-avatars.com/api/?name=${encodeURIComponent(user.name)}&background=7c3aed&color=fff`
              }
              alt={user.name}
              className="user-avatar-img"
              referrerPolicy="no-referrer"
            />
            <div className="user-card-info">
              <strong>{user.name}</strong>
              <small>{user.username ? `@${user.username}` : user.email}</small>
            </div>
            <button
              className="logout-icon-btn"
              onClick={(e) => {
                e.stopPropagation();
                handleLogout();
              }}
              title="Se déconnecter"
            >
              <LogOut size={17} />
            </button>
          </div>
        </div>
      </aside>

      {/* ================= PROFILE SPACE ================= */}
      {view === 'profile' && (
        <Suspense fallback={<div className="pf-page" />}>
          <ProfilePage
            user={user}
            onClose={() => setView('chat')}
            onUserUpdate={handleUserUpdate}
            onPrefsChange={(p) => applyPrefs(p)}
            onLogout={handleLogout}
            showToast={showToast}
            conversationsCount={conversations.filter((c) => c.messages.length > 0).length}
          />
        </Suspense>
      )}

      {/* ================= MAIN CHAT AREA ================= */}
      <main className="main-area" style={view === 'profile' ? { display: 'none' } : undefined}>
        <header className="top-header">
          <div className="header-left">
            <button className="icon-btn" onClick={() => setSidebarCollapsed(!sidebarCollapsed)} title="Afficher / masquer le menu">
              <PanelLeft size={18} />
            </button>
            <div className="header-title">
              <span className="header-title-mark"><VascoLogo size={26} /></span>
              <span className="header-title-text">{currentConversation?.messages?.length ? currentConversation.title : 'Vasco Agent'}</span>
              {isGenerating && <span className="header-live-dot" title="Génération en cours" />}
            </div>
          </div>

          <div className="header-right">
            <button className="header-pill-btn" onClick={() => setKeysModalOpen(true)}>
              <KeyRound size={15} />
              <span className="hide-sm">Clés API</span>
            </button>
            <button className={`header-pill-btn ${canvasOpen ? 'is-active' : ''}`} onClick={() => setCanvasOpen(!canvasOpen)}>
              <MonitorPlay size={15} />
              <span className="hide-sm">Canvas</span>
            </button>
            <button className="header-pill-btn" onClick={handleExportChat}>
              <Download size={15} />
              <span className="hide-sm">Exporter</span>
            </button>
            <button className="header-avatar-btn" onClick={openProfile} title="Mon profil">
              <img
                src={user.avatar || `https://ui-avatars.com/api/?name=${encodeURIComponent(user.name)}&background=7c3aed&color=fff`}
                alt=""
                referrerPolicy="no-referrer"
              />
            </button>
          </div>
        </header>

        {/* Chat Scroll Container */}
        <div ref={chatScrollRef} className="chat-scroll-container" onClick={handleChatClick}>
          {!currentConversation || currentConversation.messages.length === 0 ? (
            <div className="welcome-screen">
              <motion.div
                className="welcome-hero"
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
              >
                <motion.div
                  className="welcome-logo-wrap"
                  animate={{ y: [0, -8, 0], rotateZ: [0, -2, 0] }}
                  transition={{ duration: 6, repeat: Infinity, ease: 'easeInOut' }}
                >
                  <span className="welcome-logo-orbit" />
                  <VascoLogo size={isMobile ? 92 : 118} />
                </motion.div>
                <div className="welcome-badge">
                  <Sparkles size={13} /> Agent autonome · Web · Outils · Voix
                </div>
                <h1>
                  Bonjour {user.name.split(' ')[0]}, je suis <span className="serif-accent">Vasco</span>
                </h1>
                <p className="welcome-subtitle">
                  Un agent autonome qui <strong>cherche sur le Web, lit les sources, calcule et crée</strong> avant de
                  vous répondre — en <strong>Français, Darija, Arabe ou Anglais</strong>. Chaque étape est visible en direct.
                </p>
              </motion.div>

              <div className="starter-grid">
                {[
                  {
                    icon: Search, tone: 'blue', title: 'Recherche approfondie',
                    desc: 'L’agent cherche, lit plusieurs sources et compare dans un tableau sourcé',
                    prompt: "Fais une recherche approfondie : quelles sont les dernières avancées des voitures électriques en 2026 ? Compare 3 modèles (prix, autonomie) dans un tableau avec les sources.",
                  },
                  {
                    icon: Calculator, tone: 'teal', title: 'Calculs exacts',
                    desc: 'Crédit, pourcentages, conversions : calculés avec des outils, pas devinés',
                    prompt: "J'emprunte 250 000 MAD sur 20 ans à 4,2 % par an. Calcule la mensualité exacte et le coût total du crédit.",
                  },
                  {
                    icon: Newspaper, tone: 'amber', title: 'الدارجة المغربية & أخبار اليوم', rtl: true,
                    desc: 'هضر مع فاسكو بالدارجة أو سولو على أي خبر جديد فالمغرب والعالم',
                    prompt: 'سلام خويا فاسكو! شنو هي أهم الأخبار لي واقعة اليوم فالمغرب وفالعالم؟',
                  },
                  {
                    icon: Code2, tone: 'violet', title: 'Coder & Prévisualiser en direct',
                    desc: 'Générer une interface web HTML/CSS/JS et la tester dans le Canvas',
                    prompt: 'Crée une application complète de Pomodoro & To-Do List au design futuriste en un seul fichier HTML/CSS/JS interactif.',
                  },
                ].map((s, i) => (
                  <motion.button
                    key={s.title}
                    className={`starter-card tone-${s.tone}`}
                    onClick={() => handleSendMessage(s.prompt)}
                    initial={{ opacity: 0, y: 14 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.15 + i * 0.07, duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
                    whileHover={{ y: -3 }}
                    whileTap={{ scale: 0.98 }}
                  >
                    <ArrowUpRight size={17} className="starter-arrow" />
                    <div className="starter-icon"><s.icon size={19} /></div>
                    <div className="starter-content" dir={s.rtl ? 'rtl' : undefined}>
                      <h3>{s.title}</h3>
                      <p>{s.desc}</p>
                    </div>
                  </motion.button>
                ))}
              </div>
            </div>
          ) : (
            <div className="messages-list">
              {currentConversation.messages.map((msg, idx) => (
                <div key={idx} className={`message-row ${msg.role}`}>
                  <div className="msg-avatar">
                    {msg.role === 'user' ? (
                      user.avatar ? <img src={user.avatar} alt="" referrerPolicy="no-referrer" /> : <UserRound size={17} />
                    ) : (
                      <VascoLogo size={32} />
                    )}
                  </div>
                  <div className="msg-content-wrapper">
                    <div className="msg-header">
                      <strong>{msg.role === 'user' ? user.name : 'Vasco'}</strong>
                      {msg.model_used && (
                        <span className="msg-model-tag">
                          {msg.provider_used} • {msg.model_used}
                        </span>
                      )}
                    </div>

                    {/* Attachments */}
                    {msg.attachments?.length > 0 && (
                      <div className="msg-attachments-grid">
                        {msg.attachments.map((att, i) =>
                          att.category === 'image' && att.data_url ? (
                            <img
                              key={i}
                              src={att.data_url}
                              alt={att.filename}
                              className="msg-img-thumb"
                              onClick={() => setLightboxSrc(att.data_url)}
                            />
                          ) : (
                            <div key={i} className="msg-file-chip">
                              <FileText size={15} />
                              <strong>{att.filename}</strong>
                              <small>({att.size_human})</small>
                            </div>
                          )
                        )}
                      </div>
                    )}

                    {msg.role === 'user' ? (
                      <>
                        <div className="msg-bubble" dir="auto">
                          {msg.content}
                        </div>
                        <div className="msg-actions">
                          <button
                            className="msg-action-btn"
                            onClick={() => {
                              navigator.clipboard.writeText(msg.content || '');
                              showToast('Copié dans le presse-papiers');
                            }}
                          >
                            <Copy size={14} /> <span>Copier</span>
                          </button>
                        </div>
                      </>
                    ) : (
                      <AgentMessage
                        msg={msg}
                        isLast={idx === currentConversation.messages.length - 1}
                        onCopy={(text) => navigator.clipboard.writeText(text || '')}
                        onSpeak={handleSpeakText}
                        onRegenerate={isGenerating ? null : handleRegenerate}
                        onOpenImage={setLightboxSrc}
                      />
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* ================= MULTIMODAL PROMPT BAR ================= */}
        <div className="prompt-container-outer">
          <div className="prompt-box">
            {pendingAttachments.length > 0 && (
              <div className="attachments-tray">
                {pendingAttachments.map((att, idx) => (
                  <div key={idx} className="att-card">
                    {att.category === 'image' && att.data_url ? (
                      <img src={att.data_url} className="att-thumb" alt={att.filename} />
                    ) : (
                      <div className="att-icon-box"><FileText size={18} /></div>
                    )}
                    <div className="att-meta">
                      <span className="att-name">{att.filename}</span>
                      <span className="att-sub">{att.summary || att.size_human}</span>
                    </div>
                    <button
                      type="button"
                      className="att-remove"
                      onClick={() =>
                        setPendingAttachments((prev) => prev.filter((_, i) => i !== idx))
                      }
                    >
                      <X size={12} />
                    </button>
                  </div>
                ))}
              </div>
            )}

            <div className="prompt-input-row">
              <textarea
                dir="auto"
                rows="1"
                placeholder={
                  isMobile
                    ? 'Posez votre question à Vasco…'
                    : 'Posez votre question à Vasco... (Glissez un fichier, collez une image Ctrl+V ou cliquez sur le micro)'
                }
                value={promptText}
                onChange={(e) => setPromptText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    handleSendMessage();
                  }
                }}
                onPaste={(e) => {
                  const items = e.clipboardData?.items;
                  if (!items) return;
                  const files = [];
                  for (const item of items) {
                    if (item.kind === 'file') {
                      const f = item.getAsFile();
                      if (f) files.push(f);
                    }
                  }
                  if (files.length > 0) handleFilesUpload(files);
                }}
              />
            </div>

            <div className="prompt-toolbar">
              <div className="toolbar-left">
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  className="hidden"
                  onChange={(e) => {
                    handleFilesUpload(e.target.files);
                    e.target.value = '';
                  }}
                />
                <input
                  ref={photoInputRef}
                  type="file"
                  multiple
                  accept="image/*"
                  className="hidden"
                  onChange={(e) => {
                    handleFilesUpload(e.target.files);
                    e.target.value = '';
                  }}
                />

                <button
                  type="button"
                  className="tool-pill icon-only-sm"
                  onClick={() => fileInputRef.current?.click()}
                  title="Joindre un fichier"
                >
                  <Paperclip size={15} />
                  <span>Fichier</span>
                </button>

                <button
                  type="button"
                  className="tool-pill icon-only-sm"
                  onClick={() => photoInputRef.current?.click()}
                  title="Joindre une photo"
                >
                  <ImageIcon size={15} />
                  <span>Photo</span>
                </button>

                <button type="button" className="tool-pill icon-only-sm" onClick={openCamera} title="Prendre une photo">
                  <Camera size={15} />
                  <span className="hide-xs">Caméra</span>
                </button>

                <div className="toolbar-divider" />

                <button
                  type="button"
                  id="deepthink-toggle"
                  className={`tool-pill toggle-pill ${deepThink ? 'active' : ''}`}
                  onClick={() => setDeepThink(!deepThink)}
                  title="Réflexion approfondie"
                >
                  <Brain size={15} />
                  <span>Think</span>
                </button>

                <button
                  type="button"
                  id="websearch-toggle"
                  className={`tool-pill toggle-pill ${webSearch ? 'active' : ''}`}
                  onClick={() => setWebSearch(!webSearch)}
                  title="Recherche Web en direct"
                >
                  <Globe size={15} />
                  <span>Web Live</span>
                </button>

                <button
                  type="button"
                  id="imagegen-toggle"
                  className={`tool-pill toggle-pill ${imageGen ? 'active' : ''}`}
                  onClick={() => setImageGen(!imageGen)}
                  title="Créer une image"
                >
                  <ImagePlus size={15} />
                  <span className="hide-xs">Créer Image</span>
                </button>
              </div>

              <div className="toolbar-right">
                {/* Model selector (next to the mic) */}
                <div className="model-selector-wrapper composer-model-selector">
                  <button
                    type="button"
                    className="model-selector-btn"
                    onClick={() => setModelDropdownOpen(!modelDropdownOpen)}
                    title="Choisir le modèle IA"
                  >
                    <span className="provider-dot">{activeProvObj?.name?.split(' ')[0] || '⚡'}</span>
                    <div className="model-selector-info">
                      <span className="model-name">
                        {activeProvObj?.models?.some((m) => m.id === selectedModel)
                          ? activeModObj?.name
                          : selectedModel || 'Vasco Agent'}
                      </span>
                      <span className="provider-sub">
                        {activeProvObj?.name?.replace(/^\S+\s*/, '') || 'Vasco Cloud'} •{' '}
                        {activeModObj?.badge || 'Temps Réel'}
                      </span>
                    </div>
                    <ChevronDown size={14} className={`chevron ${modelDropdownOpen ? 'open' : ''}`} />
                  </button>

                  {modelDropdownOpen && (
                    <div className="model-dropdown">
                      <div className="dropdown-header">
                        <span>Choisir le modèle IA</span>
                        <button
                          className="link-btn"
                          onClick={() => {
                            setModelDropdownOpen(false);
                            setKeysModalOpen(true);
                          }}
                        >
                          ⚙️ Clés API
                        </button>
                      </div>
                      <div className="model-dropdown-list">
                        {providers.map((prov) => {
                          const isConfigured = !prov.requires_key || keysStatus[prov.env_key]?.configured;
                          return (
                            <div key={prov.id} className="provider-group">
                              <div className="provider-group-title">
                                <span>{prov.name}</span>
                                <span className={`key-status-pill ${isConfigured ? 'ready' : 'missing'}`}>
                                  {isConfigured ? '✓ Prêt' : 'Clé requise'}
                                </span>
                              </div>
                              {prov.models.map((m) => (
                                <button
                                  key={m.id}
                                  className={`model-option ${
                                    selectedProvider === prov.id && selectedModel === m.id ? 'selected' : ''
                                  }`}
                                  onClick={() => {
                                    setSelectedProvider(prov.id);
                                    setSelectedModel(m.id);
                                    localStorage.setItem('vasco_provider', prov.id);
                                    localStorage.setItem('vasco_model', m.id);
                                    setModelDropdownOpen(false);
                                    if (prov.requires_key && !isConfigured) {
                                      setKeysModalOpen(true);
                                    }
                                  }}
                                >
                                  <span>{m.name}</span>
                                  <span className="model-badge">{m.badge}</span>
                                </button>
                              ))}
                            </div>
                          );
                        })}
                      </div>
                      <form
                        className="custom-model-row"
                        onSubmit={(e) => {
                          e.preventDefault();
                          const id = customModel.trim();
                          if (!id) return;
                          setSelectedModel(id);
                          localStorage.setItem('vasco_model', id);
                          setModelDropdownOpen(false);
                          showToast(`✅ Modèle « ${id} » sélectionné (${activeProvObj?.name?.replace(/^\S+\s*/, '') || selectedProvider})`);
                        }}
                      >
                        <input
                          type="text"
                          placeholder={`Autre modèle ${activeProvObj?.name?.replace(/^\S+\s*/, '') || ''} (ID exact)…`}
                          value={customModel}
                          onChange={(e) => setCustomModel(e.target.value)}
                        />
                        <button type="submit" className="secondary-btn">Utiliser</button>
                      </form>
                    </div>
                  )}
                </div>

                {/* Mic Icon */}
                <button
                  type="button"
                  className="dictate-icon-btn"
                  onClick={() => {
                    primeVoiceAudio(); // inside the tap: required by iOS Safari
                    setVoiceModeOpen(true);
                  }}
                  title="Parler avec Vasco"
                >
                  <Mic size={19} />
                </button>

                {/* Live voice conversation */}
                <button
                  type="button"
                  className="voice-wave-btn"
                  onClick={() => {
                    primeVoiceAudio(); // inside the tap: required by iOS Safari
                    setVoiceModeOpen(true);
                  }}
                  title="Conversation vocale en direct avec Vasco"
                >
                  <AudioLines size={20} color="white" />
                </button>

                <AnimatePresence mode="popLayout" initial={false}>
                  {isGenerating ? (
                    <motion.button
                      key="stop"
                      type="button"
                      className="send-btn stop-btn"
                      onClick={handleStop}
                      title="Arrêter la génération"
                      initial={{ scale: 0.6, opacity: 0 }}
                      animate={{ scale: 1, opacity: 1 }}
                      exit={{ scale: 0.6, opacity: 0 }}
                    >
                      <span className="stop-square" />
                    </motion.button>
                  ) : (
                    (promptText.trim().length > 0 || pendingAttachments.length > 0) && (
                      <motion.button
                        key="send"
                        type="button"
                        className="send-btn"
                        onClick={() => handleSendMessage()}
                        title="Envoyer"
                        initial={{ scale: 0.6, opacity: 0, rotate: -45 }}
                        animate={{ scale: 1, opacity: 1, rotate: 0 }}
                        exit={{ scale: 0.6, opacity: 0 }}
                        whileTap={{ scale: 0.9 }}
                      >
                        <ArrowUp size={19} strokeWidth={2.6} />
                      </motion.button>
                    )
                  )}
                </AnimatePresence>
              </div>
            </div>
          </div>
          <div className="prompt-footnote">
            <span className="agent-tools-line">
              <strong>Agent</strong>
              {agentTools
                .filter((t) => t.enabled && (webSearch || !['web_search', 'fetch_url'].includes(t.name)))
                .map((t) => {
                  const Icon = toolIcon(t.name);
                  return (
                    <span key={t.name} className="agent-tool-chip" title={t.label}>
                      <Icon size={11} /> {t.label}
                    </span>
                  );
                })}
            </span>
          </div>
        </div>
      </main>

      {/* ================= LIVE CODE CANVAS DRAWER ================= */}
      {canvasOpen && view === 'chat' && (
        <aside className="canvas-drawer">
          <div className="canvas-header">
            <div className="canvas-title">
              <span>🖥️</span>
              <strong>Aperçu Interactif Vasco</strong>
            </div>
            <div className="canvas-actions">
              <button className="icon-btn" onClick={() => setCanvasOpen(false)}>
                ✕
              </button>
            </div>
          </div>
          <div className="canvas-body">
            <iframe
              id="canvas-iframe"
              title="Vasco Canvas"
              sandbox="allow-scripts allow-modals"
              srcDoc={
                canvasHtml ||
                `<body style="font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:90vh;color:#64748b;text-align:center;"><div><h2>🖥️ Vasco Live Canvas</h2><p>Cliquez sur <b>▶ Aperçu Live</b> sur un bloc de code HTML/SVG généré par Vasco.</p></div></body>`
              }
            />
          </div>
        </aside>
      )}

      {/* ================= LIVE VOICE MODE OVERLAY (CHATGPT CLOUD ORB) ================= */}
      <VoiceModeOverlay
        isOpen={voiceModeOpen}
        onClose={() => setVoiceModeOpen(false)}
        selectedProvider={selectedProvider}
        selectedModel={selectedModel}
        webSearch={webSearch}
        systemPrompt={systemPrompt}
        conversationMessages={currentConversation?.messages || []}
        onNewVoiceTurnSaved={handleNewVoiceTurnSaved}
      />

      {/* ================= API KEYS MODAL ================= */}
      {keysModalOpen && (
        <div className="modal-backdrop" onClick={() => setKeysModalOpen(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <h2>🔑 Configuration des Clés API</h2>
                <p>
                  Vasco fonctionne immédiatement en mode <strong>⚡ Vasco Cloud Turbo</strong>.
                  Ajoutez vos clés personnelles si vous le souhaitez.
                </p>
              </div>
              <button className="icon-btn" onClick={() => setKeysModalOpen(false)}>
                ✕
              </button>
            </div>
            <div className="modal-body">
              {[
                { key: 'GEMINI_API_KEY', label: '✨ Google Gemini', ph: 'AIzaSy...', free: 'Gratuit · sans carte bancaire · recommandé', url: 'https://aistudio.google.com/apikey' },
                { key: 'POLLINATIONS_API_KEY', label: '⚡ Vasco Cloud (Pollinations) — supprime l’attente de 45 s', ph: 'Clé pollen', free: 'Gratuit', url: 'https://enter.pollinations.ai' },
                { key: 'OPENAI_API_KEY', label: '🟢 ChatGPT (OpenAI)', ph: 'sk-proj-...', url: 'https://platform.openai.com/api-keys' },
              ].map((item) => (
                <div key={item.key} className="key-field-group">
                  <label>
                    <span>
                      {item.label}{' '}
                      {keysStatus[item.key]?.configured && (
                        <span className="key-badge ok">✓ Active ({keysStatus[item.key].masked})</span>
                      )}
                    </span>
                    <span className="key-get">
                      {item.free && <span className="key-free-badge">{item.free}</span>}
                      <a href={item.url} target="_blank" rel="noopener noreferrer">Obtenir une clé ↗</a>
                    </span>
                  </label>
                  <input
                    type="password"
                    placeholder={
                      keysStatus[item.key]?.configured
                        ? `Clé active (${keysStatus[item.key].masked})`
                        : item.ph
                    }
                    value={keyInputs[item.key]}
                    onChange={(e) =>
                      setKeyInputs((prev) => ({ ...prev, [item.key]: e.target.value }))
                    }
                  />
                </div>
              ))}
            </div>
            <div className="modal-footer">
              <button className="secondary-btn" onClick={() => setKeysModalOpen(false)}>
                Annuler
              </button>
              <button className="primary-btn" onClick={handleSaveKeys}>
                💾 Enregistrer
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ================= PERSONA MODAL ================= */}
      {personaModalOpen && (
        <div className="modal-backdrop" onClick={() => setPersonaModalOpen(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <h2>🎭 Personnaliser Vasco</h2>
                <p>Définissez les instructions personnalisées de votre agent.</p>
              </div>
              <button className="icon-btn" onClick={() => setPersonaModalOpen(false)}>
                ✕
              </button>
            </div>
            <div className="modal-body">
              <div className="key-field-group">
                <label>System Prompt :</label>
                <textarea
                  rows="4"
                  value={systemPrompt}
                  onChange={(e) => setSystemPrompt(e.target.value)}
                  placeholder="Instructions personnalisées pour Vasco..."
                />
              </div>
              <div className="checkbox-row">
                <input
                  type="checkbox"
                  id="auto-tts"
                  checked={autoTTS}
                  onChange={(e) => setAutoTTS(e.target.checked)}
                />
                <label htmlFor="auto-tts">
                  🔊 Lire automatiquement les réponses à voix haute
                </label>
              </div>
            </div>
            <div className="modal-footer">
              <button
                className="primary-btn"
                onClick={() => {
                  localStorage.setItem('vasco_system_prompt', systemPrompt);
                  localStorage.setItem('vasco_auto_tts', String(autoTTS));
                  savePrefs({ system_prompt: systemPrompt, pref_auto_tts: autoTTS });
                  setPersonaModalOpen(false);
                  showToast('✅ Préférences enregistrées !');
                }}
              >
                Enregistrer
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ================= CAMERA MODAL ================= */}
      {cameraModalOpen && (
        <div className="modal-backdrop" onClick={closeCamera}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>📷 Prendre une photo</h2>
              <button className="icon-btn" onClick={closeCamera}>
                ✕
              </button>
            </div>
            <div className="modal-body camera-body">
              <video ref={videoRef} id="camera-video" autoPlay playsInline />
            </div>
            <div className="modal-footer">
              <button className="primary-btn" onClick={snapPhoto}>
                📸 Capturer et joindre
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ================= LIGHTBOX ================= */}
      {lightboxSrc && (
        <div className="modal-backdrop" onClick={() => setLightboxSrc(null)}>
          <button className="lightbox-close" onClick={() => setLightboxSrc(null)}>
            ✕
          </button>
          <img id="lightbox-img" src={lightboxSrc} alt="Aperçu" />
        </div>
      )}

      {/* Toasts */}
      <div className="toast-container">
        <AnimatePresence>
          {toasts.map((t) => (
            <motion.div
              key={t.id}
              layout
              className="toast"
              initial={{ opacity: 0, y: 16, scale: 0.96 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 8, scale: 0.96 }}
              transition={{ type: 'spring', stiffness: 420, damping: 32 }}
            >
              {t.msg}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}
