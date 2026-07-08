"use client";

import { useState, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";

function CodeBlock({ className, children }: { className?: string; children?: React.ReactNode }) {
  const [copied, setCopied] = useState(false);
  const lang = /language-(\w+)/.exec(className ?? "")?.[1] ?? "";
  const text = String(children ?? "").replace(/\n$/, "");

  const copy = useCallback(() => {
    navigator.clipboard?.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  }, [text]);

  return (
    <div className="md-pre">
      <div className="md-pre-header">
        <span className="md-pre-lang">{lang || "code"}</span>
        <button className="md-pre-copy" onClick={copy}>
          {copied ? "kopierat ✓" : "⎘ kopiera"}
        </button>
      </div>
      <pre><code>{text}</code></pre>
    </div>
  );
}

const components: Components = {
  // suppress the outer <pre> — CodeBlock renders its own
  pre({ children }) {
    return <>{children}</>;
  },
  code({ className, children }) {
    // block code: remark gives it a language-xxx class
    if (className?.startsWith("language-")) {
      return <CodeBlock className={className}>{children}</CodeBlock>;
    }
    return <code className="md-code">{children}</code>;
  },
};

export default function Markdown({ children }: { children: string }) {
  // `prose--answer` sets the agent's spoken prose in the serif voice
  // (--font-prose), per the DS conversation-detail iteration. Raw tool/text
  // output uses the bare `.prose` class and stays sans.
  return (
    <div className="prose prose--answer">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
