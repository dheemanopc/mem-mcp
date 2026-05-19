/**
 * MarkdownContent — safe markdown renderer for memory content.
 *
 * Uses react-markdown + remark-gfm (tables, strikethrough, task lists,
 * autolinks) + rehype-sanitize. The sanitize schema starts from defaultSchema
 * (which strips <script>, <iframe>, on*= handlers, javascript: URIs) and is
 * left at the default — we don't grant any additional powers.
 *
 * Headings, paragraphs, lists, code blocks, inline code, links, blockquotes,
 * tables, hr — all render with theme-token styles (no raw colors).
 *
 * Variant "preview" caps line count for list/card preview; "full" renders
 * the whole thing.
 */

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";

export function MarkdownContent({
  content,
  variant = "full",
}: {
  content: string;
  variant?: "full" | "preview";
}) {
  const wrapperClass =
    variant === "preview"
      ? "prose prose-sm max-w-none line-clamp-4 text-ink"
      : "prose max-w-none text-ink prose-headings:text-ink prose-strong:text-ink prose-a:text-primary prose-code:text-ink prose-code:bg-surface-subtle prose-code:px-1 prose-code:rounded prose-code:before:content-none prose-code:after:content-none prose-pre:bg-surface-subtle prose-pre:text-ink prose-blockquote:border-l-primary prose-blockquote:text-ink-muted prose-hr:border-border";
  return (
    <div className={wrapperClass}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSanitize]}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
