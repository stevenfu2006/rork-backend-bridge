'use client';

import { useState } from 'react';

interface SSEEvent {
  type: string;
  message: string;
  payload: string;
  progress: number;
}

const TAB_LABELS: Record<string, string> = {
  schema: 'Schema SQL',
  migrations: 'Migrations',
  fastapi_routes: 'FastAPI Routes',
  supabase: 'Supabase Config',
  openapi: 'OpenAPI Spec',
  back_to_rork: 'Back to Rork',
};

const EVENT_ICONS: Record<string, string> = {
  schema: '[S]',
  migrations: '[M]',
  fastapi_routes: '[F]',
  supabase: '[D]',
  openapi: '[O]',
  back_to_rork: '[R]',
  done: '[✓]',
};

const ARTIFACT_TYPES = ['schema', 'migrations', 'fastapi_routes', 'supabase', 'openapi', 'back_to_rork'];

export default function Home() {
  const [description, setDescription] = useState('');
  const [appName, setAppName] = useState('');
  const [includeAuth, setIncludeAuth] = useState(false);
  const [includeSupabase, setIncludeSupabase] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [events, setEvents] = useState<SSEEvent[]>([]);
  const [isDone, setIsDone] = useState(false);
  const [activeTab, setActiveTab] = useState('schema');
  const [copiedTab, setCopiedTab] = useState<string | null>(null);
  const [currentProgress, setCurrentProgress] = useState(0);

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setIsLoading(true);
    setEvents([]);
    setIsDone(false);
    setCurrentProgress(0);
    setActiveTab('schema');

    try {
      const res = await fetch('/api/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          description,
          app_name: appName,
          include_auth: includeAuth,
          include_supabase: includeSupabase,
        }),
      });

      if (!res.body) throw new Error('No response body');

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const data = JSON.parse(line.slice(6)) as SSEEvent;
            setEvents(prev => [...prev, data]);
            setCurrentProgress(data.progress);
            if (data.type === 'done') setIsDone(true);
          } catch {
            // skip malformed line
          }
        }
      }
    } catch (err) {
      console.error('Stream error:', err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleCopy = async (text: string, tab: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedTab(tab);
      setTimeout(() => setCopiedTab(null), 2000);
    } catch {
      // clipboard unavailable (non-HTTPS)
    }
  };

  const artifacts = events.filter(e => ARTIFACT_TYPES.includes(e.type));
  const activeArtifact = artifacts.find(e => e.type === activeTab);

  return (
    <div style={{ maxWidth: '760px', margin: '0 auto', padding: '48px 20px 80px' }}>
      {/* Header */}
      <h1 style={{ fontSize: '26px', fontWeight: 700, margin: '0 0 6px', color: '#0f172a', letterSpacing: '-0.5px' }}>
        Rork Backend Bridge
      </h1>
      <p style={{ margin: '0 0 36px', fontSize: '14px', color: '#64748b' }}>
        Describe your Rork app and get a deployable backend — schema, migrations, routes, and more.
      </p>

      {/* Form */}
      <form onSubmit={handleSubmit}>
        <div style={{ marginBottom: '16px' }}>
          <label style={labelStyle}>App name</label>
          <input
            type="text"
            value={appName}
            onChange={e => setAppName(e.target.value)}
            placeholder="my-todo-app"
            required
            disabled={isLoading}
            style={inputStyle(isLoading)}
          />
        </div>

        <div style={{ marginBottom: '16px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '6px' }}>
            <label style={labelStyle}>App description</label>
            <button
              type="button"
              disabled={isLoading}
              onClick={() => setDescription('A habit tracking app where users set daily goals, log completions, build streaks, and see a weekly summary dashboard.')}
              style={{
                background: 'none',
                border: 'none',
                padding: 0,
                fontSize: '12px',
                color: '#2563eb',
                cursor: isLoading ? 'not-allowed' : 'pointer',
                opacity: isLoading ? 0.4 : 1,
                textDecoration: 'none',
                whiteSpace: 'nowrap',
              }}
            >
              Try an example →
            </button>
          </div>
          <textarea
            value={description}
            onChange={e => setDescription(e.target.value)}
            placeholder="A task manager where users can create projects, add tasks with due dates, assign them to team members, and mark them complete…"
            required
            disabled={isLoading}
            style={{ ...inputStyle(isLoading), height: '110px', resize: 'vertical' }}
          />
        </div>

        <div style={{ display: 'flex', gap: '28px', marginBottom: '24px' }}>
          {([
            { label: 'Include JWT auth', value: includeAuth, setter: setIncludeAuth },
            { label: 'Include Supabase config', value: includeSupabase, setter: setIncludeSupabase },
          ] as const).map(({ label, value, setter }) => (
            <label key={label} style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', fontSize: '14px', color: '#374151', userSelect: 'none' }}>
              <input
                type="checkbox"
                checked={value}
                onChange={e => setter(e.target.checked)}
                disabled={isLoading}
                style={{ width: '15px', height: '15px', cursor: 'pointer' }}
              />
              {label}
            </label>
          ))}
        </div>

        <button
          type="submit"
          disabled={isLoading}
          style={{
            padding: '10px 28px',
            backgroundColor: isLoading ? '#93c5fd' : '#2563eb',
            color: '#fff',
            border: 'none',
            borderRadius: '6px',
            fontSize: '14px',
            fontWeight: 600,
            cursor: isLoading ? 'not-allowed' : 'pointer',
            letterSpacing: '0.01em',
          }}
        >
          {isLoading ? 'Generating…' : 'Generate Backend'}
        </button>
      </form>

      {/* Event timeline */}
      {events.length > 0 && (
        <div style={{ marginTop: '44px' }}>
          {/* Progress bar */}
          <div style={{ marginBottom: '20px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', color: '#94a3b8', marginBottom: '6px' }}>
              <span>Progress</span>
              <span>{currentProgress}%</span>
            </div>
            <div style={{ height: '5px', backgroundColor: '#e2e8f0', borderRadius: '99px', overflow: 'hidden' }}>
              <div style={{
                height: '100%',
                width: `${currentProgress}%`,
                backgroundColor: currentProgress === 100 ? '#16a34a' : '#2563eb',
                borderRadius: '99px',
                transition: 'width 0.35s ease, background-color 0.35s ease',
              }} />
            </div>
          </div>

          {/* Event list */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginBottom: '32px' }}>
            {events.map((ev, i) => (
              <div
                key={i}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '10px',
                  padding: '9px 14px',
                  borderRadius: '6px',
                  border: `1px solid ${ev.type === 'done' ? '#bbf7d0' : '#e2e8f0'}`,
                  backgroundColor: ev.type === 'done' ? '#f0fdf4' : '#ffffff',
                }}
              >
                <span style={{
                  fontFamily: 'Monaco, Menlo, monospace',
                  fontSize: '11px',
                  fontWeight: 700,
                  color: ev.type === 'done' ? '#16a34a' : '#2563eb',
                  minWidth: '28px',
                }}>
                  {EVENT_ICONS[ev.type] ?? '[·]'}
                </span>
                <span style={{ fontSize: '13px', color: '#334155' }}>{ev.message}</span>
              </div>
            ))}
          </div>

          {/* Tabbed artifact panel */}
          {isDone && artifacts.length > 0 && (
            <div style={{ border: '1px solid #e2e8f0', borderRadius: '8px', overflow: 'hidden' }}>
              {/* Tab bar */}
              <div style={{ display: 'flex', flexWrap: 'wrap', backgroundColor: '#f8fafc', borderBottom: '1px solid #e2e8f0' }}>
                {artifacts.map(ev => (
                  <button
                    key={ev.type}
                    onClick={() => setActiveTab(ev.type)}
                    style={{
                      padding: '10px 15px',
                      border: 'none',
                      borderBottom: activeTab === ev.type ? '2px solid #2563eb' : '2px solid transparent',
                      backgroundColor: 'transparent',
                      cursor: 'pointer',
                      fontSize: '12px',
                      fontWeight: activeTab === ev.type ? 700 : 400,
                      color: activeTab === ev.type ? '#2563eb' : '#64748b',
                      whiteSpace: 'nowrap',
                      marginBottom: '-1px',
                    }}
                  >
                    {TAB_LABELS[ev.type] ?? ev.type}
                  </button>
                ))}
              </div>

              {/* Copy bar */}
              <div style={{ display: 'flex', justifyContent: 'flex-end', padding: '8px 12px', backgroundColor: '#1e293b', borderBottom: '1px solid #334155' }}>
                <button
                  onClick={() => activeArtifact && handleCopy(activeArtifact.payload, activeArtifact.type)}
                  style={{
                    padding: '4px 14px',
                    fontSize: '11px',
                    fontWeight: 600,
                    backgroundColor: copiedTab === activeTab ? '#16a34a' : '#334155',
                    color: '#e2e8f0',
                    border: '1px solid #475569',
                    borderRadius: '4px',
                    cursor: 'pointer',
                    letterSpacing: '0.03em',
                    transition: 'background-color 0.2s',
                  }}
                >
                  {copiedTab === activeTab ? 'Copied!' : 'Copy'}
                </button>
              </div>

              {/* Code pane */}
              <pre style={{
                margin: 0,
                padding: '20px',
                fontSize: '12px',
                lineHeight: '1.65',
                overflowX: 'auto',
                backgroundColor: '#0f172a',
                color: '#cbd5e1',
                fontFamily: 'Monaco, Menlo, "Courier New", monospace',
                minHeight: '220px',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}>
                {activeArtifact?.payload ?? ''}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const labelStyle: React.CSSProperties = {
  display: 'block',
  marginBottom: '6px',
  fontSize: '13px',
  fontWeight: 500,
  color: '#374151',
};

function inputStyle(disabled: boolean): React.CSSProperties {
  return {
    width: '100%',
    padding: '9px 12px',
    border: '1px solid #d1d5db',
    borderRadius: '6px',
    fontSize: '14px',
    boxSizing: 'border-box',
    fontFamily: 'inherit',
    backgroundColor: disabled ? '#f8fafc' : '#ffffff',
    color: '#0f172a',
    outline: 'none',
  };
}
