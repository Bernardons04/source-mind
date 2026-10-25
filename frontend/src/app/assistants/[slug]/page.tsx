"use client";

import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";

export default function AssistantRootRedirect() {
  const params = useParams();
  const router = useRouter();

  useEffect(() => {
    if (params?.slug) {
      router.replace(`/assistants/${params.slug}/chat`);
    }
  }, [params?.slug, router]);

  return null;
}
