import Link from "next/link";

export const metadata = {
  title: "Blog — memsys",
  description: "Stories and patterns from building memsys.",
};

interface Post {
  slug: string;
  title: string;
  date: string;
  blurb: string;
  author: string;
}

const POSTS: Post[] = [
  {
    slug: "when-the-ai-remembered-yesterdays-experiment",
    title: "When the AI Remembered Yesterday's Experiment and Rewrote Today's Design",
    date: "2026-05-19",
    blurb:
      "Three memories from yesterday rewrote a design we'd just finished agreeing to. The brainstorm provides creativity. The memory provides discipline.",
    author: "Anand Vaidya",
  },
  {
    slug: "the-day-my-backend-talked-to-claude",
    title: "The day my backend talked to Claude",
    date: "2026-05-19",
    blurb:
      "11 PM. My trading backend filed a bug report to my AI assistant. By 1 AM, three pull requests were live. Neither agent involved a human as a translator.",
    author: "Anand Vaidya",
  },
];

export default function BlogIndex() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-12">
      <h1 className="text-3xl font-bold mb-2">Blog</h1>
      <p className="text-gray-600 mb-8">
        Stories and patterns from building memsys — the cross-agent memory layer.
      </p>
      <ul className="space-y-6">
        {POSTS.map((p) => (
          <li key={p.slug} className="border-b pb-6">
            <p className="text-sm text-gray-500 mb-1">
              {new Date(p.date).toLocaleDateString(undefined, {
                year: "numeric",
                month: "long",
                day: "numeric",
              })}{" "}
              · {p.author}
            </p>
            <h2 className="text-xl font-semibold mb-2">
              <Link href={`/blog/${p.slug}`} className="hover:text-blue-600">
                {p.title}
              </Link>
            </h2>
            <p className="text-gray-700">{p.blurb}</p>
            <p className="mt-2">
              <Link
                href={`/blog/${p.slug}`}
                className="text-sm text-blue-600 hover:underline"
              >
                Read →
              </Link>
            </p>
          </li>
        ))}
      </ul>
    </main>
  );
}
