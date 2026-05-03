export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-4">
      <div className="text-center">
        <h1 className="text-4xl font-bold mb-4">mem-mcp</h1>
        <p className="text-lg text-gray-600 mb-8">
          Memory protocol web interface
        </p>
        <a
          href="#docs"
          className="inline-block px-6 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition"
        >
          View Docs
        </a>
      </div>
    </main>
  )
}
