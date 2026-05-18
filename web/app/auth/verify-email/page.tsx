"use client";

import { useEffect, useState, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";

function VerifyEmailContent() {
  const params = useSearchParams();
  const router = useRouter();
  const token = params.get("token");
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");
  const [errorMsg, setErrorMsg] = useState("");

  useEffect(() => {
    if (!token) {
      setState("error");
      setErrorMsg("Missing verification token.");
      return;
    }
    (async () => {
      try {
        const res = await fetch(
          `/api/public/signup-requests/verify?token=${encodeURIComponent(token)}`
        );
        if (res.ok) {
          setState("ok");
          setTimeout(() => router.push("/request-access?verified=1"), 1500);
        } else {
          const body = await res.json().catch(() => ({}));
          setErrorMsg(body.detail ?? "Verification failed.");
          setState("error");
        }
      } catch {
        setErrorMsg("Network error. Please try again.");
        setState("error");
      }
    })();
  }, [token, router]);

  return (
    <main className="mx-auto max-w-md px-4 py-16 text-center">
      {state === "loading" && (
        <>
          <h1 className="text-2xl font-semibold mb-2">Verifying...</h1>
          <p className="text-gray-600">Hold on a second.</p>
        </>
      )}
      {state === "ok" && (
        <>
          <h1 className="text-2xl font-semibold mb-2 text-green-700">
            Email verified
          </h1>
          <p className="text-gray-600">
            Your request is now in the review queue. Redirecting...
          </p>
        </>
      )}
      {state === "error" && (
        <>
          <h1 className="text-2xl font-semibold mb-2 text-red-700">
            Couldn&apos;t verify
          </h1>
          <p className="text-gray-700">{errorMsg}</p>
          <a
            href="/request-access"
            className="inline-block mt-4 text-blue-600 hover:underline"
          >
            Submit a new request
          </a>
        </>
      )}
    </main>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense>
      <VerifyEmailContent />
    </Suspense>
  );
}
