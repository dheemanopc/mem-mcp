export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-4">
      <div className="text-center max-w-xl">
        <h1 className="text-4xl font-bold mb-4">mem-mcp</h1>
        <p className="text-lg text-gray-600 mb-8">
          A personal memory layer for AI assistants. Connect Claude, ChatGPT,
          and other clients to a single private memory you control.
        </p>
        <div className="flex flex-col sm:flex-row gap-3 justify-center">
          <a
            href="/auth/login"
            className="inline-block px-6 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition font-medium"
          >
            Sign in with Google
          </a>
          <a
            href="/docs"
            className="inline-block px-6 py-2 border border-gray-300 text-gray-700 rounded hover:bg-gray-50 transition"
          >
            View Docs
          </a>
        </div>
        <p className="text-sm text-gray-500 mt-6">
          Don&apos;t have access yet?{" "}
          <a href="/request-access" className="text-blue-600 hover:underline">
            Request access
          </a>
          .
        </p>
      </div>
    </main>
  )
}
